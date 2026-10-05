from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.auth import require_role, Actor

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/logs", response_model=List[schemas.AuditLogResponse])
def get_audit_logs(
    limit: int = Query(default=100, le=500),
    action: Optional[str] = None,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_role(["platform_admin", "auditor"]))
):
    """
    Retrieve immutable audit logs.
    Accessible exclusively to platform_admin and auditor roles.
    """
    query = db.query(models.AuditLog)
    if action:
        query = query.filter(models.AuditLog.action.ilike(f"%{action}%"))

    return query.order_by(models.AuditLog.timestamp.desc()).limit(limit).all()
