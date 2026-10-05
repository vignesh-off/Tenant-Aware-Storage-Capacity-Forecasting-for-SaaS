from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app import models, schemas
from app.auth import get_current_actor, require_role, Actor, DELEGATED_PARTNER_TENANTS

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


@router.get("", response_model=List[schemas.TenantResponse])
def list_tenants(
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin", "tenant_admin", "external_partner"])),
    search: Optional[str] = None
):
    """
    List all accessible tenants based on actor role and organization:
    - platform_admin: all tenants
    - tenant_admin: tenants belonging to own organization
    - external_partner: delegated tenants only
    """
    query = db.query(models.Tenant).filter(models.Tenant.is_active.is_(True))

    if actor.role == "tenant_admin":
        query = query.filter(models.Tenant.org_id == actor.org_id)
    elif actor.role == "external_partner":
        query = query.filter(models.Tenant.tenant_id.in_(DELEGATED_PARTNER_TENANTS))

    if search:
        query = query.filter(models.Tenant.tenant_id.ilike(f"%{search}%"))

    tenants = query.order_by(models.Tenant.tenant_id.asc()).all()

    # Pre-fetch latest storage usage and forecast per tenant
    results = []
    for t in tenants:
        latest_storage = db.query(
            func.sum(models.DailyMetric.table_size_gb + models.DailyMetric.index_size_gb)
        ).filter(models.DailyMetric.tenant_id == t.tenant_id).scalar() or 0.0

        latest_fc = db.query(models.Forecast).filter(
            models.Forecast.tenant_id == t.tenant_id
        ).order_by(models.Forecast.forecast_date.desc()).first()

        days_exhaust = None
        urgency = "LOW"
        if latest_fc and latest_fc.p50_exhaustion_date:
            from datetime import date
            delta_days = (latest_fc.p50_exhaustion_date - date.today()).days
            days_exhaust = max(0, delta_days)
            if days_exhaust <= 14:
                urgency = "CRITICAL"
            elif days_exhaust <= 30:
                urgency = "WARNING"
            else:
                urgency = "STABLE"

        results.append(schemas.TenantResponse(
            tenant_id=t.tenant_id,
            org_id=t.org_id,
            tier=t.tier,
            storage_limit_gb=t.storage_limit_gb,
            is_active=t.is_active,
            created_at=t.created_at,
            current_storage_gb=round(latest_storage, 2),
            days_to_exhaustion=days_exhaust,
            urgency_level=urgency
        ))

    return results


@router.get("/{tenant_id}", response_model=schemas.TenantDetailResponse)
def get_tenant_detail(
    tenant_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin", "tenant_admin", "external_partner"]))
):
    """
    Get detailed tenant metadata and table schemas.
    """
    tenant = db.query(models.Tenant).filter(models.Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if not actor.can_access_tenant(tenant):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: Role '{actor.role}' cannot access tenant '{tenant_id}'."
        )

    # Fetch tables and indexes
    tables = db.query(models.TableMetadata).filter(models.TableMetadata.tenant_id == tenant_id).all()
    
    table_schemas = []
    for tbl in tables:
        indexes = db.query(models.IndexMetadata).filter(models.IndexMetadata.table_id == tbl.table_id).all()
        idx_schemas = [
            schemas.IndexSchema(
                index_id=i.index_id,
                table_id=i.table_id,
                index_name=i.index_name,
                index_factor=i.index_factor
            )
            for i in indexes
        ]
        table_schemas.append(schemas.TableSchema(
            table_id=tbl.table_id,
            tenant_id=tbl.tenant_id,
            schema_name=tbl.schema_name,
            table_name=tbl.table_name,
            row_bytes=tbl.row_bytes,
            retention_days=tbl.retention_days,
            indexes=idx_schemas
        ))

    latest_storage = db.query(
        func.sum(models.DailyMetric.table_size_gb + models.DailyMetric.index_size_gb)
    ).filter(models.DailyMetric.tenant_id == tenant.tenant_id).scalar() or 0.0

    return schemas.TenantDetailResponse(
        tenant_id=tenant.tenant_id,
        org_id=tenant.org_id,
        tier=tenant.tier,
        storage_limit_gb=tenant.storage_limit_gb,
        is_active=tenant.is_active,
        created_at=tenant.created_at,
        current_storage_gb=round(latest_storage, 2),
        days_to_exhaustion=None,
        urgency_level="LOW",
        tables=table_schemas
    )
