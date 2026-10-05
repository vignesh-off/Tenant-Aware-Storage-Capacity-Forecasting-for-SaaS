#!/usr/bin/env python3
"""
Tenant-Aware Storage & Capacity Forecaster
Backtesting Benchmark Runner

Executes 70/30 time-split evaluation comparing:
1. Baseline: 14-day linear extrapolation of aggregate tenant storage.
2. Proposed: Hierarchical table/index quantile regression with edge-case handling.

Outputs benchmark metrics and generates comparative visual plots in docs/.
"""

import sys
import os
import json
from pathlib import Path

# Configure utf-8 encoding on standard streams for cross-platform reliability
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import pandas as pd

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.database import SessionLocal
from app.backtest import run_comprehensive_backtest

# Ensure output directory exists
DOCS_DIR = BASE_DIR / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)


def plot_benchmark_results(results: dict):
    """Generate high-resolution benchmark and residual evaluation plots."""
    sns.set_theme(style="whitegrid", palette="muted")
    detailed = results.get("detailed_evaluations", [])
    if not detailed:
        print("Warning: No detailed evaluations found to plot.")
        return

    df = pd.DataFrame(detailed)

    # -------------------------------------------------------------
    # Plot 1: Baseline vs Proposed Performance Comparison
    # -------------------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # Metric Comparison Bars
    metrics = ["MAE (days)", "Median AE (days)", "% within ±14d", "P10-P90 Coverage %", "False Urgent %"]
    baseline_vals = [
        results["baseline_mae"],
        results["baseline_median_ae"],
        results["baseline_within_14d_pct"],
        0.0,
        results["baseline_false_urgent_rate"]
    ]
    proposed_vals = [
        results["proposed_mae"],
        results["proposed_median_ae"],
        results["proposed_within_14d_pct"],
        results["proposed_coverage_pct"],
        results["proposed_false_urgent_rate"]
    ]

    x = np.arange(len(metrics))
    width = 0.35

    rects1 = axes[0].bar(x - width/2, baseline_vals, width, label="Baseline (Linear)", color="#e74c3c")
    rects2 = axes[0].bar(x + width/2, proposed_vals, width, label="Proposed (Hierarchical Quantile)", color="#2ecc71")

    axes[0].set_ylabel("Value")
    axes[0].set_title("Forecast Accuracy & Reliability Benchmark", fontsize=13, fontweight="bold")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(metrics, rotation=25, ha="right")
    axes[0].legend()

    # Add value labels on top of bars
    for rect in rects1:
        h = rect.get_height()
        axes[0].annotate(f"{h:.1f}", xy=(rect.get_x() + rect.get_width()/2, h),
                         xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontsize=8)
    for rect in rects2:
        h = rect.get_height()
        axes[0].annotate(f"{h:.1f}", xy=(rect.get_x() + rect.get_width()/2, h),
                         xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontsize=8)

    # Cumulative Error Distribution
    sorted_base = np.sort(df["baseline_ae"].values)
    sorted_prop = np.sort(df["proposed_ae"].values)
    y_vals = np.linspace(0, 100, len(df))

    axes[1].plot(sorted_base, y_vals, label="Baseline Error CDF", color="#e74c3c", linewidth=2.5)
    axes[1].plot(sorted_prop, y_vals, label="Proposed Error CDF", color="#2ecc71", linewidth=2.5)
    axes[1].axvline(14, color="gray", linestyle="--", alpha=0.7, label="±14 Days SLA Target")
    axes[1].set_xlabel("Absolute Error in Days to Exhaustion")
    axes[1].set_ylabel("Cumulative Percentage of Tenants (%)")
    axes[1].set_title("Cumulative Absolute Error Distribution", fontsize=13, fontweight="bold")
    axes[1].set_xlim(0, 90)
    axes[1].set_ylim(0, 105)
    axes[1].legend()

    plt.tight_layout()
    comp_plot_path = DOCS_DIR / "backtest_comparison.png"
    plt.savefig(comp_plot_path, dpi=200)
    plt.close()
    print(f"Saved benchmark comparison plot to: {comp_plot_path}")

    # -------------------------------------------------------------
    # Plot 2: Residual Analysis and Prediction Interval Coverage
    # -------------------------------------------------------------
    fig2, axes2 = plt.subplots(1, 2, figsize=(14, 6))

    # Residuals by Actual Days
    df["base_residual"] = df["baseline_pred_days"] - df["actual_days"]
    df["prop_residual"] = df["proposed_pred_days"] - df["actual_days"]

    axes2[0].scatter(df["actual_days"], df["base_residual"], color="#e74c3c", alpha=0.6, label="Baseline Residuals", s=40)
    axes2[0].scatter(df["actual_days"], df["prop_residual"], color="#2ecc71", alpha=0.8, label="Proposed Residuals", s=40)
    axes2[0].axhline(0, color="black", linestyle="--", alpha=0.8)
    axes2[0].axhspan(-14, 14, color="green", alpha=0.1, label="±14 Days Target Zone")
    axes2[0].set_xlabel("Actual Days to Capacity Exhaustion")
    axes2[0].set_ylabel("Prediction Residual (Predicted - Actual Days)")
    axes2[0].set_title("Exhaustion Date Residuals vs Ground Truth", fontsize=13, fontweight="bold")
    axes2[0].legend()

    # Prediction Interval Bounds Display (Sample 15 tenants)
    sample_df = df.head(15).reset_index(drop=True)
    y_indices = np.arange(len(sample_df))

    axes2[1].errorbar(
        sample_df["proposed_pred_days"],
        y_indices,
        xerr=[
            sample_df["proposed_pred_days"] - sample_df["p_lower_days"],
            sample_df["p_upper_days"] - sample_df["proposed_pred_days"]
        ],
        fmt='o',
        color='#2980b9',
        ecolor='#3498db',
        elinewidth=2,
        capsize=4,
        label="Proposed P10–P90 Envelope"
    )
    axes2[1].scatter(sample_df["actual_days"], y_indices, color="#e74c3c", marker='x', s=60, label="Actual Ground Truth", zorder=5)
    axes2[1].set_yticks(y_indices)
    axes2[1].set_yticklabels(sample_df["tenant_id"])
    axes2[1].set_xlabel("Days to Exhaustion")
    axes2[1].set_title("Uncertainty Envelopes (P10–P90) vs Actuals", fontsize=13, fontweight="bold")
    axes2[1].legend()

    plt.tight_layout()
    res_plot_path = DOCS_DIR / "residuals_plot.png"
    plt.savefig(res_plot_path, dpi=200)
    plt.close()
    print(f"Saved residuals & interval coverage plot to: {res_plot_path}")


def main():
    print("=" * 80)
    print("RUNNING 70/30 TIME-SPLIT BACKTEST BENCHMARK")
    print("=" * 80)

    db = SessionLocal()
    try:
        results = run_comprehensive_backtest(db, train_ratio=0.70, horizon_days=180)
    finally:
        db.close()

    print("\nBENCHMARK RESULTS SUMMARY:")
    print("-" * 80)
    print(f"{'Metric':<32} | {'Baseline':<12} | {'Target':<10} | {'Measured':<12}")
    print("-" * 80)
    for row in results["comparison_table"]:
        print(f"{row['metric']:<32} | {str(row['baseline']):<12} | {str(row['target']):<10} | {str(row['measured']):<12}")
    print("-" * 80)
    print(f"Total Tenants Evaluated: {results['total_tenants_evaluated']}")
    print(f"Split Date:              {results['split_date']}")
    print(f"Run ID:                  {results['run_id']}")
    print("=" * 80)

    # Save results json
    results_json_path = DOCS_DIR / "measured_results.json"
    clean_results = {
        "run_id": results["run_id"],
        "split_date": results["split_date"],
        "baseline_mae": results["baseline_mae"],
        "proposed_mae": results["proposed_mae"],
        "baseline_median_ae": results["baseline_median_ae"],
        "proposed_median_ae": results["proposed_median_ae"],
        "baseline_within_14d_pct": results["baseline_within_14d_pct"],
        "proposed_within_14d_pct": results["proposed_within_14d_pct"],
        "proposed_coverage_pct": results["proposed_coverage_pct"],
        "baseline_false_urgent_rate": results["baseline_false_urgent_rate"],
        "proposed_false_urgent_rate": results["proposed_false_urgent_rate"],
        "comparison_table": results["comparison_table"]
    }
    with open(results_json_path, "w") as f:
        json.dump(clean_results, f, indent=2)
    print(f"Saved measured results json to: {results_json_path}")

    # Generate visual plots
    plot_benchmark_results(results)


if __name__ == "__main__":
    main()
