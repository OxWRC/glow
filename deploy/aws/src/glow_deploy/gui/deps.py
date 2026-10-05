"""Shared FastAPI dependencies for routes that need a signed-in AWS session."""

from __future__ import annotations

import time

from fastapi import HTTPException, Request

from glow_deploy import core, github_api

_RELEASE_TAGS_CACHE_TTL_SECONDS = 600


def require_session(request: Request):
    """Return the signed-in boto3.Session, or redirect to /signin if none."""
    session = request.app.state.session
    if session is None:
        raise HTTPException(status_code=303, headers={"Location": "/signin"})
    return session


def find_deployment(request: Request, domain: str) -> dict:
    """Look up a single deployment by domain, or 404."""
    session = require_session(request)
    deployments = core.list_deployments(session, request.app.state.region)
    for deployment in deployments:
        if deployment["domain"] == domain:
            return deployment
    raise HTTPException(
        status_code=404, detail=f"no deployment found for domain {domain!r}"
    )


def get_cached_release_tags(request: Request) -> list[str]:
    """The core app's v* release tags, cached on app.state — shared across
    page renders since GitHub's unauthenticated API is rate-limited to
    60 req/hr and home.html auto-refreshes every 30s. Failures are cached
    too (an empty list still gets stamped with the current time) so a
    slow/down GitHub doesn't force every render in the TTL window to
    re-block on a fresh timeout."""
    state = request.app.state
    cache = getattr(state, "release_tags_cache", None)  # (fetched_at, tags)
    if cache is None or time.time() - cache[0] > _RELEASE_TAGS_CACHE_TTL_SECONDS:
        tags = github_api.list_tags_with_prefix(
            core.DEFAULT_GIT_REPO_URL, core.CORE_TAG_PREFIX
        )
        state.release_tags_cache = (time.time(), tags)
        return tags
    return cache[1]
