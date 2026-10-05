import os
import sys
import json
from datetime import datetime, date
from pathlib import Path
import streamlit as st
import pandas as pd
import numpy as np
import requests
import altair as alt

# Configure Page
st.set_page_config(
    page_title="Tenant Capacity Forecaster",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# API Configuration
API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")

# Simulated User Roles and Org Bindings
ROLES_CONFIG = {
    "platform_admin": {
        "display": "Platform Admin (SRE / Global Ops)",
        "user_id": "usr_plat_admin",
        "org_id": "org_1",
        "description": "Full access to all 50 tenants across 5 orgs, capacity approvals, and retention policies."
    },
    "tenant_admin": {
        "display": "Tenant Admin (Customer Ops - Org 1)",
        "user_id": "usr_tenant_org1",
        "org_id": "org_1",
        "description": "Scoped to tenants belonging to Organization 1 only. Can request capacity and simulate retention."
    },
    "external_partner": {
        "display": "External Partner (MSP / Managed Cloud)",
        "user_id": "usr_partner_org5",
        "org_id": "org_5",
        "description": "Read-only visibility restricted exclusively to delegated partner tenant schemas."
    },
    "auditor": {
        "display": "Compliance Auditor (Internal Audit)",
        "user_id": "usr_auditor_org1",
        "org_id": "org_1",
        "description": "Read-only access strictly limited to immutable audit logs and benchmark backtests."
    }
}


def make_api_request(method: str, endpoint: str, role: str, org_id: str, user_id: str, data: dict = None, params: dict = None):
    """Utility to call FastAPI backend with RBAC headers."""
    headers = {
        "X-Role": role,
        "X-Org-Id": org_id,
        "X-User-Id": user_id,
        "Content-Type": "application/json"
    }
    url = f"{API_URL}{endpoint}"
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=params, timeout=10)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=data, timeout=15)
        else:
            return None, f"Unsupported method: {method}"
        
        if response.status_code in [200, 201]:
            return response.json(), None
        else:
            try:
                err_detail = response.json().get("detail", response.text)
            except Exception:
                err_detail = response.text
            return None, f"API Error ({response.status_code}): {err_detail}"
    except requests.exceptions.RequestException as e:
        return None, f"Could not connect to FastAPI at {API_URL}: {str(e)}"


# ==============================================================================
# SIDEBAR: ROLE SIMULATION & NAVIGATION
# ==============================================================================
st.sidebar.title("🏢 Capacity Forecaster")
st.sidebar.caption("Hierarchical Multi-Tenant Capacity Intelligence")

selected_role_key = st.sidebar.selectbox(
    "Simulate User Persona / Role:",
    options=list(ROLES_CONFIG.keys()),
    format_func=lambda k: ROLES_CONFIG[k]["display"]
)

active_role_meta = ROLES_CONFIG[selected_role_key]
st.sidebar.info(f"**Role:** `{selected_role_key}`\n\n**Org:** `{active_role_meta['org_id']}`\n\n_{active_role_meta['description']}_")

# Role-specific Navigation Options
if selected_role_key == "platform_admin":
    nav_options = ["Global Heatmap & Approvals", "Tenant Forecaster", "Backtest Benchmark", "Audit Logs", "Workflow Map"]
elif selected_role_key == "tenant_admin":
    nav_options = ["Tenant Forecaster", "Capacity Requests", "Workflow Map"]
elif selected_role_key == "external_partner":
    nav_options = ["Partner Delegated Tenants", "Workflow Map"]
else:  # auditor
    nav_options = ["Backtest Benchmark", "Audit Logs", "Workflow Map"]

selected_page = st.sidebar.radio("Navigation Menu", nav_options)

st.sidebar.divider()
st.sidebar.caption("SaaS Multi-Tenant Schema Storage Engine v1.0.0\n100% Synthetic Anonymized Dataset")


# ==============================================================================
# PAGE 1: GLOBAL HEATMAP & APPROVALS (PLATFORM ADMIN)
# ==============================================================================
if selected_page == "Global Heatmap & Approvals":
    st.title("🌐 Platform Capacity Risk Heatmap")
    st.markdown(
        "Real-time surveillance of schema storage exhaustion across all 50 tenant environments. "
        "Tenants are classified based on **P50 days-to-exhaustion** and consumption thresholds."
    )

    col_btn1, col_btn2 = st.columns([1, 4])
    with col_btn1:
        if st.button("🔄 Batch Refresh Forecasts"):
            res, err = make_api_request("POST", "/api/forecasts/generate", selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"])
            if err:
                st.error(err)
            else:
                st.success(f"Successfully refreshed forecasts for {res.get('tenants_updated', 0)} tenants!")

    # Fetch Heatmap Data
    heatmap_data, err = make_api_request("GET", "/api/admin/heatmap", selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"])
    if err:
        st.error(err)
    elif heatmap_data:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Active Tenants", heatmap_data["total_tenants"])
        m2.metric("Critical Risk (≤14 Days)", heatmap_data["critical_count"], delta="-Urgent Alert", delta_color="inverse")
        m3.metric("Warning Risk (15–30 Days)", heatmap_data["warning_count"], delta="Monitor", delta_color="off")
        m4.metric("Healthy / Stable (>30 Days)", heatmap_data["healthy_count"])

        st.subheader("Tenant Risk Register")
        tenants_df = pd.DataFrame(heatmap_data["tenants"])

        # Format dataframe for display
        display_df = tenants_df.copy()
        display_df["warning_flags"] = display_df["warning_flags"].apply(lambda flags: ", ".join(flags) if flags else "None")
        
        st.dataframe(
            display_df[[
                "tenant_id", "org_id", "tier", "current_size_gb", "storage_limit_gb",
                "pct_used", "days_to_exhaustion", "urgency_level", "warning_flags"
            ]],
            column_config={
                "tenant_id": "Tenant ID",
                "org_id": "Organization",
                "tier": "Tier",
                "current_size_gb": st.column_config.NumberColumn("Current (GB)", format="%.2f GB"),
                "storage_limit_gb": st.column_config.NumberColumn("Limit (GB)", format="%.0f GB"),
                "pct_used": st.column_config.ProgressColumn("Quota Used %", min_value=0, max_value=100, format="%.1f%%"),
                "days_to_exhaustion": st.column_config.NumberColumn("Days to Exhaustion", format="%d days"),
                "urgency_level": st.column_config.TextColumn("Urgency"),
                "warning_flags": "Active Alerts"
            },
            use_container_width=True,
            hide_index=True
        )

    st.divider()
    st.subheader("📋 Proactive Capacity Approval Queue")
    approvals, err = make_api_request("GET", "/api/admin/approvals", selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"])
    if err:
        st.warning(err)
    elif approvals:
        pending_requests = [r for r in approvals if r["status"] == "PENDING"]
        if not pending_requests:
            st.info("No pending capacity expansion requests in the queue.")
        else:
            for req in pending_requests:
                with st.expander(f"Request {req['request_id']} — Tenant: {req['tenant_id']} (+{req['requested_gb']} GB)", expanded=True):
                    c1, c2, c3, c4 = st.columns(4)
                    c1.write(f"**Current Limit:** {req['current_limit_gb']} GB")
                    c2.write(f"**Requested Expansion:** +{req['requested_gb']} GB")
                    c3.write(f"**Requested By:** `{req['requested_by']}`")
                    c4.write(f"**Reason:** {req['reason'] or 'N/A'}")

                    btn_c1, btn_c2, _ = st.columns([1, 1, 4])
                    with btn_c1:
                        if st.button("✅ Approve", key=f"app_{req['request_id']}"):
                            dec_res, dec_err = make_api_request(
                                "POST",
                                f"/api/admin/approvals/{req['request_id']}/decide",
                                selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"],
                                data={"status": "APPROVED", "decision_reason": "Approved via admin console"}
                            )
                            if dec_err:
                                st.error(dec_err)
                            else:
                                st.success("Capacity expansion approved and quota updated!")
                                st.rerun()
                    with btn_c2:
                        if st.button("❌ Reject", key=f"rej_{req['request_id']}"):
                            dec_res, dec_err = make_api_request(
                                "POST",
                                f"/api/admin/approvals/{req['request_id']}/decide",
                                selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"],
                                data={"status": "REJECTED", "decision_reason": "Deferred by platform administrator"}
                            )
                            if dec_err:
                                st.error(dec_err)
                            else:
                                st.warning("Request rejected.")
                                st.rerun()


# ==============================================================================
# PAGE 2: TENANT FORECASTER (TENANT ADMIN & PLATFORM ADMIN)
# ==============================================================================
elif selected_page == "Tenant Forecaster":
    st.title("📊 Tenant Schema & Capacity Forecaster")

    # Fetch tenants allowed for this role
    tenants_list, err = make_api_request("GET", "/api/tenants", selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"])
    if err:
        st.error(err)
        st.stop()

    tenant_options = [t["tenant_id"] for t in tenants_list]
    if not tenant_options:
        st.warning("No accessible tenants found for your credentials.")
        st.stop()

    selected_tenant_id = st.selectbox("Select Tenant Environment:", tenant_options)

    # Fetch detailed forecast
    forecast_data, fc_err = make_api_request(
        "GET",
        f"/api/forecasts/tenant/{selected_tenant_id}",
        selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"],
        params={"horizon_days": 180}
    )

    if fc_err:
        st.error(fc_err)
        st.stop()

    # Metric Cards
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Storage Quota Limit", f"{forecast_data['storage_limit_gb']} GB")
    k2.metric("Current Total Storage", f"{forecast_data['current_total_gb']} GB", delta=f"{round((forecast_data['current_total_gb']/forecast_data['storage_limit_gb'])*100, 1)}% used")
    
    p50_date_str = forecast_data["proposed_exhaustion_date"] or "Beyond 180d"
    base_date_str = forecast_data["baseline_exhaustion_date"] or "Beyond 180d"
    k3.metric("Proposed Exhaustion (P50)", p50_date_str, delta="Proactive Target")
    k4.metric("Model Confidence Score", f"{int(forecast_data['confidence'] * 100)}%", delta=f"Base: {base_date_str}", delta_color="off")

    # Warning alerts for Edge Cases
    if forecast_data["warning_flags"]:
        for flag in forecast_data["warning_flags"]:
            if flag == "spike_detected":
                st.warning("⚠️ **Spike / Backfill Detected:** An ingestion spike (>5x rolling std) was winsorized to prevent chronic over-allocation. Uncertainty bounds (P10–P90) have been widened.")
            elif flag == "new_tenant_fallback":
                st.info("ℹ️ **New Tenant Cold-Start (<7 Days):** Historical telemetry is sparse; forecaster has fallen back to tier peer-group priors with a lower confidence rating.")
            elif flag == "index_bloat":
                st.warning("⚠️ **Index Bloat Detected:** Secondary index storage growth significantly outpaces row storage. Dedicated index projection model active.")
            elif flag == "zero_or_negative_growth":
                st.success("✅ **Stable / Dormant Growth:** Net tenant growth is ≤ 0. Marked as '> horizon' to prevent false urgent alarms.")

    # Trajectory Plot
    st.subheader("Capacity Trajectory & Quantile Uncertainty Envelope (P10 / P50 / P90)")
    traj_data = forecast_data.get("trajectory", [])
    if traj_data:
        traj_df = pd.DataFrame(traj_data)
        traj_df["forecast_date"] = pd.to_datetime(traj_df["forecast_date"])

        # Altair multi-layer chart
        base_chart = alt.Chart(traj_df).encode(x=alt.X("forecast_date:T", title="Timeline (Next 180 Days)"))

        # Uncertainty Ribbon (P10 to P90)
        band = base_chart.mark_area(opacity=0.25, color="#3498db").encode(
            y=alt.Y("p10_size_gb:Q", title="Storage Volume (GB)"),
            y2="p90_size_gb:Q"
        )

        # Expected Line (P50)
        line = base_chart.mark_line(color="#2980b9", strokeWidth=3).encode(
            y="p50_size_gb:Q"
        )

        # Quota Limit Line
        limit_line = base_chart.mark_line(color="#e74c3c", strokeDash=[6, 4], strokeWidth=2).encode(
            y="limit_gb:Q"
        )

        chart = (band + line + limit_line).properties(height=380).interactive()
        st.altair_chart(chart, use_container_width=True)
        st.caption("🔵 Solid Blue: P50 (Expected) | 🟦 Blue Shading: P10–P90 Uncertainty Envelope | 🔴 Red Dashed: Storage Quota Ceiling")

    # Table & Index Level Breakdown
    st.subheader("📑 Table & Index Breakdown")
    tbl_items = forecast_data.get("table_forecasts", [])
    if tbl_items:
        tbl_df = pd.DataFrame(tbl_items)
        st.dataframe(
            tbl_df,
            column_config={
                "table_id": "Table Identifier",
                "table_name": "Table Name",
                "current_size_gb": st.column_config.NumberColumn("Table (GB)", format="%.3f GB"),
                "current_index_gb": st.column_config.NumberColumn("Index (GB)", format="%.3f GB"),
                "daily_growth_gb": st.column_config.NumberColumn("Daily Growth", format="%.4f GB/d"),
                "projected_30d_gb": st.column_config.NumberColumn("30d Projected (GB)", format="%.2f GB"),
                "p50_exhaustion_date": "Exhaustion Date",
                "confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1.0, format="%.2f"),
                "warning_flag": "Alerts"
            },
            use_container_width=True,
            hide_index=True
        )

    # What-If Retention Simulator
    st.divider()
    st.subheader("🛠️ What-If Retention Simulator & Capacity Action")
    sim_col1, sim_col2 = st.columns(2)

    with sim_col1:
        st.markdown("**Simulate Retention Policy Impact**")
        sim_days = st.slider("Modify Retention Horizon (Days):", min_value=7, max_value=180, value=60)
        if st.button("Run Simulation Scenario"):
            st.info(f"Simulating retention policy of {sim_days} days across high-velocity tables. Daily ingest will achieve equilibrium at asymptotic ceiling: ~{round(forecast_data['current_total_gb'] * (sim_days/90.0), 1)} GB.")

    with sim_col2:
        st.markdown("**Proactively Request Capacity Addition**")
        with st.form("request_capacity_form"):
            add_gb = st.number_input("Additional Storage Needed (GB):", min_value=10.0, max_value=2000.0, value=100.0, step=25.0)
            req_reason = st.text_input("Operational Justification:", value="Forecasted P50 exhaustion within upcoming quarter")
            submit_btn = st.form_submit_btn("Submit Capacity Request")

            if submit_btn:
                res, err = make_api_request(
                    "POST",
                    "/api/admin/approvals",
                    selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"],
                    data={"tenant_id": selected_tenant_id, "requested_gb": add_gb, "reason": req_reason}
                )
                if err:
                    st.error(err)
                else:
                    st.success(f"Capacity expansion request {res['request_id']} submitted successfully for Platform Admin review!")


# ==============================================================================
# PAGE 3: CAPACITY REQUESTS (TENANT ADMIN)
# ==============================================================================
elif selected_page == "Capacity Requests":
    st.title("📝 Organization Capacity Requests")
    st.markdown("Track the status of submitted capacity requests for your organization's tenant schemas.")

    approvals, err = make_api_request("GET", "/api/admin/approvals", selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"])
    if err:
        st.error(err)
    elif approvals:
        df = pd.DataFrame(approvals)
        st.dataframe(
            df[["request_id", "tenant_id", "requested_gb", "current_limit_gb", "status", "reason", "created_at"]],
            column_config={
                "request_id": "Request ID",
                "tenant_id": "Tenant",
                "requested_gb": st.column_config.NumberColumn("Requested Expansion (GB)", format="+%.0f GB"),
                "current_limit_gb": st.column_config.NumberColumn("Limit at Request (GB)", format="%.0f GB"),
                "status": "Workflow Status",
                "reason": "Justification",
                "created_at": "Submitted At"
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No capacity expansion requests currently registered for your organization.")


# ==============================================================================
# PAGE 4: PARTNER DELEGATED TENANTS (EXTERNAL PARTNER)
# ==============================================================================
elif selected_page == "Partner Delegated Tenants":
    st.title("🤝 External Partner Delegated View")
    st.caption("Read-Only Managed Capacity Portal")
    st.markdown(
        "As an **External Partner**, you possess scoped read-only visibility into delegated tenant schemas. "
        "Underlying system schemas, foreign tenants, administrative approval queues, and storage quota modifications are protected."
    )

    tenants_list, err = make_api_request("GET", "/api/tenants", selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"])
    if err:
        st.error(err)
    elif tenants_list:
        st.success(f"You have delegated read-only access to **{len(tenants_list)} tenants**.")
        for t in tenants_list:
            with st.container(border=True):
                p1, p2, p3, p4 = st.columns(4)
                p1.write(f"### `{t['tenant_id']}`")
                p1.caption(f"Tier: {t['tier'].upper()} | Org: {t['org_id']}")
                p2.metric("Storage In Use", f"{t['current_storage_gb']} GB")
                p3.metric("Assigned Quota", f"{t['storage_limit_gb']} GB")
                p4.metric("Days Remaining", f"{t['days_to_exhaustion']} days" if t['days_to_exhaustion'] is not None else "Stable")

                # Fetch trajectory
                fc, fc_err = make_api_request(
                    "GET", f"/api/forecasts/tenant/{t['tenant_id']}",
                    selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"]
                )
                if not fc_err and fc and fc.get("trajectory"):
                    sub_df = pd.DataFrame(fc["trajectory"])
                    sub_df["forecast_date"] = pd.to_datetime(sub_df["forecast_date"])
                    sub_chart = alt.Chart(sub_df).mark_line(color="#27ae60").encode(
                        x=alt.X("forecast_date:T", title="Timeline"),
                        y=alt.Y("p50_size_gb:Q", title="Projected GB")
                    ).properties(height=180)
                    st.altair_chart(sub_chart, use_container_width=True)


# ==============================================================================
# PAGE 5: BACKTEST BENCHMARK
# ==============================================================================
elif selected_page == "Backtest Benchmark":
    st.title("🧪 Empirical Backtest & Benchmark Results")
    st.markdown(
        "Evaluation of the **Baseline Linear Extrapolation** vs the **Proposed Hierarchical Quantile Regressor** "
        "using a 70/30 time-split on 180 days of multi-tenant historical telemetry."
    )

    summary, err = make_api_request("GET", "/api/admin/backtest/summary", selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"])
    if err:
        st.error(err)
    elif summary:
        # Comparison Table
        st.subheader("Empirical Benchmark Comparison")
        comp_df = pd.DataFrame(summary["comparison_table"])
        st.table(comp_df)

        b1, b2, b3, b4 = st.columns(4)
        b1.metric("MAE Reduction", f"{summary['baseline_mae']}d ➔ {summary['proposed_mae']}d", delta=f"-{round(summary['baseline_mae'] - summary['proposed_mae'], 1)} days", delta_color="normal")
        b2.metric("Median Absolute Error", f"{summary['baseline_median_ae']}d ➔ {summary['proposed_median_ae']}d", delta=f"-{round(summary['baseline_median_ae'] - summary['proposed_median_ae'], 1)} days", delta_color="normal")
        b3.metric("±14-Day Accuracy", f"{summary['proposed_within_14d_pct']}%", delta=f"+{round(summary['proposed_within_14d_pct'] - summary['baseline_within_14d_pct'], 1)}%")
        b4.metric("False Urgent Alert Rate", f"{summary['proposed_false_urgent_rate']}%", delta=f"-{round(summary['baseline_false_urgent_rate'] - summary['proposed_false_urgent_rate'], 1)}%", delta_color="inverse")

        st.divider()
        st.subheader("Benchmark Visualizations")
        p_col1, p_col2 = st.columns(2)
        comp_img = Path("docs/backtest_comparison.png")
        res_img = Path("docs/residuals_plot.png")

        with p_col1:
            if comp_img.exists():
                st.image(str(comp_img), caption="Forecast Accuracy & Cumulative Error CDF")
            else:
                st.info("Run `python scripts/run_backtest.py` to render the benchmark comparison figure.")

        with p_col2:
            if res_img.exists():
                st.image(str(res_img), caption="Exhaustion Date Residuals & Prediction Intervals")
            else:
                st.info("Run `python scripts/run_backtest.py` to render the residual coverage figure.")


# ==============================================================================
# PAGE 6: AUDIT LOGS (AUDITOR & PLATFORM ADMIN)
# ==============================================================================
elif selected_page == "Audit Logs":
    st.title("🔒 Security & Operations Audit Logs")
    st.markdown("Immutable record of storage quota modifications, retention policy updates, and capacity approvals.")

    logs, err = make_api_request("GET", "/api/audit/logs", selected_role_key, active_role_meta["org_id"], active_role_meta["user_id"])
    if err:
        st.error(err)
    elif logs:
        df_logs = pd.DataFrame(logs)
        st.dataframe(
            df_logs[["log_id", "timestamp", "actor", "role", "action", "entity", "details"]],
            column_config={
                "log_id": "Log ID",
                "timestamp": "Timestamp",
                "actor": "Actor",
                "role": "Role",
                "action": "Action",
                "entity": "Affected Entity",
                "details": "Details"
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No audit entries currently recorded.")


# ==============================================================================
# PAGE 7: WORKFLOW MAP
# ==============================================================================
elif selected_page == "Workflow Map":
    st.title("🔄 Operating Workflow: Reactive vs Proactive")
    
    st.markdown("""
    ### Transition from Reactive Capacity Addition to Proactive Forecasting
    
    The legacy process suffered from critical failure modes: ops tickets created only after tenant disk alerts fired,
    causing urgent emergency interventions and inaccurate budget planning. The proposed forecaster introduces a closed-loop
    predictive workflow.
    """)

    st.subheader("1. Legacy Reactive Workflow (Past State)")
    st.code("""
    [Tenant Schema Growth] 
           │
           ▼
    [Disk Threshold Alert (e.g. 90% full)]
           │
           ▼ (Urgent Incident Created)
    [SRE / Ops Investigation]
           │
           ▼ (Manual Schema & Table Queries)
    [Capacity Expansion Request]
           │
           ▼ (Emergency Budget Sign-off)
    [Add Storage Quota]
           │
           ▼
    [Acknowledge Monitoring Incident]
    """, language="text")

    st.subheader("2. Modern Proactive Workflow (Current System)")
    st.code("""
    [Daily Metrics Aggregation (Rows, Tables, Indexes, Telemetry)]
           │
           ▼
    [Hierarchical Quantile Forecasting (P10 / P50 / P90)]
           │
           ▼
    [Early Risk Detection (Identifies Exhaustion > 30-60 Days Out)]
           │
           ▼
    [Automated Capacity Planning & Retention Simulation]
           │
           ▼
    [RBAC-Governed One-Click Approval Queue]
           │
           ▼
    [Automated Quota Provisioning & Closed-Loop Backtest Verification]
    """, language="text")

    st.subheader("3. Role-Based Workflow Visibility Matrix")
    matrix_df = pd.DataFrame([
        {"Role": "Platform Admin", "Visibility": "All 50 Tenants, All Orgs, Global Heatmap", "Key Actions": "Approve Capacity, Set Retention/Limits, Run Batch Forecasts"},
        {"Role": "Tenant Admin", "Visibility": "Own Organization Tenants Only", "Key Actions": "View Trajectories, Simulate Retention, Submit Capacity Requests"},
        {"Role": "External Partner", "Visibility": "Delegated Partner Tenants Only", "Key Actions": "Read-Only Inspection of Storage & Forecast Trends"},
        {"Role": "Compliance Auditor", "Visibility": "Audit Logs & Backtest Benchmarks Only", "Key Actions": "Verify Compliance, Inspect Model Error & Integrity"}
    ])
    st.table(matrix_df)
