"""Lifespan wiring for the vintage-cut scheduler.

The rest of the test suite runs with GLOW_TESTING=1, which skips datastore
(and so scheduler) startup entirely - these tests exercise that branch
directly, with run_migrations/datastore/BackgroundScheduler all stubbed out.
"""

import asyncio
from typing import ClassVar

import pytest

import glow_api.main as main_module


class _FakeDataStore:
    def __init__(self):
        self.started = False
        self.shut_down = False

    def startup(self):
        self.started = True

    def shutdown(self):
        self.shut_down = True


class _FakeScheduler:
    instances: ClassVar[list["_FakeScheduler"]] = []

    def __init__(self):
        self.jobs = []
        self.started = False
        self.shut_down = False
        _FakeScheduler.instances.append(self)

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, trigger, kwargs))

    def start(self):
        self.started = True

    def shutdown(self):
        self.shut_down = True


@pytest.fixture(autouse=True)
def _reset_fake_scheduler():
    _FakeScheduler.instances = []
    yield
    _FakeScheduler.instances = []


@pytest.fixture()
def lifespan_env(monkeypatch):
    monkeypatch.delenv("GLOW_TESTING", raising=False)
    monkeypatch.setattr(main_module, "run_migrations", lambda: None)
    fake_store = _FakeDataStore()
    monkeypatch.setattr(main_module, "get_datastore", lambda: fake_store)
    monkeypatch.setattr(main_module, "BackgroundScheduler", _FakeScheduler)
    monkeypatch.setattr(main_module.settings, "AUDIT_LOG_PATH", None)
    return fake_store


async def _enter_and_exit_lifespan():
    async with main_module.lifespan(main_module.app):
        pass


def test_scheduler_started_with_configured_cron_when_bucket_set(
    lifespan_env, monkeypatch
):
    monkeypatch.setattr(main_module.settings, "VINTAGES_S3_BUCKET", "a-bucket")
    monkeypatch.setattr(main_module.settings, "VINTAGE_CADENCE_CRON_DAY", 1)
    monkeypatch.setattr(main_module.settings, "VINTAGE_CADENCE_CRON_HOUR", 3)

    asyncio.run(_enter_and_exit_lifespan())

    [scheduler] = _FakeScheduler.instances
    assert scheduler.started
    [(func, trigger, kwargs)] = scheduler.jobs
    assert func is main_module.scheduled_cut_vintage
    assert trigger == "cron"
    assert kwargs["day"] == 1
    assert kwargs["hour"] == 3
    assert kwargs["args"] == [lifespan_env]
    assert scheduler.shut_down


def test_scheduler_not_started_when_bucket_unconfigured(lifespan_env, monkeypatch):
    monkeypatch.setattr(main_module.settings, "VINTAGES_S3_BUCKET", None)

    asyncio.run(_enter_and_exit_lifespan())

    assert _FakeScheduler.instances == []


def test_datastore_still_starts_and_stops_regardless(lifespan_env, monkeypatch):
    monkeypatch.setattr(main_module.settings, "VINTAGES_S3_BUCKET", None)

    asyncio.run(_enter_and_exit_lifespan())

    assert lifespan_env.started
    assert lifespan_env.shut_down
