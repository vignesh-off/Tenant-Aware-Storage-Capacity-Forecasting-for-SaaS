import uuid
from datetime import datetime, date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app import models, schemas
from app.auth import get_current_actor, require_role, Actor, log_audit
from app.backtest import run_comprehensive_backtest

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/heatmap", response_model=schemas.HeatmapResponse)
def get_global_heatmap(
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin"]))
):
    """
    Platform Admin Risk Heatmap.
    Aggregates storage utilization and days to exhaustion across all tenants.
    """
    tenants = db.query(models.Tenant).filter(models.Tenant.is_active.is_(True)).all()
    
    tenant_items = []
    critical_count = 0
    warning_count = 0
    healthy_count = 0

    today = date.today()

    for t in tenants:
        current_size = db.query(
            func.sum(models.DailyMetric.table_size_gb + models.DailyMetric.index_size_gb)
        ).filter(models.DailyMetric.tenant_id == t.tenant_id).scalar() or 0.0

        limit_gb = float(t.storage_limit_gb)
        pct_used = min(100.0, round((current_size / max(1.0, limit_gb)) * 100, 1))

        # Fetch latest forecast
        fc = db.query(models.Forecast).filter(models.Forecast.tenant_id == t.tenant_id).order_by(
            models.Forecast.forecast_date.desc()
        ).first()

        days_exhaust = None
        urgency = "STABLE"
        flags = []

        if fc:
            if fc.p50_exhaustion_date:
                days_exhaust = max(0, (fc.p50_exhaustion_date - today).days)
                if days_exhaust <= 14:
                    urgency = "CRITICAL"
                elif days_exhaust <= 30:
                    urgency = "WARNING"
                else:
                    urgency = "STABLE"
            if fc.warning_flag:
                flags = [f.strip() for f in fc.warning_flag.split(",") if f.strip()]

        if pct_used >= 90.0 and urgency != "CRITICAL":
            urgency = "WARNING"

        if urgency == "CRITICAL":
            critical_count += 1
        elif urgency == "WARNING":
            warning_count += 1
        else:
            healthy_count += 1

        # Calculate 30d growth
        growth_30d = 5.0  # fallback
        metrics_span = db.query(models.DailyMetric.metric_date, func.sum(models.DailyMetric.table_size_gb + models.DailyMetric.index_size_gb)).filter(
            models.DailyMetric.tenant_id == t.tenant_id
        ).group_by(models.DailyMetric.metric_date).order_by(models.DailyMetric.metric_date.desc()).limit(31).all()

        if len(metrics_span) >= 2 and metrics_span[-1][1] > 0:
            growth_30d = round(((metrics_span[0][1] - metrics_span[-1][1]) / metrics_span[-1][1]) * 100, 1)

        tenant_items.append(schemas.TenantRiskItem(
            tenant_id=t.tenant_id,
            org_id=t.org_id,
            tier=t.tier,
            storage_limit_gb=limit_gb,
            current_size_gb=round(current_size, 2),
            pct_used=pct_used,
            days_to_exhaustion=days_exhaust,
            urgency_level=urgency,
            warning_flags=flags,
            growth_rate_30d_pct=growth_30d
        ))

    # Sort critical first, then warning, then lowest days to exhaustion
    urgency_weight = {"CRITICAL": 0, "WARNING": 1, "STABLE": 2}
    tenant_items.sort(key=lambda x: (urgency_weight.get(x.urgency_level, 3), x.days_to_exhaustion if x.days_to_exhaustion is not None else 999))

    return schemas.HeatmapResponse(
        total_tenants=len(tenants),
        critical_count=critical_count,
        warning_count=warning_count,
        healthy_count=healthy_count,
        tenants=tenant_items
    )


@router.get("/approvals", response_model=List[schemas.CapacityRequestResponse])
def list_capacity_requests(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin", "tenant_admin"]))
):
    """
    List capacity requests.
    Platform admin sees all; tenant admin sees their own requests.
    """
    query = db.query(models.CapacityRequest)
    if actor.role == "tenant_admin":
        # Get tenants in actor's org
        org_tenants = db.query(models.Tenant.tenant_id).filter(models.Tenant.org_id == actor.org_id).all()
        org_tenant_ids = [t[0] for t in org_tenants]
        query = query.filter(models.CapacityRequest.tenant_id.in_(org_tenant_ids))

    if status_filter:
        query = query.filter(models.CapacityRequest.status == status_filter.upper())

    return query.order_by(models.CapacityRequest.created_at.desc()).all()


@router.post("/approvals", response_model=schemas.CapacityRequestResponse)
def submit_capacity_request(
    payload: schemas.CapacityRequestCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin", "tenant_admin"]))
):
    """
    Submit a proactive capacity expansion request.
    """
    tenant = db.query(models.Tenant).filter(models.Tenant.tenant_id == payload.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if not actor.can_access_tenant(tenant):
        raise HTTPException(status_code=403, detail="Cannot request capacity for foreign tenant")

    request_id = f"cap_{uuid.uuid4().hex[:12]}"
    req = models.CapacityRequest(
        request_id=request_id,
        tenant_id=payload.tenant_id,
        requested_gb=payload.requested_gb,
        current_limit_gb=tenant.storage_limit_gb,
        status="PENDING",
        requested_by=actor.user_id,
        reason=payload.reason
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    log_audit(
        db=db,
        actor=actor,
        action="REQUEST_CAPACITY",
        entity=f"Tenant:{payload.tenant_id}",
        details=f"Requested +{payload.requested_gb} GB capacity (current limit: {tenant.storage_limit_gb} GB)"
    )

    return req


@router.post("/approvals/{request_id}/decide", response_model=schemas.CapacityRequestResponse)
def decide_capacity_request(
    request_id: str,
    decision: schemas.CapacityRequestDecision,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin"]))
):
    """
    Approve or Reject capacity request (Platform Admin only).
    If approved, updates the tenant storage quota immediately.
    """
    req = db.query(models.CapacityRequest).filter(models.CapacityRequest.request_id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Capacity request not found")

    if req.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"Request already finalized as {req.status}")

    status_choice = decision.status.upper()
    if status_choice not in ["APPROVED", "REJECTED"]:
        raise HTTPException(status_code=400, detail="Decision must be APPROVED or REJECTED")

    req.status = status_choice
    req.approved_by = actor.user_id
    req.decided_at = datetime.utcnow()

    if status_choice == "APPROVED":
        tenant = db.query(models.Tenant).filter(models.Tenant.tenant_id == req.tenant_id).first()
        if tenant:
            old_limit = tenant.storage_limit_gb
            tenant.storage_limit_gb = old_limit + req.requested_gb
            log_audit(
                db=db,
                actor=actor,
                action="APPROVE_CAPACITY",
                entity=f"Tenant:{tenant.tenant_id}",
                details=f"Approved capacity request {request_id}. Limit increased from {old_limit} GB to {tenant.storage_limit_gb} GB."
            )
    else:
        log_audit(
            db=db,
            actor=actor,
            action="REJECT_CAPACITY",
            entity=f"Tenant:{req.tenant_id}",
            details=f"Rejected capacity request {request_id}. Reason: {decision.decision_reason or 'No reason provided'}"
        )

    db.commit()
    db.refresh(req)
    return req


@router.get("/backtest/summary", response_model=schemas.BacktestSummaryResponse)
def get_backtest_summary(
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin", "auditor"]))
):
    """
    Get latest benchmark comparison results.
    """
    latest_baseline = db.query(models.BacktestResult).filter(
        models.BacktestResult.model_name.ilike("%baseline%")
    ).order_by(models.BacktestResult.evaluated_at.desc()).first()

    latest_proposed = db.query(models.BacktestResult).filter(
        models.BacktestResult.model_name.ilike("%proposed%")
    ).order_by(models.BacktestResult.evaluated_at.desc()).first()

    if not latest_baseline or not latest_proposed:
        # Run live backtest if not run yet
        res = run_comprehensive_backtest(db)
        return schemas.BacktestSummaryResponse(
            run_id=res["run_id"],
            evaluated_at=datetime.utcnow(),
            baseline_mae=res["baseline_mae"],
            proposed_mae=res["proposed_mae"],
            baseline_median_ae=res["baseline_median_ae"],
            proposed_median_ae=res["proposed_median_ae"],
            baseline_within_14d_pct=res["baseline_within_14d_pct"],
            proposed_within_14d_pct=res["proposed_within_14d_pct"],
            proposed_coverage_pct=res["proposed_coverage_pct"],
            baseline_false_urgent_rate=res["baseline_false_urgent_rate"],
            proposed_false_urgent_rate=res["proposed_false_urgent_rate"],
            comparison_table=[schemas.BacktestMetricComparison(**item) for item in res["comparison_table"]]
        )

    comparison_table = [
        {"metric": "MAE exhaustion date (days)", "baseline": f"{latest_baseline.mae_days}d", "target": "<=21d", "measured": f"{latest_proposed.mae_days}d"},
        {"metric": "Median AE (days)", "baseline": f"{latest_baseline.median_ae_days}d", "target": "<=14d", "measured": f"{latest_proposed.median_ae_days}d"},
        {"metric": "% within +/-7 days", "baseline": f"{latest_baseline.within_7_days_pct}%", "target": ">=60%", "measured": f"{latest_proposed.within_7_days_pct}%"},
        {"metric": "% within +/-14 days", "baseline": f"{latest_baseline.within_14_days_pct}%", "target": ">=80%", "measured": f"{latest_proposed.within_14_days_pct}%"},
        {"metric": "% within +/-30 days", "baseline": f"{latest_baseline.within_30_days_pct}%", "target": ">=90%", "measured": f"{latest_proposed.within_30_days_pct}%"},
        {"metric": "P10-P90 coverage", "baseline": "N/A", "target": "80%", "measured": f"{latest_proposed.coverage_pct}%"},
        {"metric": "False urgent rate", "baseline": f"{latest_baseline.false_urgent_rate}%", "target": "<=10%", "measured": f"{latest_proposed.false_urgent_rate}%"}
    ]

    return schemas.BacktestSummaryResponse(
        run_id=latest_proposed.run_id,
        evaluated_at=latest_proposed.evaluated_at,
        baseline_mae=latest_baseline.mae_days,
        proposed_mae=latest_proposed.mae_days,
        baseline_median_ae=latest_baseline.median_ae_days,
        proposed_median_ae=latest_proposed.median_ae_days,
        baseline_within_14d_pct=latest_baseline.within_14_days_pct,
        proposed_within_14d_pct=latest_proposed.within_14_days_pct,
        proposed_coverage_pct=latest_proposed.coverage_pct,
        baseline_false_urgent_rate=latest_baseline.false_urgent_rate,
        proposed_false_urgent_rate=latest_proposed.false_urgent_rate,
        comparison_table=[schemas.BacktestMetricComparison(**item) for item in comparison_table]
    )
