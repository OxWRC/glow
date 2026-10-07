"""Resolve a git ref to a commit SHA via the GitHub REST API.

Replaces the previous ``git ls-remote``-based resolution so the tool has no
local ``git`` dependency (important for the packaged GUI, which can't assume
git is installed on the user's machine).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from glow_deploy import versions
from glow_deploy.errors import DeployError

_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_GITHUB_API = "https://api.github.com"


def _parse_owner_repo(repo_url: str) -> tuple[str, str]:
    """Extract ``(owner, repo)`` from an https or scp-style GitHub URL."""
    if "://" in repo_url:
        path = urlparse(repo_url).path
    else:
        # scp-style, e.g. git@github.com:OxWRC/glow.git
        path = repo_url.split(":", 1)[-1]

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]

    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        raise DeployError(f"could not parse owner/repo from git URL: {repo_url!r}")

    return parts[0], parts[1]


def _get_ref_sha(
    client: httpx.Client, owner: str, repo: str, ref_path: str
) -> dict | None:
    response = client.get(f"/repos/{owner}/{repo}/git/ref/{ref_path}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()["object"]


def _peel_tag(
    client: httpx.Client, owner: str, repo: str, tag_object_sha: str
) -> str | None:
    response = client.get(f"/repos/{owner}/{repo}/git/tags/{tag_object_sha}")
    if response.status_code == 404:
        return None
    response.raise_for_status()

    peeled = response.json().get("object", {})
    if peeled.get("type") == "commit":
        return peeled["sha"]
    return None


def resolve_git_commit_via_github(
    repo_url: str, ref: str, token: str | None = None
) -> str:
    """Resolve a git ref to a commit SHA.

    Precedence matches the original ``git ls-remote``-based resolver:
    peeled annotated tag > tag > branch > literal 40-char SHA.
    """
    if ref and _SHA_PATTERN.fullmatch(ref):
        return ref

    owner, repo = _parse_owner_repo(repo_url)
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(base_url=_GITHUB_API, headers=headers, timeout=10.0) as client:
        tag_object = _get_ref_sha(client, owner, repo, f"tags/{ref}")
        if tag_object is not None:
            if tag_object["type"] == "tag":
                peeled = _peel_tag(client, owner, repo, tag_object["sha"])
                if peeled:
                    return peeled
            return tag_object["sha"]

        branch_object = _get_ref_sha(client, owner, repo, f"heads/{ref}")
        if branch_object is not None:
            return branch_object["sha"]

    raise DeployError(f"could not resolve git ref: {ref}")


def list_tags_with_prefix(
    repo_url: str, prefix: str, timeout: float = 5.0
) -> list[str]:
    """All tags on repo_url starting with prefix, filtered to ones versions.parse
    accepts. Never raises — feeds page renders and a background thread, so a
    network hiccup here must never break either."""
    try:
        owner, repo = _parse_owner_repo(repo_url)
        with httpx.Client(base_url=_GITHUB_API, timeout=timeout) as client:
            response = client.get(
                f"/repos/{owner}/{repo}/git/matching-refs/tags/{prefix}"
            )
            response.raise_for_status()
            refs = [item["ref"].removeprefix("refs/tags/") for item in response.json()]
        return [ref for ref in refs if versions.parse(ref, prefix) is not None]
    except Exception:
        return []


SUPPRESSION_RULES_PATH = "api/src/glow_api/suppression.yaml"


def _rules_blob_sha(
    client: httpx.Client, owner: str, repo: str, ref: str
) -> str | None:
    """Blob SHA of the rules file at ref, or None if the file is absent there.

    A contents-404 only means "absent" once the ref itself is confirmed to
    exist; otherwise (unknown ref, invisible repo) raise so the caller fails
    closed.
    """
    client.get(f"/repos/{owner}/{repo}/commits/{ref}").raise_for_status()
    response = client.get(
        f"/repos/{owner}/{repo}/contents/{SUPPRESSION_RULES_PATH}",
        params={"ref": ref},
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()["sha"]


def suppression_rules_changed(
    repo_url: str, from_ref: str, to_ref: str, timeout: float = 10.0
) -> bool:
    """Whether the suppression rules file differs between two refs.

    Compares git blob SHAs (no diff-size limits). Fails closed: any lookup
    error counts as changed, since a wrong "unchanged" could release the same
    data under two coarsenings.
    """
    try:
        owner, repo = _parse_owner_repo(repo_url)
        headers = {"Accept": "application/vnd.github+json"}
        with httpx.Client(
            base_url=_GITHUB_API, headers=headers, timeout=timeout
        ) as client:
            return _rules_blob_sha(client, owner, repo, from_ref) != _rules_blob_sha(
                client, owner, repo, to_ref
            )
    except Exception:
        return True
