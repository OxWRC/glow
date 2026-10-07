"""GET /export: API-key-only, suppressed, pseudonymised, cached per data version."""

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from glow_api import audit as audit_module
from glow_api.api_keys import issue_api_key
from glow_api.audit import AUDIT_ROUTES
from glow_api.auth import create_access_token
from glow_api.data import get_datastore
from glow_api.database import get_api_key_by_id, upsert_user_by_sub
from glow_api.export import (
    ExportCache,
    dataset_version,
    get_export_cache,
    get_suppression_rules,
)
from glow_api.main import app
from glow_api.settings import settings
from glow_api.suppression import parse_rules

# Sample data (conftest SAMPLE_CSV): 10 students, 15 rows, 5 per school.
TEST_RULES_YAML = """
min_n: 5
dimensions: [school, class, yearGroup, d_sex, d_ethnicity, d_age, d_city, d_country]
hierarchies:
  school: [{map: [{input_regex: ".*", output: "*"}]}]
  class: [{map: [{input_regex: ".*", output: "*"}]}]
  d_age: [{map: [{input_regex: ".*", output: "*"}]}]
  d_ethnicity: [{map: [{input_regex: ".*", output: "*"}]}]
  d_sex: [{map: [{input_regex: ".*", output: "*"}]}]
  yearGroup: [{map: [{input_regex: ".*", output: "*"}]}]
  d_city: [{map: [{input_regex: ".*", output: "*"}]}]
  d_country: [{map: [{input_regex: ".*", output: "*"}]}]
escalation: [class, d_age, d_ethnicity, d_sex, yearGroup, school, d_city, d_country]
"""
TEST_RULES = parse_rules(TEST_RULES_YAML)

# Same data, but d_city is not governed at all.
UNLISTED_CITY_RULES = parse_rules(
    "min_n: 5\n"
    "dimensions: [school, class, yearGroup, d_sex, d_ethnicity, d_age, d_country]\n"
    "escalation: []\n"
)


def _use_rules(rules):
    app.dependency_overrides[get_suppression_rules] = lambda: rules


def _with_min_n(n):
    return parse_rules(TEST_RULES_YAML.replace("min_n: 5", f"min_n: {n}"))


@pytest.fixture()
def export_client(auth_client, db_session):
    app.dependency_overrides[get_suppression_rules] = lambda: TEST_RULES
    cache = ExportCache()
    app.dependency_overrides[get_export_cache] = lambda: cache
    _, raw = issue_api_key(db_session, "test", None, None)
    auth_client.headers["X-API-Key"] = raw
    return auth_client


def _get(client):
    response = client.get("/export")
    assert response.status_code == 200, response.text
    return response.json()


def test_export_requires_valid_key(auth_client, db_session):
    assert auth_client.get("/export").status_code == 401
    assert (
        auth_client.get("/export", headers={"X-API-Key": "glow_nope"}).status_code
        == 401
    )
    revoked, raw_revoked = issue_api_key(db_session, "r", None, None)
    revoked.revoked_at = datetime.now(timezone.utc)
    expired, raw_expired = issue_api_key(db_session, "e", None, None)
    expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    for raw in (raw_revoked, raw_expired):
        assert auth_client.get("/export", headers={"X-API-Key": raw}).status_code == 401


def test_export_rejects_jwt_only(auth_client, db_session):
    upsert_user_by_sub(db_session, "jwt-only-sub", username="jwtonly", is_admin=True)
    token = create_access_token({"sub": "jwt-only-sub", "cognito:username": "jwtonly"})
    headers = {"Authorization": f"Bearer {token}"}
    # The token itself is good: a JWT route accepts it.
    assert auth_client.get("/me", headers=headers).status_code == 200
    assert auth_client.get("/export", headers=headers).status_code == 401


def test_export_coarsens_until_threshold(export_client):
    body = _get(export_client)
    # Sex split within school is 2/3, so sex goes; yearGroup then clears.
    assert body["coarsening"] == {
        "school": 0,
        "class": 1,
        "yearGroup": 0,
        "d_sex": 1,
        "d_ethnicity": 1,
        "d_age": 1,
        "d_city": 0,
        "d_country": 0,
    }
    assert body["suppressed"] is False
    assert body["min_n"] == 5
    assert body["rules_sha256"] == TEST_RULES.sha256
    assert len(body["rows"]) == 15
    assert {row["d_sex"] for row in body["rows"]} == {"*"}


def test_export_columns_are_allowlisted(export_client):
    row = _get(export_client)["rows"][0]
    for raw_column in ("uid", "school", "class", "wave"):
        assert raw_column not in row
    assert {"student_id", "school_id", "class_id", "period_id"} <= set(row)
    assert {"bw_wbeing_1", "yearGroup", "d_city"} <= set(row)


def test_export_ids_are_pseudonymous_and_consistent(export_client):
    rows = _get(export_client)["rows"]
    student_ids = [r["student_id"] for r in rows]
    assert len(set(student_ids)) == 10
    assert not {"S001", "S002"} & set(student_ids)
    assert all(len(s) == 16 for s in student_ids)
    assert len({r["school_id"] for r in rows}) == 2
    # class redacted to "*": one class id per school
    assert len({r["class_id"] for r in rows}) == 2


def test_export_ids_follow_coarsened_school(export_client):
    # 5 per school: clearing 10 needs school, city and country all at "*".
    _use_rules(_with_min_n(10))
    rows = _get(export_client)["rows"]
    assert len({r["school_id"] for r in rows}) == 1
    assert len({r["class_id"] for r in rows}) == 1


def test_export_suppressed_when_escalation_exhausted(export_client):
    _use_rules(_with_min_n(11))
    body = _get(export_client)
    assert body["suppressed"] is True
    assert body["rows"] == []


def test_export_is_cached_until_data_changes(export_client):
    first = _get(export_client)
    assert _get(export_client) == first

    store = app.dependency_overrides[get_datastore]()
    store._df.loc[0, "bw_wbeing_1"] = 1
    changed = _get(export_client)
    assert changed["dataset_version"] != first["dataset_version"]
    # Fresh salt: no pseudonym survives a rebuild.
    assert not {r["student_id"] for r in first["rows"]} & {
        r["student_id"] for r in changed["rows"]
    }


def test_export_records_key_use(export_client, db_session):
    _get(export_client)
    _get(export_client)
    record = get_api_key_by_id(db_session, 1)
    db_session.refresh(record)
    assert record.use_count == 2
    assert record.last_used_at is not None


def _use_count(db_session):
    record = get_api_key_by_id(db_session, 1)
    db_session.refresh(record)
    return record.use_count


def test_failed_exports_do_not_count_as_key_use(export_client, db_session):
    _use_rules(UNLISTED_CITY_RULES)
    assert export_client.get("/export").status_code == 500
    store = app.dependency_overrides[get_datastore]()
    full = store._df
    store._df = full.iloc[0:0]
    assert export_client.get("/export").status_code == 503
    assert _use_count(db_session) == 0
    store._df = full
    _use_rules(TEST_RULES)
    _get(export_client)
    assert _use_count(db_session) == 1


def test_export_blocks_when_school_not_a_dimension(export_client):
    _use_rules(
        parse_rules(
            "min_n: 5\n"
            "dimensions: [class, yearGroup, d_sex, d_ethnicity, d_age, d_city,"
            " d_country]\n"
            "escalation: []\n"
        )
    )
    response = export_client.get("/export")
    assert response.status_code == 500
    assert "school" in response.json()["detail"]


def test_export_handles_namespaced_columns(export_client):
    store = app.dependency_overrides[get_datastore]()
    store._df["form__bw_wbeing_9"] = 3
    store._extract_whitelists(store._df)
    row = _get(export_client)["rows"][0]
    assert row["form__bw_wbeing_9"] == 3

    store._df["form__d_religion"] = "None"
    response = export_client.get("/export")
    assert response.status_code == 500
    assert "form__d_religion" in response.json()["detail"]


def test_dataset_version_handles_list_cells():
    df = pd.DataFrame({"uid": ["S1"], "geo": [[51.7, -1.2]]})
    first = dataset_version(df)
    assert isinstance(first, str)
    df.at[0, "geo"] = [51.8, -1.2]
    assert dataset_version(df) != first


def test_export_blocks_unlisted_demographic(export_client):
    _use_rules(UNLISTED_CITY_RULES)
    response = export_client.get("/export")
    assert response.status_code == 500
    assert "d_city" in response.json()["detail"]


def test_export_is_audited_with_key_id(export_client, tmp_path, monkeypatch):
    assert ("GET", "/export") in AUDIT_ROUTES
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "AUDIT_LOG_PATH", str(path))
    audit_module.reset_audit_sink()
    try:
        _get(export_client)
    finally:
        audit_module.reset_audit_sink()
    [line] = [json.loads(x) for x in path.read_text().splitlines()]
    assert line["path"] == "/export"
    assert any(e.get("api_key_id") == 1 for e in line["timeline"])
