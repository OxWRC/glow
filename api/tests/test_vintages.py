"""Vintage cutting/loading/listing: S3 is the only source of truth, no DB table.

Cadence and manual-trigger wiring (scheduler job, /admin/vintages routes) are
covered separately once routers/main.py are wired up; this file covers the
vintages.py module itself - the differencing-closing mechanism.
"""

import json
import threading
import time

import boto3
import pytest
from moto import mock_aws

from glow_api.vintages import (
    VintageCache,
    cut_vintage,
    get_current_vintage_frame,
    get_latest_vintage_prefix,
    list_vintages,
    load_vintage_frame,
)
from glow_api.data import ODKFetchError
from tests.conftest import _make_mock_datastore

BUCKET = "test-glow-vintages"


@pytest.fixture()
def s3_client():
    with mock_aws():
        client = boto3.client("s3", region_name="eu-west-2")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
        )
        yield client


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


def test_cut_vintage_writes_exactly_two_objects(s3_client, sample_df):
    datastore = _make_mock_datastore(sample_df)

    cut_vintage(datastore, s3_client, bucket=BUCKET)

    objects = s3_client.list_objects_v2(Bucket=BUCKET)["Contents"]
    keys = sorted(o["Key"] for o in objects)
    assert len(keys) == 2
    assert keys[0].endswith("frame.parquet")
    assert keys[1].endswith("meta.json")
    assert keys[0].rsplit("/", 1)[0] == keys[1].rsplit("/", 1)[0]


def test_cut_vintage_prefix_is_timestamped_under_vintages(s3_client, sample_df):
    datastore = _make_mock_datastore(sample_df)

    info = cut_vintage(datastore, s3_client, bucket=BUCKET)

    assert info.prefix.startswith("vintages/")
    assert info.prefix.endswith("/")
    assert info.row_count == len(sample_df)


def test_cut_vintage_records_scheduler_trigger_as_no_creator(s3_client, sample_df):
    datastore = _make_mock_datastore(sample_df)

    info = cut_vintage(datastore, s3_client, created_by=None, bucket=BUCKET)

    assert info.created_by_user_id is None
    assert info.created_by_username is None


def test_cut_vintage_records_admin_trigger(s3_client, sample_df):
    datastore = _make_mock_datastore(sample_df)

    info = cut_vintage(datastore, s3_client, created_by=(7, "admin1"), bucket=BUCKET)

    assert info.created_by_user_id == 7
    assert info.created_by_username == "admin1"


def test_cut_vintage_fetches_latest_data_before_freezing(s3_client, sample_df):
    # No hourly full poll any more: the cut itself is the fetch, so a
    # submission made since startup must land in the vintage.
    datastore = _make_mock_datastore(sample_df)
    datastore._odk_client.submissions_df = sample_df.iloc[:3]
    datastore._odk_client.response_etags["bewell_questionnaire"] = "newer"

    info = cut_vintage(datastore, s3_client, bucket=BUCKET)

    assert info.row_count == 3
    assert datastore._odk_client.fetch_count == 1


def test_cut_vintage_writes_nothing_when_odk_fetch_fails(s3_client, sample_df):
    datastore = _make_mock_datastore(sample_df)

    def boom(**kwargs):
        raise RuntimeError("ODK down")

    datastore._odk_client.fetch_submissions = boom

    with pytest.raises(ODKFetchError):
        cut_vintage(datastore, s3_client, bucket=BUCKET)

    assert "Contents" not in s3_client.list_objects_v2(Bucket=BUCKET)


def test_overlapping_cuts_run_one_after_the_other(s3_client, sample_df):
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
    first = threading.Thread(
        target=cut_vintage, args=(datastore, s3_client), kwargs={"bucket": BUCKET}
    )
    second = threading.Thread(
        target=cut_vintage, args=(datastore, s3_client), kwargs={"bucket": BUCKET}
    )

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
    assert len(list_vintages(s3_client, bucket=BUCKET)) == 2


def _hand_set_datastore(df):
    """A store whose state is set by hand, so the cut's ODK fetch is skipped."""
    datastore = _make_mock_datastore(df)
    datastore.refresh = lambda strict=False: None
    return datastore


def test_load_vintage_frame_round_trips_df_and_whitelists(s3_client, sample_df):
    datastore = _hand_set_datastore(sample_df)
    info = cut_vintage(datastore, s3_client, bucket=BUCKET)

    loaded = load_vintage_frame(info.prefix, s3_client, bucket=BUCKET)

    assert len(loaded.df) == len(sample_df)
    assert set(loaded.df.columns) == set(sample_df.columns)
    assert loaded.categorical_whitelist == datastore._categorical_whitelist
    assert loaded.numerical_whitelist == datastore._numerical_whitelist


def test_load_vintage_frame_round_trips_none_keyed_observed_periods(
    s3_client, sample_df
):
    datastore = _hand_set_datastore(sample_df)
    datastore._observed_periods = {
        None: ["2026-W01"],
        "Focus School Academy": ["2026-W01"],
    }
    info = cut_vintage(datastore, s3_client, bucket=BUCKET)

    loaded = load_vintage_frame(info.prefix, s3_client, bucket=BUCKET)

    assert loaded.observed_periods == {
        None: ["2026-W01"],
        "Focus School Academy": ["2026-W01"],
    }


def test_get_latest_vintage_prefix_none_when_no_vintages_exist(s3_client):
    assert get_latest_vintage_prefix(s3_client, bucket=BUCKET) is None


def test_get_latest_vintage_prefix_is_the_most_recent_cut(s3_client, sample_df):
    datastore = _make_mock_datastore(sample_df)
    first = cut_vintage(datastore, s3_client, bucket=BUCKET)
    second = cut_vintage(datastore, s3_client, bucket=BUCKET)

    assert get_latest_vintage_prefix(s3_client, bucket=BUCKET) == second.prefix
    assert second.prefix > first.prefix


def test_get_latest_vintage_prefix_is_ttl_cached(s3_client, sample_df, monkeypatch):
    from glow_api import vintages as vintages_module

    datastore = _make_mock_datastore(sample_df)
    first = cut_vintage(datastore, s3_client, bucket=BUCKET)
    # Prime the cache with the pre-second-cut answer.
    assert get_latest_vintage_prefix(s3_client, bucket=BUCKET) == first.prefix

    # Write a second vintage directly, bypassing cut_vintage's invalidation,
    # to isolate the TTL-cache behaviour itself.
    second_prefix = "vintages/99999999T999999Z/"
    s3_client.put_object(Bucket=BUCKET, Key=f"{second_prefix}frame.parquet", Body=b"")
    s3_client.put_object(
        Bucket=BUCKET,
        Key=f"{second_prefix}meta.json",
        Body=json.dumps({"created_at": "2026-01-01T00:00:00+00:00"}).encode(),
    )

    assert get_latest_vintage_prefix(s3_client, bucket=BUCKET) == first.prefix

    vintages_module._latest_prefix_cache._fetched_at -= (
        vintages_module.LATEST_PREFIX_TTL_SECONDS + 1
    )
    assert get_latest_vintage_prefix(s3_client, bucket=BUCKET) == second_prefix


def test_cut_vintage_invalidates_latest_prefix_cache_immediately(s3_client, sample_df):
    """A manual cut must take effect on the very next request, not after the
    TTL window - this is the behaviour the user asked for explicitly."""
    datastore = _make_mock_datastore(sample_df)
    first = cut_vintage(datastore, s3_client, bucket=BUCKET)
    assert get_latest_vintage_prefix(s3_client, bucket=BUCKET) == first.prefix

    second = cut_vintage(datastore, s3_client, bucket=BUCKET)

    assert get_latest_vintage_prefix(s3_client, bucket=BUCKET) == second.prefix


def test_cut_vintage_does_not_disturb_other_cached_vintages(s3_client, sample_df):
    """A new cut needs no cache invalidation for correctness: its prefix is
    always new, so it's already a miss. Older vintages stay cached rather
    than being thrown away - there's no reason to re-fetch S3 objects that
    can't have changed."""
    datastore = _make_mock_datastore(sample_df)
    first = cut_vintage(datastore, s3_client, bucket=BUCKET)

    from glow_api import vintages as vintages_module

    first_calls = []
    first_frame = vintages_module._vintage_cache.get(
        first.prefix,
        lambda: (
            first_calls.append(1),
            load_vintage_frame(first.prefix, s3_client, bucket=BUCKET),
        )[1],
    )

    second = cut_vintage(datastore, s3_client, bucket=BUCKET)
    second_frame = vintages_module._vintage_cache.get(
        second.prefix,
        lambda: load_vintage_frame(second.prefix, s3_client, bucket=BUCKET),
    )
    assert second_frame is not None
    assert second.prefix != first.prefix

    # Re-requesting the first vintage after the second was cut must not
    # re-fetch it from S3 - it's still the same cached object.
    refetched_first = vintages_module._vintage_cache.get(
        first.prefix,
        lambda: (
            first_calls.append(1),
            load_vintage_frame(first.prefix, s3_client, bucket=BUCKET),
        )[1],
    )
    assert refetched_first is first_frame
    assert len(first_calls) == 1


def test_list_vintages_returns_all_in_order(s3_client, sample_df):
    datastore = _make_mock_datastore(sample_df)
    first = cut_vintage(datastore, s3_client, bucket=BUCKET)
    second = cut_vintage(datastore, s3_client, created_by=(1, "admin"), bucket=BUCKET)

    infos = list_vintages(s3_client, bucket=BUCKET)

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
def _vintages_bucket_configured():
    import glow_api.vintages as vintages_module

    original_bucket = vintages_module.settings.VINTAGES_S3_BUCKET
    vintages_module.settings.VINTAGES_S3_BUCKET = BUCKET
    yield
    vintages_module.settings.VINTAGES_S3_BUCKET = original_bucket


def test_get_current_vintage_frame_fails_closed_with_no_vintages(
    s3_client, _vintages_bucket_configured
):
    with pytest.raises(Exception) as exc_info:
        get_current_vintage_frame(s3_client=s3_client)

    # HTTPException(503) - checked via attribute to avoid importing fastapi
    # error internals into the assertion.
    assert getattr(exc_info.value, "status_code", None) == 503


def test_get_current_vintage_frame_serves_the_latest_vintage(
    s3_client, sample_df, _vintages_bucket_configured
):
    datastore = _make_mock_datastore(sample_df)
    cut_vintage(datastore, s3_client, bucket=BUCKET)

    frame = get_current_vintage_frame(s3_client=s3_client)

    assert len(frame.df) == len(sample_df)
