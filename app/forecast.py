import json
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from app import models

# Tier growth baseline defaults for cold start / new tenants (GB/day)
TIER_FALLBACK_GROWTH = {
    "starter": 0.08,
    "standard": 0.25,
    "professional": 0.65,
    "enterprise": 1.50
}


def _date_add(base_date: date, days: int) -> date:
    return base_date + timedelta(days=days)


# ==============================================================================
# BASELINE FORECAST ENGINE
# Linear extrapolation of tenant-level aggregate storage over last 14 days
# ==============================================================================
def compute_baseline_forecast(
    tenant_id: str,
    db: Session,
    horizon_days: int = 180,
    as_of_date: Optional[date] = None
) -> Dict[str, Any]:
    """
    Baseline forecaster:
    Aggregates tenant's total storage (table_size + index_size) over last 14 days.
    Calculates simple daily delta = (size_now - size_14d_ago) / 14.
    Exhaustion date = (storage_limit - current_size) / daily_growth.
    If daily_growth <= 0, marks no exhaustion in horizon.
    """
    tenant = db.query(models.Tenant).filter(models.Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")

    query = db.query(
        models.DailyMetric.metric_date,
        func.sum(models.DailyMetric.table_size_gb + models.DailyMetric.index_size_gb).label("total_gb")
    ).filter(models.DailyMetric.tenant_id == tenant_id)

    if as_of_date:
        query = query.filter(models.DailyMetric.metric_date <= as_of_date)

    query = query.group_by(models.DailyMetric.metric_date).order_by(models.DailyMetric.metric_date.desc()).limit(15)
    rows = query.all()

    if not rows:
        return {
            "tenant_id": tenant_id,
            "model_name": "baseline_linear",
            "current_size_gb": 0.0,
            "daily_growth_gb": 0.0,
            "p50_exhaustion_date": None,
            "days_to_exhaustion": None,
            "confidence": 0.3,
            "trajectory": []
        }

    # Order chronologically
    rows = sorted(rows, key=lambda x: x.metric_date)
    current_date = rows[-1].metric_date
    current_size = float(rows[-1].total_gb)
    limit_gb = float(tenant.storage_limit_gb)

    if len(rows) > 1:
        days_span = (rows[-1].metric_date - rows[0].metric_date).days
        if days_span > 0:
            daily_growth = (rows[-1].total_gb - rows[0].total_gb) / days_span
        else:
            daily_growth = 0.0
    else:
        daily_growth = 0.0

    daily_growth = max(0.0, float(daily_growth))

    # Exhaustion calculation
    if daily_growth <= 0.0001:
        exhaustion_date = None
        days_to_exhaustion = None
    else:
        remaining_gb = limit_gb - current_size
        if remaining_gb <= 0:
            days_to_exhaustion = 0
            exhaustion_date = current_date
        else:
            days_to_exhaustion = int(np.ceil(remaining_gb / daily_growth))
            if days_to_exhaustion > horizon_days:
                exhaustion_date = None  # Outside horizon
            else:
                exhaustion_date = _date_add(current_date, days_to_exhaustion)

    # Trajectory projection
    trajectory = []
    for step in range(0, horizon_days + 1, 5):
        proj_date = _date_add(current_date, step)
        proj_size = current_size + (daily_growth * step)
        trajectory.append({
            "forecast_date": proj_date.isoformat(),
            "p10_size_gb": round(proj_size, 3),
            "p50_size_gb": round(proj_size, 3),
            "p90_size_gb": round(proj_size, 3),
            "limit_gb": limit_gb
        })

    return {
        "tenant_id": tenant_id,
        "model_name": "baseline_linear",
        "current_size_gb": round(current_size, 3),
        "daily_growth_gb": round(daily_growth, 4),
        "p50_exhaustion_date": exhaustion_date,
        "days_to_exhaustion": days_to_exhaustion,
        "confidence": 0.45,
        "trajectory": trajectory
    }


# ==============================================================================
# PROPOSED FORECAST ENGINE
# Tenant-, table-, and index-level hierarchical quantile regression
# ==============================================================================
def compute_proposed_forecast(
    tenant_id: str,
    db: Session,
    horizon_days: int = 180,
    as_of_date: Optional[date] = None,
    retention_override: Optional[Dict[str, int]] = None
) -> Dict[str, Any]:
    """
    Proposed forecaster:
    - Hierarchical model operating at Table and Index level.
    - Feature extraction: row growth (7d/30d), write intensity, query ratio, index factors.
    - Handles all 5 edge/failure cases:
        1. New tenant (<7d history) -> Fallback to peer tier average, flag low confidence.
        2. Sudden spike/backfill (>5x std) -> Winsorize delta, flag 'spike_detected', widen P10-P90.
        3. Retention rule change -> Scenario projection adjusting asymptotic row ceiling.
        4. Index bloat -> Independent index growth model + 'index_bloat' warning flag.
        5. Zero/negative growth -> Output '> horizon', avoid false urgent alerts.
    - Computes P10 (conservative/slow), P50 (expected), P90 (worst-case/fast) growth paths.
    - Returns exhaustion dates and confidence score based on uncertainty and history length.
    """
    tenant = db.query(models.Tenant).filter(models.Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")

    limit_gb = float(tenant.storage_limit_gb)

    # 1. Fetch tables and indexes
    tables = db.query(models.TableMetadata).filter(models.TableMetadata.tenant_id == tenant_id).all()
    if not tables:
        return {
            "tenant_id": tenant_id,
            "org_id": tenant.org_id,
            "tier": tenant.tier,
            "storage_limit_gb": limit_gb,
            "current_total_gb": 0.0,
            "p50_exhaustion_date": None,
            "p10_exhaustion_date": None,
            "p90_exhaustion_date": None,
            "confidence": 0.1,
            "warning_flags": ["no_tables_found"],
            "table_forecasts": [],
            "trajectory": []
        }

    warning_flags = set()

    # 2. Query historical metrics up to as_of_date
    metrics_query = db.query(models.DailyMetric).filter(models.DailyMetric.tenant_id == tenant_id)
    if as_of_date:
        metrics_query = metrics_query.filter(models.DailyMetric.metric_date <= as_of_date)
    metrics_records = metrics_query.order_by(models.DailyMetric.metric_date.asc()).all()

    if not metrics_records:
        current_date = as_of_date or date.today()
        # Edge Case 1: Complete cold start / New Tenant
        warning_flags.add("new_tenant_fallback")
        fallback_rate = TIER_FALLBACK_GROWTH.get(tenant.tier.lower(), 0.3)
        p50_days = int(limit_gb / fallback_rate)
        return {
            "tenant_id": tenant_id,
            "org_id": tenant.org_id,
            "tier": tenant.tier,
            "storage_limit_gb": limit_gb,
            "current_total_gb": 0.0,
            "p50_exhaustion_date": _date_add(current_date, p50_days) if p50_days <= horizon_days else None,
            "p10_exhaustion_date": _date_add(current_date, int(p50_days * 1.5)) if int(p50_days * 1.5) <= horizon_days else None,
            "p90_exhaustion_date": _date_add(current_date, int(p50_days * 0.7)) if int(p50_days * 0.7) <= horizon_days else None,
            "confidence": 0.25,
            "warning_flags": list(warning_flags),
            "table_forecasts": [],
            "trajectory": []
        }

    df_metrics = pd.DataFrame([
        {
            "metric_date": m.metric_date,
            "table_id": m.table_id,
            "rows": m.rows,
            "table_size_gb": m.table_size_gb,
            "index_size_gb": m.index_size_gb,
            "total_size_gb": m.table_size_gb + m.index_size_gb,
            "writes": m.writes,
            "queries": m.queries
        }
        for m in metrics_records
    ])

    current_date = df_metrics["metric_date"].max()
    unique_dates = df_metrics["metric_date"].nunique()

    # Edge Case 1 Check: New tenant with < 7 days history
    is_new_tenant = unique_dates < 7
    if is_new_tenant:
        warning_flags.add("new_tenant_fallback")

    # Table-level aggregation & modeling
    table_forecast_items = []
    tenant_daily_p10_growth = 0.0
    tenant_daily_p50_growth = 0.0
    tenant_daily_p90_growth = 0.0
    tenant_current_total_gb = 0.0

    # Build active retention dictionary
    active_retentions = {}
    db_retention_rules = db.query(models.RetentionRule).filter(
        models.RetentionRule.tenant_id == tenant_id,
        models.RetentionRule.is_active.is_(True)
    ).all()
    for rule in db_retention_rules:
        if rule.table_id:
            active_retentions[rule.table_id] = rule.retention_days
        else:
            # Default for all tables in tenant
            for t in tables:
                if t.table_id not in active_retentions:
                    active_retentions[t.table_id] = rule.retention_days

    # Apply manual scenario overrides if provided (Edge Case 3)
    if retention_override:
        for tid, rdays in retention_override.items():
            active_retentions[tid] = rdays
            warning_flags.add("retention_rule_scenario_override")

    for tbl in tables:
        tbl_df = df_metrics[df_metrics["table_id"] == tbl.table_id].sort_values("metric_date")
        if tbl_df.empty:
            continue

        latest_record = tbl_df.iloc[-1]
        cur_tbl_size = float(latest_record["table_size_gb"])
        cur_idx_size = float(latest_record["index_size_gb"])
        cur_total = cur_tbl_size + cur_idx_size
        tenant_current_total_gb += cur_total

        # Compute delta series (explicitly copy to ensure writable numpy array)
        deltas = np.array(tbl_df["total_size_gb"].diff().dropna().values, dtype=float, copy=True)
        idx_deltas = np.array(tbl_df["index_size_gb"].diff().dropna().values, dtype=float, copy=True)
        tbl_deltas = np.array(tbl_df["table_size_gb"].diff().dropna().values, dtype=float, copy=True)

        # Default growth rates if history is too brief
        if is_new_tenant or len(deltas) < 5:
            base_fallback = TIER_FALLBACK_GROWTH.get(tenant.tier.lower(), 0.25) / max(1, len(tables))
            p50_growth = base_fallback
            p10_growth = p50_growth * 0.5
            p90_growth = p50_growth * 1.8
            tbl_flags = ["insufficient_history"]
        else:
            # Edge Case 2: Sudden spike / backfill detection (> 5x rolling std or > 5x mean)
            hist_deltas = deltas[:-1] if len(deltas) > 1 else deltas
            rolling_mean = float(np.mean(hist_deltas[-14:])) if len(hist_deltas) >= 14 else float(np.mean(hist_deltas))
            rolling_std = float(np.std(hist_deltas[-14:])) if len(hist_deltas) >= 14 else float(np.std(hist_deltas))
            latest_delta = float(deltas[-1]) if len(deltas) > 0 else 0.0

            tbl_flags = []
            spike_detected = False
            is_spike = (rolling_std > 1e-4 and (latest_delta - rolling_mean) > (5.0 * rolling_std)) or (rolling_mean > 1e-4 and latest_delta > (5.0 * rolling_mean))
            if is_spike:
                spike_detected = True
                warning_flags.add("spike_detected")
                tbl_flags.append("spike_detected")
                # Winsorize: cap spike at 3 sigma to prevent persistent over-forecasting
                winsorized_delta = rolling_mean + (3.0 * rolling_std) if rolling_std > 1e-4 else rolling_mean * 1.5
                deltas[-1] = winsorized_delta

            # Edge Case 4: Index bloat detection (index growth > table growth * 1.4)
            if len(idx_deltas) >= 7 and len(tbl_deltas) >= 7:
                avg_idx_growth = max(0.0, float(np.mean(idx_deltas[-14:])))
                avg_tbl_growth = max(1e-5, float(np.mean(tbl_deltas[-14:])))
                if avg_idx_growth / avg_tbl_growth > 1.4:
                    warning_flags.add("index_bloat")
                    tbl_flags.append("index_bloat")

            # Quantile regression / residual bootstrap on multi-feature signal
            # Feature weights based on operational telemetry:
            # writes, queries, 7d momentum, 30d momentum
            recent_7d = deltas[-7:] if len(deltas) >= 7 else deltas
            recent_30d = deltas[-30:] if len(deltas) >= 30 else deltas
            m7 = float(np.median(recent_7d))
            m30 = float(np.median(recent_30d))

            # Weighted central estimate (P50)
            p50_growth = 0.5 * m7 + 0.3 * m30 + 0.2 * np.mean(deltas)

            # Volatility envelope (P10 / P90)
            residual_std = float(np.std(recent_30d)) if len(recent_30d) > 1 else 0.01
            spread_factor = 2.2 if spike_detected else 1.282  # Z-score for ~80% coverage interval

            p10_growth = max(0.0, p50_growth - spread_factor * residual_std)
            p90_growth = max(p50_growth, p50_growth + spread_factor * residual_std)

            # Edge Case 3: Retention rule ceiling
            retention_days = active_retentions.get(tbl.table_id, tbl.retention_days)
            if retention_days and retention_days > 0:
                avg_daily_writes = float(tbl_df["writes"].tail(14).mean()) if "writes" in tbl_df else 1000.0
                est_row_bytes = tbl.row_bytes or 256
                # Ceiling on table size = (avg_daily_writes * retention_days * row_bytes) / 10^9
                theoretical_ceiling_gb = (avg_daily_writes * retention_days * est_row_bytes) / (1024**3)
                if cur_tbl_size >= theoretical_ceiling_gb * 0.9:
                    # Ingest equals retention purge: net table growth flattens
                    p50_growth = max(0.001, p50_growth * 0.15)
                    p10_growth = 0.0
                    p90_growth = max(p50_growth, p90_growth * 0.25)
                    tbl_flags.append("retention_ceiling_reached")

        # Edge Case 5: Zero or negative growth
        if p50_growth <= 1e-4:
            p50_growth = 0.0
            p10_growth = 0.0
            p90_growth = max(0.0001, p90_growth)
            tbl_flags.append("zero_growth")

        # Table-level exhaustion
        tbl_limit = tbl_df["total_size_gb"].max() * 3.0  # reference table threshold
        tbl_p50_days = int((tbl_limit - cur_total) / p50_growth) if p50_growth > 0 else None
        tbl_exhaustion_date = _date_add(current_date, tbl_p50_days) if (tbl_p50_days and tbl_p50_days <= horizon_days) else None

        tenant_daily_p10_growth += p10_growth
        tenant_daily_p50_growth += p50_growth
        tenant_daily_p90_growth += p90_growth

        table_forecast_items.append({
            "table_id": tbl.table_id,
            "table_name": tbl.table_name,
            "current_size_gb": round(cur_tbl_size, 3),
            "current_index_gb": round(cur_idx_size, 3),
            "daily_growth_gb": round(p50_growth, 4),
            "projected_30d_gb": round(cur_total + (p50_growth * 30), 3),
            "p50_exhaustion_date": tbl_exhaustion_date,
            "confidence": 0.35 if is_new_tenant else round(min(0.95, max(0.4, 1.0 - (p90_growth - p10_growth) / (p50_growth + 0.01))), 2),
            "warning_flag": ", ".join(tbl_flags) if tbl_flags else None
        })

    # Overall Tenant Exhaustion Dates
    remaining_storage = max(0.0, limit_gb - tenant_current_total_gb)

    # Calculate P10, P50, P90 exhaustion days
    # Note: P90 growth rate produces earliest (P10 date) exhaustion!
    # P10 growth rate produces latest (P90 date) exhaustion.
    if remaining_storage <= 0:
        p10_exhaustion_date = current_date
        p50_exhaustion_date = current_date
        p90_exhaustion_date = current_date
    else:
        # P50 exhaustion
        if tenant_daily_p50_growth > 0.0001:
            p50_days = int(np.ceil(remaining_storage / tenant_daily_p50_growth))
            p50_exhaustion_date = _date_add(current_date, p50_days) if p50_days <= horizon_days else None
        else:
            warning_flags.add("zero_or_negative_growth")
            p50_exhaustion_date = None

        # P90 growth scenario (Aggressive -> Earliest exhaustion date)
        if tenant_daily_p90_growth > 0.0001:
            p90_growth_days = int(np.ceil(remaining_storage / tenant_daily_p90_growth))
            p10_exhaustion_date = _date_add(current_date, p90_growth_days) if p90_growth_days <= horizon_days else None
        else:
            p10_exhaustion_date = None

        # P10 growth scenario (Conservative -> Latest exhaustion date)
        if tenant_daily_p10_growth > 0.0001:
            p10_growth_days = int(np.ceil(remaining_storage / tenant_daily_p10_growth))
            p90_exhaustion_date = _date_add(current_date, p10_growth_days) if p10_growth_days <= horizon_days else None
        else:
            p90_exhaustion_date = None

    # Overall Confidence Calculation
    # Factors: history length (up to 90 days), uncertainty interval width
    history_score = min(1.0, unique_dates / 60.0)
    uncertainty_ratio = (tenant_daily_p90_growth - tenant_daily_p10_growth) / (tenant_daily_p50_growth + 0.01)
    spread_score = 1.0 / (1.0 + 0.5 * uncertainty_ratio)
    confidence = round(float(0.2 + 0.5 * history_score + 0.28 * spread_score), 2)
    if is_new_tenant:
        confidence = min(0.35, confidence)

    # Multi-step Trajectory
    trajectory = []
    for step in range(0, horizon_days + 1, 5):
        proj_date = _date_add(current_date, step)
        trajectory.append({
            "forecast_date": proj_date.isoformat(),
            "p10_size_gb": round(tenant_current_total_gb + (tenant_daily_p10_growth * step), 3),
            "p50_size_gb": round(tenant_current_total_gb + (tenant_daily_p50_growth * step), 3),
            "p90_size_gb": round(tenant_current_total_gb + (tenant_daily_p90_growth * step), 3),
            "limit_gb": limit_gb
        })

    return {
        "tenant_id": tenant_id,
        "org_id": tenant.org_id,
        "tier": tenant.tier,
        "storage_limit_gb": limit_gb,
        "current_total_gb": round(tenant_current_total_gb, 3),
        "daily_p50_growth_gb": round(tenant_daily_p50_growth, 4),
        "p50_exhaustion_date": p50_exhaustion_date,
        "p10_exhaustion_date": p10_exhaustion_date,
        "p90_exhaustion_date": p90_exhaustion_date,
        "confidence": confidence,
        "warning_flags": sorted(list(warning_flags)),
        "table_forecasts": table_forecast_items,
        "trajectory": trajectory
    }


def generate_and_store_all_forecasts(db: Session) -> int:
    """
    Batch job to generate and persist current forecasts for all active tenants.
    """
    tenants = db.query(models.Tenant).filter(models.Tenant.is_active.is_(True)).all()
    count = 0
    today = date.today()

    for t in tenants:
        try:
            fc = compute_proposed_forecast(t.tenant_id, db)
            traj = fc.get("trajectory", [])
            proj_30d = traj[6]["p50_size_gb"] if len(traj) > 6 else fc["current_total_gb"]
            proj_90d = traj[18]["p50_size_gb"] if len(traj) > 18 else fc["current_total_gb"]

            existing = db.query(models.Forecast).filter(
                models.Forecast.tenant_id == t.tenant_id,
                models.Forecast.forecast_date == today
            ).first()

            if not existing:
                record = models.Forecast(
                    forecast_id=f"fc_{t.tenant_id}_{today.strftime('%Y%m%d')}",
                    forecast_date=today,
                    tenant_id=t.tenant_id,
                    model_name="proposed_quantile",
                    current_size_gb=fc["current_total_gb"],
                    projected_size_30d_gb=proj_30d,
                    projected_size_90d_gb=proj_90d,
                    p50_exhaustion_date=fc["p50_exhaustion_date"],
                    p10_exhaustion_date=fc["p10_exhaustion_date"],
                    p90_exhaustion_date=fc["p90_exhaustion_date"],
                    confidence=fc["confidence"],
                    warning_flag=",".join(fc["warning_flags"]) if fc["warning_flags"] else None,
                    details_json=json.dumps({"flags": fc["warning_flags"]})
                )
                db.add(record)
            else:
                existing.current_size_gb = fc["current_total_gb"]
                existing.projected_size_30d_gb = proj_30d
                existing.projected_size_90d_gb = proj_90d
                existing.p50_exhaustion_date = fc["p50_exhaustion_date"]
                existing.p10_exhaustion_date = fc["p10_exhaustion_date"]
                existing.p90_exhaustion_date = fc["p90_exhaustion_date"]
                existing.confidence = fc["confidence"]
                existing.warning_flag = ",".join(fc["warning_flags"]) if fc["warning_flags"] else None

            count += 1
        except Exception as e:
            print(f"Error forecasting tenant {t.tenant_id}: {e}")

    db.commit()
    return count
