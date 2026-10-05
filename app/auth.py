import uuid
from datetime import datetime
from typing import List, Optional
from fastapi import Header, HTTPException, status, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

# Available valid roles
ROLES = {
    "platform_admin": "Full system access, cross-tenant visibility, approval workflows",
    "tenant_admin": "Org-specific tenant visibility, can request capacity and view forecasts",
    "external_partner": "Delegated external tenant visibility only, read-only forecasts",
    "auditor": "Read-only access to audit logs and backtesting benchmarks"
}

# Delegated tenants for external partner demo simulation
DELEGATED_PARTNER_TENANTS = {"tenant_041", "tenant_042", "tenant_043", "tenant_044", "tenant_045"}


class Actor:
    """Represents the authenticated actor in the request context."""
    def __init__(self, user_id: str, role: str, org_id: str):
        self.user_id = user_id
        self.role = role
        self.org_id = org_id

    def can_access_tenant(self, tenant: models.Tenant) -> bool:
        if self.role == "platform_admin":
            return True
        if self.role == "tenant_admin":
            return tenant.org_id == self.org_id
        if self.role == "external_partner":
            return tenant.tenant_id in DELEGATED_PARTNER_TENANTS
        if self.role == "auditor":
            return False  # Auditor does not inspect raw tenant schemas directly, only audit & backtest
        return False


def get_current_actor(
    x_role: Optional[str] = Header(default="platform_admin", alias="X-Role"),
    x_org_id: Optional[str] = Header(default="org_1", alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(default="admin_usr", alias="X-User-Id"),
) -> Actor:
    """
    Extract actor information from request headers.
    Defaults to platform_admin for seamless development testing.
    """
    role = (x_role or "platform_admin").strip().lower()
    if role not in ROLES:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid X-Role: '{x_role}'. Allowed roles: {list(ROLES.keys())}"
        )
    return Actor(user_id=x_user_id or "anonymous", role=role, org_id=x_org_id or "org_1")


def require_role(allowed_roles: List[str]):
    """
    FastAPI dependency factory enforcing allowed roles.
    """
    def role_checker(actor: Actor = Depends(get_current_actor)) -> Actor:
        if actor.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Role '{actor.role}' lacks permission. Required one of: {allowed_roles}"
            )
        return actor
    return role_checker


def log_audit(
    db: Session,
    actor: Actor,
    action: str,
    entity: str,
    details: Optional[str] = None
):
    """
    Append an immutable event to the audit_logs table.
    """
    audit_entry = models.AuditLog(
        log_id=f"audit_{uuid.uuid4().hex[:12]}",
        timestamp=datetime.utcnow(),
        actor=actor.user_id,
        role=actor.role,
        action=action,
        entity=entity,
        details=details or ""
    )
    db.add(audit_entry)
    db.commit()
