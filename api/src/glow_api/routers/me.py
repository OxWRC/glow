"""Router for the /me endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from sqlalchemy.orm import Session
from typing import Optional

from glow_api.auth import sync_user_claims, verifier
from glow_api.database import get_db, get_user_by_sub
from glow_api.models import MeResponse, MeAnonymous, MeAuthenticated, SchoolSummary

router = APIRouter(tags=["identity"])

# Use HTTPBearer with auto_error=False to make auth optional
security = HTTPBearer(auto_error=False)


@router.get("/me", response_model=None)
def get_me(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Get current user information or anonymous response.

    This endpoint supports both authenticated and anonymous access:
    - No token: returns anonymous response
    - Invalid/expired token: returns 401
    - Valid token: returns authenticated response with school summaries
    """
    # No credentials provided - return anonymous
    if credentials is None:
        return MeAnonymous()

    # Try to decode token
    try:
        claims = verifier.decode(credentials.credentials)
        sub: str | None = claims.get("sub")
        if sub is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    # Get user from database
    user = get_user_by_sub(db, sub)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    sync_user_claims(db, user, claims)

    # Return authenticated response
    schools = [SchoolSummary(id=school.id, name=school.name) for school in user.schools]

    return MeAuthenticated(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        email=user.email,
        schools=schools,
    )
