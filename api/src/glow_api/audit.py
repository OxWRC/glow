"""Audit-tier routing and the durable local audit log sink.

Audit-tier requests (data reads and identity/access-changing actions) get
their full request log line additionally appended to a local rotating JSONL
file, since the CloudWatch log group the API's stdout normally lands in only
retains 14 days — not a real audit trail.
"""

import json
import logging
import logging.handlers
from pathlib import Path
from typing import Any

from glow_api.settings import settings

# (method, route path template) pairs considered audit-tier. Both slash
# variants are listed where the app registers both forms of a route.
AUDIT_ROUTES: set[tuple[str, str]] = {
    ("GET", "/query"),
    ("GET", "/query/"),
    ("GET", "/dimensions"),
    ("POST", "/admin/users"),
    ("POST", "/admin/users/"),
    ("PUT", "/admin/users/{user_id}"),
    ("DELETE", "/admin/users/{user_id}"),
    ("POST", "/admin/schools"),
    ("POST", "/admin/schools/"),
    ("PUT", "/admin/schools/{school_id}"),
    ("DELETE", "/admin/schools/{school_id}"),
}


def is_audit_route(method: str, route_path: str | None) -> bool:
    if route_path is None:
        return False
    return (method, route_path) in AUDIT_ROUTES


_audit_logger: logging.Logger | None = None


def _get_audit_logger() -> logging.Logger | None:
    """Lazily build the dedicated stdlib logger + rotating file handler for
    the JSONL audit sink. Returns None (disabled) if settings.AUDIT_LOG_PATH
    is unset — the normal case in tests/local dev.
    """
    global _audit_logger
    if _audit_logger is not None:
        return _audit_logger
    if not settings.AUDIT_LOG_PATH:
        return None

    path = Path(settings.AUDIT_LOG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("glow_api.audit_sink")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    handler = logging.handlers.RotatingFileHandler(
        filename=str(path),
        maxBytes=settings.AUDIT_LOG_MAX_BYTES,
        backupCount=settings.AUDIT_LOG_BACKUP_COUNT,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    _audit_logger = logger
    return logger


def reset_audit_sink() -> None:
    """Test hook: drop the cached logger so a changed AUDIT_LOG_PATH takes effect."""
    global _audit_logger
    _audit_logger = None


def write_audit_line(fields: dict[str, Any]) -> None:
    logger = _get_audit_logger()
    if logger is None:
        return
    logger.info(json.dumps(fields, default=str))
