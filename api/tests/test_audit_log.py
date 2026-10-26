"""Queryable audit log (#41): WHO did WHAT and WHEN, stored in the metadata
DB and browsable by admins at GET /admin/audit."""

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from glow_api import audit as audit_module
from glow_api.api_keys import issue_api_key
from glow_api.auth import create_access_token
from glow_api.counts_store import CountsStore
from glow_api.metadata_models import AuditEvent
from tests.mock_odk import MockODKClient


def _events(db_session) -> list[AuditEvent]:
    db_session.expire_all()
    return db_session.query(AuditEvent).order_by(AuditEvent.id).all()


def _bearer(db_session, user, sub="admin-sub") -> dict:
    user.oidc_sub = sub
    db_session.commit()
    return {"Authorization": f"Bearer {create_access_token({'sub': sub})}"}


@pytest.fixture()
def keyed(auth_client, db_session):
    record, raw = issue_api_key(db_session, name="termly")
    record.email = "analyst@example.org"
    db_session.commit()
    return record, raw


def test_api_key_who_is_email_plus_short_code(auth_client, db_session, keyed):
    record, raw = keyed
    auth_client.get("/export", headers={"X-API-Key": raw})

    [event] = _events(db_session)
    assert event.actor == f"analyst@example.org#{record.key_hash[:7]}"
    assert event.action == "data.export"
    assert event.ts.tzinfo is not None


def test_cli_issued_key_falls_back_to_name(auth_client, db_session):
    record, raw = issue_api_key(db_session, name="nightly")
    auth_client.get("/export", headers={"X-API-Key": raw})

    [event] = _events(db_session)
    assert event.actor == f"nightly#{record.key_hash[:7]}"


def test_failed_auth_is_anonymous(auth_client, db_session):
    auth_client.get("/export", headers={"X-API-Key": "glow_nope"})

    [event] = _events(db_session)
    assert event.actor == "anonymous"
    assert event.status_code == 401


def test_user_who_is_email(auth_client, db_session, admin_user):
    admin_user.email = "boss@example.org"
    headers = _bearer(db_session, admin_user)
    resp = auth_client.post(
        "/admin/users", json={"email": "new@example.org"}, headers=headers
    )
    assert resp.status_code == 201

    [event] = _events(db_session)
    assert event.actor == "boss@example.org"
    assert event.action == "user.create"
    assert event.status_code == 201
    [mutation] = [
        e for e in event.details["timeline"] if e["stage"] == "admin_mutation"
    ]
    assert mutation["target_username"] == "new@example.org"


def test_non_audit_route_records_nothing(auth_client, db_session):
    auth_client.get("/health")
    assert _events(db_session) == []


def test_scheduled_vintage_cut_is_system(db_session, monkeypatch, tmp_path):
    from glow_api import vintages
    from glow_api.data import DataStore
    from glow_api.settings import settings

    monkeypatch.setattr(settings, "VINTAGES_DIR", str(tmp_path))
    df = pd.DataFrame({"uid": ["S1"], "school": ["Focus"], "bw_wbeing_1": [3]})
    vintages.scheduled_cut_vintage(
        DataStore(odk_client=MockODKClient(submissions_df=df))
    )

    [event] = _events(db_session)
    assert (event.actor, event.action) == ("system:scheduler", "vintage.cut")
    assert event.details["row_count"] == 1


def test_counts_poll_records_new_records_only_when_changed(db_session):
    frame = pd.DataFrame(
        {"uid": ["S1"], "school": ["Focus"], "createdAt": ["2024-10-01T00:00:00Z"]}
    )
    client = MockODKClient(submissions_by_form={"f1": frame})
    store = CountsStore(odk_client=client, refresh_hours=0)

    store.refresh()
    store.refresh()  # nothing new

    [event] = _events(db_session)
    assert (event.actor, event.action) == ("system:poll", "data.ingested")
    assert event.details == {"rows": 1, "new_rows": 1}


def test_counts_poll_failure_is_recorded(db_session):
    class Broken(MockODKClient):
        def fetch_submissions(self, *a, **k):
            raise RuntimeError("down")

    CountsStore(odk_client=Broken(), refresh_hours=0).refresh()

    [event] = _events(db_session)
    assert (event.actor, event.action) == ("system:poll", "data.poll_failed")


class TestAdminAuditEndpoint:
    @pytest.fixture()
    def seeded(self, db_session):
        base = datetime(2026, 10, 1, tzinfo=timezone.utc)
        for i, (actor, action) in enumerate(
            [
                ("boss@example.org", "user.create"),
                ("analyst@example.org#abc1234", "data.export"),
                ("system:scheduler", "vintage.cut"),
                ("analyst@example.org#def5678", "data.export"),
            ]
        ):
            audit_module.record(
                db_session,
                actor=actor,
                action=action,
                status_code=200,
                ts=base + timedelta(days=i),
            )

    def _get(self, admin_client, **params):
        resp = admin_client.get("/admin/audit", params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_newest_first(self, admin_client, seeded):
        entries = self._get(admin_client)
        assert [e["action"] for e in entries][:4] == [
            "data.export",
            "vintage.cut",
            "data.export",
            "user.create",
        ]
        assert entries[0]["actor"] == "analyst@example.org#def5678"
        assert entries[0]["ts"].endswith(("Z", "+00:00"))

    def test_filter_who_matches_email_or_short_code(self, admin_client, seeded):
        by_email = self._get(admin_client, who="ANALYST@")
        assert {e["actor"] for e in by_email} == {
            "analyst@example.org#abc1234",
            "analyst@example.org#def5678",
        }
        [by_code] = self._get(admin_client, who="abc1234")
        assert by_code["actor"] == "analyst@example.org#abc1234"

    def test_filter_what(self, admin_client, seeded):
        entries = self._get(admin_client, what="vintage")
        assert [e["action"] for e in entries] == ["vintage.cut"]

    def test_filter_when(self, admin_client, seeded):
        entries = self._get(
            admin_client,
            since="2026-10-02T00:00:00Z",
            until="2026-10-03T00:00:00Z",
        )
        assert [e["action"] for e in entries if e["action"] != "audit.read"] == [
            "vintage.cut",
            "data.export",
        ]

    def test_limit_and_offset(self, admin_client, seeded):
        [first] = self._get(admin_client, what="data.export", limit=1)
        [second] = self._get(admin_client, what="data.export", limit=1, offset=1)
        assert first["actor"].endswith("def5678")
        assert second["actor"].endswith("abc1234")

    def test_requires_admin(self, client):
        assert client.get("/admin/audit").status_code == 403

    def test_reading_the_log_is_itself_audited(self, admin_client, db_session):
        self._get(admin_client)
        assert [e.action for e in _events(db_session)] == ["audit.read"]


def test_api_key_read_exposes_short_code(admin_client, db_session):
    record, _ = issue_api_key(db_session, name="x")
    [listed] = admin_client.get("/admin/api-keys").json()
    assert listed["short_code"] == record.key_hash[:7]


class TestAdminAuditExport:
    @pytest.fixture()
    def seeded(self, db_session):
        base = datetime(2026, 10, 1, tzinfo=timezone.utc)
        for i, (actor, action) in enumerate(
            [
                ("boss@example.org", "user.create"),
                ("analyst@example.org#abc1234", "data.export"),
                ("system:scheduler", "vintage.cut"),
            ]
        ):
            audit_module.record(
                db_session,
                actor=actor,
                action=action,
                details={"n": i},
                ts=base + timedelta(days=i),
            )

    def _export(self, admin_client, **params):
        resp = admin_client.get("/admin/audit/export", params=params)
        assert resp.status_code == 200, resp.text
        return resp, [json.loads(line) for line in resp.text.splitlines()]

    def test_downloads_jsonl_oldest_first(self, admin_client, seeded):
        resp, lines = self._export(admin_client)
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        assert resp.headers["content-disposition"].startswith("attachment;")
        assert ".jsonl" in resp.headers["content-disposition"]
        assert [line["action"] for line in lines] == [
            "user.create",
            "data.export",
            "vintage.cut",
        ]
        assert lines[1]["actor"] == "analyst@example.org#abc1234"
        assert lines[1]["details"] == {"n": 1}

    def test_honours_who_what_when(self, admin_client, seeded):
        _, lines = self._export(admin_client, who="abc1234")
        assert [line["action"] for line in lines] == ["data.export"]
        _, lines = self._export(admin_client, what="vintage")
        assert [line["action"] for line in lines] == ["vintage.cut"]
        _, lines = self._export(admin_client, until="2026-10-01T12:00:00Z")
        assert [line["action"] for line in lines] == ["user.create"]

    def test_is_itself_audited(self, admin_client, db_session):
        self._export(admin_client)
        assert [e.action for e in _events(db_session)] == ["audit.export"]

    def test_requires_admin(self, client):
        assert client.get("/admin/audit/export").status_code == 403
