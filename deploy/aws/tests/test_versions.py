from glow_deploy import versions


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def test_parse_accepts_exact_prefixed_semver():
    assert versions.parse("v1.2.3", "v") == (1, 2, 3)
    assert versions.parse("gui-v0.0.2", "gui-v") == (0, 0, 2)


def test_parse_rejects_wrong_prefix():
    assert versions.parse("gui-v1.2.3", "v") is None
    assert versions.parse("v1.2.3", "gui-v") is None


def test_parse_rejects_git_describe_dirty_suffix():
    assert versions.parse("v1.2.3-5-gabcdef", "v") is None


def test_parse_rejects_garbage():
    assert versions.parse("main", "v") is None
    assert versions.parse("", "v") is None
    assert versions.parse("v1.2", "v") is None


# ---------------------------------------------------------------------------
# highest
# ---------------------------------------------------------------------------


def test_highest_picks_max_parseable_ref():
    assert versions.highest(["v1.0.0", "v1.4.0", "v1.2.0"], "v") == "v1.4.0"


def test_highest_ignores_unparseable_refs():
    assert versions.highest(["main", "v1.0.0", "gui-v9.9.9"], "v") == "v1.0.0"


def test_highest_returns_none_when_nothing_parses():
    assert versions.highest(["main", "gui-v1.0.0"], "v") is None
    assert versions.highest([], "v") is None


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_classify_returns_none_for_unparseable_current():
    assert versions.classify("main", ["v1.0.0"], "v") is None
    assert versions.classify("v1.2.3-5-gabcdef", ["v1.3.0"], "v") is None


def test_classify_patch_update_only():
    result = versions.classify("v1.2.0", ["v1.2.3", "v1.0.0"], "v")
    assert result == {"current": "v1.2.0", "update_to": "v1.2.3", "upgrade_to": None}


def test_classify_minor_update_only():
    result = versions.classify("v1.2.0", ["v1.4.0"], "v")
    assert result == {"current": "v1.2.0", "update_to": "v1.4.0", "upgrade_to": None}


def test_classify_update_and_upgrade_simultaneously():
    result = versions.classify("v1.2.0", ["v1.4.0", "v2.1.0", "v1.0.0"], "v")
    assert result == {
        "current": "v1.2.0",
        "update_to": "v1.4.0",
        "upgrade_to": "v2.1.0",
    }


def test_classify_upgrade_only_no_same_major_update():
    result = versions.classify("v1.2.0", ["v2.1.0"], "v")
    assert result == {"current": "v1.2.0", "update_to": None, "upgrade_to": "v2.1.0"}


def test_classify_already_at_latest_gives_no_update_or_upgrade():
    result = versions.classify("v1.4.0", ["v1.4.0", "v1.2.0"], "v")
    assert result == {"current": "v1.4.0", "update_to": None, "upgrade_to": None}


def test_classify_empty_available_refs():
    result = versions.classify("v1.2.0", [], "v")
    assert result == {"current": "v1.2.0", "update_to": None, "upgrade_to": None}


def test_classify_ignores_lower_versions_and_wrong_prefix_noise():
    result = versions.classify("v1.2.0", ["v1.0.0", "gui-v9.9.9", "main"], "v")
    assert result == {"current": "v1.2.0", "update_to": None, "upgrade_to": None}
