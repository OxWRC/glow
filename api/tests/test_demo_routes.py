from glow_api.audit import AUDIT_ROUTES
from glow_api.contract_examples import get_example
from glow_api.database import list_schools
from glow_api.models import DemoInfo


def test_demo_info_lists_schools(auth_client, sample_schools):
    resp = auth_client.get("/demo/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schools"] == sorted(body["schools"], key=lambda s: s["id"])
    assert {s["name"] for s in body["schools"]} == {
        s.name for s in sample_schools.values()
    }


def test_demo_info_matches_contract_shape():
    DemoInfo.model_validate(get_example("demo.info")["response"])


def test_demo_reset_invalidates_existing_tokens(auth_client):
    token = auth_client.post("/demo/login", json={"role": "admin"}).json()[
        "access_token"
    ]
    assert auth_client.post("/demo/reset").status_code == 204
    me = auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


def test_demo_reset_restores_schools(auth_client, db_session):
    before = {s.name for s in list_schools(db_session)}
    assert auth_client.post("/demo/reset").status_code == 204
    assert {s.name for s in list_schools(db_session)} == before


def test_demo_reset_503_when_data_not_loaded(auth_client_empty_data, sample_schools):
    resp = auth_client_empty_data.post("/demo/reset")
    assert resp.status_code == 503
    # nothing was wiped
    assert auth_client_empty_data.get("/demo/info").json()["schools"]


def test_demo_reset_is_audit_tier():
    assert ("POST", "/demo/reset") in AUDIT_ROUTES
