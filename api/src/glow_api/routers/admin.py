import smtplib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Query as SAQuery
from sqlalchemy.orm import Session

from glow_api import mailer, request_context
from glow_api.api_keys import api_key_read, lifetime_days, offer_api_key
from glow_api.auth import require_current_user
from glow_api.database import (
    create_school,
    create_user,
    delete_school,
    delete_user,
    get_api_key_by_id,
    get_db,
    get_school_by_id,
    get_user_by_email,
    get_user_by_id,
    get_user_by_username,
    list_api_keys,
    list_schools,
    list_users,
    revoke_api_key,
    set_geographical_neighbors,
    set_statistical_neighbors,
    update_school,
    update_user,
)
from glow_api.data import DataStore, get_datastore
from glow_api.metadata_models import ApiKey, AuditEvent
from glow_api.models import (
    ApiKeyCreate,
    ApiKeyRead,
    AuditEventRead,
    SchoolCreate,
    SchoolRead,
    SchoolUpdate,
    UserCreate,
    UserRead,
    UserUpdate,
    VintageRead,
)
from glow_api.vintages import VintageInfo, cut_vintage, get_vintages_dir, list_vintages

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(current_user: UserRead = Depends(require_current_user)) -> UserRead:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


# ---------------------------------------------------------------------------
# User management endpoints
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[UserRead])
def list_all_users(
    _: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> list[UserRead]:
    users = list_users(db)
    result = []
    for u in users:
        result.append(
            UserRead(
                id=u.id,
                username=u.username,
                school_ids=[s.id for s in u.schools],
                school_names=[s.name for s in u.schools],
                is_active=u.is_active,
                is_admin=u.is_admin,
                email=u.email,
            )
        )
    return result


@router.post("/users/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
@router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_new_user(
    payload: UserCreate,
    current_admin: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> UserRead:
    if (
        get_user_by_username(db, payload.email) is not None
        or get_user_by_email(db, payload.email) is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User '{payload.email}' already exists",
        )
    user = create_user(
        db,
        username=payload.email,
        email=payload.email,
        school_ids=payload.school_ids,
        is_admin=payload.is_admin,
    )
    request_context.record_event(
        "admin_mutation",
        action="create_user",
        target_user_id=user.id,
        target_username=user.username,
        actor_username=current_admin.username,
        actor_id=current_admin.id,
    )
    return UserRead(
        id=user.id,
        username=user.username,
        school_ids=[s.id for s in user.schools],
        school_names=[s.name for s in user.schools],
        is_active=user.is_active,
        is_admin=user.is_admin,
        email=user.email,
    )


@router.put("/users/{user_id}", response_model=UserRead)
def update_existing_user(
    user_id: int,
    payload: UserUpdate,
    current_admin: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> UserRead:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    updated = update_user(
        db,
        user,
        school_ids=payload.school_ids,
        is_active=payload.is_active,
        is_admin=payload.is_admin,
    )
    request_context.record_event(
        "admin_mutation",
        action="update_user",
        target_user_id=user_id,
        actor_username=current_admin.username,
        actor_id=current_admin.id,
        fields_changed=list(payload.model_dump(exclude_unset=True).keys()),
    )
    return UserRead(
        id=updated.id,
        username=updated.username,
        school_ids=[s.id for s in updated.schools],
        school_names=[s.name for s in updated.schools],
        is_active=updated.is_active,
        is_admin=updated.is_admin,
        email=updated.email,
    )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_user(
    user_id: int,
    current_admin: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> None:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )
    delete_user(db, user)
    request_context.record_event(
        "admin_mutation",
        action="delete_user",
        target_user_id=user_id,
        actor_username=current_admin.username,
        actor_id=current_admin.id,
    )


# ---------------------------------------------------------------------------
# School management endpoints
# ---------------------------------------------------------------------------


@router.get("/schools", response_model=list[SchoolRead])
def list_all_schools(
    _: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> list[SchoolRead]:
    schools = list_schools(db)
    return [
        SchoolRead(
            id=s.id,
            name=s.name,
            size=s.size,
            category=s.category,
            odk_school_id=s.odk_school_id,
            geographical_neighbor_ids=[n.id for n in s.geographical_neighbors],
            statistical_neighbor_ids=[n.id for n in s.statistical_neighbors],
        )
        for s in schools
    ]


@router.post(
    "/schools/", response_model=SchoolRead, status_code=status.HTTP_201_CREATED
)
@router.post(
    "/schools",
    response_model=SchoolRead,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_new_school(
    payload: SchoolCreate,
    current_admin: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> SchoolRead:
    school = create_school(
        db,
        name=payload.name,
        size=payload.size,
        category=payload.category,
        odk_school_id=payload.odk_school_id,
    )
    request_context.record_event(
        "admin_mutation",
        action="create_school",
        target_school_id=school.id,
        target_school_name=school.name,
        actor_username=current_admin.username,
        actor_id=current_admin.id,
    )
    return SchoolRead(
        id=school.id,
        name=school.name,
        size=school.size,
        category=school.category,
        odk_school_id=school.odk_school_id,
        geographical_neighbor_ids=[],
        statistical_neighbor_ids=[],
    )


@router.put("/schools/{school_id}", response_model=SchoolRead)
def update_existing_school(
    school_id: int,
    payload: SchoolUpdate,
    current_admin: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> SchoolRead:
    school = get_school_by_id(db, school_id)
    if school is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="School not found"
        )

    updated = update_school(
        db,
        school,
        name=payload.name,
        size=payload.size,
        category=payload.category,
        odk_school_id=payload.odk_school_id,
    )

    # Update neighbors if provided
    if payload.geographical_neighbor_ids is not None:
        updated = set_geographical_neighbors(
            db, updated, payload.geographical_neighbor_ids
        )
    if payload.statistical_neighbor_ids is not None:
        updated = set_statistical_neighbors(
            db, updated, payload.statistical_neighbor_ids
        )

    request_context.record_event(
        "admin_mutation",
        action="update_school",
        target_school_id=school_id,
        actor_username=current_admin.username,
        actor_id=current_admin.id,
        fields_changed=list(payload.model_dump(exclude_unset=True).keys()),
    )
    return SchoolRead(
        id=updated.id,
        name=updated.name,
        size=updated.size,
        category=updated.category,
        odk_school_id=updated.odk_school_id,
        geographical_neighbor_ids=[n.id for n in updated.geographical_neighbors],
        statistical_neighbor_ids=[n.id for n in updated.statistical_neighbors],
    )


@router.delete("/schools/{school_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_school(
    school_id: int,
    current_admin: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> None:
    school = get_school_by_id(db, school_id)
    if school is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="School not found"
        )
    delete_school(db, school)
    request_context.record_event(
        "admin_mutation",
        action="delete_school",
        target_school_id=school_id,
        actor_username=current_admin.username,
        actor_id=current_admin.id,
    )


# ---------------------------------------------------------------------------
# Current user info endpoint
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserRead)
def get_current_admin(
    current_user: UserRead = Depends(require_current_user),
) -> UserRead:
    """Return the current user's details, including is_admin flag."""
    return current_user


# ---------------------------------------------------------------------------
# API keys (pseudonymous export)
# ---------------------------------------------------------------------------


@router.get("/api-keys", response_model=list[ApiKeyRead])
def list_keys(
    _: UserRead = Depends(_require_admin), db: Session = Depends(get_db)
) -> list[ApiKeyRead]:
    return [api_key_read(k) for k in list_api_keys(db)]


def _offer(db: Session, admin: UserRead, **fields) -> ApiKeyRead:
    if not mailer.configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email is not configured (GLOW_SMTP_HOST); cannot send claim links",
        )
    try:
        record = offer_api_key(db, created_by_user_id=admin.id, **fields)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not send the claim email: {exc}",
        ) from exc
    request_context.record_event(
        "api_key_created", actor=admin.username, api_key_id=record.id
    )
    return api_key_read(record)


@router.post(
    "/api-keys", response_model=ApiKeyRead, status_code=status.HTTP_201_CREATED
)
def create_key(
    body: ApiKeyCreate,
    admin: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> ApiKeyRead:
    """Issue an unclaimed key and email its one-time claim link."""
    return _offer(db, admin, **body.model_dump())


def _get_key_or_404(db: Session, key_id: int) -> ApiKey:
    record = get_api_key_by_id(db, key_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        )
    return record


@router.post(
    "/api-keys/{key_id}/regenerate",
    response_model=ApiKeyRead,
    status_code=status.HTTP_201_CREATED,
)
def regenerate_key(
    key_id: int,
    admin: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> ApiKeyRead:
    """Reissue a key's details to a new unclaimed key, then revoke the old one
    (only once the new link is sent, so a mail failure leaves it working)."""
    old = _get_key_or_404(db, key_id)
    if old.email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Key has no email to send a claim link to",
        )
    new = _offer(
        db,
        admin,
        email=old.email,
        name=old.name,
        notes=old.notes,
        expires_in_days=lifetime_days(old),
    )
    revoke_api_key(db, old)
    request_context.record_event(
        "api_key_revoked", actor=admin.username, api_key_id=key_id
    )
    return new


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(
    key_id: int,
    admin: UserRead = Depends(_require_admin),
    db: Session = Depends(get_db),
) -> None:
    revoke_api_key(db, _get_key_or_404(db, key_id))
    request_context.record_event(
        "api_key_revoked", actor=admin.username, api_key_id=key_id
    )


# ---------------------------------------------------------------------------
# Vintages (periodic, immutable dataset archives /query and /export pin
# against). The vintages directory is the only source of truth - see glow_api.vintages.
# ---------------------------------------------------------------------------


def _vintage_read(info: VintageInfo) -> VintageRead:
    return VintageRead(
        prefix=info.prefix,
        created_at=info.created_at,
        dataset_version=info.dataset_version,
        row_count=info.row_count,
        created_by_username=info.created_by_username,
    )


@router.get("/vintages", response_model=list[VintageRead])
def list_vintages_route(
    _: UserRead = Depends(_require_admin),
    root: Path = Depends(get_vintages_dir),
) -> list[VintageRead]:
    return [_vintage_read(info) for info in list_vintages(root)]


@router.post(
    "/vintages", response_model=VintageRead, status_code=status.HTTP_201_CREATED
)
def cut_vintage_route(
    admin: UserRead = Depends(_require_admin),
    datastore: DataStore = Depends(get_datastore),
    root: Path = Depends(get_vintages_dir),
) -> VintageRead:
    info = cut_vintage(datastore, root, created_by=(admin.id, admin.username))
    request_context.record_event(
        "vintage_cut",
        actor=admin.username,
        prefix=info.prefix,
        row_count=info.row_count,
    )
    return _vintage_read(info)


# ---------------------------------------------------------------------------
# Audit log (#41): WHO did WHAT and WHEN, newest first
# ---------------------------------------------------------------------------


def _audit_query(
    who: str | None = Query(None, description="Case-insensitive actor substring"),
    what: str | None = Query(None, description="Case-insensitive action substring"),
    since: datetime | None = Query(None, description="Inclusive lower bound"),
    until: datetime | None = Query(None, description="Inclusive upper bound"),
    db: Session = Depends(get_db),
) -> SAQuery:
    """The WHO / WHAT / WHEN filters shared by the list and the export."""
    query = db.query(AuditEvent)
    if who:
        query = query.filter(AuditEvent.actor.icontains(who, autoescape=True))
    if what:
        query = query.filter(AuditEvent.action.icontains(what, autoescape=True))
    if since:
        query = query.filter(AuditEvent.ts >= since)
    if until:
        query = query.filter(AuditEvent.ts <= until)
    return query


@router.get("/audit", response_model=list[AuditEventRead])
def list_audit_events(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: UserRead = Depends(_require_admin),
    query: SAQuery = Depends(_audit_query),
) -> list[AuditEvent]:
    """Newest first, paged."""
    return (
        query.order_by(AuditEvent.ts.desc(), AuditEvent.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get(
    "/audit/export",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {}}}},
)
def export_audit_events(
    _: UserRead = Depends(_require_admin),
    query: SAQuery = Depends(_audit_query),
) -> StreamingResponse:
    """Every matching event as JSON Lines, oldest first, as a download."""
    rows = query.order_by(AuditEvent.ts, AuditEvent.id).yield_per(1000)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(
        (AuditEventRead.model_validate(r).model_dump_json() + "\n" for r in rows),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.jsonl"'},
    )
