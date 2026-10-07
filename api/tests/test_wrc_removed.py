"""The WRC user role no longer exists: no routes, no flag, no demo role."""

from glow_api.metadata_models import User
from glow_api.models import MeAuthenticated, UserRead


def test_wrc_routes_are_gone(client):
    assert client.get("/wrc/ping").status_code == 404
    assert client.get("/wrc/keys").status_code == 404


def test_user_models_have_no_wrc_flag():
    assert not hasattr(User, "is_wrc")
    assert "is_wrc" not in UserRead.model_fields
    assert "is_wrc" not in MeAuthenticated.model_fields


def test_demo_login_rejects_wrc_role(client):
    response = client.post("/demo/login", json={"role": "wrc"})
    assert response.status_code == 422
