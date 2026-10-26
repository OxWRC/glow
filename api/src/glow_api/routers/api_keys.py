"""Key-holder endpoints: claim a key from an emailed link, and let a key
revoke itself (no admin needed if it leaks)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from glow_api import request_context
from glow_api.api_keys import claim_api_key
from glow_api.auth import require_api_key
from glow_api.database import get_db, revoke_api_key
from glow_api.metadata_models import ApiKey
from glow_api.models import ApiKeyClaim, ApiKeyClaimed

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post("/claim", response_model=ApiKeyClaimed)
def claim_key(body: ApiKeyClaim, db: Session = Depends(get_db)) -> ApiKeyClaimed:
    claimed = claim_api_key(db, body.token)
    if claimed is None:
        request_context.record_event("api_key_claim_failed")
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This link is invalid, expired or has already been used",
        )
    record, raw_key = claimed
    request_context.record_event("api_key_claimed", api_key_id=record.id)
    return ApiKeyClaimed(key=raw_key, expires_at=record.expires_at)


@router.delete("/self", status_code=status.HTTP_204_NO_CONTENT)
def revoke_self(
    api_key: ApiKey = Depends(require_api_key), db: Session = Depends(get_db)
) -> None:
    revoke_api_key(db, api_key)
    request_context.record_event("api_key_revoked", actor="self", api_key_id=api_key.id)
