from fastapi import APIRouter

# Password-based /auth/login was removed with Cognito auth (Task 2 of the
# Cognito migration): its only dependency, authenticate_user, is gone.
# Task 4 adds the dev-bypass login route back onto this router.
router = APIRouter(prefix="/auth", tags=["auth"])
