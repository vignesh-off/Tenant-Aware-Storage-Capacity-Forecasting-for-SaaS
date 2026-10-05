#!/usr/bin/env python3
"""
Tenant-Aware Storage & Capacity Forecaster
Synthetic Data Generator

Generates 100% synthetic, anonymized time-series and metadata across 50 tenants,
5 organizations, 4-12 tables per tenant, and 0-3 indexes per table across 180 days.
"""

import os
import sys
import random
from datetime import date, timedelta, datetime
from pathlib import Path
import numpy as np
import pandas as pd

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import func
from app.database import engine, Base, SessionLocal
from app import models

# Ensure reproducible generation
np.random.seed(42)
random.seed(42)

TABLE_NAMES_POOL = [
    "users", "user_sessions", "audit_logs", "events_stream",
    "transactions", "orders", "order_items", "invoices",
    "notifications", "attachments_meta", "device_telemetry", "report_cache"
]

INDEX_NAMES_POOL = [
    "ix_created_at", "ix_tenant_status", "ix_lookup_uuid", "ix_composite_date_id"
]

ORGS = [f"org_{i}" for i in range(1, 6)]
TIERS = ["starter", "standard", "professional", "enterprise"]
TIER_DISTRIBUTION = [0.20, 0.30, 0.30, 0.20]  # 10 starter, 15 standard, 15 pro, 10 enterprise


def generate_synthetic_dataset():
    print("=" * 80)
    print("STARTING SYNTHETIC DATA GENERATION FOR SAAS CAPACITY FORECASTER")
    print("=" * 80)

    # 1. Reset and Recreate Schema
    print("Initializing SQLite database tables...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    # 2. Generate System Users (RBAC simulation)
    print("Creating simulated RBAC users...")
    system_users = [
        models.User(user_id="usr_plat_admin", org_id="org_1", role="platform_admin", username="Platform Admin", email="admin@platform.internal"),
        models.User(user_id="usr_tenant_org1", org_id="org_1", role="tenant_admin", username="Tenant Admin Org 1", email="admin@org1.example.com"),
        models.User(user_id="usr_tenant_org2", org_id="org_2", role="tenant_admin", username="Tenant Admin Org 2", email="admin@org2.example.com"),
        models.User(user_id="usr_tenant_org3", org_id="org_3", role="tenant_admin", username="Tenant Admin Org 3", email="admin@org3.example.com"),
        models.User(user_id="usr_partner_org5", org_id="org_5", role="external_partner", username="External Partner Lead", email="partner@managed.cloud"),
        models.User(user_id="usr_auditor_org1", org_id="org_1", role="auditor", username="Compliance Auditor", email="auditor@compliance.org"),
    ]
    db.add_all(system_users)
    db.commit()

    # 3. Generate 50 Tenants across 5 Organizations
    print("Generating 50 tenants across 5 organizations...")
    tenants = []
    tenant_ids = [f"tenant_{i:03d}" for i in range(1, 51)]

    # Assign tiers
    tier_assignments = (
        ["starter"] * 10 +
        ["standard"] * 15 +
        ["professional"] * 15 +
        ["enterprise"] * 10
    )
    random.shuffle(tier_assignments)

    for i, tid in enumerate(tenant_ids):
        org_id = f"org_{(i % 5) + 1}"
        tier = tier_assignments[i]

        # Quota allocation based on tier
        if tier == "starter":
            storage_limit = random.choice([25.0, 40.0, 50.0])
        elif tier == "standard":
            storage_limit = random.choice([100.0, 150.0, 200.0])
        elif tier == "professional":
            storage_limit = random.choice([350.0, 500.0, 600.0])
        else:  # enterprise
            storage_limit = random.choice([1000.0, 1500.0, 2000.0])

        tenant = models.Tenant(
            tenant_id=tid,
            org_id=org_id,
            tier=tier,
            storage_limit_gb=storage_limit,
            created_at=datetime.utcnow() - timedelta(days=200),
            is_active=True
        )
        tenants.append(tenant)

    db.add_all(tenants)
    db.commit()

    # 4. Generate Tables, Indexes, and Retention Rules per Tenant
    print("Generating schema tables, indexes, and retention rules...")
    tables_list = []
    indexes_list = []
    retention_rules_list = []

    for t in tenants:
        tid = t.tenant_id
        # 4 to 12 tables per tenant
        num_tables = random.randint(4, 12)
        selected_table_names = random.sample(TABLE_NAMES_POOL, num_tables)

        for tbl_name in selected_table_names:
            table_id = f"{tid}_{tbl_name}"
            row_bytes = random.choice([128, 256, 384, 512, 1024])

            # Retention rules for volatile/audit tables
            retention_days = None
            if tbl_name in ["audit_logs", "user_sessions", "device_telemetry"]:
                retention_days = random.choice([30, 60, 90])
            elif tbl_name == "report_cache":
                retention_days = 14

            tbl = models.TableMetadata(
                table_id=table_id,
                tenant_id=tid,
                schema_name=f"schema_{tid}",
                table_name=tbl_name,
                row_bytes=row_bytes,
                retention_days=retention_days
            )
            tables_list.append(tbl)

            # Active retention rule entity if applicable
            if retention_days:
                retention_rules_list.append(models.RetentionRule(
                    rule_id=f"rule_{table_id}",
                    tenant_id=tid,
                    table_id=table_id,
                    retention_days=retention_days,
                    action="DELETE",
                    is_active=True
                ))

            # 0 to 3 indexes per table
            num_indexes = random.randint(0, 3)
            if num_indexes > 0:
                selected_idx_names = random.sample(INDEX_NAMES_POOL, num_indexes)
                for idx_name in selected_idx_names:
                    idx_id = f"{table_id}_{idx_name}"
                    # Normal index factor between 0.15 and 0.35
                    idx_factor = round(random.uniform(0.15, 0.35), 3)

                    # Edge Case 4 Injection: Simulate index bloat on tenant_012 and tenant_025
                    if tid in ["tenant_012", "tenant_025"] and idx_name == "ix_composite_date_id":
                        idx_factor = 0.85  # Severe bloat

                    indexes_list.append(models.IndexMetadata(
                        index_id=idx_id,
                        table_id=table_id,
                        index_name=idx_name,
                        index_factor=idx_factor
                    ))

    db.add_all(tables_list)
    db.add_all(indexes_list)
    db.add_all(retention_rules_list)
    db.commit()

    # 5. Generate 180 Days of Daily Time-Series Metrics
    print("Generating 180 days of daily metrics per table (with seasonality, spikes, and retention)...")
    end_date = date.today()
    start_date = end_date - timedelta(days=179)
    date_range = [start_date + timedelta(days=d) for d in range(180)]

    daily_metrics_batch = []
    total_metrics_count = 0

    # Map tables by tenant
    table_index_factors = {}
    for idx in indexes_list:
        table_index_factors[idx.table_id] = table_index_factors.get(idx.table_id, 0.0) + idx.index_factor

    for t in tenants:
        tid = t.tenant_id
        t_tier = t.tier

        # Tier-based baseline volume multiplier
        if t_tier == "starter":
            tier_multiplier = 0.5
        elif t_tier == "standard":
            tier_multiplier = 1.5
        elif t_tier == "professional":
            tier_multiplier = 4.0
        else:
            tier_multiplier = 12.0

        # Special test edge cases
        is_new_tenant = (tid in ["tenant_048", "tenant_049", "tenant_050"])
        is_zero_growth = (tid in ["tenant_030", "tenant_031"])
        has_spike = (tid in ["tenant_005", "tenant_011", "tenant_014", "tenant_018", "tenant_022", "tenant_027", "tenant_035", "tenant_042"])

        tenant_tables = [tbl for tbl in tables_list if tbl.tenant_id == tid]

        for tbl in tenant_tables:
            # Baseline daily new rows
            base_new_rows = int(random.uniform(500, 3000) * tier_multiplier)
            if is_zero_growth:
                base_new_rows = 5  # negligible drift

            current_rows = int(random.uniform(10000, 50000) * tier_multiplier)
            retention_days = tbl.retention_days
            row_bytes = tbl.row_bytes
            total_idx_factor = table_index_factors.get(tbl.table_id, 0.20)

            # Circular queue for tracking rows for retention purging
            history_new_rows = []

            # If new tenant, only generate last 5 days
            active_dates = date_range[-5:] if is_new_tenant else date_range

            for day_idx, d in enumerate(active_dates):
                # Day of week seasonality: Mon-Fri = higher, Sat-Sun = lower
                dow = d.weekday()
                seasonality = 1.30 if dow in [1, 2, 3] else (0.60 if dow in [5, 6] else 1.0)
                noise = np.random.normal(1.0, 0.08)

                daily_added_rows = int(base_new_rows * seasonality * noise)

                # Edge Case 2 Injection: Backfill spike on day 121 (in 14d baseline window) and day 165
                if has_spike and (day_idx in [121, 165]) and tbl.table_name in ["events_stream", "audit_logs"]:
                    spike_rows = int(4_000_000 * tier_multiplier / max(1, len(tenant_tables)))
                    daily_added_rows += spike_rows  # Multi-gigabyte bulk migration spike

                # Retention purge simulation
                purged_rows = 0
                if retention_days and len(history_new_rows) >= retention_days:
                    purged_rows = history_new_rows[len(history_new_rows) - retention_days]
                history_new_rows.append(daily_added_rows)

                current_rows = max(1000, current_rows + daily_added_rows - purged_rows)

                # Calculate storage in GB
                table_size_gb = (current_rows * row_bytes) / (1024 ** 3)

                # Edge Case 4: Index bloat growth acceleration over time
                if "bloat" in str(tbl.table_id):
                    total_idx_factor += 0.002
                index_size_gb = table_size_gb * total_idx_factor

                # Writes and queries
                writes = int(daily_added_rows * random.uniform(1.1, 1.8))
                queries = int(writes * random.uniform(4.0, 15.0))

                daily_metrics_batch.append(models.DailyMetric(
                    metric_date=d,
                    tenant_id=tid,
                    table_id=tbl.table_id,
                    rows=current_rows,
                    table_size_gb=round(table_size_gb, 4),
                    index_size_gb=round(index_size_gb, 4),
                    writes=writes,
                    queries=queries
                ))

                total_metrics_count += 1

                # Flush in chunks to optimize memory and speed
                if len(daily_metrics_batch) >= 5000:
                    db.bulk_save_objects(daily_metrics_batch)
                    db.commit()
                    daily_metrics_batch = []

    if daily_metrics_batch:
        db.bulk_save_objects(daily_metrics_batch)
        db.commit()

    # 6. Generate Realistic Storage Limits Calibrated to Exhaustion Horizons
    print("Seeding storage limits table calibrated to realistic exhaustion horizons...")
    limits_list = []

    # Map total storage at split date (day 126) for each tenant
    split_date_target = date_range[126]
    early_date_target = date_range[96]
    split_storage_map = {}
    growth_map = {}

    for t in tenants:
        tid = t.tenant_id
        split_size = db.query(
            func.sum(models.DailyMetric.table_size_gb + models.DailyMetric.index_size_gb)
        ).filter(models.DailyMetric.tenant_id == tid, models.DailyMetric.metric_date == split_date_target).scalar() or 20.0

        early_size = db.query(
            func.sum(models.DailyMetric.table_size_gb + models.DailyMetric.index_size_gb)
        ).filter(models.DailyMetric.tenant_id == tid, models.DailyMetric.metric_date == early_date_target).scalar() or 18.0

        growth_30d = (split_size - early_size) / 30.0 if split_size > early_size else 0.05
        split_storage_map[tid] = float(split_size)
        growth_map[tid] = max(0.01, float(growth_30d))

    # Distribute target days to exhaustion:
    # ~12 tenants exhaust in 10-50 days (critical/warning during test window)
    # ~15 tenants exhaust in 55-90 days (warning/mid-term)
    # ~13 tenants exhaust in 95-180 days (long-term)
    # ~10 tenants have high headroom / zero growth (>250 days)
    target_horizons = (
        [random.randint(12, 48) for _ in range(12)] +
        [random.randint(55, 90) for _ in range(15)] +
        [random.randint(95, 180) for _ in range(13)] +
        [random.randint(250, 450) for _ in range(10)]
    )
    random.shuffle(target_horizons)

    for i, t in enumerate(tenants):
        tid = t.tenant_id
        is_zero_growth = (tid in ["tenant_030", "tenant_031"])
        split_size = split_storage_map.get(tid, 20.0)
        daily_growth = growth_map.get(tid, 0.2)

        if is_zero_growth:
            calibrated_limit = round(split_size * 2.5 + 20.0, 1)
        else:
            target_days = target_horizons[i]
            calibrated_limit = max(5.0, round(split_size + (daily_growth * target_days), 1))

        t.storage_limit_gb = calibrated_limit
        limits_list.append(models.StorageLimit(
            limit_id=f"lim_{tid}",
            tenant_id=tid,
            table_id=None,
            limit_gb=calibrated_limit
        ))

    db.add_all(limits_list)
    db.commit()

    # 7. Print Summary Statistics and Privacy Notice
    print("=" * 80)
    print("SYNTHETIC DATA GENERATION COMPLETE")
    print("=" * 80)
    print(f"Total Tenants:        {len(tenants)} (Starter: 10, Standard: 15, Pro: 15, Enterprise: 10)")
    print(f"Total Organizations:  5 (org_1 to org_5)")
    print(f"Total Tables:         {len(tables_list)}")
    print(f"Total Indexes:        {len(indexes_list)}")
    print(f"Total Retention Rules:{len(retention_rules_list)}")
    print(f"Total Metric Records: {total_metrics_count}")
    print(f"Timeline Span:        {start_date.isoformat()} to {end_date.isoformat()} (180 days)")
    print(f"Database File:        {BASE_DIR / 'data' / 'saas_capacity.db'}")
    print("-" * 80)
    print("PRIVACY ASSUMPTIONS & COMPLIANCE NOTICE:")
    print(" * 100% SYNTHETIC DATA GENERATION: No production or customer data was accessed or used.")
    print(" * ZERO PII: All identifiers (tenant_001, usr_plat_admin) and schema names are synthetic.")
    print(" * ACCESS CONTROL: Scoped to RBAC boundaries and tenant-isolated schemas.")
    print("=" * 80)

    db.close()


if __name__ == "__main__":
    generate_synthetic_dataset()
