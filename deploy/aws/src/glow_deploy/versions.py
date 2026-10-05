"""Prefix-aware semantic-version parsing and comparison.

Serves two independent tag families over the same repo: ``v*`` (the deployed
app's release tags) and ``gui-v*`` (this deploy GUI's own release tags) —
same comparison logic, different prefix, never confused with each other since
each prefix only ever matches its own family.
"""

from __future__ import annotations

import re

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)$")


def parse(ref: str, prefix: str) -> tuple[int, int, int] | None:
    """f"{prefix}X.Y.Z" -> (X,Y,Z); anything else (wrong prefix, a
    git-describe dirty suffix like -5-gabcdef, garbage) -> None. Exact
    match only — this is also what naturally excludes drifted/advanced-ref
    deployments from auto-tracking, since their live-reported version won't
    be a clean tag."""
    if not ref.startswith(prefix):
        return None
    match = _VERSION_RE.fullmatch(ref[len(prefix) :])
    return tuple(int(group) for group in match.groups()) if match else None


def highest(available_refs: list[str], prefix: str) -> str | None:
    """Highest-parsing ref among available_refs, or None if none parse."""
    parsed = [(parse(ref, prefix), ref) for ref in available_refs]
    parsed = [(version, ref) for version, ref in parsed if version is not None]
    return max(parsed)[1] if parsed else None


def classify(current_ref: str, available_refs: list[str], prefix: str) -> dict | None:
    """None if current_ref doesn't parse with this prefix. Otherwise:
    {"current": "v1.2.0", "update_to": "v1.4.0" | None, "upgrade_to": "v2.1.0" | None}
    - update_to: highest parsed ref sharing current's major, > current (or None)
    - upgrade_to: highest parsed ref with major > current's major (or None)
    Both independent, can both be non-None simultaneously.
    """
    current = parse(current_ref, prefix)
    if current is None:
        return None

    same_major = [
        ref for ref in available_refs if (parse(ref, prefix) or (-1,))[0] == current[0]
    ]
    higher_major = [
        ref for ref in available_refs if (parse(ref, prefix) or (-1,))[0] > current[0]
    ]

    update_candidate = highest(same_major, prefix)
    if update_candidate is not None and parse(update_candidate, prefix) <= current:
        update_candidate = None

    return {
        "current": current_ref,
        "update_to": update_candidate,
        "upgrade_to": highest(higher_major, prefix),
    }
