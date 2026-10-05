from __future__ import annotations

from glow_deploy.gui import update_check


def test_latest_release_tag_picks_highest_newer_tag(monkeypatch):
    monkeypatch.setattr(
        update_check.github_api,
        "list_tags_with_prefix",
        lambda repo_url, prefix, timeout=5.0: ["gui-v1.2.4", "gui-v1.2.0"],
    )

    assert update_check.latest_release_tag("gui-v1.2.3") == "gui-v1.2.4"


def test_latest_release_tag_returns_none_when_already_at_max(monkeypatch):
    monkeypatch.setattr(
        update_check.github_api,
        "list_tags_with_prefix",
        lambda repo_url, prefix, timeout=5.0: ["gui-v1.2.3", "gui-v1.2.0"],
    )

    assert update_check.latest_release_tag("gui-v1.2.3") is None


def test_latest_release_tag_returns_none_for_unparseable_current_without_http_call(
    monkeypatch,
):
    def fail_if_called(repo_url, prefix, timeout=5.0):
        raise AssertionError("should not fetch tags for an unparseable current version")

    monkeypatch.setattr(
        update_check.github_api, "list_tags_with_prefix", fail_if_called
    )

    assert update_check.latest_release_tag("dev") is None


def test_latest_release_tag_returns_none_when_no_tags_available(monkeypatch):
    monkeypatch.setattr(
        update_check.github_api,
        "list_tags_with_prefix",
        lambda repo_url, prefix, timeout=5.0: [],
    )

    assert update_check.latest_release_tag("gui-v1.2.3") is None
