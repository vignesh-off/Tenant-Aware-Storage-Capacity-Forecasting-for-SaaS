from datetime import date, datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# ---------------------------------------------------------
# Tenant & Metadata Schemas
# ---------------------------------------------------------
class IndexSchema(BaseModel):
    index_id: str
    table_id: str
    index_name: str
    index_factor: float

    class Config:
        from_attributes = True


class TableSchema(BaseModel):
    table_id: str
    tenant_id: str
    schema_name: str
    table_name: str
    row_bytes: int
    retention_days: Optional[int] = None
    indexes: List[IndexSchema] = []

    class Config:
        from_attributes = True


class TenantBase(BaseModel):
    tenant_id: str
    org_id: str
    tier: str
    storage_limit_gb: float
    is_active: bool = True


class TenantResponse(TenantBase):
    created_at: datetime
    current_storage_gb: Optional[float] = 0.0
    days_to_exhaustion: Optional[int] = None
    urgency_level: Optional[str] = "LOW"  # CRITICAL (<=14d), WARNING (<=30d), STABLE (>30d)

    class Config:
        from_attributes = True


class TenantDetailResponse(TenantResponse):
    tables: List[TableSchema] = []


# ---------------------------------------------------------
# Daily Metrics Schemas
# ---------------------------------------------------------
class DailyMetricSchema(BaseModel):
    metric_date: date
    tenant_id: str
    table_id: str
    rows: int
    table_size_gb: float
    index_size_gb: float
    total_size_gb: float
    writes: int
    queries: int

    class Config:
        from_attributes = True


# ---------------------------------------------------------
# Retention Rules & Storage Limits
# ---------------------------------------------------------
class RetentionRuleCreate(BaseModel):
    tenant_id: str
    table_id: Optional[str] = None
    retention_days: int = Field(gt=0, description="Retention horizon in days")
    action: str = Field(default="DELETE", description="Action: DELETE, ARCHIVE, COMPRESS")


class RetentionRuleResponse(BaseModel):
    rule_id: str
    tenant_id: str
    table_id: Optional[str]
    retention_days: int
    action: str
    created_at: datetime
    is_active: bool

    class Config:
        from_attributes = True


class StorageLimitCreate(BaseModel):
    tenant_id: str
    table_id: Optional[str] = None
    limit_gb: float = Field(gt=0, description="New quota limit in GB")


class StorageLimitResponse(BaseModel):
    limit_id: str
    tenant_id: str
    table_id: Optional[str]
    limit_gb: float
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------
# Capacity Requests & Approval
# ---------------------------------------------------------
class CapacityRequestCreate(BaseModel):
    tenant_id: str
    requested_gb: float = Field(gt=0)
    reason: Optional[str] = "Anticipated organic growth"


class CapacityRequestDecision(BaseModel):
    status: str = Field(description="APPROVED or REJECTED")
    decision_reason: Optional[str] = None


class CapacityRequestResponse(BaseModel):
    request_id: str
    tenant_id: str
    requested_gb: float
    current_limit_gb: float
    status: str
    requested_by: str
    approved_by: Optional[str]
    reason: Optional[str]
    created_at: datetime
    decided_at: Optional[datetime]

    class Config:
        from_attributes = True


# ---------------------------------------------------------
# Forecasts
# ---------------------------------------------------------
class ForecastPoint(BaseModel):
    forecast_date: date
    p10_size_gb: float
    p50_size_gb: float
    p90_size_gb: float


class ForecastResponse(BaseModel):
    forecast_id: str
    forecast_date: date
    tenant_id: str
    table_id: Optional[str]
    model_name: str
    current_size_gb: float
    projected_size_30d_gb: Optional[float]
    projected_size_90d_gb: Optional[float]
    p50_exhaustion_date: Optional[date]
    p10_exhaustion_date: Optional[date]
    p90_exhaustion_date: Optional[date]
    days_to_p50_exhaustion: Optional[int]
    confidence: float
    warning_flag: Optional[str]
    forecast_curve: Optional[List[ForecastPoint]] = None

    class Config:
        from_attributes = True


class TableForecastItem(BaseModel):
    table_id: str
    table_name: str
    current_size_gb: float
    current_index_gb: float
    daily_growth_gb: float
    projected_30d_gb: float
    p50_exhaustion_date: Optional[date]
    confidence: float
    warning_flag: Optional[str]


class TenantForecastDetailResponse(BaseModel):
    tenant_id: str
    org_id: str
    tier: str
    storage_limit_gb: float
    current_total_gb: float
    baseline_exhaustion_date: Optional[date]
    proposed_exhaustion_date: Optional[date]
    p10_exhaustion_date: Optional[date]
    p90_exhaustion_date: Optional[date]
    confidence: float
    warning_flags: List[str]
    table_forecasts: List[TableForecastItem]
    trajectory: List[Dict[str, Any]]


# ---------------------------------------------------------
# Backtest Results
# ---------------------------------------------------------
class BacktestMetricComparison(BaseModel):
    metric: str
    baseline: Any
    target: Any
    measured: Any


class BacktestSummaryResponse(BaseModel):
    run_id: str
    evaluated_at: datetime
    baseline_mae: float
    proposed_mae: float
    baseline_median_ae: float
    proposed_median_ae: float
    baseline_within_14d_pct: float
    proposed_within_14d_pct: float
    proposed_coverage_pct: float
    baseline_false_urgent_rate: float
    proposed_false_urgent_rate: float
    comparison_table: List[BacktestMetricComparison]


# ---------------------------------------------------------
# Platform Admin Risk Heatmap
# ---------------------------------------------------------
class TenantRiskItem(BaseModel):
    tenant_id: str
    org_id: str
    tier: str
    storage_limit_gb: float
    current_size_gb: float
    pct_used: float
    days_to_exhaustion: Optional[int]
    urgency_level: str
    warning_flags: List[str]
    growth_rate_30d_pct: float


class HeatmapResponse(BaseModel):
    total_tenants: int
    critical_count: int
    warning_count: int
    healthy_count: int
    tenants: List[TenantRiskItem]


# ---------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------
class AuditLogResponse(BaseModel):
    log_id: str
    timestamp: datetime
    actor: str
    role: str
    action: str
    entity: str
    details: Optional[str]

    class Config:
        from_attributes = True
