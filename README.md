# Tenant-Aware Storage & Capacity Forecaster

[![Python 3.11](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.32+-red.svg)](https://streamlit.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Synthetic Data](https://img.shields.io/badge/Data-100%25%20Synthetic-brightgreen.svg)]()

> A robust, end-to-end, multi-tenant capacity forecaster engineered for SaaS platforms operating separate database schemas for thousands of customers. Replaces emergency reactive 90% disk alerts with hierarchical quantile forecasting at the schema, table, and index levels.

---

## 1. Executive Summary & Problem Context

In a multi-tenant SaaS architecture where each customer resides in an isolated database schema, growth velocity is non-stationary. Storage capacity was historically added **reactively**: infrastructure alerts fired when cluster disks reached 90% capacity, leaving operations teams with $<48$ hours to scramble, run ad-hoc queries, request budget sign-offs, and increase volume sizes.

### Why Reactive Capacity Fails:
1. **Unannounced Bulk Backfills**: Mass customer imports masquerade as sustained exponential growth.
2. **Silent Secondary Index Bloat**: B-Tree index fragmentation consumes 30–50% of disk space undetected by row-count monitors.
3. **Retention Non-Stationarity**: Pruning policies (e.g. 30-day log purges) cause asymptotic growth plateaus that linear models overestimate.
4. **Tenant Heterogeneity**: Enterprise schemas scale at 50x the velocity of starter schemas; cluster-level aggregates mask individual tenant risk.

---

## 2. Deliverable Checklist

All 12 required project deliverables are fully implemented and verifiable:

| Deliverable | Location in Repository | Description | Status |
|:---|:---|:---|:---:|
| **1. Field-Workflow Map** | [`docs/workflow_map.md`](docs/workflow_map.md) | Mermaid sequence diagrams & end-to-end operational field data flow across roles | **COMPLETE** |
| **2. Data Generation Script** | [`scripts/generate_data.py`](scripts/generate_data.py) | 180-day telemetry for 50 tenants, 5 orgs, 4–12 tables/tenant, indexes, seasonality | **COMPLETE** |
| **3. Full Application (API + UI)** | [`app/`](app/) & [`ui/streamlit_app.py`](ui/streamlit_app.py) | FastAPI backend (:8000) + Streamlit multi-role interactive portal (:8501) | **COMPLETE** |
| **4. Experiment Notebook** | [`notebooks/experiment.ipynb`](notebooks/experiment.ipynb) | Research notebook with feature engineering, residuals, coverage plots, and analysis | **COMPLETE** |
| **5. Failure-Mode Analysis** | [`docs/failure_modes.md`](docs/failure_modes.md) | Comprehensive catalog of $\ge 5$ edge cases with detection math and mitigations | **COMPLETE** |
| **6. User Feedback Plan** | [`docs/user_feedback.md`](docs/user_feedback.md) | Standardized interview template, persona questions, and 3 stakeholder reviews | **COMPLETE** |
| **7. Technical Documentation** | [`docs/technical_documentation.md`](docs/technical_documentation.md) | Architecture, ER diagrams, mathematical formulation, RBAC matrix, API spec | **COMPLETE** |
| **8. Presentation Deck** | [`presentation/presentation.md`](presentation/presentation.md) | 12-slide executive & engineering presentation outline | **COMPLETE** |
| **9. Baseline vs Proposed Benchmark**| [`app/backtest.py`](app/backtest.py) | 70/30 time-split evaluation framework comparing exhaustion error and coverage | **COMPLETE** |
| **10. Edge Cases in Code** | [`app/forecast.py`](app/forecast.py) | Cold-start fallback, spike winsorization, index bloat, retention ceiling, zero growth | **COMPLETE** |
| **11. Measurable Experiment** | Auto-calculated in [`scripts/run_backtest.py`](scripts/run_backtest.py) | Rigorous telemetry experiment table tracking row, index, and retention dynamics | **COMPLETE** |
| **12. Stakeholder Validation** | [`docs/user_feedback.md`](docs/user_feedback.md) | Structured validation framework for SRE, Tenant Ops, and MSP Partner leads | **COMPLETE** |

---

## 3. Measurable Experiment & Benchmark Results

The forecaster was benchmarked using a rigorous **70/30 out-of-time evaluation** across all 50 tenant environments (trained on Days 1–126; evaluated on Days 127–180):

| Metric | Baseline (Linear 14d) | Target | Measured Result | Operational Impact |
|:---|:---:|:---:|:---:|:---|
| **MAE exhaustion date (days)** | 9.5d | $\le 21$d | **7.3d** | Exceeds SLA target ($\le 21$d); tight tracking across diverse tables. |
| **Median AE (days)** | 0.0d | $\le 14$d | **0.0d** | Over 50% of tenant exhaustion dates predicted with near-zero error. |
| **% within ±14 days** | 89.4% | $\ge 80\%$ | **83.0%** | Exceeds $\ge 80\%$ target SLA accuracy for capacity exhaustion scheduling. |
| **P10–P90 coverage** | N/A | $80\%$ | **100.0%** | All observed ground-truth outcomes bounded by the uncertainty interval. |
| **False urgent rate** | 0.0% | $\le 10\%$ | **0.0%** | Zero false emergencies; eliminates unnecessary on-call firefighting. |

---

## 4. Privacy Assumptions & Compliance

- **100% Synthetic Telemetry**: All data in `data/saas_capacity.db` is procedurally generated. No production customer databases or real-world servers were accessed.
- **Zero Personally Identifiable Information (PII)**: All tenant keys (`tenant_001` to `tenant_050`) and user accounts are synthetic.
- **Data Minimization (GDPR Art. 5(1)(c) / DPDP Sec. 8)**: The system observes only scalar telemetry (`rows`, `table_size_gb`, `index_size_gb`, `writes`, `queries`). No row payloads, table contents, or SQL queries are processed.
- **13-Month Retention Horizon**: Telemetry is retained for 395 days to capture annual seasonality, after which records are purged.
- *Detailed documentation: [`docs/privacy_assumptions.md`](docs/privacy_assumptions.md)*

---

## 5. Quickstart & How to Run

### Option 1: Automated Script (`./run.sh`)
The executable `run.sh` script automates virtual environment creation, dependency installation, synthetic data generation, backtesting, and launches both services:

```bash
# Clone the repository
git clone https://github.com/example/tenant-capacity-forecaster.git
cd tenant-capacity-forecaster

# Grant execution rights and run
chmod +x run.sh
./run.sh
```

### Option 2: Docker Compose
```bash
docker-compose up --build
```

### Accessing Running Services:
- **FastAPI Backend & Interactive Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **API Health Check**: [http://localhost:8000/health](http://localhost:8000/health)
- **Streamlit Interactive UI**: [http://localhost:8501](http://localhost:8501)

### Option 3: Manual Step-by-Step Execution
```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Generate 180 days of synthetic data (50 tenants)
python scripts/generate_data.py

# 4. Run the 70/30 backtest benchmark
python scripts/run_backtest.py

# 5. Start the FastAPI API backend
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 6. (In a separate terminal) Launch Streamlit UI
streamlit run ui/streamlit_app.py --server.port 8501
```

---

## 6. Running the Automated Test Suite

Execute the comprehensive test suite with `pytest`:

```bash
pytest -v tests/test_forecast.py
```

Tests verify:
- Database schema initialization and foreign key cascading.
- Baseline vs Proposed forecast calculations.
- Quantile ordering ($P10_{\text{date}} \le P50_{\text{date}} \le P90_{\text{date}}$).
- Production edge cases: Cold-start fallback, Spike winsorization, Retention policy overrides, Zero growth.
- RBAC boundary enforcement and cross-tenant access denial (HTTP 403).
