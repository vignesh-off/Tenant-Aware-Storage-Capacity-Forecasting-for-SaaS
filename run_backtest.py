"""
Runs the baseline forecaster through the rolling-origin backtest at index,
table, and tenant levels, plus the tenant-level exhaustion-date backtest,
and prints a results report. Persists all results back to storage.db so
Step 3 (the ML model) can be compared against the exact same cutoffs.

Run:
    python3 run_backtest.py
"""
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import metrics as m
from backtest import (
    load_data, pivot_series, generate_cutoffs,
    backtest_series_forecast_accuracy, backtest_exhaustion,
    HORIZONS_DAYS, MIN_HISTORY_DAYS, CUTOFF_SPACING_DAYS, MAX_EXHAUSTION_HORIZON_DAYS,
)

DB_PATH = "/home/claude/storage_forecasting/data/storage.db"
BASELINE_WINDOW_DAYS = 7
BASELINE_METHOD = "avg_delta"
END_BUFFER_DAYS = 30  # leave room so most cutoffs still have some future data


def summarize_accuracy(df, label):
    print(f"\n--- {label}: storage forecast accuracy ---")
    if df.empty:
        print("  (no evaluable records)")
        return
    for h in sorted(df.horizon_days.unique()):
        sub = df[df.horizon_days == h]
        mae_gb = m.mae(sub.error) / 1e9
        smape = m.smape(sub.actual, sub.predicted)
        print(f"  Horizon {h:>3}d | n={len(sub):>7,} | MAE={mae_gb:>9.4f} GB | sMAPE={smape:>6.2f}%")


def summarize_exhaustion(df, label):
    print(f"\n--- {label}: exhaustion-date backtest ---")
    n_total = len(df)
    if n_total == 0:
        print("  (no evaluable records -- check that some tenants are near their storage limit)")
        return
    n_censored = int(df.censored.sum())
    print(f"  total (tenant, cutoff) predictions: {n_total}")
    print(f"  censored (tenant never actually crossed its limit within the "
          f"data window): {n_censored} ({n_censored / n_total * 100:.1f}%)")

    known_actual = df[~df.censored]
    if len(known_actual) == 0:
        print("  (no cases with a known actual exhaustion date -- cannot score exhaustion-date error)")
        return
    missed = int(known_actual.predicted_none.sum())
    print(f"  of {len(known_actual)} cases with a KNOWN actual exhaustion date:")
    print(f"    baseline predicted 'never' (missed entirely): {missed} "
          f"({missed / len(known_actual) * 100:.1f}%)")

    evald = known_actual[known_actual.error_days.notna()]
    print(f"    baseline gave a numeric date, scorable: {len(evald)}")
    if len(evald) == 0:
        print("  (not enough scorable cases to compute error metrics)")
        return
    errors = evald.error_days.to_numpy()
    print(f"\n  BASELINE RESULTS")
    print(f"  Exhaustion-date MAE:   {m.mae(errors):.1f} days")
    print(f"  Median error:          {m.median_abs_error(errors):.1f} days")
    print(f"  RMSE:                  {m.rmse(errors):.1f} days")
    print(f"  Within +/-7 days:      {m.pct_within(errors, 7):.1f}%")
    print(f"  Within +/-30 days:     {m.pct_within(errors, 30):.1f}%")
    bias = float(errors.mean())
    direction = "predicts exhaustion TOO LATE on average" if bias > 0 else "predicts exhaustion TOO EARLY on average"
    print(f"  Mean signed error:     {bias:+.1f} days ({direction})")


def main():
    t0 = time.time()
    storage, tables, tenants, index_storage = load_data(DB_PATH)

    table_pivot = pivot_series(storage, "table_id", "table_bytes")
    tenant_pivot = pivot_series(storage, "tenant_id", "table_bytes", agg=True)
    index_pivot = pivot_series(index_storage, "index_id", "index_bytes")

    all_dates = table_pivot.index
    cutoffs = generate_cutoffs(all_dates, MIN_HISTORY_DAYS, CUTOFF_SPACING_DAYS, END_BUFFER_DAYS)
    print(f"Loaded data in {time.time()-t0:.1f}s. Backtesting with {len(cutoffs)} rolling-origin "
          f"cutoffs ({cutoffs.min().date()} .. {cutoffs.max().date()}),")
    print(f"baseline = TrailingTrendBaseline(window={BASELINE_WINDOW_DAYS}d, method='{BASELINE_METHOD}')")

    t1 = time.time()
    table_acc = backtest_series_forecast_accuracy(table_pivot, cutoffs, BASELINE_WINDOW_DAYS, BASELINE_METHOD)
    index_acc = backtest_series_forecast_accuracy(index_pivot, cutoffs, BASELINE_WINDOW_DAYS, BASELINE_METHOD)
    tenant_acc = backtest_series_forecast_accuracy(tenant_pivot, cutoffs, BASELINE_WINDOW_DAYS, BASELINE_METHOD)
    print(f"Forecast-accuracy backtests done in {time.time()-t1:.1f}s "
          f"(table={len(table_acc):,}, index={len(index_acc):,}, tenant={len(tenant_acc):,} records)")

    t2 = time.time()
    limits = tenants.set_index("tenant_id")["storage_limit_bytes"]
    tenant_exhaustion = backtest_exhaustion(
        tenant_pivot, limits, cutoffs, BASELINE_WINDOW_DAYS, BASELINE_METHOD,
        max_horizon=MAX_EXHAUSTION_HORIZON_DAYS)
    print(f"Exhaustion backtest done in {time.time()-t2:.1f}s ({len(tenant_exhaustion):,} records)")

    print("\n" + "=" * 70)
    print(" BASELINE FORECASTER -- BACKTEST RESULTS")
    print("=" * 70)
    summarize_accuracy(table_acc, "Table-level")
    summarize_accuracy(index_acc, "Index-level")
    summarize_accuracy(tenant_acc, "Tenant-level (aggregate)")
    summarize_exhaustion(tenant_exhaustion, "Tenant-level")

    print("\n--- Failure-case slice: table-level sMAPE by growth archetype (horizon=30d) ---")
    merged = table_acc.merge(tables[["table_id", "growth_archetype"]],
                              left_on="series_id", right_on="table_id")
    h30 = merged[merged.horizon_days == 30]
    slice_summary = (h30.groupby("growth_archetype")
                      .apply(lambda g: pd.Series({
                          "n": len(g), "sMAPE": m.smape(g.actual, g.predicted),
                          "MAE_GB": m.mae(g.error) / 1e9,
                      }))
                      .sort_values("sMAPE", ascending=False))
    print(slice_summary.to_string(float_format=lambda x: f"{x:.3f}"))

    print("\n--- Failure-case slice: tenant exhaustion-date error by plan tier ---")
    tmerged = tenant_exhaustion.merge(tenants[["tenant_id", "plan_tier"]],
                                       left_on="series_id", right_on="tenant_id")
    for plan, grp in tmerged.groupby("plan_tier"):
        evald = grp[~grp.censored & grp.error_days.notna()]
        if len(evald) == 0:
            print(f"  {plan:<12} n=0 evaluable (all censored or all 'never' predictions)")
            continue
        errs = evald.error_days.to_numpy()
        print(f"  {plan:<12} n={len(evald):>4}  MAE={m.mae(errs):>7.1f}d  "
              f"within+/-7d={m.pct_within(errs, 7):.1f}%")

    # persist for later comparison against the Step 3 ML model
    conn = sqlite3.connect(DB_PATH)
    table_acc.assign(model="baseline").to_sql("backtest_table_accuracy", conn, if_exists="replace", index=False)
    index_acc.assign(model="baseline").to_sql("backtest_index_accuracy", conn, if_exists="replace", index=False)
    tenant_acc.assign(model="baseline").to_sql("backtest_tenant_accuracy", conn, if_exists="replace", index=False)
    tenant_exhaustion.assign(model="baseline").to_sql("backtest_tenant_exhaustion", conn, if_exists="replace", index=False)
    conn.close()
    print(f"\nResults persisted to {DB_PATH} (backtest_table_accuracy, backtest_index_accuracy, "
          f"backtest_tenant_accuracy, backtest_tenant_exhaustion)")
    print(f"\nTotal runtime: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
