"""Period-oriented multi-variable query endpoint."""

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from glow_api import request_context
from glow_api.auth import get_optional_school_user
from glow_api.canonical_query import normalize_query
from glow_api.query_execution import execute_query, compute_query_etag
from glow_api.data import DataStore, get_datastore
from glow_api.database import get_db
from glow_api.settings import settings
from sqlalchemy.orm import Session

router = APIRouter(prefix="/query", tags=["query"])

# Use HTTPBearer with auto_error=False to make auth optional for dataset-scoped queries
security = HTTPBearer(auto_error=False)


@router.get("", response_model=None, include_in_schema=False)
@router.get("/", response_model=None)
def query_get(
    response: Response,
    v: list[str] = Query(default=[], description="Variable names (repeatable)"),
    d: list[str] = Query(default=[], description="Dimension names (repeatable)"),
    variable_prefix: list[str] = Query(
        default=[], description="Variable prefixes (repeatable)"
    ),
    school_id: Optional[int] = Query(
        None, description="Optional school ID for school-scoped query"
    ),
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
    datastore: DataStore = Depends(get_datastore),
) -> dict:
    """Execute a new period-oriented multi-variable query.

    This endpoint supports:
    - Dataset-scoped queries (no school_id, anonymous access OK)
    - School-scoped queries (with school_id, requires authorization)
    - Variable selection via repeated 'v' params or 'variable_prefix' params
    - Dimension selection via repeated 'd' params
    - Period-organized results with independent suppression per period
    - ETag-based caching with If-None-Match support

    Returns:
        NewQueryResponse with period-organized multi-variable results
    """
    # Normalize query parameters
    canonical = normalize_query(
        school_id=school_id,
        v=v,
        d=d,
        variable_prefix=variable_prefix,
    )

    # Get dataset version for ETag
    dfwl = datastore.to_frozen()
    dataset_version = dfwl.metadata.get("_etag", "unknown")

    # Compute ETag
    etag = compute_query_etag(
        query=canonical,
        dataset_version=dataset_version,
        api_version=settings.APP_VERSION,
    )

    # Check If-None-Match
    if if_none_match and if_none_match == etag:
        # Data hasn't changed
        request_context.record_event("query_executed", etag_matched=True, computed=False)
        response.status_code = status.HTTP_304_NOT_MODIFIED
        return {}

    # Set ETag header
    response.headers["ETag"] = etag

    # If school_id is provided, check authorization
    if school_id is not None:
        _user, school = get_optional_school_user(credentials, db, school_id)

        # Filter data to this school, joining on the raw ODK "school" value
        # (odk_school_id), not the admin-facing display name. A school not
        # yet linked to ODK data (odk_school_id is None) matches nothing.
        df = dfwl.df
        if "school" in df.columns and school.odk_school_id is not None:
            df = df[df["school"] == school.odk_school_id]
        else:
            df = df.iloc[0:0]  # no data loaded yet, or school not linked to ODK data
        school_name = school.odk_school_id
    else:
        request_context.record_event("auth_assessed", outcome="anonymous", success=None, school_id=None)
        # Dataset-scoped query
        df = dfwl.df
        school_name = None

    # Data should already be normalized with period_id column from DataStore
    # But verify it exists
    if "period_id" not in df.columns:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Data normalization error: period_id column missing",
        )

    # Get numerical whitelist and observed periods from pre-computed metadata
    numerical_whitelist = dfwl.numerical_whitelist
    observed_periods = dfwl.observed_periods.get(school_name, [])

    # Get form metadata for version compatibility
    form_metadata = dfwl.metadata

    # Execute query
    _t0 = time.perf_counter()
    result = execute_query(
        df=df,
        query=canonical,
        numerical_whitelist=numerical_whitelist,
        observed_periods=observed_periods,
        min_n=settings.MIN_N,
        form_metadata=form_metadata,
    )
    request_context.record_event(
        "query_executed",
        etag_matched=False,
        computed=True,
        duration_s=round(time.perf_counter() - _t0, 4),
        input_rows=len(df),
        periods_returned=len(result.get("periods", [])),
        variables_returned=len(result.get("variables", [])),
    )

    return result
