"""Vintage cutting/loading/listing: the filesystem is the only source of truth, no DB table.

Cadence and manual-trigger wiring (scheduler job, /admin/vintages routes) are
covered separately once routers/main.py are wired up; this file covers the
vintages.py module itself - the differencing-closing mechanism.
"""

import json
import threading
import time

import pytest

from glow_api.vintages import (
    VintageStorageNotConfigured,
    get_vintages_dir,
    VintageCache,
    cut_vintage,
    get_current_vintage_frame,
    get_latest_vintage_prefix,
    list_vintages,
    load_vintage_frame,
)
from glow_api.data import ODKFetchError
from tests.conftest import _make_mock_datastore


@pytest.fixture()
def root(tmp_path):
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_caches():
    # Module-level caches are process-global; reset around every test so
    # ordering between tests can't leak a stale "latest" answer or frame.
    from glow_api import vintages as vintages_module

    vintages_module._latest_prefix_cache.invalidate()
    vintages_module._vintage_cache.clear()
    yield
    vintages_module._latest_prefix_cache.invalidate()
    vintages_module._vintage_cache.clear()


def test_cut_vintage_writes_exactly_two_files(root, sample_df):
    datastore = _make_mock_datastore(sample_df)

    cut_vintage(datastore, root)

    files = sorted(
        str(p.relative_to(root / "vintages")) for p in (root / "vintages").rglob("*")
    )
    [vintage_dir] = {f.split("/")[0] for f in files}
    assert files == [
        vintage_dir,
        f"{vintage_dir}/frame.parquet",
        f"{vintage_dir}/meta.json",
    ]


def test_cut_vintage_prefix_is_timestamped_under_vintages(root, sample_df):
    datastore = _make_mock_datastore(sample_df)

    info = cut_vintage(datastore, root)

    assert info.prefix.startswith("vintages/")
    assert info.prefix.endswith("/")
    assert info.row_count == len(sample_df)


def test_cut_vintage_records_scheduler_trigger_as_no_creator(root, sample_df):
    datastore = _make_mock_datastore(sample_df)

    info = cut_vintage(datastore, root, created_by=None)

    assert info.created_by_user_id is None
    assert info.created_by_username is None


def test_cut_vintage_records_admin_trigger(root, sample_df):
    datastore = _make_mock_datastore(sample_df)

    info = cut_vintage(datastore, root, created_by=(7, "admin1"))

    assert info.created_by_user_id == 7
    assert info.created_by_username == "admin1"


def test_cut_vintage_fetches_latest_data_before_freezing(root, sample_df):
    # No hourly full poll any more: the cut itself is the fetch, so a
    # submission made since startup must land in the vintage.
    datastore = _make_mock_datastore(sample_df)
    datastore._odk_client.submissions_df = sample_df.iloc[:3]
    datastore._odk_client.response_etags["bewell_questionnaire"] = "newer"

    info = cut_vintage(datastore, root)

    assert info.row_count == 3
    assert datastore._odk_client.fetch_count == 1


def test_cut_vintage_writes_nothing_when_odk_fetch_fails(root, sample_df):
    datastore = _make_mock_datastore(sample_df)

    def boom(**kwargs):
        raise RuntimeError("ODK down")

    datastore._odk_client.fetch_submissions = boom

    with pytest.raises(ODKFetchError):
        cut_vintage(datastore, root)

    assert list_vintages(root) == []
    assert not any((root / "vintages").glob("*"))


def test_overlapping_cuts_run_one_after_the_other(root, sample_df):
    # A manual cut racing a scheduled one must not interleave: otherwise the
    # older fetch can be published last and frozen under the newer timestamp.
    datastore = _make_mock_datastore(sample_df)
    first_in_refresh = threading.Event()
    release_first = threading.Event()
    events = []

    def refresh(strict=False):
        call = len([e for e in events if e.startswith("start")])
        events.append(f"start {call}")
        if call == 0:
            first_in_refresh.set()
            release_first.wait(timeout=5)
        events.append(f"end {call}")

    datastore.refresh = refresh
    first = threading.Thread(target=cut_vintage, args=(datastore, root))
    second = threading.Thread(target=cut_vintage, args=(datastore, root))

    first.start()
    try:
        assert first_in_refresh.wait(timeout=5)
        second.start()
        time.sleep(0.2)
        assert events == ["start 0"]
    finally:
        release_first.set()
        first.join(timeout=5)
        if second.ident is not None:  # started
            second.join(timeout=5)

    assert events == ["start 0", "end 0", "start 1", "end 1"]
    assert len(list_vintages(root)) == 2


def _hand_set_datastore(df):
    """A store whose state is set by hand, so the cut's ODK fetch is skipped."""
    datastore = _make_mock_datastore(df)
    datastore.refresh = lambda strict=False: None
    return datastore


def test_load_vintage_frame_round_trips_df_and_whitelists(root, sample_df):
    datastore = _hand_set_datastore(sample_df)
    info = cut_vintage(datastore, root)

    loaded = load_vintage_frame(info.prefix, root)

    assert len(loaded.df) == len(sample_df)
    assert set(loaded.df.columns) == set(sample_df.columns)
    assert loaded.categorical_whitelist == datastore._categorical_whitelist
    assert loaded.numerical_whitelist == datastore._numerical_whitelist


def test_load_vintage_frame_round_trips_none_keyed_observed_periods(root, sample_df):
    datastore = _hand_set_datastore(sample_df)
    datastore._observed_periods = {
        None: ["2026-W01"],
        "Focus School Academy": ["2026-W01"],
    }
    info = cut_vintage(datastore, root)

    loaded = load_vintage_frame(info.prefix, root)

    assert loaded.observed_periods == {
        None: ["2026-W01"],
        "Focus School Academy": ["2026-W01"],
    }


def test_get_latest_vintage_prefix_none_when_no_vintages_exist(root):
    assert get_latest_vintage_prefix(root) is None


def test_get_latest_vintage_prefix_is_the_most_recent_cut(root, sample_df):
    datastore = _make_mock_datastore(sample_df)
    first = cut_vintage(datastore, root)
    second = cut_vintage(datastore, root)

    assert get_latest_vintage_prefix(root) == second.prefix
    assert second.prefix > first.prefix


def test_get_latest_vintage_prefix_is_ttl_cached(root, sample_df, monkeypatch):
    from glow_api import vintages as vintages_module

    datastore = _make_mock_datastore(sample_df)
    first = cut_vintage(datastore, root)
    # Prime the cache with the pre-second-cut answer.
    assert get_latest_vintage_prefix(root) == first.prefix

    # Write a second vintage directly, bypassing cut_vintage's invalidation,
    # to isolate the TTL-cache behaviour itself.
    second_prefix = "vintages/99999999T999999Z/"
    second_dir = root / second_prefix
    second_dir.mkdir()
    (second_dir / "frame.parquet").write_bytes(b"")
    (second_dir / "meta.json").write_text(
        json.dumps({"created_at": "2026-01-01T00:00:00+00:00"})
    )

    assert get_latest_vintage_prefix(root) == first.prefix

    vintages_module._latest_prefix_cache._fetched_at -= (
        vintages_module.LATEST_PREFIX_TTL_SECONDS + 1
    )
    assert get_latest_vintage_prefix(root) == second_prefix


def test_cut_vintage_invalidates_latest_prefix_cache_immediately(root, sample_df):
    """A manual cut must take effect on the very next request, not after the
    TTL window - this is the behaviour the user asked for explicitly."""
    datastore = _make_mock_datastore(sample_df)
    first = cut_vintage(datastore, root)
    assert get_latest_vintage_prefix(root) == first.prefix

    second = cut_vintage(datastore, root)

    assert get_latest_vintage_prefix(root) == second.prefix


def test_cut_vintage_does_not_disturb_other_cached_vintages(root, sample_df):
    """A new cut needs no cache invalidation for correctness: its prefix is
    always new, so it's already a miss. Older vintages stay cached rather
    than being thrown away - there's no reason to re-read vintage files that
    can't have changed."""
    datastore = _make_mock_datastore(sample_df)
    first = cut_vintage(datastore, root)

    from glow_api import vintages as vintages_module

    first_calls = []
    first_frame = vintages_module._vintage_cache.get(
        first.prefix,
        lambda: (
            first_calls.append(1),
            load_vintage_frame(first.prefix, root),
        )[1],
    )

    second = cut_vintage(datastore, root)
    second_frame = vintages_module._vintage_cache.get(
        second.prefix,
        lambda: load_vintage_frame(second.prefix, root),
    )
    assert second_frame is not None
    assert second.prefix != first.prefix

    # Re-requesting the first vintage after the second was cut must not
    # re-read it from disk - it's still the same cached object.
    refetched_first = vintages_module._vintage_cache.get(
        first.prefix,
        lambda: (
            first_calls.append(1),
            load_vintage_frame(first.prefix, root),
        )[1],
    )
    assert refetched_first is first_frame
    assert len(first_calls) == 1


def test_list_vintages_returns_all_in_order(root, sample_df):
    datastore = _make_mock_datastore(sample_df)
    first = cut_vintage(datastore, root)
    second = cut_vintage(datastore, root, created_by=(1, "admin"))

    infos = list_vintages(root)

    assert [i.prefix for i in infos] == [first.prefix, second.prefix]
    assert infos[1].created_by_username == "admin"


def test_vintage_cache_returns_identical_object_for_same_prefix():
    cache = VintageCache()
    calls = []

    def load():
        calls.append(1)
        return object()

    first = cache.get("p1", load)
    second = cache.get("p1", load)

    assert first is second
    assert len(calls) == 1


def test_vintage_cache_refetches_on_new_prefix():
    cache = VintageCache()
    calls = []

    def load():
        calls.append(1)
        return object()

    first = cache.get("p1", load)
    second = cache.get("p2", load)

    assert first is not second
    assert len(calls) == 2


def test_vintage_cache_retains_multiple_distinct_prefixes():
    """Never evicts: both earlier entries stay cached alongside a new one."""
    cache = VintageCache()
    calls = []

    def load():
        calls.append(1)
        return object()

    first = cache.get("p1", load)
    second = cache.get("p2", load)
    assert cache.get("p1", load) is first
    assert cache.get("p2", load) is second
    assert len(calls) == 2


def test_vintage_cache_clear_forces_refetch():
    cache = VintageCache()
    calls = []

    def load():
        calls.append(1)
        return object()

    cache.get("p1", load)
    cache.clear()
    cache.get("p1", load)

    assert len(calls) == 2


@pytest.fixture()
def _vintages_dir_configured(root, monkeypatch):
    import glow_api.vintages as vintages_module

    monkeypatch.setattr(vintages_module.settings, "VINTAGES_DIR", str(root))


def test_get_current_vintage_frame_fails_closed_with_no_vintages(
    root, _vintages_dir_configured
):
    with pytest.raises(Exception) as exc_info:
        get_current_vintage_frame(root=root)

    # HTTPException(503) - checked via attribute to avoid importing fastapi
    # error internals into the assertion.
    assert getattr(exc_info.value, "status_code", None) == 503


def test_get_current_vintage_frame_serves_the_latest_vintage(
    root, sample_df, _vintages_dir_configured
):
    datastore = _make_mock_datastore(sample_df)
    cut_vintage(datastore, root)

    frame = get_current_vintage_frame(root=root)

    assert len(frame.df) == len(sample_df)


def test_unset_vintages_dir_returns_503(monkeypatch):
    import glow_api.vintages as vintages_module

    monkeypatch.setattr(vintages_module.settings, "VINTAGES_DIR", None)

    with pytest.raises(VintageStorageNotConfigured):
        get_vintages_dir()


def test_get_vintages_dir_returns_configured_path(root, _vintages_dir_configured):
    assert get_vintages_dir() == root


def test_partial_cut_is_never_latest(root, sample_df, monkeypatch):
    from glow_api import vintages as vintages_module

    datastore = _make_mock_datastore(sample_df)
    previous = cut_vintage(datastore, root)

    def crash(*args, **kwargs):
        raise OSError("crashed after frame.parquet")

    with monkeypatch.context() as patch:
        patch.setattr(vintages_module, "_write_meta", crash)
        with pytest.raises(OSError):
            cut_vintage(datastore, root)
    vintages_module._latest_prefix_cache.invalidate()

    assert get_latest_vintage_prefix(root) == previous.prefix
    assert [i.prefix for i in list_vintages(root)] == [previous.prefix]


def test_vintage_files_are_private(root, sample_df):
    info = cut_vintage(_make_mock_datastore(sample_df), root)

    vintage_dir = root / info.prefix
    assert (vintage_dir.stat().st_mode & 0o777) == 0o700
    assert ((root / "vintages").stat().st_mode & 0o777) == 0o700
    for name in ("frame.parquet", "meta.json"):
        assert ((vintage_dir / name).stat().st_mode & 0o777) == 0o600


def test_cut_sweeps_stale_tmp_dirs(root, sample_df):
    import os

    vintages = root / "vintages"
    stale = vintages / ".tmp-crashed"
    stale.mkdir(parents=True)
    (stale / "frame.parquet").write_bytes(b"unsuppressed")
    old = time.time() - 2 * 3600
    os.utime(stale, (old, old))
    fresh = vintages / ".tmp-in-flight-elsewhere"
    fresh.mkdir()
    keep = cut_vintage(_make_mock_datastore(sample_df), root)
    assert not stale.exists()
    assert fresh.exists()  # may belong to another process's live cut
    assert [i.prefix for i in list_vintages(root)] == [keep.prefix]
