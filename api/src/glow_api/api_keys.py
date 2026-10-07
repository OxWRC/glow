"""API key generation/hashing helpers for the pseudonymous export.

Keys are issued by admins, never stored raw - only their sha256 hash. The
prefix is stored alongside for display (so an admin can tell keys apart in a
list) without ever re-exposing the secret.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy.orm import Session

from glow_api.database import create_api_key
from glow_api.metadata_models import ApiKey
from glow_api.models import ApiKeyRead
from glow_api.settings import settings

KEY_PREFIX = "glow_"
DISPLAY_PREFIX_LEN = 12


def generate_api_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def display_prefix(raw_key: str) -> str:
    return raw_key[:DISPLAY_PREFIX_LEN]


def issue_api_key(
    db: Session,
    name: str,
    expires_in_days: int | None,
    created_by_user_id: int | None,
) -> tuple[ApiKey, str]:
    """Create a key; returns (record, raw key). The raw key is never stored."""
    raw_key = generate_api_key()
    days = (
        expires_in_days if expires_in_days is not None else settings.API_KEY_EXPIRE_DAYS
    )
    record = create_api_key(
        db,
        name=name,
        key_hash=hash_api_key(raw_key),
        prefix=display_prefix(raw_key),
        expires_at=datetime.now(timezone.utc) + timedelta(days=days),
        created_by_user_id=created_by_user_id,
    )
    return record, raw_key


def key_status(
    api_key: ApiKey, now: datetime
) -> Literal["active", "expired", "revoked"]:
    if api_key.revoked_at is not None:
        return "revoked"
    if api_key.expires_at < now:
        return "expired"
    return "active"


def api_key_read(api_key: ApiKey) -> ApiKeyRead:
    return ApiKeyRead(
        id=api_key.id,
        name=api_key.name,
        prefix=api_key.prefix,
        created_by=api_key.created_by.username if api_key.created_by else None,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
        revoked_at=api_key.revoked_at,
        last_used_at=api_key.last_used_at,
        use_count=api_key.use_count or 0,
    )
