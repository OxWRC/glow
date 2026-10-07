"""Pseudonymous whole-dataset export.

Suppression (suppression.py) runs first. Then IDs are replaced with salted
hashes of their coarsened values, so a school or class that suppression
collapsed to "*" also collapses to one id. Output columns are an allowlist.
One build is cached per dataset version, and each build draws a fresh salt
that lives only in memory, so ids never match across versions or restarts.
"""

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from glow_api.data import DataFrameWithWhitelists
from glow_api.settings import settings
from glow_api.suppression import Rules, label, load_rules, suppress

ID_COLUMNS = {"uid": "student_id", "school": "school_id", "class": "class_id"}


class UnlistedDemographicError(RuntimeError):
    def __init__(self, columns: list[str]):
        super().__init__(
            "demographic columns not covered by the suppression rules: "
            + ", ".join(columns)
        )
        self.columns = columns


def dataset_version(df: pd.DataFrame) -> str:
    digest = hashlib.sha256(",".join(map(str, df.columns)).encode())
    # astype(str): list/dict cells (e.g. ODK geopoints) are unhashable.
    digest.update(
        pd.util.hash_pandas_object(df.astype(str), index=False).values.tobytes()
    )
    return digest.hexdigest()[:16]


def _is_demographic(column: str) -> bool:
    variable = column.split("__", 1)[1] if "__" in column else column
    prefix, _, rest = variable.partition("_")
    return bool(rest) and prefix in settings.DATA_DEMOGRAPHIC_PREFIXES


def unlisted_demographics(frozen: DataFrameWithWhitelists, rules: Rules) -> list[str]:
    """Columns that would be released without suppression governing them.
    school/class must be dimensions too: their ids are in every row."""
    return sorted(
        c
        for c in frozen.df.columns
        if (
            c in frozen.categorical_whitelist
            or _is_demographic(c)
            or (c in ID_COLUMNS and c != "uid")
        )
        and c not in rules.dimensions
    )


def _pseudonym(salt: bytes, column: str, value: str) -> str:
    return hashlib.sha256(salt + f"{column}:{value}".encode()).hexdigest()[:16]


def build_export(
    frozen: DataFrameWithWhitelists,
    rules: Rules,
    salt: bytes,
    version: str,
) -> dict[str, Any]:
    unlisted = unlisted_demographics(frozen, rules)
    if unlisted:
        raise UnlistedDemographicError(unlisted)

    payload: dict[str, Any] = {
        "dataset_version": version,
        "rules_sha256": rules.sha256,
        "min_n": rules.min_n,
        "generated_at": datetime.now(timezone.utc),
        "suppressed": False,
        "coarsening": {},
        "rows": [],
    }
    suppressed, coarsening = suppress(frozen.df, rules, rules.min_n)
    payload["coarsening"] = coarsening
    if suppressed is None:
        payload["suppressed"] = True
        return payload
    if suppressed.empty:
        return payload

    out = pd.DataFrame(index=suppressed.index)
    out["student_id"] = suppressed["uid"].map(
        lambda v: _pseudonym(salt, "uid", label(v))
    )
    school = (
        suppressed["school"].map(label)
        if "school" in suppressed
        else pd.Series("", index=suppressed.index)
    )
    if "school" in suppressed:
        out["school_id"] = school.map(lambda v: _pseudonym(salt, "school", v))
    if "class" in suppressed:
        out["class_id"] = [
            _pseudonym(salt, "class", f"{s}\x1f{label(c)}")
            for s, c in zip(school, suppressed["class"])
        ]
    if "period_id" in suppressed:
        out["period_id"] = suppressed["period_id"]
    for column in rules.dimensions:
        if column in suppressed and column not in ID_COLUMNS:
            out[column] = suppressed[column]
    for column in frozen.numerical_whitelist:
        if column in suppressed:
            out[column] = suppressed[column]
    payload["rows"] = json.loads(out.to_json(orient="records"))
    return payload


class ExportCache:
    """One cached build, keyed by dataset version."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._version: str | None = None
        self._payload: dict[str, Any] | None = None

    def get(self, version: str, build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        # Holding the lock across build() makes concurrent requests wait for
        # one build instead of each doing (and salting) their own.
        with self._lock:
            if self._payload is None or self._version != version:
                self._payload = build()
                self._version = version
            return self._payload


_cache = ExportCache()
# Loaded at import: an invalid rules file stops the API from starting.
_rules = load_rules()


def get_export_cache() -> ExportCache:
    return _cache


def get_suppression_rules() -> Rules:
    return _rules
