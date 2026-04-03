# Economic-indicators-Data-Pipleine
## Introduction
This project aims to tract the correlation and causality of US treasury bond and commodities Gold and Silver and test whether US treasury bond has a suppressing power on these assets.<br>
This will be an end-to-end data engineering pipeline that extracts macroeconomic and market data from public APIs, transforms and loads it into a PostgreSQL database, runs statistical tests, and presents findings through an interactive dashboard, and the entire workflow is orchestrated by Apache Airflow running in Docker.<br>
The analysis covers roughly 2018 to the present, a period that includes several major economic and geopolitical regime shifts: the US-China trade war, the COVID-19 pandemic, the Federal Reserve's rate hiking cycle, the Russia-Ukraine war, the 2025 tariff escalation. Each of these events altered the relationship between Treasuries and gold in different ways, and the pipeline is designed to make those shifts visible and testable.

## Data Sources
### **FRED API**
The pipeline pulls four series from FRED:
    - DGS10: the 10-Year Treasury Constant Maturity Rate, which is the most widely watched benchmark for long-term US interest rates. This is the main "yield" variable in the analysis.
    - DGS2: the 2-Year Treasury Constant Maturity Rate. Including this alongside the 10-Year allows the dashboard to show the yield curve slope (the spread between the two), which is a well-known recession indicator.
    - DFEDTARU: the Federal Funds Upper Target Rate. This captures Federal Reserve policy decisions directly, as distinct from market-determined yields.
    - T10YIE: the 10-Year Breakeven Inflation Rate, derived from the spread between nominal and inflation-protected Treasuries. This matters because gold is often described as an inflation hedge, so disentangling real yield effects from inflation expectations is important.
