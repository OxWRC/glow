"""Authentication and authorization logic.

Token verification uses any OIDC issuer (discovery + JWKS) in any
environment with GLOW_OIDC_ISSUER configured; a local HS256 "dev" mode is
available for development and testing when no issuer is configured (see settings.validate_auth_config for
the guard that keeps the two modes from being enabled together).
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import requests
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    OAuth2PasswordBearer,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from glow_api import request_context
from glow_api.api_keys import hash_api_key
from glow_api.database import (
    get_api_key_by_hash,
    get_db,
    get_school_by_id,
    get_unlinked_user_by_email,
    get_user_by_sub,
    normalise_email,
)
from glow_api.metadata_models import ApiKey, School, User
from glow_api.models import UserRead
from glow_api.settings import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

DEV_ISSUER = "glow-dev"


def _fetch_discovery(url: str) -> dict[str, Any]:
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    return resp.json()


class _OidcVerifier:
    """Verifies RS256/ES256 tokens from any OIDC issuer via discovery + JWKS.

    Discovery is lazy (first decode) and retried on the next call if it fails,
    so an IdP outage at startup doesn't stop the API from booting.
    """

    def __init__(self, issuer: str, client_id: str):
        self._issuer = issuer
        self._client_id = client_id
        self._jwks_client: jwt.PyJWKClient | None = None

    def _get_jwks_client(self) -> jwt.PyJWKClient:
        if self._jwks_client is None:
            url = f"{self._issuer.rstrip('/')}/.well-known/openid-configuration"
            try:
                doc = _fetch_discovery(url)
                if not isinstance(doc, dict):
                    raise ValueError("discovery document is not a JSON object")
                jwks_uri = doc["jwks_uri"]
                doc_issuer = doc["issuer"]
                if not isinstance(jwks_uri, str) or not jwks_uri:
                    raise ValueError("jwks_uri is not a string")
            except (requests.RequestException, OSError, ValueError, KeyError) as exc:
                raise jwt.PyJWTError(f"OIDC discovery failed: {exc}") from exc
            if doc_issuer != self._issuer:
                raise jwt.PyJWTError("OIDC discovery issuer does not match config")
            self._jwks_client = jwt.PyJWKClient(jwks_uri)
        return self._jwks_client

    def decode(self, token: str) -> dict[str, Any]:
        signing_key = self._get_jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            issuer=self._issuer,
            audience=self._client_id,
            options={"require": ["exp", "iss", "aud"]},
        )


class _DevVerifier:
    """HS256 verifier against SECRET_KEY, for local dev / tests when no OIDC issuer is configured."""

    def decode(self, token: str) -> dict[str, Any]:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            issuer=DEV_ISSUER,
        )


def _build_verifier() -> _OidcVerifier | _DevVerifier:
    # Fail closed, not open: refuse to pick a verifier at all if the config
    # is ambiguous or incomplete (see validate_auth_config's docstring).
    settings.validate_auth_config()
    if settings.OIDC_ISSUER:
        return _OidcVerifier(settings.OIDC_ISSUER, settings.OIDC_CLIENT_ID)
    if not settings.DEMO_MODE:
        raise RuntimeError(
            "No OIDC issuer configured (GLOW_OIDC_ISSUER) and "
            "GLOW_DEMO_MODE is off - refusing to start with demo-mode auth."
        )
    return _DevVerifier()


verifier = _build_verifier()


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Mint an HS256 dev-mode token (used by the demo-mode login path)."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    to_encode.setdefault("iss", DEV_ISSUER)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _email_verified(claims: dict[str, Any]) -> bool:
    return claims.get("email_verified") in (True, "true")


def resolve_user(db: Session, claims: dict[str, Any]) -> User | None:
    """Map verified token claims to the active local user, or None.

    A known `sub` wins. Otherwise the token's verified email may claim a
    pre-registered user whose oidc_sub is still NULL (first login). Users are
    never created here, and an unverified email never links.
    """
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    user = get_user_by_sub(db, sub)
    if user is not None:
        return user if user.is_active else None

    email = claims.get("email")
    if not isinstance(email, str) or not _email_verified(claims):
        return None
    user = get_unlinked_user_by_email(db, email)
    if user is None or not user.is_active:
        return None
    user.oidc_sub = sub
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return user


def sync_user_claims(db: Session, user: User, claims: dict[str, Any]) -> None:
    """Update the local row's email if the verified token's email drifted.

    No extra network call - reuses the claims already returned with the
    verified token. Best-effort: an email collision rolls back rather than
    breaking the auth path.
    """
    token_email = normalise_email(claims.get("email"))
    if not token_email or token_email == user.email or not _email_verified(claims):
        return
    user.email = token_email
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def _user_model_to_read(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        username=user.username,
        school_ids=[s.id for s in user.schools],
        school_names=[s.name for s in user.schools],
        is_active=user.is_active,
        is_admin=user.is_admin,
        email=user.email,
    )


def _authenticate_token(token: str, db: Session) -> UserRead:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        claims = verifier.decode(token)
    except jwt.PyJWTError:
        raise credentials_exception

    user = resolve_user(db, claims)
    if user is None:
        raise credentials_exception

    sync_user_claims(db, user, claims)

    return _user_model_to_read(user)


async def require_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> UserRead:
    return _authenticate_token(token, db)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
) -> UserRead | None:
    """Same as require_current_user, but returns None (rather than 401ing)
    when no bearer token is present at all - for routes that serve both
    anonymous and authenticated callers.
    """
    if token is None:
        return None
    return _authenticate_token(token, db)


def get_optional_school_user(
    credentials: HTTPAuthorizationCredentials | None,
    db: Session,
    school_id: int,
) -> tuple[UserRead, School]:
    """Shared auth check for school-scoped, optionally-authenticated endpoints
    (/query and /dimensions). Preserves the exact status codes/detail strings
    both endpoints already used, and emits one 'auth_assessed' timeline event
    covering every outcome.
    """
    if credentials is None:
        request_context.record_event(
            "auth_assessed",
            outcome="no_credentials",
            success=False,
            school_id=school_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for school-scoped queries",
        )

    try:
        claims = verifier.decode(credentials.credentials)
    except jwt.PyJWTError:
        request_context.record_event(
            "auth_assessed", outcome="invalid_token", success=False, school_id=school_id
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    user = resolve_user(db, claims)
    if user is None:
        request_context.record_event(
            "auth_assessed",
            outcome="unknown_or_inactive_user",
            success=False,
            username=claims.get("sub"),
            school_id=school_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    sync_user_claims(db, user, claims)

    user_school_ids = [s.id for s in user.schools]
    if not user.is_admin and school_id not in user_school_ids:
        request_context.record_event(
            "auth_assessed",
            outcome="forbidden",
            success=False,
            username=user.username,
            is_admin=user.is_admin,
            school_id=school_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You do not have access to school {school_id}",
        )

    school = get_school_by_id(db, school_id)
    if school is None:
        request_context.record_event(
            "auth_assessed",
            outcome="school_not_found",
            success=False,
            username=user.username,
            school_id=school_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"School {school_id} not found",
        )

    request_context.record_event(
        "auth_assessed",
        outcome="success",
        success=True,
        username=user.username,
        is_admin=user.is_admin,
        school_id=school_id,
    )
    return _user_model_to_read(user), school


async def require_api_key(
    api_key: str | None = Security(api_key_header),
    db: Session = Depends(get_db),
) -> ApiKey:
    """Auth for the pseudonymous export only. Never accepts a JWT, and no
    JWT-authenticated route accepts this."""
    record = get_api_key_by_hash(db, hash_api_key(api_key)) if api_key else None
    if (
        record is None
        or record.revoked_at is not None
        or record.expires_at < datetime.now(timezone.utc)
    ):
        request_context.record_event(
            "auth_assessed", outcome="invalid_api_key", success=False
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    request_context.record_event(
        "auth_assessed", outcome="success", success=True, api_key_id=record.id
    )
    return record
