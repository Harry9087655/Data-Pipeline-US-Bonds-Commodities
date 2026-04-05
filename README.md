# SEC Financial Analytics Pipeline

An end-to-end data engineering project that extracts structured financial data from SEC EDGAR filings, normalises inconsistent XBRL concept tags using an LLM, stores the data in a PostgreSQL star schema, and surfaces key financial metrics across companies in a Power BI dashboard.

Built as a portfolio project to demonstrate a full data pipeline — from raw regulatory filings to analyst-ready visualisations — across major US-listed companies in the technology sector.

---

## Data Source

**SEC EDGAR XBRL API** — `https://data.sec.gov/api/xbrl/companyfacts/{CIK}.json`

The US Securities and Exchange Commission requires all public companies to file annual reports (10-K) and quarterly reports (10-Q) in XBRL format, making structured financial data freely available via their public API. Each company is identified by a Central Index Key (CIK).

**Coverage**
- Filing types: 10-K (annual)
- History: 5 years (FY2019–FY2024)
- 8 Companies: Apple (AAPL), Microsoft (MSFT), Alphabet (GOOGL), Meta (META), Amazon (AMZN)

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

> **Note on XBRL inconsistency:** Different companies use different tag names for the same financial concept. The LLM normalisation layer (see Workflow below) resolves these into a single canonical concept dictionary before any metrics are computed.

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
│   ├── edgar_client.py             # SEC EDGAR API requests, rate limiting, raw JSON storage
│   └── raw/                        # Raw API responses (gitignored)
│       └── {CIK}_companyfacts.json
│
├── normalisation/
│   ├── llm_normaliser.py           # LLM API calls to classify XBRL tags → canonical concepts
│   ├── concept_mapping.py          # Loads and applies the mapping table
│   └── concept_map.json            # Output: {xbrl_tag: canonical_concept} dictionary
│
├── transformation/
│   ├── transform.py                # Pandas transformations: derived metrics calculation
│   └── load.py                     # Loads transformed data into PostgreSQL fact/dim tables
│
├── schema/
│   └── init.sql                    # Star schema DDL — creates all tables on container start
│
├── dags/
│   └── sec_pipeline_dag.py         # Apache Airflow DAG — schedules and orchestrates pipeline stages
│
├── validation/
│   └── validate_metrics.py         # Cross-checks computed metrics against Macrotrends benchmarks
│
├── powerbi/
│   └── sec_dashboard.pbix          # Power BI report file (connects to PostgreSQL)
│
└── notebooks/
    └── exploration.ipynb           # EDA and metric spot-checks during development
```

---

## Workflow

The pipeline runs in five sequential stages. Apache Airflow orchestrates stages 1–4 via a scheduled DAG; stage 5 (Power BI) connects live to the database.

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  1. Extraction  │────▶│ 2. Normalisation  │────▶│  3. Transformation  │
│  edgar_client   │     │  llm_normaliser   │     │  transform.py       │
│  → raw JSON     │     │  → concept_map    │     │  → derived metrics  │
└─────────────────┘     └──────────────────┘     └─────────────────────┘
                                                           │
                                                           ▼
                                              ┌─────────────────────────┐
                                              │  4. Load (PostgreSQL)   │
                                              │  Star schema via        │
                                              │  load.py                │
                                              └─────────────────────────┘
                                                           │
                                                           ▼
                                              ┌─────────────────────────┐
                                              │  5. Visualisation       │
                                              │  Power BI → Postgres    │
                                              └─────────────────────────┘
```

### Stage 1 — Extraction (`extraction/edgar_client.py`)

Hits the SEC EDGAR XBRL API for each company CIK defined in `config/companies.yaml`. Respects the SEC's rate limit (10 requests/second). Saves raw JSON responses to `extraction/raw/`. Logs any missing concepts or malformed responses for manual review.

### Stage 2 — LLM Normalisation (`normalisation/llm_normaliser.py`)

The EDGAR API returns data keyed by the company's chosen XBRL tag names, which vary across filers. This stage sends the list of observed tags to the LLM API with a prompt that classifies each tag against a predefined canonical concept list. The output is a `concept_map.json` file mapping every observed tag to its canonical equivalent (e.g. `"CostOfGoodsSoldAndServiceRevenue"` → `"cost_of_goods_sold"`). Tags that cannot be confidently classified are flagged for manual review rather than silently dropped.

### Stage 3 — Transformation (`transformation/transform.py`)

Applies the concept map to the raw data using pandas, then computes all derived financial metrics (see Financial Analysis section below). Outputs a clean, normalised DataFrame with one row per company per fiscal year.

### Stage 4 — Load (`transformation/load.py` + Airflow)

Loads the transformed data into a PostgreSQL star schema running in Docker. The schema follows a standard dimensional model: a central `fact_financials` table linked to `dim_company`, `dim_period`, and `dim_concept` dimension tables. The Airflow DAG (`dags/sec_pipeline_dag.py`) chains all four stages as sequential tasks and runs on a configurable schedule, with task-level logging and retry handling built in.

### Stage 5 — Visualisation (Power BI)

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
Grouped bar charts comparing all five companies on gross margin %, EBITDA margin %, net margin %, and FCF yield side by side for a selected fiscal year. Highlights which companies are expanding vs compressing margins over the period.

**Page 3 — Working capital**
Multi-line trend chart plotting DSO, DIO, DPO, and CCC over 5 years per company. Surfaces operational efficiency differences — for example, Amazon's structurally negative CCC versus peers — that are not visible in the income statement alone.

---

## Setup & Running

**Prerequisites:** Docker, Python 3.10+, Power BI Desktop

```bash
# 1. Start PostgreSQL
docker-compose up -d

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Initialise the schema
psql -h localhost -U postgres -d sec_db -f schema/init.sql

# 4. Run the full pipeline
python extraction/edgar_client.py
python normalisation/llm_normaliser.py
python transformation/transform.py
python transformation/load.py

# 5. Open the dashboard
# Open powerbi/sec_dashboard.pbix in Power BI Desktop
# Update the PostgreSQL connection string if needed
```

To automate with Airflow, place `dags/sec_pipeline_dag.py` in your Airflow DAGs folder and trigger via the Airflow UI or CLI (`airflow dags trigger sec_pipeline`).

---


## Technologies used

| Layer | Tool |
|-------|------|
| Extraction | Python, `requests`, SEC EDGAR XBRL API |
| Normalisation | LLM API (Claude), `json` |
| Transformation | `pandas` |
| Storage | PostgreSQL 15 (Docker) |
| Orchestration | Apache Airflow |
| Containerisation | Docker, Docker Compose |
| Visualisation | Microsoft Power BI Desktop |

