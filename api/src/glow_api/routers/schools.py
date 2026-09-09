from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from glow_api.auth import get_current_user
from glow_api.database import get_db, list_schools
from glow_api.models import SchoolListResponse, SchoolRead, UserRead

router = APIRouter(prefix="/schools", tags=["schools"])


@router.get("", response_model=SchoolListResponse)
def get_schools(
    current_user: UserRead = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SchoolRead]:
    """
    List all schools the user can access.
    Admins can access all schools, regular users can only access their assigned schools.
    """
    all_schools = list_schools(db)

    # If user is admin, return all schools
    if current_user.is_admin:
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
            for s in all_schools
        ]

    # Otherwise, filter to only schools the user has access to
    user_school_ids = set(current_user.school_ids)
    accessible_schools = [s for s in all_schools if s.id in user_school_ids]

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
        for s in accessible_schools
    ]
