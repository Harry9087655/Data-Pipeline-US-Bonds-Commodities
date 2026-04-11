# SEC Financial Analytics Pipeline

An end-to-end data engineering project that extracts structured financial data from SEC EDGAR filings using `edgartools`, applies a thin normalisation adapter to map standardised financial concepts to a consistent database schema, stores the data in a PostgreSQL star schema, and surfaces key financial metrics across companies in a Power BI dashboard.

Built as a portfolio project to demonstrate a full production-style pipeline — from raw regulatory filings to analyst-ready visualisations — across major US-listed companies.

---

## Data Source

**SEC EDGAR** via [`edgartools`](https://github.com/dgunning/edgartools) — a Python library that wraps the SEC EDGAR API and provides pre-standardised financial statements (income statement, balance sheet, cash flow) with ~95 canonical concepts mapped from ~18,000 raw XBRL tags.

The US Securities and Exchange Commission requires all public companies to file annual reports (10-K) in XBRL format, making structured financial data freely available via their public API. Each company is identified by a Central Index Key (CIK). `edgartools` handles CIK lookup, rate limiting, and XBRL parsing internally.

**Coverage**
- Filing types: 10-K (annual)
- History: 5 years (FY2019–FY2024)
- Companies: Apple (AAPL), Microsoft (MSFT), Alphabet (GOOGL), Meta (META), Amazon (AMZN)
- Expandable via `config/companies.yaml` — add any SEC-filing company by CIK/ticker

**Key financial concepts used** (standardised by `edgartools`)

| Concept (standard_concept)  | Used for                             |
|-----------------------------|--------------------------------------|
| Revenue                     | Top-line income                      |
| CostOfRevenue               | Cost of goods sold                   |
| OperatingIncome             | Operating profit                     |
| NetIncome                   | Bottom-line profit                   |
| Assets                      | Total assets                         |
| Liabilities                 | Total liabilities                    |
| Equity                      | Total stockholders' equity           |
| Cash                        | Cash & equivalents                   |
| Capex                       | Capital expenditures                 |
| DepreciationAndAmortization | D&A expense                          |
| OperatingCashFlow           | Cash from operations                 |
| AccountsReceivable          | Trade receivables                    |
| Inventory                   | Inventory on hand                    |
| AccountsPayable             | Trade payables                       |

> **Note on XBRL inconsistency:** Different companies use different XBRL tag names for the same financial concept (e.g., `Revenues`, `RevenueFromContractWithCustomer`, `SalesRevenueNet` all mean revenue). `edgartools` resolves this automatically using data-driven mappings built from 32,000+ real SEC filings, with industry-aware overrides via the Fama-French 48 classification. Our pipeline's normalisation layer is a thin adapter that maps `edgartools`' `standard_concept` names to our database schema column names — it does not perform XBRL tag resolution itself.

---

## File Structure

```
sec-financial-pipeline/
│
├── README.md
│
├── docker-compose.yml              # PostgreSQL + pgAdmin containers
│
├── config/
│   └── companies.yaml              # CIK numbers, tickers, company names
│
├── extraction/
│   ├── edgar_client.py             # Uses edgartools to fetch standardised financial statements
│   ├── lineage.py                  # Records source file, filing date, extraction timestamp per record
│   └── raw/                        # Raw API responses (gitignored)
│       └── {CIK}_companyfacts.json
│
├── normalisation/
│   ├── concept_mapping.py          # Thin adapter: maps edgartools standard_concept → database schema names
│   ├── llm_normaliser.py           # Fallback: LLM-assisted mapping for tags edgartools cannot resolve
│   └── concept_map.json            # Persisted {standard_concept: db_column_name} dictionary
│
├── transformation/
│   ├── transform.py                # Pandas transformations: derived metrics, growth rates, return ratios
│   └── load.py                     # Upserts transformed data into PostgreSQL fact/dim tables
│
├── schema/
│   └── init.sql                    # Star schema DDL — creates all tables on container start
│
├── validation/
│   ├── validate_quality.py         # Structural checks: nulls, bounds, accounting identity
│   └── validate_metrics.py         # Benchmark reconciliation against external sources
│
├── dags/
│   └── sec_pipeline_dag.py         # Airflow DAG — idempotent tasks, retries, data checkpoints
│
├── powerbi/
│   └── sec_dashboard.pbix          # Power BI report (connects to PostgreSQL)
│
└── notebooks/
    └── exploration.ipynb           # EDA and metric spot-checks during development
```

---

## Workflow

The pipeline runs in six sequential stages. Each Airflow task is idempotent — it can be re-run independently without corrupting downstream data. Stage 6 (Power BI) connects live to the database and reflects new data automatically.

```
┌──────────────────┐     ┌───────────────────┐     ┌──────────────────────┐
│  1. Extraction   │────▶│  2. Normalisation  │────▶│  3. Transformation   │
│  edgartools      │     │  concept_mapping   │     │  transform.py        │
│  standardised    │     │  (thin adapter:    │     │  metrics + growth    │
│  + lineage log   │     │   std → db schema) │     │  rates + ROE/ROA     │
└──────────────────┘     └───────────────────┘     └──────────────────────┘
                                                              │
                                                              ▼
                                                 ┌────────────────────────┐
                                                 │  4. Validation         │
                                                 │  quality checks +      │
                                                 │  benchmark reconcile   │
                                                 └────────────────────────┘
                                                              │
                                                              ▼
                                                 ┌────────────────────────┐
                                                 │  5. Load (PostgreSQL)  │
                                                 │  upsert → star schema  │
                                                 └────────────────────────┘
                                                              │
                                                              ▼
                                                 ┌────────────────────────┐
                                                 │  6. Visualisation      │
                                                 │  Power BI → Postgres   │
                                                 └────────────────────────┘
```

### Stage 1 — Extraction (`extraction/edgar_client.py`)

Uses `edgartools` to fetch standardised financial statements (income statement, balance sheet, cash flow) for each company defined in `config/companies.yaml`. `edgartools` handles CIK lookup, SEC rate limiting, XBRL parsing, and concept standardisation internally — mapping ~18,000 raw XBRL tags to ~95 canonical concepts using data-driven mappings built from 32,000+ real filings, with industry-aware overrides via the Fama-French 48 classification. Implements incremental loading — only fetches filings newer than the most recently ingested period. Saves raw responses to `extraction/raw/` and records source file, filing date, and extraction timestamp to the `dim_lineage` table via `lineage.py` for full data lineage tracking.

### Stage 2 — Normalisation (`normalisation/concept_mapping.py`)

A thin adapter layer that maps `edgartools`' `standard_concept` column names to the pipeline's database schema column names using `concept_map.json`. This decouples the pipeline from `edgartools`' naming conventions — if the library changes its labels in a future version, only `concept_map.json` needs updating, not the database schema or Power BI dashboard. For any XBRL tags that `edgartools` cannot standardise (returned as null `standard_concept`), `llm_normaliser.py` can be invoked manually as a fallback. Its suggestions are human-reviewed before being persisted to `concept_map.json`, keeping the pipeline deterministic and auditable.

### Stage 3 — Transformation (`transformation/transform.py`)

Applies the confirmed concept map to produce a clean, normalised DataFrame with one row per company per fiscal year. Computes all derived financial metrics including year-on-year revenue growth rates and return ratios (see Financial Analysis below).

### Stage 4 — Validation (`validation/`)

Two validation scripts run before any data reaches the warehouse:

`validate_quality.py` — structural checks applied to every row:
- No null values in core fields (Revenue, COGS, Assets, Liabilities)
- Revenue ≥ 0; margins bounded between −100% and 100%
- Accounting identity check: Assets ≈ Liabilities + Equity (within 1% tolerance to account for rounding)
- Row count and field completeness logged per run

`validate_metrics.py` — benchmark reconciliation for a sample of (company, year, metric) tuples against figures from Macrotrends or company investor relations pages. Any discrepancy above a 0.5% threshold raises an alert and halts the load stage.

### Stage 5 — Load (`transformation/load.py`)

Upserts validated, transformed data into a PostgreSQL star schema running in Docker. Uses `INSERT ... ON CONFLICT DO UPDATE` so re-runs do not create duplicate records. The schema follows a Kimball-style dimensional model: a central `fact_financials` table linked to `dim_company`, `dim_period`, `dim_concept`, and `dim_lineage` dimension tables.

The Airflow DAG (`dags/sec_pipeline_dag.py`) chains all five stages as sequential tasks with task-level retries, data checkpoints between stages, and structured JSON logging of rows processed and field completeness per run.

### Stage 6 — Visualisation (Power BI)

Power BI connects directly to PostgreSQL via the ODBC connector. DAX measures compute margin percentages and ratios dynamically, so the report stays current as new filings load. Slicers allow filtering by company, fiscal year, and metric group.

---

## Financial Analysis

All metrics are computed in `transformation/transform.py` and stored in `fact_financials`. The dashboard presents these across three report pages.

### Metrics computed

**Profitability**

| Metric | Formula |
|--------|---------|
| Gross margin % | `(Revenue − COGS) / Revenue × 100` |
| EBITDA margin % | `(Operating income + D&A) / Revenue × 100` |
| Net margin % | `Net income / Revenue × 100` |
| Free cash flow | `Operating cash flow − Capex` |
| Return on equity (ROE) | `Net income / Total equity × 100` |
| Return on assets (ROA) | `Net income / Total assets × 100` |
| Revenue growth % (YoY) | `(Revenue_t − Revenue_t-1) / Revenue_t-1 × 100` |

**Liquidity & solvency**

| Metric | Formula |
|--------|---------|
| Current ratio | `Current assets / Current liabilities` |
| Debt-to-equity | `Total liabilities / Total equity` |

**Working capital efficiency**

| Metric | Formula |
|--------|---------|
| Days sales outstanding (DSO) | `Accounts receivable / (Revenue / 365)` |
| Days inventory outstanding (DIO) | `Inventory / (COGS / 365)` |
| Days payable outstanding (DPO) | `Accounts payable / (COGS / 365)` |
| Cash conversion cycle (CCC) | `DSO + DIO − DPO` |

### Dashboard pages

**Page 1 — Executive summary**
KPI cards showing the latest-year values for Revenue, Gross margin %, EBITDA margin %, and FCF per company. Line charts showing 5-year trends in revenue and gross margin %. Company slicer allows single or multi-select comparison.

**Page 2 — Peer comparison**
Grouped bar charts comparing all five companies on gross margin %, EBITDA margin %, net margin %, ROE, and FCF yield side by side for a selected fiscal year. Highlights which companies are expanding vs compressing margins over the period.

**Page 3 — Working capital**
Multi-line trend chart plotting DSO, DIO, DPO, and CCC over 5 years per company. Surfaces operational efficiency differences — for example, Amazon's structurally negative CCC versus peers — that are not visible in the income statement alone.

---

## Setup & Running

**Prerequisites:** Docker, Python 3.10+, Power BI Desktop, Apache Airflow 2.x

```bash
# 1. Start PostgreSQL
docker-compose up -d

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Initialise the schema
psql -h localhost -U postgres -d sec_db -f schema/init.sql

# 4. Run the full pipeline manually (for development)
python extraction/edgar_client.py
python normalisation/concept_mapping.py
python transformation/transform.py
python validation/validate_quality.py
python validation/validate_metrics.py
python transformation/load.py

# 5. Or trigger via Airflow (for scheduled runs)
airflow dags trigger sec_pipeline

# 6. Open the dashboard
# Open powerbi/sec_dashboard.pbix in Power BI Desktop
# Update the PostgreSQL connection string if needed
```

**Handling unmapped XBRL tags:** If `edgartools` returns rows with a null `standard_concept` (tags it could not resolve), the LLM fallback can suggest mappings. Review and confirm before committing:

```bash
python normalisation/llm_normaliser.py --review
# Suggests mappings for unresolved tags using Claude API
# Updates concept_map.json on confirmation only
```

---

## Scalability Considerations

The current implementation is scoped to 5 companies and 5 years of annual filings, which is sufficient for a portfolio demonstration. The architecture is designed with scale-out in mind:

| Constraint | Current approach | Path to scale |
|---|---|---|
| Data volume | pandas in-memory | Migrate to DuckDB or Polars for larger datasets; PySpark for 500+ companies |
| Ingestion | Sequential API calls | Parallelise with `concurrent.futures` or Airflow dynamic task mapping |
| Storage | Single Postgres instance | Partition `fact_financials` by `company_id` and `fiscal_year` |
| Normalisation | Thin adapter + `edgartools` built-in | Promote to a versioned database table with full audit trail |
| Orchestration | Single DAG | Decompose into modular DAGs per company group for parallel execution |


---

## Technologies Used

| Layer | Tool |
|-------|------|
| Extraction | Python, `edgartools`, SEC EDGAR XBRL API |
| Normalisation | Thin adapter (`concept_map.json`); LLM fallback (Claude) for unresolved tags |
| Transformation | `pandas` |
| Validation | Python assertions, manual benchmark reconciliation |
| Storage | PostgreSQL 15 (Docker) |
| Orchestration | Apache Airflow 2.x |
| Containerisation | Docker, Docker Compose |
| Visualisation | Microsoft Power BI Desktop |

