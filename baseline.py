"""
The baseline forecaster: "what a capacity planner could do with a
spreadsheet." No seasonality, no cross-tenant learning, no activity
features -- just extrapolate the recent local trend forward. This is the
bar the Step 3 ML model has to clear.

Designed with a fit/predict interface so the backtest harness in backtest.py
can run the ML forecaster through the exact same evaluation code later --
same cutoffs, same metrics, same failure-case slices. Only this class
changes between "baseline results" and "model results".
"""
import numpy as np


class TrailingTrendBaseline:
    def __init__(self, window_days=7, method="avg_delta"):
        """
        window_days: how many trailing days of history to use.
        method:
          - "avg_delta": mean day-over-day change over the window (matches
            the simple "average of last N daily deltas" a human would do
            by hand).
          - "linear_trend": OLS slope over the window (slightly more
            robust to a single noisy day, still "simple").
        """
        self.window_days = window_days
        self.method = method
        self.current_value = None
        self.daily_rate = None

    def fit(self, window_values):
        """window_values: 1D array-like of observed values, chronological,
        ending at the cutoff date (inclusive). Returns self."""
        values = np.asarray(window_values, dtype=float)
        values = values[~np.isnan(values)]
        if len(values) < 2:
            self.current_value = values[-1] if len(values) else np.nan
            self.daily_rate = 0.0
            return self

        self.current_value = values[-1]
        if self.method == "avg_delta":
            self.daily_rate = float(np.mean(np.diff(values)))
        elif self.method == "linear_trend":
            x = np.arange(len(values))
            slope, _ = np.polyfit(x, values, 1)
            self.daily_rate = float(slope)
        else:
            raise ValueError(f"unknown method: {self.method}")
        return self

    def predict(self, days_ahead):
        """days_ahead: array-like of integer day offsets from the cutoff.
        Returns predicted values (linear extrapolation)."""
        days_ahead = np.asarray(days_ahead, dtype=float)
        return self.current_value + self.daily_rate * days_ahead

    def predict_exhaustion_day(self, limit_value, max_days=730):
        """
        Smallest integer day-offset (>=0) at which the linear projection
        first reaches limit_value, or None if:
          - the trend is flat/declining (daily_rate <= 0), or
          - the projected crossing is further than max_days out.
        None means "baseline predicts this will not run out of capacity
        within the forecast horizon" -- a real, reportable prediction, not
        a missing value.
        """
        if self.daily_rate is None or np.isnan(self.daily_rate) or self.daily_rate <= 0:
            return None
        remaining = limit_value - self.current_value
        if remaining <= 0:
            return 0  # already at/over limit at the cutoff
        days = remaining / self.daily_rate
        if days > max_days:
            return None
        return int(np.ceil(days))
