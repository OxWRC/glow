"""CountsStore: the hourly, slim ($select-narrowed) ODK poll behind
/query/counts and /export/counts. The full fetch only happens at vintage cuts.
"""

import pandas as pd

from glow_api import counts_store as counts_module
from glow_api.counts_store import COUNT_FIELDS, CountsStore
from tests.mock_odk import MockODKClient


def _slim(uids, school, created_at="2024-10-01T00:00:00Z"):
    return pd.DataFrame(
        {
            "uid": uids,
            "school": [school] * len(uids),
            "createdAt": [created_at] * len(uids),
        }
    )


def test_refresh_requests_only_count_fields():
    client = MockODKClient(submissions_by_form={"f1": _slim(["S1"], "Focus")})
    store = CountsStore(odk_client=client, refresh_hours=0)

    store.refresh()

    assert client.last_select == COUNT_FIELDS
    assert set(COUNT_FIELDS) == {"__id", "__system/submissionDate", "uid", "school"}


def test_refresh_keeps_uid_school_and_period_only():
    client = MockODKClient(
        submissions_by_form={
            "f1": _slim(["S1", "S2"], "Focus").assign(bw_wbeing_1=[3, 4])
        }
    )
    store = CountsStore(odk_client=client, refresh_hours=0)

    store.refresh()

    frame = store.frame()
    assert sorted(frame.columns) == ["period_id", "school", "uid"]
    assert list(frame["uid"]) == ["S1", "S2"]
    assert set(frame["period_id"]) == {"2024-2025"}


def test_refresh_combines_forms():
    client = MockODKClient(
        submissions_by_form={
            "bewell": _slim(["S1"], "Focus"),
            "phq9": _slim(["S1", "S2"], "Focus"),
        }
    )
    store = CountsStore(odk_client=client, refresh_hours=0)

    store.refresh()

    assert sorted(store.frame()["uid"]) == ["S1", "S1", "S2"]


def test_refresh_reuses_previous_rows_for_unchanged_forms():
    client = MockODKClient(
        submissions_by_form={
            "bewell": _slim(["S1"], "Focus"),
            "phq9": _slim(["S2"], "Focus"),
        },
        response_etags={"bewell": "b1", "phq9": "p1"},
    )
    store = CountsStore(odk_client=client, refresh_hours=0)
    store.refresh()

    client.submissions_by_form["phq9"] = _slim(["S2", "S3"], "Focus")
    client.response_etags["phq9"] = "p2"
    store.refresh()

    assert sorted(store.frame()["uid"]) == ["S1", "S2", "S3"]
    assert client.last_etags == {"bewell": "b1", "phq9": "p1"}


def test_refresh_failure_keeps_previous_counts():
    client = MockODKClient(submissions_by_form={"f1": _slim(["S1"], "Focus")})
    store = CountsStore(odk_client=client, refresh_hours=0)
    store.refresh()

    def boom(**kwargs):
        raise RuntimeError("ODK down")

    client.fetch_submissions = boom
    store.refresh()

    assert list(store.frame()["uid"]) == ["S1"]


def test_frame_is_empty_with_expected_columns_before_first_refresh():
    store = CountsStore(odk_client=MockODKClient(), refresh_hours=0)

    frame = store.frame()

    assert frame.empty
    assert sorted(frame.columns) == ["period_id", "school", "uid"]


def test_startup_schedules_hourly_poll(monkeypatch):
    jobs = []

    class FakeScheduler:
        running = False

        def __init__(self, **kwargs):
            pass

        def add_job(self, func, trigger, **kwargs):
            jobs.append((func, trigger, kwargs))

        def start(self):
            self.running = True

        def shutdown(self, wait=True):
            self.running = False

    monkeypatch.setattr(counts_module, "BackgroundScheduler", FakeScheduler)
    store = CountsStore(odk_client=MockODKClient(), refresh_hours=1)

    store.startup()
    store.shutdown()

    [(func, trigger, kwargs)] = jobs
    assert func == store.refresh
    assert trigger == "interval"
    assert kwargs["hours"] == 1


def test_refresh_through_real_client_with_narrow_system_payload(monkeypatch):
    # Shape ODK Central v2026.1 returns for $select=__system/submissionDate:
    # __system holds only the selected property.
    from glow_api.odk_client import ODKClient

    class Response:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200
            self.headers = {"etag": "e1"}

        def json(self):
            return self._payload

    monkeypatch.setattr(ODKClient, "_try_login", lambda self: True)
    client = ODKClient(
        base_url="http://odk.test", username="u", password="p", project_id=1
    )

    def fake_get(url, **kw):
        if url.endswith("/forms"):
            return Response([{"xmlFormId": "bewell"}])
        return Response(
            {
                "value": [
                    {
                        "__id": "a",
                        "uid": "S1",
                        "school": "Focus",
                        "__system": {"submissionDate": "2025-08-31T23:30:00Z"},
                    }
                ]
            }
        )

    monkeypatch.setattr(client, "get", fake_get)
    store = CountsStore(odk_client=client, refresh_hours=0)

    store.refresh()

    frame = store.frame()
    assert list(frame["uid"]) == ["S1"]
    assert list(frame["school"]) == ["Focus"]
    # 00:30 BST on 1 September: the new academic year, not the old one.
    assert list(frame["period_id"]) == ["2025-2026"]


def test_refresh_writes_cache_that_a_fresh_store_loads(tmp_path):
    # glow-init seeds schools from this file instead of fetching from ODK.
    cache_path = tmp_path / "counts.parquet"
    client = MockODKClient(submissions_by_form={"f1": _slim(["S1", "S2"], "Focus")})
    CountsStore(odk_client=client, refresh_hours=0, cache_path=cache_path).refresh()

    offline = MockODKClient()
    store = CountsStore(odk_client=offline, refresh_hours=0, cache_path=cache_path)
    frame = store.ensure_loaded()

    assert sorted(frame["uid"]) == ["S1", "S2"]
    assert offline.fetch_count == 0


def test_ensure_loaded_fetches_when_there_is_no_cache(tmp_path):
    client = MockODKClient(submissions_by_form={"f1": _slim(["S1"], "Focus")})
    store = CountsStore(
        odk_client=client, refresh_hours=0, cache_path=tmp_path / "none.parquet"
    )

    frame = store.ensure_loaded()

    assert list(frame["uid"]) == ["S1"]
    assert client.fetch_count == 1


def test_failed_refresh_leaves_cache_untouched(tmp_path):
    cache_path = tmp_path / "counts.parquet"
    client = MockODKClient(submissions_by_form={"f1": _slim(["S1"], "Focus")})
    store = CountsStore(odk_client=client, refresh_hours=0, cache_path=cache_path)
    store.refresh()

    def boom(**kwargs):
        raise RuntimeError("ODK down")

    client.fetch_submissions = boom
    store.refresh()

    reloaded = CountsStore(
        odk_client=MockODKClient(), refresh_hours=0, cache_path=cache_path
    )
    assert list(reloaded.ensure_loaded()["uid"]) == ["S1"]


def test_startup_serves_cached_counts_before_first_poll(tmp_path, monkeypatch):
    cache_path = tmp_path / "counts.parquet"
    seed = MockODKClient(submissions_by_form={"f1": _slim(["S1"], "Focus")})
    CountsStore(odk_client=seed, refresh_hours=0, cache_path=cache_path).refresh()

    store = CountsStore(
        odk_client=MockODKClient(), refresh_hours=0, cache_path=cache_path
    )
    # Hold the background poll back so only the cache can have filled the frame.
    monkeypatch.setattr(store, "refresh", lambda: None)
    store.startup()

    assert list(store.frame()["uid"]) == ["S1"]
