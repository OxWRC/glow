"""All internal time is UTC; the host/container TZ must not change behaviour."""

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest
from pydantic import ValidationError

from glow_api.settings import settings


@pytest.fixture
def host_tz(monkeypatch):
    def _set(zone: str):
        monkeypatch.setenv("TZ", zone)
        time.tzset()

    yield _set
    monkeypatch.undo()
    time.tzset()


def test_vintage_scheduler_runs_in_utc(host_tz):
    from glow_api.main import build_vintage_scheduler

    host_tz("Pacific/Auckland")
    scheduler = build_vintage_scheduler(MagicMock())
    assert str(scheduler.timezone) == "UTC"
    scheduler.start(paused=True)
    try:
        job = scheduler.get_jobs()[0]
        now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        nxt = job.trigger.get_next_fire_time(None, now)
        assert nxt == datetime(
            2026,
            2,
            settings.VINTAGE_CADENCE_CRON_DAY,
            settings.VINTAGE_CADENCE_CRON_HOUR,
            tzinfo=timezone.utc,
        )
    finally:
        scheduler.shutdown(wait=False)


def _counts_store():
    from glow_api.counts_store import CountsStore

    return CountsStore(MagicMock(), refresh_hours=1)


def test_counts_scheduler_timezone_is_utc(host_tz):
    host_tz("Pacific/Auckland")
    assert str(_counts_store()._scheduler.timezone) == "UTC"


def test_schedulers_start_under_posix_tz(host_tz):
    from glow_api.main import build_vintage_scheduler

    host_tz("BST-1")
    vintage = build_vintage_scheduler(MagicMock())
    vintage.start(paused=True)
    vintage.shutdown(wait=False)
    counts = _counts_store()
    counts._scheduler.start(paused=True)
    counts._scheduler.shutdown(wait=False)


def test_query_createdat_parse_is_utc_aware():
    from glow_api.query_execution import deduplicate_submissions

    df = pd.DataFrame(
        {
            "uid": ["a", "a"],
            "period_id": ["2025-2026"] * 2,
            "school": ["s"] * 2,
            "createdAt": ["2026-01-01T03:00:00Z", "2026-01-02T03:00:00"],
            "v": [1, 2],
        }
    )
    out = deduplicate_submissions(df, "v")
    assert str(out["createdAt"].dt.tz) == "UTC"
    assert out["v"].tolist() == [2]


def test_vintage_meta_without_offset_reads_as_utc(host_tz):
    from glow_api.vintages import _vintage_info_from_meta

    host_tz("Pacific/Auckland")
    info = _vintage_info_from_meta(
        "p",
        {
            "created_at": "2026-01-01T03:00:00",
            "dataset_version": "v",
            "row_count": 1,
        },
    )
    assert info.created_at.isoformat() == "2026-01-01T03:00:00+00:00"


def test_response_models_reject_naive_datetimes():
    from glow_api.models import ApiKeyRead, VintageRead

    naive = datetime(2026, 1, 1)
    with pytest.raises(ValidationError):
        VintageRead(prefix="p", created_at=naive, dataset_version="v", row_count=1)
    with pytest.raises(ValidationError):
        ApiKeyRead(
            id=1, name="n", prefix="p", created_at=naive, expires_at=naive, use_count=0
        )
