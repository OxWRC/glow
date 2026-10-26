"""API key generation/hashing/claiming for the pseudonymous export.

Keys are never stored raw - only their sha256 hash. Admin-issued keys start
unclaimed (no hash at all): the recipient is emailed a short-lived, one-time
claim link and the key is generated and shown when they open it, so no admin
ever sees it. The prefix is stored for operators (CLI list) to tell keys apart.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from sqlalchemy.orm import Session

from glow_api.database import create_api_key, delete_api_key, set_api_key_hash
from glow_api.mailer import send_mail
from glow_api.metadata_models import ApiKey
from glow_api.models import ApiKeyRead
from glow_api.settings import settings

KEY_PREFIX = "glow_"
DISPLAY_PREFIX_LEN = 12
# Distinct audience so a claim token can never pass as an access token (the
# dev verifier requires iss, and rejects any aud) or vice versa.
CLAIM_AUDIENCE = "glow-api-key-claim"


def generate_api_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def display_prefix(raw_key: str) -> str:
    return raw_key[:DISPLAY_PREFIX_LEN]


def short_code(api_key: ApiKey) -> str | None:
    """Git-short-sha-style handle telling a holder's keys apart in the audit
    log. From the hash, not the key, so it reveals nothing; None until
    claimed."""
    return api_key.key_hash[:7] if api_key.key_hash else None


def actor_label(api_key: ApiKey) -> str:
    """Audit-log WHO for a key: its email (CLI keys have none: their name)
    plus short code."""
    return f"{api_key.email or api_key.name or 'api-key'}#{short_code(api_key)}"


def _create(db: Session, expires_in_days: int | None, **fields) -> ApiKey:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=expires_in_days) if expires_in_days else None
    return create_api_key(db, created_at=now, expires_at=expires_at, **fields)


def issue_api_key(
    db: Session,
    *,
    name: str | None = None,
    expires_in_days: int | None = None,
) -> tuple[ApiKey, str]:
    """CLI issue: the operator sees the key at once, so it's claimed at issue.
    Returns (record, raw key). The raw key is never stored."""
    raw_key = generate_api_key()
    record = _create(
        db,
        expires_in_days,
        name=name,
        key_hash=hash_api_key(raw_key),
        prefix=display_prefix(raw_key),
        claimed_at=datetime.now(timezone.utc),
    )
    return record, raw_key


def offer_api_key(
    db: Session,
    *,
    email: str,
    name: str | None,
    notes: str | None,
    expires_in_days: int | None,
    created_by_user_id: int | None,
) -> ApiKey:
    """Create an unclaimed key and email its claim link. If sending fails the
    record is removed (a key nobody can claim is just clutter) and the error
    re-raised."""
    record = _create(
        db,
        expires_in_days,
        email=email,
        name=name,
        notes=notes,
        created_by_user_id=created_by_user_id,
    )
    try:
        _send_claim_link(record)
    except Exception:
        delete_api_key(db, record)
        raise
    return record


def lifetime_days(api_key: ApiKey) -> int | None:
    if api_key.expires_at is None:
        return None
    return round((api_key.expires_at - api_key.created_at).total_seconds() / 86400)


def claim_token(api_key: ApiKey) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=settings.API_KEY_CLAIM_HOURS)
    return jwt.encode(
        {"aud": CLAIM_AUDIENCE, "key_id": api_key.id, "exp": exp},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def _send_claim_link(api_key: ApiKey) -> None:
    link = (
        f"{settings.DASHBOARD_URL.rstrip('/')}/en/claim-key#token="
        f"{claim_token(api_key)}"
    )
    label = f' "{api_key.name}"' if api_key.name else ""
    send_mail(
        api_key.email,
        "Your GLOW API key",
        f"A GLOW data export API key{label} has been issued to you.\n\n"
        f"Open this link within {settings.API_KEY_CLAIM_HOURS} hours to reveal it."
        " The link works once and the key is shown only once, so have somewhere"
        " safe to store it:\n\n"
        f"{link}\n\n"
        "If you weren't expecting this, you can ignore this email.\n",
    )


def claim_api_key(db: Session, token: str) -> tuple[ApiKey, str] | None:
    """Generate and store the key for a valid, unused claim link; returns
    (record, raw key), or None if the link is invalid, expired or spent."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=CLAIM_AUDIENCE,
            options={"require": ["exp", "aud"]},
        )
    except jwt.PyJWTError:
        return None
    key_id = payload.get("key_id")
    if not isinstance(key_id, int):
        return None
    raw_key = generate_api_key()
    record = set_api_key_hash(
        db, key_id, hash_api_key(raw_key), display_prefix(raw_key)
    )
    return (record, raw_key) if record else None


def key_status(
    api_key: ApiKey, now: datetime
) -> Literal["unclaimed", "active", "expired", "revoked"]:
    if api_key.revoked_at is not None:
        return "revoked"
    if api_key.expires_at is not None and api_key.expires_at < now:
        return "expired"
    if api_key.key_hash is None:
        return "unclaimed"
    return "active"


def api_key_read(api_key: ApiKey) -> ApiKeyRead:
    return ApiKeyRead(
        id=api_key.id,
        email=api_key.email,
        name=api_key.name,
        notes=api_key.notes,
        status=key_status(api_key, datetime.now(timezone.utc)),
        short_code=short_code(api_key),
        created_by=api_key.created_by.username if api_key.created_by else None,
        created_at=api_key.created_at,
        claimed_at=api_key.claimed_at,
        expires_at=api_key.expires_at,
        revoked_at=api_key.revoked_at,
        last_used_at=api_key.last_used_at,
        use_count=api_key.use_count or 0,
    )
