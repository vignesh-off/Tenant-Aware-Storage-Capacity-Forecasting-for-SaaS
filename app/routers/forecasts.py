import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.auth import get_current_actor, require_role, Actor, log_audit
from app.forecast import (
    compute_baseline_forecast,
    compute_proposed_forecast,
    generate_and_store_all_forecasts
)

router = APIRouter(prefix="/api/forecasts", tags=["forecasts"])


@router.get("/tenant/{tenant_id}", response_model=schemas.TenantForecastDetailResponse)
def get_tenant_forecast(
    tenant_id: str,
    horizon_days: int = 180,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin", "tenant_admin", "external_partner"]))
):
    """
    Generate real-time hierarchical capacity forecast for a tenant.
    Compares Baseline linear extrapolation against Proposed Quantile Regression.
    """
    tenant = db.query(models.Tenant).filter(models.Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if not actor.can_access_tenant(tenant):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: Role '{actor.role}' cannot access tenant '{tenant_id}'."
        )

    # Compute baseline
    baseline_res = compute_baseline_forecast(tenant_id, db, horizon_days=horizon_days)

    # Compute proposed
    proposed_res = compute_proposed_forecast(tenant_id, db, horizon_days=horizon_days)

    return schemas.TenantForecastDetailResponse(
        tenant_id=tenant.tenant_id,
        org_id=tenant.org_id,
        tier=tenant.tier,
        storage_limit_gb=proposed_res["storage_limit_gb"],
        current_total_gb=proposed_res["current_total_gb"],
        baseline_exhaustion_date=baseline_res["p50_exhaustion_date"],
        proposed_exhaustion_date=proposed_res["p50_exhaustion_date"],
        p10_exhaustion_date=proposed_res["p10_exhaustion_date"],
        p90_exhaustion_date=proposed_res["p90_exhaustion_date"],
        confidence=proposed_res["confidence"],
        warning_flags=proposed_res["warning_flags"],
        table_forecasts=[
            schemas.TableForecastItem(**item) for item in proposed_res["table_forecasts"]
        ],
        trajectory=proposed_res["trajectory"]
    )


@router.post("/retention-rules", response_model=schemas.RetentionRuleResponse)
def set_retention_rule(
    payload: schemas.RetentionRuleCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin"]))
):
    """
    Create or update data retention rules.
    Restricted to platform_admin. Generates audit record.
    """
    rule_id = f"rule_{uuid.uuid4().hex[:12]}"
    rule = models.RetentionRule(
        rule_id=rule_id,
        tenant_id=payload.tenant_id,
        table_id=payload.table_id,
        retention_days=payload.retention_days,
        action=payload.action.upper(),
        is_active=True
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    log_audit(
        db=db,
        actor=actor,
        action="CREATE_RETENTION_RULE",
        entity=f"Tenant:{payload.tenant_id}",
        details=f"Set retention to {payload.retention_days} days for table {payload.table_id or 'ALL'} with action {payload.action}"
    )

    return rule


@router.post("/storage-limits", response_model=schemas.StorageLimitResponse)
def update_storage_limit(
    payload: schemas.StorageLimitCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin"]))
):
    """
    Update storage limit quota for a tenant or table.
    Restricted to platform_admin. Generates audit record.
    """
    tenant = db.query(models.Tenant).filter(models.Tenant.tenant_id == payload.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    old_limit = tenant.storage_limit_gb
    tenant.storage_limit_gb = payload.limit_gb

    limit_id = f"limit_{uuid.uuid4().hex[:12]}"
    limit_record = models.StorageLimit(
        limit_id=limit_id,
        tenant_id=payload.tenant_id,
        table_id=payload.table_id,
        limit_gb=payload.limit_gb
    )
    db.add(limit_record)
    db.commit()
    db.refresh(limit_record)

    log_audit(
        db=db,
        actor=actor,
        action="UPDATE_STORAGE_LIMIT",
        entity=f"Tenant:{payload.tenant_id}",
        details=f"Modified storage quota from {old_limit} GB to {payload.limit_gb} GB"
    )

    return limit_record


@router.post("/generate", response_model=Dict[str, Any])
def trigger_forecast_generation(
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin"]))
):
    """
    Trigger batch capacity forecasting for all active tenants.
    """
    updated_count = generate_and_store_all_forecasts(db)
    log_audit(
        db=db,
        actor=actor,
        action="BATCH_FORECAST_GENERATION",
        entity="System:AllTenants",
        details=f"Refreshed forecasts for {updated_count} tenants"
    )
    return {"status": "success", "tenants_updated": updated_count}
