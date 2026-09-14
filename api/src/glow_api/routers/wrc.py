from fastapi import APIRouter, Depends, HTTPException, status

from glow_api.auth import get_current_user
from glow_api.models import UserRead

router = APIRouter(prefix="/wrc", tags=["wrc"])


def require_wrc(current_user: UserRead = Depends(get_current_user)) -> UserRead:
    if not current_user.is_wrc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="WRC access required",
        )
    return current_user


# ---------------------------------------------------------------------------
# Placeholder route establishing the guard + router; WRC's actual feature
# surface is out of scope for this task.
# ---------------------------------------------------------------------------


@router.get("/ping")
def ping(_: UserRead = Depends(require_wrc)) -> dict:
    return {"status": "ok"}
