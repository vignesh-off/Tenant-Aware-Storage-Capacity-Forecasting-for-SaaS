# Baseline Forecaster + Backtesting Harness

## Files
- `baseline.py` — `TrailingTrendBaseline`: extrapolates a trailing-window
  trend forward. `fit(window_values) -> predict(days_ahead)` /
  `predict_exhaustion_day(limit)`. This exact interface is what Step 3's ML
  model will implement too, so it drops into the same harness.
- `metrics.py` — MAE, RMSE, median absolute error, % within threshold,
  sMAPE (symmetric MAPE, used instead of plain MAPE because many series
  are near-zero early in their history where MAPE is unstable/undefined).
- `backtest.py` — the harness:
  - `backtest_series_forecast_accuracy(pivot, cutoffs, ...)` — rolling-
    origin point-forecast accuracy at 7/30/90-day horizons. Runs at index,
    table, and tenant-aggregate levels.
  - `backtest_exhaustion(pivot, limits, cutoffs, ...)` — tenant-level only
    (that's where `storage_limit_gb` lives). Predicts an exhaustion date,
    then checks it against what actually happened in the data.
- `run_backtest.py` — orchestrates everything, prints the report, writes
  results back to `storage.db` as `backtest_table_accuracy`,
  `backtest_index_accuracy`, `backtest_tenant_accuracy`,
  `backtest_tenant_exhaustion` (all tagged `model='baseline'` so later ML
  results can sit alongside them for direct comparison).

## Run
```bash
python3 run_backtest.py
```

## Method
- **Rolling-origin backtesting**: cutoffs every 30 days across the
  dataset's history (min 60 days of prior history required, 30-day buffer
  left at the end). At each cutoff, the baseline only sees data up to that
  point — no leakage from the future.
- **Baseline itself**: mean of the last 7 daily deltas, extrapolated
  linearly forward. Deliberately simple — this is the bar a real model
  needs to clear, not a competitor to it.

## Important methodological point: censoring
Most tenants never actually hit their storage limit within the ~3.7 years
of synthetic history. For those, "did the baseline get the exhaustion date
right" has no true answer to check against — the actual date is unknown,
not "never" (it's *right-censored*: we only know it hadn't happened by
`end_date`). The harness flags these `censored=True` and **excludes them
from the error-distance metrics** rather than silently treating "no
prediction" as correct or wrong. Only the ~116 tenants with a real,
observed crossing are used to compute exhaustion-date MAE/RMSE/etc. The
censoring rate itself (currently ~96.5%) is reported, since a low count of
scorable cases is itself a limitation worth being upfront about — more
history, a lower churn/plateau archetype mix, or tighter quotas would
increase it.

## Known baseline weaknesses (feed directly into Step 3 motivation)
- **44% miss rate**: of tenants with a real exhaustion event, the 7-day
  trailing-window baseline predicted "never" 44% of the time — it's too
  short-sighted to see acceleration coming.
- **Heavy-tailed exhaustion errors**: median error (12 days) is much
  better than MAE (89 days) — a handful of badly-missed predictions
  dominate the mean. Worth showing the error distribution, not just MAE,
  in the final write-up.
- **Archetype-dependent accuracy**: `noisy_small` and `sawtooth_retention`
  tables are ~2-5x harder than `steady_linear`/`plateaued_churned` — a
  linear trend literally cannot represent a purge cycle or high-variance
  small tenant. This is expected and is exactly the gap a model with
  seasonality/activity features should close.
- **Aggregation helps accuracy**: tenant-level (summed) forecasts are
  meaningfully more accurate than individual table forecasts (sMAPE ~3x
  lower at every horizon) — pure statistical smoothing from summing many
  noisy series, not a modeling improvement. Worth stating explicitly so
  it isn't mistaken for the ML model's contribution later.
