"""Tests for structured request logging: the timeline, audit-tier routing,
the local JSONL sink, and unhandled-exception traceback filtering.
"""

import json

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from glow_api import audit as audit_module
from glow_api.data import get_datastore
from glow_api.database import get_db
from glow_api.main import app
from glow_api.settings import settings


@pytest.fixture(autouse=True)
def _reset_audit_sink():
    """Ensure each test starts/ends with a clean audit-sink logger cache, so
    changes to settings.AUDIT_LOG_PATH within a test actually take effect."""
    audit_module.reset_audit_sink()
    yield
    audit_module.reset_audit_sink()


@pytest.fixture()
def client_no_raise(db_session, sample_user, sample_schools, sample_df):
    """Like the `client` fixture, but lets unhandled exceptions surface as a
    real 500 response instead of propagating out of the test client."""
    from tests.conftest import _make_mock_datastore

    fake_store = _make_mock_datastore(sample_df)

    def override_get_db():
        yield db_session

    def override_get_datastore():
        return fake_store

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_datastore] = override_get_datastore

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


def _events(logs, event_name):
    return [e for e in logs if e.get("event") == event_name]


class TestAuditTimeline:
    def test_school_scoped_query_logs_full_timeline(self, auth_client, sample_schools):
        school_id = sample_schools["Focus School Academy"].id
        token_resp = auth_client.post(
            "/auth/dev-login", json={"role": "school", "school_id": school_id}
        )
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]

        with capture_logs() as logs:
            resp = auth_client.get(
                "/query/",
                params={"school_id": school_id, "v": ["bw_wbeing_1"]},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        completed = _events(logs, "request_completed")
        assert len(completed) == 1
        entry = completed[0]
        assert entry["audit"] is True
        stages = [t["stage"] for t in entry["timeline"]]
        assert stages == ["auth_assessed", "query_executed"]
        assert entry["timeline"][0]["success"] is True
        assert entry["timeline"][0]["outcome"] == "success"
        assert entry["timeline"][1]["computed"] is True
        assert entry["timeline"][1]["etag_matched"] is False

    def test_dataset_scoped_query_is_audit_tier_and_anonymous(self, client):
        with capture_logs() as logs:
            resp = client.get("/query/", params={"v": ["bw_wbeing_1"]})

        assert resp.status_code == 200
        entry = _events(logs, "request_completed")[0]
        assert entry["audit"] is True
        stages = [t["stage"] for t in entry["timeline"]]
        assert stages == ["auth_assessed", "query_executed"]
        assert entry["timeline"][0]["outcome"] == "anonymous"

    def test_health_is_not_audit_tier(self, client):
        with capture_logs() as logs:
            resp = client.get("/health")

        assert resp.status_code == 200
        entry = _events(logs, "request_completed")[0]
        assert entry["audit"] is False
        assert "timeline" not in entry


class TestUnhandledExceptionLogging:
    def test_unhandled_exception_returns_500_with_filtered_trace(
        self, client_no_raise, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("glow_api.routers.query.execute_query", boom)

        with capture_logs() as logs:
            resp = client_no_raise.get("/query/", params={"v": ["bw_wbeing_1"]})

        assert resp.status_code == 500
        failed = _events(logs, "request_failed")
        assert len(failed) == 1
        error = failed[0]["error"]
        assert error["message"] == "boom"
        assert error["type"] == "RuntimeError"
        assert len(error["trace"]) > 0
        for frame in error["trace"]:
            assert "glow_api" in frame["file"]
            assert "starlette" not in frame["file"]
            assert "fastapi" not in frame["file"]


class TestAuditFileSink:
    def test_non_audit_route_does_not_write_file(self, client, tmp_path, monkeypatch):
        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(settings, "AUDIT_LOG_PATH", str(audit_path))
        audit_module.reset_audit_sink()

        resp = client.get("/health")
        assert resp.status_code == 200
        assert not audit_path.exists() or audit_path.read_text() == ""

    def test_audit_route_writes_one_json_line(self, client, tmp_path, monkeypatch):
        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(settings, "AUDIT_LOG_PATH", str(audit_path))
        audit_module.reset_audit_sink()

        resp = client.get("/query/", params={"v": ["bw_wbeing_1"]})
        assert resp.status_code == 200

        lines = [ln for ln in audit_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["path"] == "/query/"
        assert payload["audit"] is True


class TestAdminMutationAuditEvents:
    def test_create_user_logs_actor_and_target(self, admin_client):
        with capture_logs() as logs:
            resp = admin_client.post(
                "/admin/users",
                json={
                    "username": "brandnewuser",
                    "password": "s3cret-pass",
                    "school_ids": [],
                    "is_admin": False,
                },
            )
        assert resp.status_code == 201
        entry = _events(logs, "request_completed")[0]
        mutation = entry["timeline"][0]
        assert mutation["stage"] == "admin_mutation"
        assert mutation["action"] == "create_user"
        assert mutation["actor_username"] == "adminuser"
        assert mutation["target_username"] == "brandnewuser"
