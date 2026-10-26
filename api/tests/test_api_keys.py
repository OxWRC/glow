"""Admin-issued pseudonymous-export API keys.

Admins never see a key: they issue an unclaimed record, the recipient gets a
one-time claim link by email and reveals the key themselves.
"""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import pytest

import glow_api.api_keys as api_keys_module
from glow_api.api_keys import issue_api_key, key_status
from glow_api.audit import AUDIT_ROUTES
from glow_api.auth import create_access_token, verifier
from glow_api.database import get_api_key_by_id
from glow_api.settings import settings


@pytest.fixture()
def outbox(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(settings, "SMTP_HOST", "mail.example")
    monkeypatch.setattr(settings, "DASHBOARD_URL", "https://glow.example")
    monkeypatch.setattr(
        api_keys_module,
        "send_mail",
        lambda to, subject, body: sent.append(
            {"to": to, "subject": subject, "body": body}
        ),
    )
    return sent


def _token(mail: dict) -> str:
    match = re.search(r"https://glow\.example/en/claim-key#token=(\S+)", mail["body"])
    assert match, mail["body"]
    return unquote(match.group(1))


def _create(admin_client, **body):
    return admin_client.post(
        "/admin/api-keys", json={"email": "Analyst@Example.org", **body}
    )


def _claim(client, token):
    return client.post("/api-keys/claim", json={"token": token})


def test_admin_creates_unclaimed_key_and_link_is_emailed(admin_client, outbox):
    response = _create(admin_client, name="WRC 2026", notes="Termly pull")
    assert response.status_code == 201
    body = response.json()
    assert "key" not in body and "prefix" not in body
    assert body["email"] == "analyst@example.org"
    assert body["status"] == "unclaimed"
    assert body["expires_at"] is None

    [mail] = outbox
    assert mail["to"] == "analyst@example.org"
    assert _token(mail)

    [listed] = admin_client.get("/admin/api-keys").json()
    assert listed["name"] == "WRC 2026"
    assert listed["notes"] == "Termly pull"
    assert listed["status"] == "unclaimed"
    assert listed["use_count"] == 0
    assert listed["last_used_at"] is None


def test_create_validates_input(admin_client, outbox):
    assert admin_client.post("/admin/api-keys", json={}).status_code == 422
    assert _create(admin_client, email="not-an-email").status_code == 422
    assert _create(admin_client, expires_in_days=0).status_code == 422
    assert _create(admin_client, expires_in_days=366).status_code == 422
    assert outbox == []


def test_create_with_expiry(admin_client, outbox):
    body = _create(admin_client, expires_in_days=30).json()
    created = datetime.fromisoformat(body["created_at"])
    assert datetime.fromisoformat(body["expires_at"]) - created == timedelta(days=30)


def test_create_without_smtp_is_503_and_leaves_no_key(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    assert _create(admin_client).status_code == 503
    assert admin_client.get("/admin/api-keys").json() == []


def test_create_mail_failure_is_502_and_leaves_no_key(
    admin_client, outbox, monkeypatch
):
    def boom(*_):
        raise OSError("relay down")

    monkeypatch.setattr(api_keys_module, "send_mail", boom)
    assert _create(admin_client).status_code == 502
    assert admin_client.get("/admin/api-keys").json() == []


def test_claim_reveals_key_once(admin_client, outbox):
    key_id = _create(admin_client).json()["id"]
    token = _token(outbox[0])

    first = _claim(admin_client, token)
    assert first.status_code == 200
    raw = first.json()["key"]
    assert raw.startswith("glow_")

    assert _claim(admin_client, token).status_code == 410
    [listed] = admin_client.get("/admin/api-keys").json()
    assert listed["id"] == key_id
    assert listed["status"] == "active"
    assert listed["claimed_at"] is not None
    assert raw not in str(listed)


def test_claimed_key_authenticates_and_unclaimed_does_not(admin_client, outbox):
    _create(admin_client)
    # Unclaimed: no hash stored, so nothing can authenticate as it.
    assert (
        admin_client.delete("/api-keys/self", headers={"X-API-Key": ""}).status_code
        == 401
    )
    raw = _claim(admin_client, _token(outbox[0])).json()["key"]
    assert (
        admin_client.delete("/api-keys/self", headers={"X-API-Key": raw}).status_code
        == 204
    )


def test_expired_claim_link_is_rejected(admin_client, outbox, monkeypatch):
    monkeypatch.setattr(settings, "API_KEY_CLAIM_HOURS", -1)
    _create(admin_client)
    assert _claim(admin_client, _token(outbox[0])).status_code == 410


def test_revoked_key_cannot_be_claimed(admin_client, outbox):
    key_id = _create(admin_client).json()["id"]
    admin_client.delete(f"/admin/api-keys/{key_id}")
    assert _claim(admin_client, _token(outbox[0])).status_code == 410


def test_expired_key_cannot_be_claimed(admin_client, outbox, db_session):
    key_id = _create(admin_client, expires_in_days=1).json()["id"]
    record = get_api_key_by_id(db_session, key_id)
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert _claim(admin_client, _token(outbox[0])).status_code == 410


def test_other_tokens_cannot_claim(client):
    assert _claim(client, "garbage").status_code == 410
    access = create_access_token({"sub": "dev-admin", "key_id": 1})
    assert _claim(client, access).status_code == 410


def test_claim_token_is_not_an_access_token(admin_client, outbox):
    _create(admin_client)
    with pytest.raises(Exception):
        verifier.decode(_token(outbox[0]))


def test_regenerate_revokes_old_and_reissues_copy(admin_client, outbox, db_session):
    old = _create(
        admin_client, name="Pipeline", notes="nightly", expires_in_days=10
    ).json()
    response = admin_client.post(f"/admin/api-keys/{old['id']}/regenerate")
    assert response.status_code == 201
    new = response.json()

    assert new["id"] != old["id"]
    assert new["status"] == "unclaimed"
    for field in ("email", "name", "notes"):
        assert new[field] == old[field]
    lifetime = datetime.fromisoformat(new["expires_at"]) - datetime.fromisoformat(
        new["created_at"]
    )
    assert lifetime == timedelta(days=10)
    assert get_api_key_by_id(db_session, old["id"]).revoked_at is not None
    assert len(outbox) == 2
    assert _token(outbox[0]) != _token(outbox[1])


def test_regenerate_unknown_key_404s(admin_client, outbox):
    assert admin_client.post("/admin/api-keys/9999/regenerate").status_code == 404


def test_regenerate_cli_key_without_email_400s(admin_client, outbox, db_session):
    record, _ = issue_api_key(db_session, name="cli")
    response = admin_client.post(f"/admin/api-keys/{record.id}/regenerate")
    assert response.status_code == 400
    db_session.refresh(record)
    assert record.revoked_at is None


def test_admin_revokes_key(admin_client, db_session, outbox):
    created = _create(admin_client).json()
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


def test_key_revokes_itself(auth_client, db_session):
    record, raw = issue_api_key(db_session, name="k")
    headers = {"X-API-Key": raw}
    assert auth_client.delete("/api-keys/self", headers=headers).status_code == 204
    db_session.refresh(record)
    assert record.revoked_at is not None
    assert auth_client.delete("/api-keys/self", headers=headers).status_code == 401
    assert auth_client.delete("/api-keys/self").status_code == 401


def test_non_admin_cannot_manage_keys(client):
    assert client.get("/admin/api-keys").status_code == 403
    assert client.post("/admin/api-keys", json={"email": "a@b.co"}).status_code == 403
    assert client.post("/admin/api-keys/1/regenerate").status_code == 403
    assert client.delete("/admin/api-keys/1").status_code == 403


def test_api_key_cannot_manage_keys(auth_client, db_session):
    _, raw = issue_api_key(db_session, name="k")
    headers = {"X-API-Key": raw}
    assert auth_client.get("/admin/api-keys", headers=headers).status_code == 401
    assert (
        auth_client.post(
            "/admin/api-keys", json={"email": "a@b.co"}, headers=headers
        ).status_code
        == 401
    )
    assert auth_client.delete("/admin/api-keys/1", headers=headers).status_code == 401


def test_key_status(db_session):
    record, _ = issue_api_key(db_session, name="k", expires_in_days=1)
    now = datetime.now(timezone.utc)
    assert key_status(record, now) == "active"
    assert key_status(record, now + timedelta(days=2)) == "expired"
    record.revoked_at = now
    assert key_status(record, now) == "revoked"
    record.revoked_at, record.key_hash = None, None
    assert key_status(record, now) == "unclaimed"


def test_key_without_expiry_never_expires(db_session):
    record, _ = issue_api_key(db_session, name="k")
    assert record.expires_at is None
    assert key_status(record, datetime.now(timezone.utc) + timedelta(days=9999)) == (
        "active"
    )


def test_key_routes_are_audit_tier():
    for route in [
        ("POST", "/admin/api-keys"),
        ("POST", "/admin/api-keys/{key_id}/regenerate"),
        ("DELETE", "/admin/api-keys/{key_id}"),
        ("POST", "/api-keys/claim"),
        ("DELETE", "/api-keys/self"),
    ]:
        assert route in AUDIT_ROUTES
