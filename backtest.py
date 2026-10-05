"""
Rolling-origin backtesting harness.

Generic over any forecaster that implements TrailingTrendBaseline's
fit/predict/predict_exhaustion_day interface -- so Step 3's ML model runs
through the exact same two functions below (backtest_series_forecast_accuracy,
backtest_exhaustion) for a like-for-like comparison against this baseline.

Two separate backtests, because they answer different questions:
  1. Forecast accuracy: at horizons of 7/30/90 days, how close is the
     predicted storage value to what actually happened? Runs at index,
     table, and tenant-aggregate levels.
  2. Exhaustion-date accuracy: for tenants (which have a real storage_limit),
     how close is the predicted exhaustion date to the actual one? Only
     meaningful at the tenant level, since that's where the quota lives.
"""
import sqlite3

import numpy as np
import pandas as pd

from baseline import TrailingTrendBaseline

HORIZONS_DAYS = [7, 30, 90]
MIN_HISTORY_DAYS = 60
CUTOFF_SPACING_DAYS = 30
MAX_EXHAUSTION_HORIZON_DAYS = 730


def load_data(db_path):
    conn = sqlite3.connect(db_path)
    storage = pd.read_sql(
        "select date, tenant_id, table_id, table_bytes from fact_daily_storage",
        conn, parse_dates=["date"])
    tables = pd.read_sql(
        "select table_id, tenant_id, growth_archetype, table_category from dim_table", conn)
    tenants = pd.read_sql(
        "select tenant_id, org_id, plan_tier, storage_limit_gb, churn_flag from dim_tenant", conn)
    index_storage = pd.read_sql(
        "select date, index_id, index_bytes from fact_daily_index_storage",
        conn, parse_dates=["date"])
    conn.close()
    tenants["storage_limit_bytes"] = tenants.storage_limit_gb * 1e9
    return storage, tables, tenants, index_storage


def pivot_series(storage, id_col, value_col, agg=False):
    """Wide DataFrame: index=date, columns=id_col, values=value_col.
    agg=True sums duplicate (date, id_col) rows first (used to roll table
    bytes up to a tenant-level aggregate)."""
    if agg:
        g = storage.groupby(["date", id_col])[value_col].sum().reset_index()
    else:
        g = storage[["date", id_col, value_col]]
    return g.pivot(index="date", columns=id_col, values=value_col).sort_index()


def generate_cutoffs(all_dates, min_history_days, spacing_days, min_end_buffer_days):
    start = all_dates.min() + pd.Timedelta(days=min_history_days)
    end = all_dates.max() - pd.Timedelta(days=min_end_buffer_days)
    if start >= end:
        return pd.DatetimeIndex([])
    return pd.date_range(start, end, freq=f"{spacing_days}D")


def backtest_series_forecast_accuracy(pivot, cutoffs, window_days, method,
                                       horizons=HORIZONS_DAYS,
                                       min_history_days=MIN_HISTORY_DAYS,
                                       forecaster_cls=TrailingTrendBaseline):
    """For every (series, cutoff) pair with enough trailing history, fit the
    forecaster and record predicted vs. actual at each horizon."""
    records = []
    dates = pivot.index
    date_pos = {d: i for i, d in enumerate(dates)}
    n_dates = len(dates)

    for col in pivot.columns:
        series = pivot[col]
        valid_mask = series.notna().to_numpy()
        if valid_mask.sum() < min_history_days:
            continue
        first_valid_date = series.index[valid_mask][0]
        values = series.to_numpy()

        for cutoff in cutoffs:
            cpos = date_pos.get(cutoff)
            if cpos is None or cutoff < first_valid_date + pd.Timedelta(days=min_history_days):
                continue
            window_vals = values[max(0, cpos - window_days + 1): cpos + 1]
            if np.isnan(window_vals).all():
                continue
            model = forecaster_cls(window_days, method).fit(window_vals)

            for h in horizons:
                target_pos = cpos + h
                if target_pos >= n_dates:
                    continue
                actual = values[target_pos]
                if np.isnan(actual):
                    continue
                pred = model.predict([h])[0]
                records.append((col, cutoff, h, actual, pred, pred - actual))

    return pd.DataFrame.from_records(
        records, columns=["series_id", "cutoff_date", "horizon_days", "actual", "predicted", "error"])


def backtest_exhaustion(pivot, limits, cutoffs, window_days, method,
                         min_history_days=MIN_HISTORY_DAYS,
                         max_horizon=MAX_EXHAUSTION_HORIZON_DAYS,
                         forecaster_cls=TrailingTrendBaseline):
    """
    limits: Series/dict {series_id: limit_value_bytes}.

    For each (series, cutoff): predict an exhaustion date, then search the
    *actual* remaining history (up to the end of the dataset) for the first
    date the series really crosses the limit.

    Important: many series never cross their limit within the available
    data. That's not a missing prediction, it's right-censored ground
    truth -- we genuinely don't know when (or if) it would exhaust. Those
    rows are flagged `censored=True` and excluded from the error-distance
    metrics (which would otherwise be meaningless), but kept in the output
    so censoring rate itself is visible.
    """
    records = []
    dates = pivot.index
    date_pos = {d: i for i, d in enumerate(dates)}
    n_dates = len(dates)

    for col in pivot.columns:
        if col not in limits or pd.isna(limits[col]):
            continue
        limit_val = float(limits[col])
        series = pivot[col]
        valid_mask = series.notna().to_numpy()
        if valid_mask.sum() < min_history_days:
            continue
        valid_positions = np.where(valid_mask)[0]
        first_valid_date = series.index[valid_positions[0]]
        last_valid_pos = valid_positions[-1]
        values = series.to_numpy()

        for cutoff in cutoffs:
            cpos = date_pos.get(cutoff)
            if cpos is None or cutoff < first_valid_date + pd.Timedelta(days=min_history_days):
                continue
            if cpos > last_valid_pos:
                continue
            window_vals = values[max(0, cpos - window_days + 1): cpos + 1]
            if np.isnan(window_vals).all():
                continue
            model = forecaster_cls(window_days, method).fit(window_vals)
            pred_offset = model.predict_exhaustion_day(limit_val, max_days=max_horizon)
            predicted_date = cutoff + pd.Timedelta(days=pred_offset) if pred_offset is not None else None

            future_vals = values[cpos + 1: last_valid_pos + 1]
            future_dates = dates[cpos + 1: last_valid_pos + 1]
            crossing = np.where(future_vals >= limit_val)[0]
            if len(crossing) > 0:
                actual_date = future_dates[crossing[0]]
                censored = False
            else:
                actual_date = None
                censored = True

            error_days = (predicted_date - actual_date).days if (predicted_date is not None and not censored) else None

            records.append((
                col, cutoff, model.current_value, limit_val, model.daily_rate,
                predicted_date, actual_date, censored, predicted_date is None, error_days,
            ))

    return pd.DataFrame.from_records(records, columns=[
        "series_id", "cutoff_date", "current_value", "limit_value", "daily_rate",
        "predicted_exhaustion_date", "actual_exhaustion_date", "censored",
        "predicted_none", "error_days",
    ])
