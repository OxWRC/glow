"""Dev-bypass login: only mounted (see main.py) when settings.DEV_AUTH_BYPASS
is on - never reachable in a real deployment.

Mints tokens with create_access_token, the same HS256 key/issuer the
_DevVerifier decodes with, rather than a separate ad-hoc encoder - so the
normal claim-mapping path (get_current_user -> get_user_by_sub ->
sync_user_claims) is exercised exactly as it would be for a real login.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from glow_api.auth import create_access_token
from glow_api.database import get_db, get_school_by_id, list_schools, upsert_user_by_sub

router = APIRouter(prefix="/auth", tags=["auth"])


class DevLoginRequest(BaseModel):
    role: Literal["admin", "wrc", "school"]
    school_id: int | None = None


class DevLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/dev-login", response_model=DevLoginResponse)
def dev_login(
    payload: DevLoginRequest, db: Session = Depends(get_db)
) -> DevLoginResponse:
    if payload.role == "admin":
        sub = "dev-admin"
        # /me returns exactly a user's assigned schools (no implicit "admin
        # sees everything" expansion - see routers/me.py), so without this
        # dev-admin gets schools: [] on a fresh DB and the dashboard's school
        # picker never renders (nothing to admin-query against).
        user = upsert_user_by_sub(
            db,
            sub,
            username=sub,
            is_admin=True,
            school_ids=[s.id for s in list_schools(db)],
        )
    elif payload.role == "wrc":
        sub = "dev-wrc"
        user = upsert_user_by_sub(db, sub, username=sub, is_wrc=True)
    else:
        school_id = payload.school_id
        if school_id is None:
            schools = list_schools(db)
            if not schools:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No schools seeded to dev-login as",
                )
            school_id = schools[0].id
        elif get_school_by_id(db, school_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"School {school_id} not found",
            )
        sub = f"dev-school-{school_id}"
        user = upsert_user_by_sub(db, sub, username=sub, school_ids=[school_id])

    token = create_access_token(
        {"sub": sub, "cognito:username": user.username, "email": user.email}
    )
    return DevLoginResponse(access_token=token)
