"""Admin-issued pseudonymous-export API keys: management is admin-JWT only."""

from datetime import datetime, timedelta, timezone

from glow_api.api_keys import issue_api_key, key_status
from glow_api.audit import AUDIT_ROUTES
from glow_api.database import get_api_key_by_id


def test_admin_creates_key_and_sees_it_once(admin_client, admin_user):
    response = admin_client.post("/admin/api-keys", json={"name": "WRC 2026"})
    assert response.status_code == 201
    body = response.json()
    assert body["key"].startswith("glow_")
    assert body["name"] == "WRC 2026"

    [listed] = admin_client.get("/admin/api-keys").json()
    assert "key" not in listed
    assert listed["prefix"] == body["key"][:12]
    assert listed["created_by"] == admin_user.username
    assert listed["use_count"] == 0
    assert listed["last_used_at"] is None


def test_create_rejects_out_of_range_expiry_and_blank_name(admin_client):
    assert (
        admin_client.post(
            "/admin/api-keys", json={"name": "x", "expires_in_days": 366}
        ).status_code
        == 422
    )
    assert admin_client.post("/admin/api-keys", json={"name": ""}).status_code == 422


def test_admin_revokes_key(admin_client, db_session):
    created = admin_client.post("/admin/api-keys", json={"name": "k"}).json()
    assert admin_client.delete(f"/admin/api-keys/{created['id']}").status_code == 204
    record = get_api_key_by_id(db_session, created["id"])
    assert record.revoked_at is not None
    first_revoked_at = record.revoked_at
    # Revoking again keeps the original revocation time.
    assert admin_client.delete(f"/admin/api-keys/{created['id']}").status_code == 204
    db_session.refresh(record)
    assert record.revoked_at == first_revoked_at


def test_revoke_unknown_key_404s(admin_client):
    assert admin_client.delete("/admin/api-keys/9999").status_code == 404


def test_non_admin_cannot_manage_keys(client):
    assert client.get("/admin/api-keys").status_code == 403
    assert client.post("/admin/api-keys", json={"name": "x"}).status_code == 403
    assert client.delete("/admin/api-keys/1").status_code == 403


def test_api_key_cannot_manage_keys(auth_client, db_session):
    _, raw = issue_api_key(db_session, "k", None, None)
    headers = {"X-API-Key": raw}
    assert auth_client.get("/admin/api-keys", headers=headers).status_code == 401
    assert (
        auth_client.post(
            "/admin/api-keys", json={"name": "x"}, headers=headers
        ).status_code
        == 401
    )
    assert auth_client.delete("/admin/api-keys/1", headers=headers).status_code == 401


def test_key_status(db_session):
    record, _ = issue_api_key(db_session, "k", 1, None)
    now = datetime.now(timezone.utc)
    assert key_status(record, now) == "active"
    assert key_status(record, now + timedelta(days=2)) == "expired"
    record.revoked_at = now
    assert key_status(record, now) == "revoked"


def test_key_management_routes_are_audit_tier():
    assert ("POST", "/admin/api-keys") in AUDIT_ROUTES
    assert ("DELETE", "/admin/api-keys/{key_id}") in AUDIT_ROUTES
