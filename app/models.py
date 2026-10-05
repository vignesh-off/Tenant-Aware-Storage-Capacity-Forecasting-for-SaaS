from datetime import datetime, date
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Date,
    DateTime,
    ForeignKey,
    PrimaryKeyConstraint,
    Text,
    Boolean,
    Index as SQLIndex
)
from sqlalchemy.orm import relationship
from app.database import Base


class Tenant(Base):
    """
    SaaS Tenant entity.
    Each customer schema belongs to a tenant within a specific organization.
    """
    __tablename__ = "tenants"

    tenant_id = Column(String(64), primary_key=True, index=True)
    org_id = Column(String(64), nullable=False, index=True)
    tier = Column(String(32), nullable=False, default="standard")  # starter, standard, professional, enterprise
    storage_limit_gb = Column(Float, nullable=False, default=100.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    # Relationships
    tables = relationship("TableMetadata", back_populates="tenant", cascade="all, delete-orphan")
    retention_rules = relationship("RetentionRule", back_populates="tenant", cascade="all, delete-orphan")
    storage_limits = relationship("StorageLimit", back_populates="tenant", cascade="all, delete-orphan")
    forecasts = relationship("Forecast", back_populates="tenant", cascade="all, delete-orphan")
    capacity_requests = relationship("CapacityRequest", back_populates="tenant", cascade="all, delete-orphan")


class TableMetadata(Base):
    """
    Table metadata for tenant schemas.
    """
    __tablename__ = "tables"

    table_id = Column(String(128), primary_key=True, index=True)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    schema_name = Column(String(64), nullable=False)
    table_name = Column(String(64), nullable=False)
    row_bytes = Column(Integer, nullable=False, default=256)
    retention_days = Column(Integer, nullable=True)  # Null indicates infinite retention

    # Relationships
    tenant = relationship("Tenant", back_populates="tables")
    indexes = relationship("IndexMetadata", back_populates="table", cascade="all, delete-orphan")
    daily_metrics = relationship("DailyMetric", back_populates="table", cascade="all, delete-orphan")
    forecasts = relationship("Forecast", back_populates="table", cascade="all, delete-orphan")


class IndexMetadata(Base):
    """
    Index metadata on tenant tables.
    """
    __tablename__ = "indexes"

    index_id = Column(String(128), primary_key=True, index=True)
    table_id = Column(String(128), ForeignKey("tables.table_id", ondelete="CASCADE"), nullable=False, index=True)
    index_name = Column(String(64), nullable=False)
    index_factor = Column(Float, nullable=False, default=0.25)  # size ratio relative to raw table data

    # Relationships
    table = relationship("TableMetadata", back_populates="indexes")


class DailyMetric(Base):
    """
    Daily aggregated storage and operational metrics per table.
    """
    __tablename__ = "daily_metrics"

    metric_date = Column(Date, nullable=False, index=True)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    table_id = Column(String(128), ForeignKey("tables.table_id", ondelete="CASCADE"), nullable=False, index=True)
    rows = Column(Integer, nullable=False, default=0)
    table_size_gb = Column(Float, nullable=False, default=0.0)
    index_size_gb = Column(Float, nullable=False, default=0.0)
    writes = Column(Integer, nullable=False, default=0)
    queries = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        PrimaryKeyConstraint("metric_date", "table_id", name="pk_daily_metrics"),
        SQLIndex("ix_daily_metrics_tenant_date", "tenant_id", "metric_date"),
    )

    # Relationships
    table = relationship("TableMetadata", back_populates="daily_metrics")


class RetentionRule(Base):
    """
    Configured data retention rules per tenant or specific table.
    """
    __tablename__ = "retention_rules"

    rule_id = Column(String(64), primary_key=True, index=True)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    table_id = Column(String(128), ForeignKey("tables.table_id", ondelete="CASCADE"), nullable=True, index=True)
    retention_days = Column(Integer, nullable=False)
    action = Column(String(32), nullable=False, default="DELETE")  # DELETE, ARCHIVE, COMPRESS
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    tenant = relationship("Tenant", back_populates="retention_rules")


class StorageLimit(Base):
    """
    Explicit storage quotas assigned per tenant and optionally per table.
    """
    __tablename__ = "storage_limits"

    limit_id = Column(String(64), primary_key=True, index=True)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    table_id = Column(String(128), ForeignKey("tables.table_id", ondelete="CASCADE"), nullable=True, index=True)
    limit_gb = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="storage_limits")


class User(Base):
    """
    User accounts and role assignment.
    """
    __tablename__ = "users"

    user_id = Column(String(64), primary_key=True, index=True)
    org_id = Column(String(64), nullable=False, index=True)
    role = Column(String(32), nullable=False)  # platform_admin, tenant_admin, external_partner, auditor
    username = Column(String(64), nullable=False)
    email = Column(String(128), nullable=True)


class Forecast(Base):
    """
    Generated capacity and exhaustion date forecasts.
    """
    __tablename__ = "forecasts"

    forecast_id = Column(String(64), primary_key=True, index=True)
    forecast_date = Column(Date, nullable=False, default=date.today, index=True)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    table_id = Column(String(128), ForeignKey("tables.table_id", ondelete="CASCADE"), nullable=True, index=True)
    model_name = Column(String(32), nullable=False, default="proposed_quantile")  # baseline_linear, proposed_quantile
    current_size_gb = Column(Float, nullable=False)
    projected_size_30d_gb = Column(Float, nullable=True)
    projected_size_90d_gb = Column(Float, nullable=True)
    p50_exhaustion_date = Column(Date, nullable=True)
    p10_exhaustion_date = Column(Date, nullable=True)  # Aggressive growth / earliest exhaustion
    p90_exhaustion_date = Column(Date, nullable=True)  # Conservative growth / latest exhaustion
    confidence = Column(Float, nullable=False, default=0.85)
    warning_flag = Column(String(64), nullable=True)  # e.g., 'spike_detected', 'new_tenant', 'index_bloat'
    details_json = Column(Text, nullable=True)

    tenant = relationship("Tenant", back_populates="forecasts")
    table = relationship("TableMetadata", back_populates="forecasts")


class BacktestResult(Base):
    """
    Backtesting benchmark metrics comparing baseline and proposed algorithms.
    """
    __tablename__ = "backtest_results"

    run_id = Column(String(64), primary_key=True, index=True)
    evaluated_at = Column(DateTime, default=datetime.utcnow)
    model_name = Column(String(32), nullable=False)
    mae_days = Column(Float, nullable=False)
    median_ae_days = Column(Float, nullable=False)
    within_7_days_pct = Column(Float, nullable=True)
    within_14_days_pct = Column(Float, nullable=False)
    within_30_days_pct = Column(Float, nullable=True)
    coverage_pct = Column(Float, nullable=False)  # P10-P90 coverage
    false_urgent_rate = Column(Float, nullable=False)  # False alarms for <=14 days exhaustion
    false_urgent_pct = Column(Float, nullable=True)  # Alias for compatibility with queries


class AuditLog(Base):
    """
    Immutable audit log for security compliance and tracking capacity operations.
    """
    __tablename__ = "audit_logs"

    log_id = Column(String(64), primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    actor = Column(String(64), nullable=False)
    role = Column(String(32), nullable=False)
    action = Column(String(64), nullable=False)
    entity = Column(String(64), nullable=False)
    details = Column(Text, nullable=True)


class CapacityRequest(Base):
    """
    Proactive capacity addition requests workflow and approval queue.
    """
    __tablename__ = "capacity_requests"

    request_id = Column(String(64), primary_key=True, index=True)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    requested_gb = Column(Float, nullable=False)
    current_limit_gb = Column(Float, nullable=False)
    status = Column(String(32), nullable=False, default="PENDING")  # PENDING, APPROVED, REJECTED
    requested_by = Column(String(64), nullable=False)
    approved_by = Column(String(64), nullable=True)
    reason = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)

    tenant = relationship("Tenant", back_populates="capacity_requests")
