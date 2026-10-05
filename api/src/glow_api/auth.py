"""Authentication and authorization logic.

Token verification is Cognito-backed in any environment with a configured
user pool; a local HS256 "dev" mode is available for development and
testing when no pool is configured (see settings.validate_auth_config for
the guard that keeps the two modes from being enabled together).
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    OAuth2PasswordBearer,
)
import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from glow_api import request_context
from glow_api.api_keys import hash_api_key
from glow_api.database import (
    get_api_key_by_hash,
    get_db,
    get_school_by_id,
    get_user_by_id,
    get_user_by_sub,
    touch_api_key_last_used,
)
from glow_api.metadata_models import School, User
from glow_api.models import UserRead
from glow_api.settings import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

DEV_ISSUER = "glow-dev"


class _CognitoVerifier:
    """Verifies RS256 tokens issued by an AWS Cognito user pool via its JWKS endpoint."""

    def __init__(self, user_pool_id: str, client_id: str, region: str):
        self._issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self._client_id = client_id
        self._jwks_client = jwt.PyJWKClient(f"{self._issuer}/.well-known/jwks.json")

    def decode(self, token: str) -> dict[str, Any]:
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=self._issuer,
            audience=self._client_id,
        )


class _DevVerifier:
    """HS256 verifier against SECRET_KEY, for local dev / tests when Cognito isn't configured."""

    def decode(self, token: str) -> dict[str, Any]:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            issuer=DEV_ISSUER,
        )


def _build_verifier() -> _CognitoVerifier | _DevVerifier:
    # Fail closed, not open: refuse to pick a verifier at all if the config
    # is ambiguous or incomplete (see validate_auth_config's docstring).
    settings.validate_auth_config()
    if settings.COGNITO_USER_POOL_ID:
        return _CognitoVerifier(
            settings.COGNITO_USER_POOL_ID,
            settings.COGNITO_CLIENT_ID,
            settings.COGNITO_REGION,
        )
    if not settings.DEV_AUTH_BYPASS:
        raise RuntimeError(
            "No Cognito pool configured (GLOW_COGNITO_USER_POOL_ID) and "
            "GLOW_DEV_AUTH_BYPASS is off - refusing to start with dev-mode auth."
        )
    return _DevVerifier()


verifier = _build_verifier()


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Mint an HS256 dev-mode token (used by the dev-bypass login path)."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    to_encode.setdefault("iss", DEV_ISSUER)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def sync_user_claims(db: Session, user: User, claims: dict[str, Any]) -> None:
    """Update the local row if the token's username/email claims have drifted.

    No extra network call - reuses the claims already returned with the
    verified token. Best-effort: a username collision rolls back rather than
    breaking the auth path.
    """
    changed = False
    token_username = claims.get("cognito:username")
    if token_username and token_username != user.username:
        user.username = token_username
        changed = True
    token_email = claims.get("email")
    if token_email and token_email != user.email:
        user.email = token_email
        changed = True
    if not changed:
        return
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
        is_wrc=user.is_wrc,
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
        sub: str | None = claims.get("sub")
        if sub is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = get_user_by_sub(db, sub)
    if user is None or not user.is_active:
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
    when no bearer token is present at all - for routes that also accept an
    API key, where the absence of a JWT isn't itself an error.
    """
    if token is None:
        return None
    return _authenticate_token(token, db)


async def require_api_key_user(
    api_key: str | None = Security(api_key_header),
    db: Session = Depends(get_db),
) -> UserRead:
    """Auth dependency for API-key-based (script) access to WRC routes.

    Separate from require_current_user's JWT path entirely - a WRC API key
    never grants anything a JWT-authenticated session does, and vice versa.
    """
    invalid_key_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )
    if api_key is None:
        raise invalid_key_exception

    record = get_api_key_by_hash(db, hash_api_key(api_key))
    if record is None or record.revoked_at is not None:
        raise invalid_key_exception
    if record.expires_at < datetime.now(timezone.utc):
        raise invalid_key_exception

    user = get_user_by_id(db, record.user_id)
    if user is None or not user.is_active or not user.is_wrc:
        raise invalid_key_exception

    touch_api_key_last_used(db, record)
    return _user_model_to_read(user)


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
        sub: str | None = claims.get("sub")
        if sub is None:
            request_context.record_event(
                "auth_assessed",
                outcome="invalid_token",
                success=False,
                school_id=school_id,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )
    except jwt.PyJWTError:
        request_context.record_event(
            "auth_assessed", outcome="invalid_token", success=False, school_id=school_id
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    user = get_user_by_sub(db, sub)
    if user is None or not user.is_active:
        request_context.record_event(
            "auth_assessed",
            outcome="unknown_or_inactive_user",
            success=False,
            username=claims.get("cognito:username", sub),
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
