import pytest

import glow_deploy.github_api as github_api


class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 404:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get(self, path):
        return self._responses[path]


def _install_fake_responses(monkeypatch, responses):
    monkeypatch.setattr(
        github_api.httpx, "Client", lambda **kwargs: _FakeClient(responses)
    )


REPO = "https://github.com/OxWRC/glow.git"


def test_resolve_git_commit_via_github_short_circuits_a_full_sha():
    sha = "a" * 40
    assert github_api.resolve_git_commit_via_github(REPO, sha) == sha


def test_resolve_git_commit_via_github_prefers_peeled_annotated_tag_commit(
    monkeypatch,
):
    tag_object_sha = "1111111111111111111111111111111111111111"
    peeled_commit_sha = "2222222222222222222222222222222222222222"
    _install_fake_responses(
        monkeypatch,
        {
            "/repos/OxWRC/glow/git/ref/tags/v1.2.3": _FakeResponse(
                200, {"object": {"sha": tag_object_sha, "type": "tag"}}
            ),
            f"/repos/OxWRC/glow/git/tags/{tag_object_sha}": _FakeResponse(
                200, {"object": {"sha": peeled_commit_sha, "type": "commit"}}
            ),
        },
    )

    assert github_api.resolve_git_commit_via_github(REPO, "v1.2.3") == peeled_commit_sha


def test_resolve_git_commit_via_github_uses_lightweight_tag_commit_directly(
    monkeypatch,
):
    commit_sha = "3333333333333333333333333333333333333333"
    _install_fake_responses(
        monkeypatch,
        {
            "/repos/OxWRC/glow/git/ref/tags/v1.2.3": _FakeResponse(
                200, {"object": {"sha": commit_sha, "type": "commit"}}
            ),
        },
    )

    assert github_api.resolve_git_commit_via_github(REPO, "v1.2.3") == commit_sha


def test_resolve_git_commit_via_github_prefers_tag_over_branch_of_the_same_name(
    monkeypatch,
):
    tag_commit_sha = "4444444444444444444444444444444444444444"
    branch_commit_sha = "5555555555555555555555555555555555555555"
    _install_fake_responses(
        monkeypatch,
        {
            "/repos/OxWRC/glow/git/ref/tags/release": _FakeResponse(
                200, {"object": {"sha": tag_commit_sha, "type": "commit"}}
            ),
            "/repos/OxWRC/glow/git/ref/heads/release": _FakeResponse(
                200, {"object": {"sha": branch_commit_sha, "type": "commit"}}
            ),
        },
    )

    assert github_api.resolve_git_commit_via_github(REPO, "release") == tag_commit_sha


def test_resolve_git_commit_via_github_falls_back_to_branch_when_no_tag_matches(
    monkeypatch,
):
    branch_commit_sha = "6666666666666666666666666666666666666666"
    _install_fake_responses(
        monkeypatch,
        {
            "/repos/OxWRC/glow/git/ref/tags/main": _FakeResponse(404),
            "/repos/OxWRC/glow/git/ref/heads/main": _FakeResponse(
                200, {"object": {"sha": branch_commit_sha, "type": "commit"}}
            ),
        },
    )

    assert github_api.resolve_git_commit_via_github(REPO, "main") == branch_commit_sha


def test_resolve_git_commit_via_github_raises_when_ref_not_found(monkeypatch):
    _install_fake_responses(
        monkeypatch,
        {
            "/repos/OxWRC/glow/git/ref/tags/missing": _FakeResponse(404),
            "/repos/OxWRC/glow/git/ref/heads/missing": _FakeResponse(404),
        },
    )

    with pytest.raises(github_api.DeployError, match="could not resolve git ref"):
        github_api.resolve_git_commit_via_github(REPO, "missing")


@pytest.mark.parametrize(
    "repo_url,expected",
    [
        ("https://github.com/OxWRC/glow.git", ("OxWRC", "glow")),
        ("https://github.com/OxWRC/glow", ("OxWRC", "glow")),
        ("git@github.com:OxWRC/glow.git", ("OxWRC", "glow")),
    ],
)
def test_parse_owner_repo_handles_https_and_scp_style_urls(repo_url, expected):
    assert github_api._parse_owner_repo(repo_url) == expected


def test_parse_owner_repo_rejects_urls_without_a_repo_path():
    with pytest.raises(github_api.DeployError, match="could not parse owner/repo"):
        github_api._parse_owner_repo("https://github.com/OxWRC")


# ---------------------------------------------------------------------------
# list_tags_with_prefix
# ---------------------------------------------------------------------------


def test_list_tags_with_prefix_returns_matching_clean_tags(monkeypatch):
    _install_fake_responses(
        monkeypatch,
        {
            "/repos/OxWRC/glow/git/matching-refs/tags/v": _FakeResponse(
                200,
                [
                    {"ref": "refs/tags/v1.0.0", "object": {"sha": "a" * 40}},
                    {"ref": "refs/tags/v1.2.3", "object": {"sha": "b" * 40}},
                ],
            ),
        },
    )

    assert github_api.list_tags_with_prefix(REPO, "v") == ["v1.0.0", "v1.2.3"]


def test_list_tags_with_prefix_filters_out_dirty_or_unparseable_tags(monkeypatch):
    _install_fake_responses(
        monkeypatch,
        {
            "/repos/OxWRC/glow/git/matching-refs/tags/v": _FakeResponse(
                200,
                [
                    {"ref": "refs/tags/v1.0.0", "object": {"sha": "a" * 40}},
                    {"ref": "refs/tags/v1.0.0-rc1", "object": {"sha": "b" * 40}},
                ],
            ),
        },
    )

    assert github_api.list_tags_with_prefix(REPO, "v") == ["v1.0.0"]


def test_list_tags_with_prefix_does_not_leak_other_tag_families(monkeypatch):
    """matching-refs prefix-matches the ref name itself, so a gui-v* tag never
    shows up when listing v*, and vice versa — no exclusion logic needed."""
    _install_fake_responses(
        monkeypatch,
        {
            "/repos/OxWRC/glow/git/matching-refs/tags/gui-v": _FakeResponse(
                200,
                [{"ref": "refs/tags/gui-v0.0.2", "object": {"sha": "a" * 40}}],
            ),
        },
    )

    assert github_api.list_tags_with_prefix(REPO, "gui-v") == ["gui-v0.0.2"]


def test_list_tags_with_prefix_returns_empty_list_on_empty_response(monkeypatch):
    _install_fake_responses(
        monkeypatch,
        {"/repos/OxWRC/glow/git/matching-refs/tags/v": _FakeResponse(200, [])},
    )

    assert github_api.list_tags_with_prefix(REPO, "v") == []


def test_list_tags_with_prefix_returns_empty_list_on_http_error(monkeypatch):
    _install_fake_responses(
        monkeypatch,
        {"/repos/OxWRC/glow/git/matching-refs/tags/v": _FakeResponse(500)},
    )

    assert github_api.list_tags_with_prefix(REPO, "v") == []


def test_list_tags_with_prefix_returns_empty_list_on_bad_repo_url():
    assert (
        github_api.list_tags_with_prefix("https://github.com/no-repo-path", "v") == []
    )
