"""Point-in-time archives ("vintages") of the dataset.

/query and /export compute against the current vintage instead of the live
DataStore. This is what closes the differencing hole: two calls between
vintage cuts return byte-identical suppressed output, because they're
reading the same frozen frame, not two different live reads a caller could
subtract from each other.

S3 is the only source of truth: no DB table. A vintage is identified by a
lexicographically-sortable timestamp prefix, so "latest" is just "last in a
sorted listing" - see get_latest_vintage_prefix.
"""

import io
import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd
from fastapi import Depends, HTTPException

from glow_api import audit as audit_module
from glow_api.data import DataFrameWithWhitelists, DataStore
from glow_api.export import dataset_version
from glow_api.settings import settings

PREFIX_TIME_FORMAT = "%Y%m%dT%H%M%S.%fZ"
LATEST_PREFIX_TTL_SECONDS = 60

# JSON object keys must be strings; observed_periods uses a `None` key for
# the dataset-wide bucket. Round-trip it through this sentinel explicitly
# rather than relying on json.dumps's implicit None -> "null" coercion,
# which doesn't reverse itself on load.
_NULL_KEY = "\x00null\x00"


@dataclass
class VintageInfo:
    prefix: str
    created_at: datetime
    dataset_version: str
    row_count: int
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None


class VintageStorageNotConfigured(RuntimeError):
    """No bucket configured - main.py maps this to a 503."""


def _bucket(bucket: Optional[str]) -> str:
    bucket = bucket or settings.VINTAGES_S3_BUCKET
    if not bucket:
        raise VintageStorageNotConfigured("GLOW_VINTAGES_S3_BUCKET is not set")
    return bucket


def _vintage_prefix(created_at: datetime) -> str:
    # Microsecond precision keeps ordering correct in practice (two real
    # cut_vintage calls - even a double-click "cut now" - essentially never
    # land on the same microsecond). The random suffix is only a guard
    # against the pathological case where they do: it guarantees the keys
    # still differ (no silent overwrite), at the cost of an arbitrary - but
    # otherwise harmless - tiebreak for "which is latest" in that one
    # unreachable-in-practice collision.
    disambiguator = secrets.token_hex(3)
    return f"vintages/{created_at.strftime(PREFIX_TIME_FORMAT)}-{disambiguator}/"


def _frame_key(prefix: str) -> str:
    return f"{prefix}frame.parquet"


def _meta_key(prefix: str) -> str:
    return f"{prefix}meta.json"


def _serialize_observed_periods(observed_periods: dict) -> dict:
    return {
        (_NULL_KEY if k is None else k): v for k, v in observed_periods.items()
    }


def _deserialize_observed_periods(raw: dict) -> dict:
    return {
        (None if k == _NULL_KEY else k): v for k, v in raw.items()
    }


class _LatestPrefixCache:
    """TTL-cached "last vintage prefix", explicitly invalidated by cut_vintage."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._prefix: Optional[str] = None
        self._fetched_at: float = 0.0
        self._loaded = False

    def get(self, s3_client: Any, bucket: str) -> Optional[str]:
        with self._lock:
            now = time.monotonic()
            if self._loaded and (now - self._fetched_at) < self._ttl:
                return self._prefix
            self._prefix = _list_latest_prefix(s3_client, bucket)
            self._fetched_at = now
            self._loaded = True
            return self._prefix

    def invalidate(self) -> None:
        with self._lock:
            self._loaded = False


class VintageCache:
    """Every frame this process has been asked to serve, keyed by prefix.

    Never evicts: S3 objects are immutable from the app's perspective once
    written (a vintage's prefix is never reused), so there's no reason to
    throw an old entry away - at most ~60 vintages over the project's
    5-year life, a few MB each, the whole history costs nothing to keep in
    memory. "Latest" is just whichever prefix get_latest_vintage_prefix
    currently points at; this cache doesn't track that itself.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: dict[str, DataFrameWithWhitelists] = {}

    def get(
        self, prefix: str, load: Callable[[], DataFrameWithWhitelists]
    ) -> DataFrameWithWhitelists:
        with self._lock:
            if prefix not in self._frames:
                self._frames[prefix] = load()
            return self._frames[prefix]

    def clear(self) -> None:
        """Test-only: reset between test cases. Not used by cut_vintage -
        a newly cut vintage always has a fresh prefix, so it's already a
        cache miss; there's nothing to invalidate for correctness."""
        with self._lock:
            self._frames = {}


_latest_prefix_cache = _LatestPrefixCache(LATEST_PREFIX_TTL_SECONDS)
_vintage_cache = VintageCache()


def get_vintage_cache() -> VintageCache:
    return _vintage_cache


def _list_prefixes(s3_client: Any, bucket: str) -> list[str]:
    response = s3_client.list_objects_v2(
        Bucket=bucket, Prefix="vintages/", Delimiter="/"
    )
    return sorted(entry["Prefix"] for entry in response.get("CommonPrefixes", []))


def _list_latest_prefix(s3_client: Any, bucket: str) -> Optional[str]:
    prefixes = _list_prefixes(s3_client, bucket)
    return prefixes[-1] if prefixes else None


def _read_meta(s3_client: Any, bucket: str, prefix: str) -> dict:
    obj = s3_client.get_object(Bucket=bucket, Key=_meta_key(prefix))
    return json.loads(obj["Body"].read())


def _vintage_info_from_meta(prefix: str, meta: dict) -> VintageInfo:
    return VintageInfo(
        prefix=prefix,
        created_at=datetime.fromisoformat(meta["created_at"]),
        dataset_version=meta["dataset_version"],
        row_count=meta["row_count"],
        created_by_user_id=meta.get("created_by_user_id"),
        created_by_username=meta.get("created_by_username"),
    )


def cut_vintage(
    datastore: DataStore,
    s3_client: Any,
    created_by: Optional[tuple[int, str]] = None,
    bucket: Optional[str] = None,
) -> VintageInfo:
    """Freeze the current DataStore frame and upload it as a new vintage.

    created_by is (user_id, username) for an admin-triggered cut, or None
    for a scheduler-triggered one - mirrors ApiKey.created_by_user_id's
    existing nullable-FK convention for "no user", just without a live FK.
    """
    bucket = _bucket(bucket)
    frozen = datastore.to_frozen()
    created_at = datetime.now(timezone.utc)
    prefix = _vintage_prefix(created_at)
    version = dataset_version(frozen.df)
    row_count = len(frozen.df)

    frame_buf = io.BytesIO()
    frozen.df.to_parquet(frame_buf)
    s3_client.put_object(
        Bucket=bucket, Key=_frame_key(prefix), Body=frame_buf.getvalue()
    )

    created_by_user_id, created_by_username = created_by if created_by else (None, None)
    meta = {
        "created_at": created_at.isoformat(),
        "dataset_version": version,
        "row_count": row_count,
        "categorical_whitelist": frozen.categorical_whitelist,
        "numerical_whitelist": frozen.numerical_whitelist,
        "observed_periods": _serialize_observed_periods(frozen.observed_periods),
        "metadata": frozen.metadata,
        "created_by_user_id": created_by_user_id,
        "created_by_username": created_by_username,
    }
    s3_client.put_object(
        Bucket=bucket,
        Key=_meta_key(prefix),
        Body=json.dumps(meta, default=str).encode(),
    )

    # A manual cut must take effect on the very next request, not after the
    # latest-prefix TTL window. The frame cache needs no invalidation here:
    # this prefix is brand new, so it's already a guaranteed miss there.
    _latest_prefix_cache.invalidate()

    return _vintage_info_from_meta(prefix, meta)


def scheduled_cut_vintage(datastore: DataStore) -> VintageInfo:
    """Scheduler job entry point. Bypasses HTTP middleware, so it writes its
    own audit line directly - same reasoning as the CLI's _audit_cli."""
    info = cut_vintage(datastore, get_s3_client(), created_by=None)
    audit_module.write_audit_line(
        {
            "actor": "scheduler",
            "action": "vintage_cut",
            "ts": datetime.now(timezone.utc).isoformat(),
            "prefix": info.prefix,
            "row_count": info.row_count,
        }
    )
    return info


def list_vintages(s3_client: Any, bucket: Optional[str] = None) -> list[VintageInfo]:
    bucket = _bucket(bucket)
    prefixes = _list_prefixes(s3_client, bucket)
    return [
        _vintage_info_from_meta(prefix, _read_meta(s3_client, bucket, prefix))
        for prefix in prefixes
    ]


def get_latest_vintage_prefix(s3_client: Any, bucket: Optional[str] = None) -> Optional[str]:
    return _latest_prefix_cache.get(s3_client, _bucket(bucket))


def load_vintage_frame(
    prefix: str, s3_client: Any, bucket: Optional[str] = None
) -> DataFrameWithWhitelists:
    bucket = _bucket(bucket)
    frame_obj = s3_client.get_object(Bucket=bucket, Key=_frame_key(prefix))
    df = pd.read_parquet(io.BytesIO(frame_obj["Body"].read()))
    meta = _read_meta(s3_client, bucket, prefix)
    return DataFrameWithWhitelists(
        df=df,
        categorical_whitelist=meta["categorical_whitelist"],
        numerical_whitelist=meta["numerical_whitelist"],
        metadata=meta["metadata"],
        observed_periods=_deserialize_observed_periods(meta["observed_periods"]),
    )


def get_s3_client() -> Any:
    import boto3

    return boto3.client("s3", region_name=settings.AWS_REGION)


def get_current_vintage_frame(
    s3_client: Any = Depends(get_s3_client),
) -> DataFrameWithWhitelists:
    """FastAPI dependency: the frame /query and /export must read instead of
    datastore.to_frozen(). Fails closed (503) rather than falling back to
    live data - a silent fallback would reopen the differencing hole this
    module exists to close, invisibly and inconsistently before/after the
    first cut.
    """
    prefix = get_latest_vintage_prefix(s3_client)
    if prefix is None:
        raise HTTPException(status_code=503, detail="No vintage available yet")
    return _vintage_cache.get(prefix, lambda: load_vintage_frame(prefix, s3_client))
