import secrets

from fastapi import APIRouter, Depends, HTTPException, status

from glow_api import request_context
from glow_api.auth import require_api_key
from glow_api.data import DataStore, get_datastore
from glow_api.export import (
    ExportCache,
    UnlistedDemographicError,
    build_export,
    dataset_version,
    get_export_cache,
    get_suppression_rules,
)
from glow_api.metadata_models import ApiKey
from glow_api.models import ExportResponse
from glow_api.suppression import Rules

router = APIRouter(tags=["export"])


@router.get("/export", response_model=ExportResponse)
def pseudonymous_export(
    _: ApiKey = Depends(require_api_key),
    datastore: DataStore = Depends(get_datastore),
    rules: Rules = Depends(get_suppression_rules),
    cache: ExportCache = Depends(get_export_cache),
) -> dict:
    """The whole dataset, suppressed and pseudonymised. No filters by design."""
    frozen = datastore.to_frozen()
    if frozen.df.empty:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Data not loaded yet",
        )
    version = dataset_version(frozen.df)
    try:
        payload = cache.get(
            version,
            lambda: build_export(frozen, rules, secrets.token_bytes(32), version),
        )
    except UnlistedDemographicError as exc:
        request_context.record_event("export_blocked", columns=exc.columns)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export blocked: {exc}",
        )
    request_context.record_event(
        "export_served",
        dataset_version=version,
        suppressed=payload["suppressed"],
        rows=len(payload["rows"]),
    )
    return payload
