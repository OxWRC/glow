"""Point-in-time archives ("vintages") of the dataset.

/query and /export compute against the current vintage instead of the live
DataStore. This is what closes the differencing hole: two calls between
vintage cuts return byte-identical suppressed output, because they're
reading the same frozen frame, not two different live reads a caller could
subtract from each other.

A local directory (settings.VINTAGES_DIR) is the only source of truth: no DB
table. A vintage is identified by a lexicographically-sortable timestamp
prefix, so "latest" is just "last in a sorted listing" - see
get_latest_vintage_prefix. Layout: <root>/vintages/<prefix>/frame.parquet and
meta.json. A vintage is published by renaming a fully written ".tmp-" directory
into place, so a crashed cut is never listed or served.
"""

import json
import os
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from fastapi import Depends, HTTPException

from glow_api import audit as audit_module
from glow_api.data import DataFrameWithWhitelists, DataStore
from glow_api.export import dataset_version
from glow_api.models import VintageStatus
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
    """No vintages directory configured - main.py maps this to a 503."""


def get_vintages_dir() -> Path:
    """FastAPI dependency: the configured root, or fail closed."""
    if not settings.VINTAGES_DIR:
        raise VintageStorageNotConfigured("GLOW_VINTAGES_DIR is not set")
    return Path(settings.VINTAGES_DIR)


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


_PRIVATE_DIR = 0o700
_PRIVATE_FILE = 0o600


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _PRIVATE_FILE)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _write_meta(directory: Path, meta: dict) -> None:
    _write_private(directory / "meta.json", json.dumps(meta, default=str).encode())


def _serialize_observed_periods(observed_periods: dict) -> dict:
    return {(_NULL_KEY if k is None else k): v for k, v in observed_periods.items()}


def _deserialize_observed_periods(raw: dict) -> dict:
    return {(None if k == _NULL_KEY else k): v for k, v in raw.items()}


class _LatestPrefixCache:
    """TTL-cached "last vintage prefix", explicitly invalidated by cut_vintage."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._prefix: Optional[str] = None
        self._fetched_at: float = 0.0
        self._loaded = False

    def get(self, root: Path) -> Optional[str]:
        with self._lock:
            now = time.monotonic()
            if self._loaded and (now - self._fetched_at) < self._ttl:
                return self._prefix
            self._prefix = _list_latest_prefix(root)
            self._fetched_at = now
            self._loaded = True
            return self._prefix

    def invalidate(self) -> None:
        with self._lock:
            self._loaded = False


class VintageCache:
    """Every frame this process has been asked to serve, keyed by prefix.

    Never evicts: vintage files are immutable from the app's perspective once
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


def _list_prefixes(root: Path) -> list[str]:
    vintages_root = root / "vintages"
    if not vintages_root.is_dir():
        return []
    # Dot-prefixed names are in-flight (".tmp-") cuts, never vintages.
    return sorted(
        f"vintages/{entry.name}/"
        for entry in vintages_root.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    )


def _list_latest_prefix(root: Path) -> Optional[str]:
    prefixes = _list_prefixes(root)
    return prefixes[-1] if prefixes else None


def _read_meta(root: Path, prefix: str) -> dict:
    return json.loads((root / prefix / "meta.json").read_bytes())


def _vintage_info_from_meta(prefix: str, meta: dict) -> VintageInfo:
    created_at = datetime.fromisoformat(meta["created_at"])
    if created_at.tzinfo is None:
        # Naive timestamps from data are interpreted as UTC.
        created_at = created_at.replace(tzinfo=timezone.utc)
    return VintageInfo(
        prefix=prefix,
        created_at=created_at,
        dataset_version=meta["dataset_version"],
        row_count=meta["row_count"],
        created_by_user_id=meta.get("created_by_user_id"),
        created_by_username=meta.get("created_by_username"),
    )


# Cuts run one at a time. Overlapping manual and scheduled cuts would
# otherwise race on the shared DataStore: the older ODK fetch could be
# published last and frozen under the newer cut's timestamp.
_cut_lock = threading.Lock()


def cut_vintage(
    datastore: DataStore,
    root: Path,
    created_by: Optional[tuple[int, str]] = None,
) -> VintageInfo:
    """Fetch the latest data from ODK, freeze it, and write it as a new vintage.

    The cut is the only regular full fetch (see DataStore), so it raises
    ODKFetchError rather than freezing stale data if ODK can't be reached.

    created_by is (user_id, username) for an admin-triggered cut, or None
    for a scheduler-triggered one - mirrors ApiKey.created_by_user_id's
    existing nullable-FK convention for "no user", just without a live FK.
    """
    with _cut_lock:
        return _cut_vintage(datastore, root, created_by)


def _cut_vintage(
    datastore: DataStore,
    root: Path,
    created_by: Optional[tuple[int, str]],
) -> VintageInfo:
    datastore.refresh(strict=True)
    frozen = datastore.to_frozen()
    created_at = datetime.now(timezone.utc)
    prefix = _vintage_prefix(created_at)
    version = dataset_version(frozen.df)
    row_count = len(frozen.df)

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

    # Publish atomically: write everything into a dot-prefixed temp dir, then
    # rename it into place. Listing ignores dot-prefixed names, so a crash
    # mid-cut can never leave a half-written vintage that looks like latest.
    vintages_root = root / "vintages"
    vintages_root.mkdir(mode=_PRIVATE_DIR, parents=True, exist_ok=True)
    vintages_root.chmod(_PRIVATE_DIR)
    final_dir = root / prefix
    tmp_dir = vintages_root / f".tmp-{final_dir.name}"
    tmp_dir.mkdir(mode=_PRIVATE_DIR)
    try:
        frame_path = tmp_dir / "frame.parquet"
        frozen.df.to_parquet(frame_path)
        frame_path.chmod(_PRIVATE_FILE)
        _write_meta(tmp_dir, meta)
        os.rename(tmp_dir, final_dir)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    # A manual cut must take effect on the very next request, not after the
    # latest-prefix TTL window. The frame cache needs no invalidation here:
    # this prefix is brand new, so it's already a guaranteed miss there.
    _latest_prefix_cache.invalidate()

    return _vintage_info_from_meta(prefix, meta)


def scheduled_cut_vintage(datastore: DataStore) -> VintageInfo:
    """Scheduler job entry point. Bypasses HTTP middleware, so it writes its
    own audit line directly - same reasoning as the CLI's _audit_cli."""
    info = cut_vintage(datastore, get_vintages_dir(), created_by=None)
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


def list_vintages(root: Path) -> list[VintageInfo]:
    return [
        _vintage_info_from_meta(prefix, _read_meta(root, prefix))
        for prefix in _list_prefixes(root)
    ]


def get_latest_vintage_prefix(root: Path) -> Optional[str]:
    return _latest_prefix_cache.get(root)


def get_vintage_status(root: Path) -> VintageStatus:
    """Whether a vintage exists, and when the latest was cut - read off its
    prefix, so it costs no more than the TTL-cached listing."""
    prefix = get_latest_vintage_prefix(root)
    if prefix is None:
        return VintageStatus(available=False)
    timestamp = prefix.removeprefix("vintages/").rsplit("-", 1)[0]
    created_at = datetime.strptime(timestamp, PREFIX_TIME_FORMAT).replace(
        tzinfo=timezone.utc
    )
    return VintageStatus(available=True, created_at=created_at)


def load_vintage_frame(prefix: str, root: Path) -> DataFrameWithWhitelists:
    df = pd.read_parquet(root / prefix / "frame.parquet")
    meta = _read_meta(root, prefix)
    return DataFrameWithWhitelists(
        df=df,
        categorical_whitelist=meta["categorical_whitelist"],
        numerical_whitelist=meta["numerical_whitelist"],
        metadata=meta["metadata"],
        observed_periods=_deserialize_observed_periods(meta["observed_periods"]),
    )


def get_current_vintage_frame(
    root: Path = Depends(get_vintages_dir),
) -> DataFrameWithWhitelists:
    """FastAPI dependency: the frame /query and /export must read instead of
    datastore.to_frozen(). Fails closed (503) rather than falling back to
    live data - a silent fallback would reopen the differencing hole this
    module exists to close, invisibly and inconsistently before/after the
    first cut.
    """
    prefix = get_latest_vintage_prefix(root)
    if prefix is None:
        raise HTTPException(status_code=503, detail="No vintage available yet")
    return _vintage_cache.get(prefix, lambda: load_vintage_frame(prefix, root))
