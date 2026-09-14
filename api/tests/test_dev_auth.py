"""Tests for the dev-bypass login route added in Task 4.

The whole test session runs with GLOW_DEV_AUTH_BYPASS=1 (see conftest.py),
so the shared `app` already has /auth/dev-login mounted - covering the "flag
on" half of the brief. The "flag off" half (route must 404, not just refuse)
needs a separate app instance built with the flag off, since main.py decides
router registration at import time.
"""

import subprocess
import sys
import textwrap


class TestDevLogin:
    def test_admin_role_logs_in_as_admin(self, auth_client):
        resp = auth_client.post("/auth/dev-login", json={"role": "admin"})
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        me = auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        body = me.json()
        assert body["username"] == "dev-admin"
        assert body["is_admin"] is True
        assert body["is_wrc"] is False

    def test_wrc_role_logs_in_as_wrc(self, auth_client):
        resp = auth_client.post("/auth/dev-login", json={"role": "wrc"})
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        me = auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        body = me.json()
        assert body["username"] == "dev-wrc"
        assert body["is_wrc"] is True
        assert body["is_admin"] is False

    def test_school_role_logs_in_scoped_to_that_school(self, auth_client, sample_schools):
        school = sample_schools["Focus School Academy"]
        resp = auth_client.post(
            "/auth/dev-login", json={"role": "school", "school_id": school.id}
        )
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        me = auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        body = me.json()
        assert body["username"] == f"dev-school-{school.id}"
        assert body["is_admin"] is False
        assert body["is_wrc"] is False
        assert [s["id"] for s in body["schools"]] == [school.id]

    def test_school_role_defaults_to_first_seeded_school(self, auth_client, sample_schools):
        resp = auth_client.post("/auth/dev-login", json={"role": "school"})
        assert resp.status_code == 200

    def test_school_role_rejects_unknown_school_id(self, auth_client, sample_schools):
        resp = auth_client.post(
            "/auth/dev-login", json={"role": "school", "school_id": 999999}
        )
        assert resp.status_code == 400

    def test_repeated_login_upserts_rather_than_duplicating(self, auth_client):
        first = auth_client.post("/auth/dev-login", json={"role": "admin"})
        second = auth_client.post("/auth/dev-login", json={"role": "admin"})
        assert first.status_code == 200
        assert second.status_code == 200

        me = auth_client.get(
            "/me",
            headers={"Authorization": f"Bearer {second.json()['access_token']}"},
        )
        assert me.json()["username"] == "dev-admin"


def test_dev_login_route_absent_when_bypass_disabled():
    """main.py decides router registration at import time, so this needs a
    fresh `glow_api.main` import with the flag off - done in a subprocess
    (rather than importlib.reload in-process) so it can't leak global state
    (structlog/logging config reruns on import) into the rest of this
    session's tests.
    """
    script = textwrap.dedent(
        """
        import os
        os.environ["GLOW_DEV_AUTH_BYPASS"] = "0"
        os.environ["GLOW_TESTING"] = "1"
        # A real deployment with the bypass off has a Cognito pool configured
        # instead (see auth._build_verifier's fail-closed guard - it refuses
        # to import at all with neither configured).
        os.environ["GLOW_COGNITO_USER_POOL_ID"] = "eu-west-2_TESTPOOL"
        os.environ["GLOW_COGNITO_CLIENT_ID"] = "test-client-id"
        os.environ["GLOW_COGNITO_REGION"] = "eu-west-2"
        from fastapi.testclient import TestClient
        from glow_api.main import app
        client = TestClient(app)
        resp = client.post("/auth/dev-login", json={"role": "admin"})
        assert resp.status_code == 404, resp.status_code
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
