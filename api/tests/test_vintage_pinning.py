"""Integration tests: /query and /export actually read a pinned vintage, not
live DataStore data.

This is the mechanism that closes the /query differencing hole: a caller
with repeated access (e.g. a teacher authenticated for their own school)
could previously capture (mean, n) before and after a change and recover a
new student's exact value algebraically. Pinning both endpoints to an
immutable vintage means two calls between cuts return byte-identical
output, however much the live data underneath has changed.

These go through the REAL get_current_vintage_frame dependency chain (a tmp_path
vintages dir), unlike most other /query and /export tests, which use conftest's
shortcut of pinning the dependency straight to a fixed frame for
convenience - that shortcut would make this specific behaviour untestable.
"""

import threading

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from glow_api.api_keys import issue_api_key
from glow_api.data import DataStore, get_datastore
from glow_api.database import get_db
from glow_api.export import get_suppression_rules
from glow_api.main import app
from glow_api.suppression import parse_rules
from glow_api.vintages import cut_vintage
from tests.mock_odk import MockODKClient

# Six students, one period, one school - well clear of min_n=5 so the
# period isn't suppressed and an actual mean is returned. No "createdAt"
# derivation needed: period_id is set directly, same technique
# test_api.py's namespaced_df test uses.
_PERIOD_DF = pd.DataFrame(
    {
        "uid": [f"S{i:03d}" for i in range(1, 7)],
        "school": ["Focus School Academy"] * 6,
        "period_id": ["2026-W01"] * 6,
        "bw_wbeing_1": [3, 4, 2, 4, 3, 3],
    }
)

# min_n=1 keeps this independent of whatever min_n is in the real,
# checked-in suppression.yaml (loaded by get_suppression_rules() by
# default) - these tests are about pinning, not suppression thresholds.
_TEST_RULES = parse_rules("min_n: 1\ndimensions: [school]\nescalation: []\n")


def _make_period_store() -> DataStore:
    ds = DataStore(odk_client=MockODKClient())
    ds._df = _PERIOD_DF.copy()
    ds._categorical_whitelist = []
    ds._numerical_whitelist = ["bw_wbeing_1"]
    ds._observed_periods = {
        None: ["2026-W01"],
        "Focus School Academy": ["2026-W01"],
    }
    ds._lock = threading.Lock()
    ds.startup = lambda: None
    ds.shutdown = lambda: None
    # The frame above is set by hand: a cut's ODK fetch mustn't replace it.
    ds.refresh = lambda strict=False: None
    return ds


@pytest.fixture()
def vintages_root(tmp_path):
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_vintage_caches():
    from glow_api import vintages as vintages_module

    vintages_module._latest_prefix_cache.invalidate()
    vintages_module._vintage_cache.clear()
    yield
    vintages_module._latest_prefix_cache.invalidate()
    vintages_module._vintage_cache.clear()


@pytest.fixture()
def pinned_client(db_session, vintages_root, monkeypatch):
    import glow_api.vintages as vintages_module

    monkeypatch.setattr(vintages_module.settings, "VINTAGES_DIR", str(vintages_root))

    fake_store = _make_period_store()

    def override_get_db():
        yield db_session

    def override_get_datastore():
        return fake_store

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_datastore] = override_get_datastore
    app.dependency_overrides[get_suppression_rules] = lambda: _TEST_RULES

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, fake_store

    app.dependency_overrides.clear()


def _mutate_one_value(fake_store):
    """Simulate a live submission changing, with no new vintage cut."""
    fake_store._df = fake_store._df.copy()
    idx = fake_store._df.index[0]
    fake_store._df.loc[idx, "bw_wbeing_1"] = 999


def test_query_before_first_vintage_fails_closed(pinned_client):
    client, _ = pinned_client

    response = client.get("/query/", params={"v": ["bw_wbeing_1"]})

    assert response.status_code == 503


def test_dimensions_before_first_vintage_fails_closed(pinned_client):
    from glow_api.contract_examples import get_example

    client, _ = pinned_client
    example = get_example("dimensions.no-vintage")

    response = client.get("/dimensions")

    assert response.status_code == example["status"]
    assert response.json() == example["response"]


def test_vintage_status_before_first_cut_says_no_data_yet(pinned_client):
    # The dashboard checks this before anything else, so it must answer
    # (not 503) and need no auth.
    from glow_api.contract_examples import get_example

    client, _ = pinned_client
    example = get_example("query.vintage.none")

    response = client.get("/query/vintage")

    assert response.status_code == example["status"]
    assert response.json() == example["response"]


def test_vintage_status_after_cut_reports_when(pinned_client, vintages_root):
    from datetime import datetime

    client, fake_store = pinned_client
    info = cut_vintage(fake_store, vintages_root)

    body = client.get("/query/vintage").json()

    assert body["available"] is True
    assert datetime.fromisoformat(body["created_at"]) == info.created_at


def test_query_is_pinned_not_live(pinned_client, vintages_root):
    client, fake_store = pinned_client
    cut_vintage(fake_store, vintages_root)

    first = client.get("/query/", params={"v": ["bw_wbeing_1"]})
    second = client.get("/query/", params={"v": ["bw_wbeing_1"]})
    assert first.status_code == 200
    assert first.json() == second.json()

    _mutate_one_value(fake_store)

    third = client.get("/query/", params={"v": ["bw_wbeing_1"]})
    assert third.json() == first.json(), (
        "a live data change leaked into /query's output before a new vintage was cut"
    )

    cut_vintage(fake_store, vintages_root)

    fourth = client.get("/query/", params={"v": ["bw_wbeing_1"]})
    assert fourth.json() != first.json(), (
        "cutting a new vintage should be the only thing that changes /query's output"
    )


def test_export_is_pinned_not_live(pinned_client, vintages_root, db_session):
    client, fake_store = pinned_client
    _, raw_key = issue_api_key(db_session, name="test")
    client.headers["X-API-Key"] = raw_key
    cut_vintage(fake_store, vintages_root)

    first = client.get("/export")
    second = client.get("/export")
    assert first.status_code == 200
    assert first.json()["rows"] == second.json()["rows"]

    _mutate_one_value(fake_store)

    third = client.get("/export")
    assert third.json()["rows"] == first.json()["rows"], (
        "a live data change leaked into /export's output before a new vintage was cut"
    )
