"""Evaluation metrics, shared by baseline and (later) ML model evaluation."""
import numpy as np


def mae(errors):
    errors = np.asarray(errors, dtype=float)
    return float(np.mean(np.abs(errors)))


def rmse(errors):
    errors = np.asarray(errors, dtype=float)
    return float(np.sqrt(np.mean(np.square(errors))))


def median_abs_error(errors):
    errors = np.asarray(errors, dtype=float)
    return float(np.median(np.abs(errors)))


def pct_within(errors, threshold):
    errors = np.asarray(errors, dtype=float)
    return float(np.mean(np.abs(errors) <= threshold) * 100)


def smape(actual, predicted):
    """Symmetric MAPE -- used instead of plain MAPE because many tables/
    tenants have near-zero storage early in their history, where plain MAPE
    blows up to huge or undefined values."""
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    denom = np.abs(actual) + np.abs(predicted)
    denom = np.where(denom == 0, 1.0, denom)
    return float(np.mean(2 * np.abs(predicted - actual) / denom) * 100)
