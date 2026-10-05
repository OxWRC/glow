"""Demo-mode login: only mounted (see main.py) when settings.DEMO_MODE
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
from glow_api.data import DataStore, get_datastore
from glow_api.database import (
    get_db,
    get_school_by_id,
    list_schools,
    seed_demo,
    upsert_user_by_sub,
)
from glow_api.models import DemoInfo, DemoSchool

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoLoginRequest(BaseModel):
    role: Literal["admin", "wrc", "school"]
    school_id: int | None = None


class DemoLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=DemoLoginResponse)
def demo_login(
    payload: DemoLoginRequest, db: Session = Depends(get_db)
) -> DemoLoginResponse:
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
                    detail="No schools seeded to demo login as",
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
    return DemoLoginResponse(access_token=token)


@router.get("/info", response_model=DemoInfo)
def demo_info(db: Session = Depends(get_db)) -> DemoInfo:
    schools = sorted(list_schools(db), key=lambda s: s.id)
    return DemoInfo(schools=[DemoSchool(id=s.id, name=s.name) for s in schools])


@router.post("/reset", status_code=status.HTTP_204_NO_CONTENT)
def demo_reset(
    db: Session = Depends(get_db), datastore: DataStore = Depends(get_datastore)
) -> None:
    df = datastore.to_frozen().df
    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Data not loaded yet; cannot reset demo",
        )
    seed_demo(db, df)
