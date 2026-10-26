"""Audit-tier routing and the audit log.

Every audited event (an audit-tier request, a CLI action, a system job)
records WHO did WHAT and WHEN as an audit_events row in the metadata DB
(durable and backed up, unlike stdout logs). Admins browse it at
GET /admin/audit and download it as JSONL from GET /admin/audit/export.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from glow_api.database import SessionLocal
from glow_api.metadata_models import AuditEvent

logger = logging.getLogger(__name__)

# Audit-tier (method, route path template) -> the action (WHAT) recorded for
# it. Both slash variants are listed where the app registers both forms.
AUDIT_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/query"): "data.query",
    ("GET", "/query/"): "data.query",
    ("GET", "/dimensions"): "data.dimensions",
    ("GET", "/query/counts"): "data.query_counts",
    ("GET", "/export"): "data.export",
    ("GET", "/export/counts"): "data.export_counts",
    ("POST", "/admin/users"): "user.create",
    ("POST", "/admin/users/"): "user.create",
    ("PUT", "/admin/users/{user_id}"): "user.update",
    ("DELETE", "/admin/users/{user_id}"): "user.delete",
    ("POST", "/admin/schools"): "school.create",
    ("POST", "/admin/schools/"): "school.create",
    ("PUT", "/admin/schools/{school_id}"): "school.update",
    ("DELETE", "/admin/schools/{school_id}"): "school.delete",
    ("POST", "/admin/api-keys"): "api_key.create",
    ("POST", "/admin/api-keys/{key_id}/regenerate"): "api_key.regenerate",
    ("DELETE", "/admin/api-keys/{key_id}"): "api_key.revoke",
    ("POST", "/api-keys/claim"): "api_key.claim",
    ("DELETE", "/api-keys/self"): "api_key.revoke_self",
    ("POST", "/admin/vintages"): "vintage.cut",
    ("GET", "/admin/audit"): "audit.read",
    ("GET", "/admin/audit/export"): "audit.export",
    ("POST", "/demo/reset"): "demo.reset",
    ("GET", "/demo/export/raw"): "demo.export_raw",
}


def audit_action(method: str, route_path: str | None) -> str | None:
    """The action for an audit-tier route, or None if it isn't one."""
    if route_path is None:
        return None
    return AUDIT_ROUTES.get((method, route_path))


def record(
    db: Session,
    *,
    actor: str,
    action: str,
    status_code: int | None = None,
    details: dict[str, Any] | None = None,
    ts: datetime | None = None,
) -> None:
    """Write one audit event to the audit_events table.

    A DB failure is logged, never raised: auditing must not break the action
    it describes (which has already happened by now).
    """
    ts = ts or datetime.now(timezone.utc)
    # Round-trip through JSON so non-JSON values (datetimes etc.) are str()'d
    # rather than breaking the JSON column.
    details = json.loads(json.dumps(details, default=str)) if details else None
    try:
        db.add(
            AuditEvent(
                ts=ts,
                actor=actor,
                action=action,
                status_code=status_code,
                details=details,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to write audit event %s by %s", action, actor)


def record_system(actor: str, action: str, **details: Any) -> None:
    """For callers outside any request (scheduler jobs, polls): own session."""
    with SessionLocal() as db:
        record(db, actor=actor, action=action, details=details or None)
