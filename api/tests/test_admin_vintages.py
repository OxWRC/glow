"""POST/GET /admin/vintages: admin-JWT only, mirrors /admin/api-keys."""

import pytest

from glow_api.audit import AUDIT_ROUTES


@pytest.fixture()
def vintages_dir(monkeypatch, tmp_path):
    import glow_api.vintages as vintages_module

    monkeypatch.setattr(vintages_module.settings, "VINTAGES_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_vintage_caches():
    from glow_api import vintages as vintages_module

    vintages_module._latest_prefix_cache.invalidate()
    vintages_module._vintage_cache.clear()
    yield
    vintages_module._latest_prefix_cache.invalidate()
    vintages_module._vintage_cache.clear()


def test_admin_cuts_vintage_and_sees_it_listed(admin_client, admin_user, vintages_dir):
    response = admin_client.post("/admin/vintages")

    assert response.status_code == 201
    body = response.json()
    assert body["created_by_username"] == admin_user.username
    assert body["row_count"] >= 0

    [listed] = admin_client.get("/admin/vintages").json()
    assert listed["prefix"] == body["prefix"]


def test_non_admin_cannot_manage_vintages(client, vintages_dir):
    assert client.get("/admin/vintages").status_code == 403
    assert client.post("/admin/vintages").status_code == 403


def test_api_key_cannot_manage_vintages(auth_client, db_session, vintages_dir):
    from glow_api.api_keys import issue_api_key

    _, raw = issue_api_key(db_session, "k", None, None)
    headers = {"X-API-Key": raw}

    assert auth_client.get("/admin/vintages", headers=headers).status_code == 401
    assert auth_client.post("/admin/vintages", headers=headers).status_code == 401


def test_vintage_creation_is_audit_tier():
    assert ("POST", "/admin/vintages") in AUDIT_ROUTES


def test_listing_vintages_before_any_cut_is_empty(admin_client, vintages_dir):
    assert admin_client.get("/admin/vintages").json() == []


def test_cut_without_vintages_dir_is_a_clear_503_not_an_opaque_500(
    admin_client, monkeypatch
):
    # The demo shipped without GLOW_VINTAGES_DIR: the unhandled error skipped CORS, and the dashboard could
    # only report "NetworkError".
    import glow_api.vintages as vintages_module

    monkeypatch.setattr(vintages_module.settings, "VINTAGES_DIR", None)
    origin = "http://localhost:3000"

    response = admin_client.post("/admin/vintages", headers={"Origin": origin})

    assert response.status_code == 503
    assert "GLOW_VINTAGES_DIR" in response.json()["detail"]
    assert "access-control-allow-origin" in response.headers


def test_cut_when_odk_unreachable_is_a_502_and_cuts_nothing(admin_client, vintages_dir):
    import glow_api.data as data_module

    def boom(**kwargs):
        raise RuntimeError("ODK down")

    data_module.datastore._odk_client.fetch_submissions = boom
    origin = "http://localhost:3000"

    response = admin_client.post("/admin/vintages", headers={"Origin": origin})

    assert response.status_code == 502
    assert "ODK" in response.json()["detail"]
    assert "access-control-allow-origin" in response.headers
    assert admin_client.get("/admin/vintages").json() == []
