from fastapi import APIRouter

# Password-based /auth/login was removed with Cognito auth (Task 2 of the
# Cognito migration): its only dependency, authenticate_user, is gone,
# leaving this router with no routes.
#
# The demo-mode login (Task 4) deliberately does NOT attach here: this
# router is registered unconditionally in main.py, so any route added to it
# is always live. The demo route instead lives in its own demo.router
# (routers/demo.py), which main.py only registers inside
# `if settings.DEMO_MODE:` - keeping it out of the route table
# entirely, not just guarded at request time, when the flag is off.
router = APIRouter(prefix="/auth", tags=["auth"])
