# SEC Financial Analytics Pipeline

An end-to-end data engineering project that extracts structured financial data from SEC EDGAR filings, normalises inconsistent XBRL concept tags using an LLM-assisted mapping layer, stores the data in a PostgreSQL star schema, and surfaces key financial metrics across companies in a Power BI dashboard.

Built as a portfolio project to demonstrate a full production-style pipeline — from raw regulatory filings to analyst-ready visualisations — across major US-listed technology companies.

---

## Data Source

**SEC EDGAR XBRL API** — `https://data.sec.gov/api/xbrl/companyfacts/{CIK}.json`

The US Securities and Exchange Commission requires all public companies to file annual reports (10-K) in XBRL format, making structured financial data freely available via their public API. Each company is identified by a Central Index Key (CIK).

**Coverage**
- Filing types: 10-K (annual)
- History: 5 years (FY2019–FY2024)
- Companies: Apple (AAPL), Microsoft (MSFT), Alphabet (GOOGL), Meta (META), Amazon (AMZN)

**Key XBRL concept tags extracted**

| Canonical concept      | Example XBRL tags pulled                                          |
|------------------------|-------------------------------------------------------------------|
| Revenue                | `Revenues`, `RevenueFromContractWithCustomer`                     |
| Cost of goods sold     | `CostOfRevenue`, `CostOfGoodsSold`, `CostOfGoodsSoldAndServices`  |
| Operating income       | `OperatingIncomeLoss`                                             |
| Net income             | `NetIncomeLoss`                                                   |
| Total assets           | `Assets`                                                          |
| Total liabilities      | `Liabilities`                                                     |
| Cash & equivalents     | `CashAndCashEquivalentsAtCarryingValue`                           |
| Capex                  | `PaymentsToAcquirePropertyPlantAndEquipment`                      |
| Depreciation & amort.  | `DepreciationDepletionAndAmortization`                            |
| Operating cash flow    | `NetCashProvidedByUsedInOperatingActivities`                      |
| Accounts receivable    | `AccountsReceivableNetCurrent`                                    |
| Inventory              | `InventoryNet`                                                    |
| Accounts payable       | `AccountsPayableCurrent`                                          |

> **Note on XBRL inconsistency:** Different companies use different tag names for the same financial concept. The normalisation layer resolves these into a single canonical concept dictionary. The LLM is used only once to bootstrap mappings for previously unseen tags — all confirmed mappings are persisted in `concept_map.json` so that subsequent pipeline runs are fully deterministic and require no LLM calls.

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
│   ├── edgar_client.py             # SEC EDGAR API requests, rate limiting, incremental fetch
│   ├── lineage.py                  # Records source file, filing date, extraction timestamp per record
│   └── raw/                        # Raw API responses (gitignored)
│       └── {CIK}_companyfacts.json
│
├── normalisation/
│   ├── llm_normaliser.py           # LLM bootstrap: suggests mappings for unseen tags only
│   ├── concept_mapping.py          # Applies concept_map.json at runtime — no LLM dependency
│   └── concept_map.json            # Persisted {xbrl_tag: canonical_concept} dictionary (source of truth)
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
│  edgar_client    │     │  concept_mapping   │     │  transform.py        │
│  incremental     │     │  (LLM bootstrap    │     │  metrics + growth    │
│  + lineage log   │     │   → persisted map) │     │  rates + ROE/ROA     │
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

Hits the SEC EDGAR XBRL API for each company CIK defined in `config/companies.yaml`. Implements incremental loading — only fetches filings newer than the most recently ingested period, avoiding redundant API calls on subsequent runs. Respects the SEC rate limit (10 requests/second). Saves raw JSON responses to `extraction/raw/` and records source file, filing date, and extraction timestamp to the `dim_lineage` table via `lineage.py` for full data lineage tracking.

### Stage 2 — Normalisation (`normalisation/concept_mapping.py`)

Applies `concept_map.json` — a persisted dictionary mapping every known XBRL tag to its canonical concept — to the raw data. This stage is fully deterministic at runtime: no LLM is called. The LLM (`normalisation/llm_normaliser.py`) is invoked only when a previously unseen tag is detected. Its suggested mapping is reviewed and manually confirmed before being written back to `concept_map.json`. Tags that cannot be confidently classified are flagged and excluded rather than silently mapped, keeping the normalisation layer auditable and reproducible.

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

**Handling new XBRL tags:** If the extraction stage encounters a tag not in `concept_map.json`, run the LLM normaliser manually, review its suggestions, and commit the updated map before re-triggering the pipeline:

```bash
python normalisation/llm_normaliser.py --review
# Confirm or reject each suggested mapping interactively
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
| Normalisation | Flat JSON map | Promote to a versioned database table with full audit trail |
| Orchestration | Single DAG | Decompose into modular DAGs per company group for parallel execution |


---

## Technologies Used

| Layer | Tool |
|-------|------|
| Extraction | Python, `requests`, SEC EDGAR XBRL API |
| Normalisation | LLM API (Claude) for bootstrap; `json` for runtime |
| Transformation | `pandas` |
| Validation | Python assertions, manual benchmark reconciliation |
| Storage | PostgreSQL 15 (Docker) |
| Orchestration | Apache Airflow 2.x |
| Containerisation | Docker, Docker Compose |
| Visualisation | Microsoft Power BI Desktop |

