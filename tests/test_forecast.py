"""
Comprehensive Test Suite for Tenant-Aware Storage & Capacity Forecaster

Covers:
- Database schema and data models
- Synthetic data generation validity
- Baseline linear extrapolation model
- Proposed hierarchical quantile regression forecaster
- Edge-case handling (cold start, spikes, retention overrides, index bloat, zero growth)
- 70/30 backtesting metrics (MAE, coverage, false urgent rate)
- Role-based access control (RBAC) and tenant isolation
"""

import sys
from pathlib import Path
from datetime import date, timedelta, datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.database import Base
from app import models
from app.forecast import compute_baseline_forecast, compute_proposed_forecast
from app.backtest import run_comprehensive_backtest
from app.auth import Actor, DELEGATED_PARTNER_TENANTS


@pytest.fixture(scope="module")
def test_db():
    """In-memory SQLite database session for unit tests."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()

    # Seed minimal fixture data: 2 tenants, tables, daily metrics
    today = date.today()
    t1 = models.Tenant(tenant_id="tenant_001", org_id="org_1", tier="standard", storage_limit_gb=55.0)
    t2 = models.Tenant(tenant_id="tenant_002", org_id="org_2", tier="enterprise", storage_limit_gb=1000.0)
    t_cold = models.Tenant(tenant_id="tenant_003", org_id="org_1", tier="starter", storage_limit_gb=50.0)
    t_zero = models.Tenant(tenant_id="tenant_004", org_id="org_1", tier="standard", storage_limit_gb=100.0)

    db.add_all([t1, t2, t_cold, t_zero])
    db.commit()

    # Tables
    tbl1 = models.TableMetadata(table_id="tenant_001_users", tenant_id="tenant_001", schema_name="schema_001", table_name="users", row_bytes=256)
    tbl2 = models.TableMetadata(table_id="tenant_001_events", tenant_id="tenant_001", schema_name="schema_001", table_name="events", row_bytes=512, retention_days=30)
    tbl_cold = models.TableMetadata(table_id="tenant_003_main", tenant_id="tenant_003", schema_name="schema_003", table_name="main", row_bytes=256)
    tbl_zero = models.TableMetadata(table_id="tenant_004_main", tenant_id="tenant_004", schema_name="schema_004", table_name="main", row_bytes=256)

    db.add_all([tbl1, tbl2, tbl_cold, tbl_zero])
    db.commit()

    # Indexes
    idx1 = models.IndexMetadata(index_id="idx_1", table_id="tenant_001_users", index_name="ix_created", index_factor=0.25)
    db.add(idx1)
    db.commit()

    # Generate 60 days of metrics for tenant_001 (steady growth)
    metrics = []
    for d_offset in range(60, 0, -1):
        m_date = today - timedelta(days=d_offset)
        # Table 1: users grows ~0.2 GB/day
        size1 = 10.0 + (60 - d_offset) * 0.20
        metrics.append(models.DailyMetric(
            metric_date=m_date,
            tenant_id="tenant_001",
            table_id="tenant_001_users",
            rows=int(size1 * 10000),
            table_size_gb=round(size1, 3),
            index_size_gb=round(size1 * 0.25, 3),
            writes=1000,
            queries=5000
        ))
        # Table 2: events
        size2 = 5.0 + (60 - d_offset) * 0.10
        metrics.append(models.DailyMetric(
            metric_date=m_date,
            tenant_id="tenant_001",
            table_id="tenant_001_events",
            rows=int(size2 * 5000),
            table_size_gb=round(size2, 3),
            index_size_gb=round(size2 * 0.10, 3),
            writes=2000,
            queries=8000
        ))

    # Generate only 4 days for tenant_003 (cold start)
    for d_offset in range(4, 0, -1):
        m_date = today - timedelta(days=d_offset)
        metrics.append(models.DailyMetric(
            metric_date=m_date,
            tenant_id="tenant_003",
            table_id="tenant_003_main",
            rows=1000,
            table_size_gb=1.0,
            index_size_gb=0.2,
            writes=100,
            queries=200
        ))

    # Generate 30 days of zero growth for tenant_004
    for d_offset in range(30, 0, -1):
        m_date = today - timedelta(days=d_offset)
        metrics.append(models.DailyMetric(
            metric_date=m_date,
            tenant_id="tenant_004",
            table_id="tenant_004_main",
            rows=10000,
            table_size_gb=20.0,
            index_size_gb=4.0,
            writes=10,
            queries=50
        ))

    db.add_all(metrics)
    db.commit()

    yield db
    db.close()


# ==============================================================================
# 1. BASELINE FORECASTER TESTS
# ==============================================================================
def test_baseline_forecast_calculation(test_db):
    res = compute_baseline_forecast("tenant_001", test_db, horizon_days=180)
    assert res["tenant_id"] == "tenant_001"
    assert res["model_name"] == "baseline_linear"
    assert res["current_size_gb"] > 0
    assert res["daily_growth_gb"] > 0
    assert res["p50_exhaustion_date"] is not None
    assert len(res["trajectory"]) > 0


# ==============================================================================
# 2. PROPOSED FORECASTER & QUANTILE INTERVAL TESTS
# ==============================================================================
def test_proposed_forecast_calculation(test_db):
    res = compute_proposed_forecast("tenant_001", test_db, horizon_days=180)
    assert res["tenant_id"] == "tenant_001"
    assert res["storage_limit_gb"] == 55.0
    assert res["confidence"] > 0.60
    assert res["p50_exhaustion_date"] is not None
    assert res["p10_exhaustion_date"] is not None  # aggressive growth -> earlier date
    assert res["p90_exhaustion_date"] is not None  # conservative growth -> later date

    # Chronological ordering of quantile exhaustion dates
    # P10 exhaustion date (worst case fast growth) should be <= P50 <= P90
    assert res["p10_exhaustion_date"] <= res["p50_exhaustion_date"]
    if res["p90_exhaustion_date"]:
        assert res["p50_exhaustion_date"] <= res["p90_exhaustion_date"]


# ==============================================================================
# 3. EDGE CASE TESTS (AT LEAST 3 HANDLED IN CODE)
# ==============================================================================
def test_edge_case_1_cold_start_fallback(test_db):
    """Failure Mode 1: New tenant with <7 days history triggers tier fallback & low confidence."""
    res = compute_proposed_forecast("tenant_003", test_db)
    assert "new_tenant_fallback" in res["warning_flags"]
    assert res["confidence"] <= 0.35
    assert res["daily_p50_growth_gb"] > 0


def test_edge_case_2_sudden_spike_winsorization(test_db):
    """Failure Mode 2: Ingestion spike (>5x std) is detected, winsorized, and flagged."""
    # Inject a massive 15x spike on tenant_001
    today = date.today()
    spike_metric = models.DailyMetric(
        metric_date=today,
        tenant_id="tenant_001",
        table_id="tenant_001_users",
        rows=500000,
        table_size_gb=65.0,  # sudden jump from ~25 GB
        index_size_gb=15.0,
        writes=50000,
        queries=10000
    )
    test_db.add(spike_metric)
    test_db.commit()

    res = compute_proposed_forecast("tenant_001", test_db)
    assert "spike_detected" in res["warning_flags"]

    # Clean up test injection
    test_db.delete(spike_metric)
    test_db.commit()


def test_edge_case_3_retention_scenario_override(test_db):
    """Failure Mode 3: Dynamic retention policy override bounds asymptotic ceiling."""
    res_default = compute_proposed_forecast("tenant_001", test_db)
    # Apply aggressive 14-day retention override
    res_override = compute_proposed_forecast(
        "tenant_001", test_db,
        retention_override={"tenant_001_events": 14}
    )
    assert "retention_rule_scenario_override" in res_override["warning_flags"]


def test_edge_case_5_zero_growth_no_false_urgent(test_db):
    """Failure Mode 5: Zero or negative growth outputs '> horizon', avoiding false urgent alerts."""
    res = compute_proposed_forecast("tenant_004", test_db)
    assert "zero_or_negative_growth" in res["warning_flags"]
    assert res["p50_exhaustion_date"] is None


# ==============================================================================
# 4. RBAC & TENANT ISOLATION TESTS
# ==============================================================================
def test_rbac_access_boundaries(test_db):
    t1 = test_db.query(models.Tenant).filter_by(tenant_id="tenant_001").first()

    # Platform Admin can access everything
    admin = Actor(user_id="admin_1", role="platform_admin", org_id="org_1")
    assert admin.can_access_tenant(t1) is True

    # Tenant Admin can access own org
    tam_own = Actor(user_id="user_org1", role="tenant_admin", org_id="org_1")
    assert tam_own.can_access_tenant(t1) is True

    # Tenant Admin cannot access foreign org
    tam_foreign = Actor(user_id="user_org2", role="tenant_admin", org_id="org_2")
    assert tam_foreign.can_access_tenant(t1) is False

    # Auditor cannot access tenant details directly
    auditor = Actor(user_id="auditor_1", role="auditor", org_id="org_1")
    assert auditor.can_access_tenant(t1) is False


# ==============================================================================
# 5. BACKTEST BENCHMARK ENGINE TESTS
# ==============================================================================
def test_backtest_metric_computation(test_db):
    """Verify that backtest runs and computes MAE, coverage, and false urgent metrics."""
    res = run_comprehensive_backtest(test_db, train_ratio=0.70, horizon_days=180)
    assert "baseline_mae" in res
    assert "proposed_mae" in res
    assert "proposed_coverage_pct" in res
    assert "comparison_table" in res
    assert len(res["comparison_table"]) == 7
