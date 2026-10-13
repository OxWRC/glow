"""GET /demo/export/raw: demo-only, API-key-gated, unredacted twin of /export
so demo users can compare what suppression/coarsening/pseudonymisation do."""

import pytest

from glow_api.api_keys import issue_api_key
from glow_api.audit import AUDIT_ROUTES
from glow_api.contract_examples import get_example
from glow_api.database import get_api_key_by_id
from glow_api.export import dataset_version
from glow_api.models import RawExportResponse


@pytest.fixture()
def raw_client(auth_client, db_session):
    key, raw = issue_api_key(db_session, "test", None, None)
    auth_client.headers["X-API-Key"] = raw
    auth_client.key_id = key.id
    return auth_client


def test_raw_export_requires_valid_key(auth_client):
    assert auth_client.get("/demo/export/raw").status_code == 401
    resp = auth_client.get("/demo/export/raw", headers={"X-API-Key": "glow_nope"})
    assert resp.status_code == 401


def test_raw_export_is_unredacted(raw_client, sample_df):
    resp = raw_client.get("/demo/export/raw")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["rows"]) == 15
    assert {"S001", "S010"} <= {r["uid"] for r in body["rows"]}
    assert {r["school"] for r in body["rows"]} == {
        "Focus School Academy",
        "Neighbouring School",
    }
    assert {r["d_sex"] for r in body["rows"]} == {"M", "F"}
    # Same vintage as /export, so the two can be compared row-for-row.
    assert body["dataset_version"] == dataset_version(sample_df)


def test_raw_export_records_key_use(raw_client, db_session):
    assert raw_client.get("/demo/export/raw").status_code == 200
    db_session.expire_all()
    assert get_api_key_by_id(db_session, raw_client.key_id).use_count == 1


def test_raw_export_503_when_data_not_loaded(auth_client_empty_data, db_session):
    _, raw = issue_api_key(db_session, "test", None, None)
    resp = auth_client_empty_data.get("/demo/export/raw", headers={"X-API-Key": raw})
    assert resp.status_code == 503


def test_raw_export_is_audit_tier():
    assert ("GET", "/demo/export/raw") in AUDIT_ROUTES


def test_raw_export_matches_contract_shape():
    RawExportResponse.model_validate(get_example("demo.export.raw")["response"])
