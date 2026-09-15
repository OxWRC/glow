from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Security, status
from sqlalchemy.orm import Session

from glow_api.api_keys import display_prefix, generate_api_key, hash_api_key
from glow_api.auth import (
    api_key_header,
    get_current_user,
    require_api_key_user,
)
from glow_api.database import (
    create_api_key,
    get_api_key_by_id,
    get_db,
    list_api_keys_for_user,
    revoke_api_key,
)
from glow_api.models import ApiKeyCreate, ApiKeyCreated, ApiKeyRead, UserRead
from glow_api.settings import settings

router = APIRouter(prefix="/wrc", tags=["wrc"])


async def require_wrc(
    api_key: str | None = Security(api_key_header),
    current_user: UserRead | None = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserRead:
    """WRC access, by either credential: a WRC user's JWT, or a WRC API key.

    API key takes priority when both happen to be present. Scripts use the
    key; the dashboard uses the JWT - either lands a WRC-flagged UserRead.
    """
    if api_key is not None:
        return await require_api_key_user(api_key=api_key, db=db)
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    if not current_user.is_wrc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="WRC access required",
        )
    return current_user


# ---------------------------------------------------------------------------
# Placeholder route establishing the guard + router; WRC's actual feature
# surface (e.g. the data export scripts will hit) is out of scope for this
# task - this only establishes the auth path, reachable by JWT or API key.
# ---------------------------------------------------------------------------


@router.get("/ping")
def ping(_: UserRead = Depends(require_wrc)) -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API key management - JWT-authenticated, self-service for WRC users.
# ---------------------------------------------------------------------------


@router.post("/keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_key(
    body: ApiKeyCreate,
    current_user: UserRead = Depends(require_wrc),
    db: Session = Depends(get_db),
) -> ApiKeyCreated:
    raw_key = generate_api_key()
    expires_days = (
        body.expires_in_days
        if body.expires_in_days is not None
        else settings.API_KEY_EXPIRE_DAYS
    )
    record = create_api_key(
        db,
        user_id=current_user.id,
        name=body.name,
        key_hash=hash_api_key(raw_key),
        prefix=display_prefix(raw_key),
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_days),
    )
    return ApiKeyCreated(
        id=record.id,
        name=record.name,
        prefix=record.prefix,
        key=raw_key,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


@router.get("/keys", response_model=list[ApiKeyRead])
def list_keys(
    current_user: UserRead = Depends(require_wrc),
    db: Session = Depends(get_db),
) -> list[ApiKeyRead]:
    return list_api_keys_for_user(db, current_user.id)


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(
    key_id: int,
    current_user: UserRead = Depends(require_wrc),
    db: Session = Depends(get_db),
) -> None:
    record = get_api_key_by_id(db, key_id)
    if record is None or record.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        )
    revoke_api_key(db, record)
