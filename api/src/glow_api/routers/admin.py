from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from glow_api import request_context
from glow_api.auth import get_current_user, get_password_hash
from glow_api.database import (
    create_school,
    create_user,
    delete_school,
    delete_user,
    get_db,
    get_school_by_id,
    get_user_by_id,
    get_user_by_username,
    list_schools,
    list_users,
    set_geographical_neighbors,
    set_statistical_neighbors,
    update_school,
    update_user,
)
from glow_api.models import (
    SchoolCreate,
    SchoolRead,
    SchoolUpdate,
    UserCreate,
    UserRead,
    UserUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(current_user: UserRead = Depends(get_current_user)) -> UserRead:
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
    existing = get_user_by_username(db, payload.username)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{payload.username}' already exists",
        )
    hashed = get_password_hash(payload.password)
    user = create_user(
        db,
        username=payload.username,
        hashed_password=hashed,
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

    hashed = get_password_hash(payload.password) if payload.password else None
    updated = update_user(
        db,
        user,
        hashed_password=hashed,
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
def get_current_admin(current_user: UserRead = Depends(get_current_user)) -> UserRead:
    """Return the current user's details, including is_admin flag."""
    return current_user
