"""GET /query/counts and GET /export/counts: live, un-pinned, floored at
min_n. Mirrors each endpoint's own access scope (school-filtered for an
authenticated /query caller, dataset-wide for /export's API-key holders).
Served from the slim CountsStore, never the full DataStore.
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from glow_api.api_keys import issue_api_key
from glow_api.audit import AUDIT_ROUTES
from glow_api.auth import create_access_token
from glow_api.counts_store import CountsStore, get_counts_store
from glow_api.data import get_datastore
from glow_api.database import get_db, upsert_user_by_sub
from glow_api.export import get_suppression_rules
from glow_api.main import app
from glow_api.suppression import parse_rules
from tests.mock_odk import MockODKClient

# Focus School Academy: 6 students in one period (clears a min_n of 5).
# Neighbouring School: 3 students in one period (does not).
_COUNTS_DF = pd.DataFrame(
    {
        "uid": [f"F{i}" for i in range(6)] + [f"N{i}" for i in range(3)],
        "school": ["Focus School Academy"] * 6 + ["Neighbouring School"] * 3,
        "period_id": ["2026-W01"] * 9,
    }
)

# /export's own min_n comes from the rules file, not settings.MIN_N.
# Deliberately set above the dataset-wide total (9) while settings.MIN_N
# (5) is below it, so the two thresholds disagree on whether to floor -
# this is what lets the test tell which one export_counts actually used.
_EXPORT_RULES = parse_rules("min_n: 10\ndimensions: [school]\nescalation: []\n")


def _make_counts_store() -> CountsStore:
    store = CountsStore(odk_client=MockODKClient(), refresh_hours=0)
    store._frame = _COUNTS_DF.copy()
    return store


def _no_full_datastore():
    raise AssertionError("counts must not read the full DataStore")


@pytest.fixture()
def counts_client(db_session):
    fake_store = _make_counts_store()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_counts_store] = lambda: fake_store
    app.dependency_overrides[get_datastore] = _no_full_datastore
    app.dependency_overrides[get_suppression_rules] = lambda: _EXPORT_RULES

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


def test_query_counts_is_audited():
    assert ("GET", "/query/counts") in AUDIT_ROUTES


def test_export_counts_is_audited():
    assert ("GET", "/export/counts") in AUDIT_ROUTES


def test_query_counts_dataset_wide_uses_settings_min_n(counts_client):
    # settings.MIN_N defaults to 5: Focus (6) clears it, Neighbouring (3) doesn't.
    response = counts_client.get("/query/counts")

    assert response.status_code == 200
    assert response.json() == {"counts": {"2026-W01": 9}}


def test_query_counts_school_scoped_filters_to_one_school(
    counts_client, db_session, sample_schools
):
    upsert_user_by_sub(
        db_session,
        oidc_sub="focus-teacher-sub",
        username="focus_teacher",
        school_ids=[sample_schools["Focus School Academy"].id],
    )
    token = create_access_token({"sub": "focus-teacher-sub"})
    counts_client.headers["Authorization"] = f"Bearer {token}"

    response = counts_client.get(
        "/query/counts",
        params={"school_id": sample_schools["Focus School Academy"].id},
    )

    assert response.status_code == 200
    assert response.json() == {"counts": {"2026-W01": 6}}


def test_export_counts_requires_api_key(counts_client):
    response = counts_client.get("/export/counts")

    assert response.status_code == 401


def test_export_counts_uses_rules_min_n_not_settings_min_n(counts_client, db_session):
    _, raw_key = issue_api_key(db_session, "test", None, None)
    counts_client.headers["X-API-Key"] = raw_key

    response = counts_client.get("/export/counts")

    # Total is 9: below rules.min_n (10) but above settings.MIN_N (5). A
    # floored-to-None result proves rules.min_n was used; an exact 9 would
    # mean it wrongly used settings.MIN_N instead.
    assert response.status_code == 200
    assert response.json() == {"counts": {"2026-W01": None}}
