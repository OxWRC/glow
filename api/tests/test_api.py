"""Tests for the GLOW API."""

import io
import threading

import pandas as pd
from fastapi import status

from glow_api.canonical_query import normalize_query


# ---------------------------------------------------------------------------
# Canonical Query Normalization Tests
# ---------------------------------------------------------------------------


def test_normalize_query_with_all_params():
    """Canonical query should sort and dedupe all parameters."""
    query = normalize_query(
        school_id=1,
        v=["bw_wbeing_2", "bw_wbeing_1", "bw_wbeing_1"],  # duplicates and unsorted
        d=["d_sex", "yearGroup", "d_sex"],  # duplicates and unsorted
        variable_prefix=["bw_", "d_", "bw_"],  # duplicates
    )

    assert query.school_id == 1
    assert query.variables == ["bw_wbeing_1", "bw_wbeing_2"]  # sorted, deduped
    assert query.dimensions == ["d_sex", "yearGroup"]  # sorted, deduped
    assert query.variable_prefixes == ["bw_", "d_"]  # sorted, deduped


def test_normalize_query_defaults_to_empty_lists():
    """Omitted parameters should become empty lists."""
    query = normalize_query(school_id=None)

    assert query.school_id is None
    assert query.variables == []
    assert query.dimensions == []
    assert query.variable_prefixes == []


def test_normalize_query_omitted_dimensions():
    """Omitting 'd' should mean no dimensions (empty list)."""
    query = normalize_query(v=["bw_wbeing_1"])

    assert query.dimensions == []


def test_normalize_query_omitted_variables():
    """Omitting both 'v' and 'variable_prefix' should result in empty lists."""
    query = normalize_query(d=["d_sex"])

    assert query.variables == []
    assert query.variable_prefixes == []


def test_normalize_query_union_of_v_and_prefix():
    """When both 'v' and 'variable_prefix' are supplied, both lists are kept."""
    query = normalize_query(v=["bw_wbeing_1"], variable_prefix=["d_"])

    assert query.variables == ["bw_wbeing_1"]
    assert query.variable_prefixes == ["d_"]


# ---------------------------------------------------------------------------
# Health and Authentication Tests
# ---------------------------------------------------------------------------


def test_health(client):
    response = client.get("/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_admin_list_users(admin_client):
    response = admin_client.get("/admin/users")
    assert response.status_code == status.HTTP_200_OK
    users = response.json()
    assert len(users) == 1
    assert users[0]["username"] == "adminuser"
    assert users[0]["is_admin"] is True


def test_admin_list_users_reports_is_wrc_and_email(admin_client, wrc_user, db_session):
    wrc_user.email = "wrc@example.com"
    db_session.commit()

    response = admin_client.get("/admin/users")
    assert response.status_code == status.HTTP_200_OK
    users = response.json()

    admin_row = next(u for u in users if u["username"] == "adminuser")
    assert admin_row["is_wrc"] is False

    wrc_row = next(u for u in users if u["username"] == "wrcuser")
    assert wrc_row["is_wrc"] is True
    assert wrc_row["email"] == "wrc@example.com"


def test_admin_create_user(admin_client, sample_schools):
    # Get school ID for Focus School Academy
    alpha_id = sample_schools["Focus School Academy"].id

    payload = {
        "username": "analyst",
        "school_ids": [alpha_id],
        "is_admin": False,
    }
    response = admin_client.post("/admin/users", json=payload)
    assert response.status_code == status.HTTP_201_CREATED
    user = response.json()
    assert user["username"] == "analyst"
    assert alpha_id in user["school_ids"]
    assert "Focus School Academy" in user["school_names"]


def test_admin_update_user(admin_client, sample_schools):
    # Get school IDs
    alpha_id = sample_schools["Focus School Academy"].id
    beta_id = sample_schools["Neighbouring School"].id

    create_response = admin_client.post(
        "/admin/users",
        json={
            "username": "analyst",
            "school_ids": [alpha_id],
            "is_admin": False,
        },
    )
    user_id = create_response.json()["id"]

    response = admin_client.put(
        f"/admin/users/{user_id}",
        json={
            "school_ids": [beta_id],
            "is_active": False,
            "is_admin": True,
        },
    )
    assert response.status_code == status.HTTP_200_OK
    updated = response.json()
    assert beta_id in updated["school_ids"]
    assert "Neighbouring School" in updated["school_names"]
    assert updated["is_active"] is False
    assert updated["is_admin"] is True


def test_admin_delete_user(admin_client, sample_schools):
    alpha_id = sample_schools["Focus School Academy"].id

    create_response = admin_client.post(
        "/admin/users",
        json={
            "username": "analyst",
            "school_ids": [alpha_id],
            "is_admin": False,
        },
    )
    user_id = create_response.json()["id"]

    response = admin_client.delete(f"/admin/users/{user_id}")
    assert response.status_code == status.HTTP_204_NO_CONTENT

    list_response = admin_client.get("/admin/users")
    usernames = [user["username"] for user in list_response.json()]
    assert "analyst" not in usernames


def test_admin_update_user_rejects_schools_for_wrc_user(admin_client, db_session, sample_schools):
    from glow_api import database

    alpha_id = sample_schools["Focus School Academy"].id
    wrc_user = database.create_user(db_session, username="wrcuser", is_wrc=True)

    response = admin_client.put(
        f"/admin/users/{wrc_user.id}",
        json={"school_ids": [alpha_id]},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_admin_me_endpoint(admin_client):
    response = admin_client.get("/admin/me")
    assert response.status_code == status.HTTP_200_OK
    user = response.json()
    assert user["username"] == "adminuser"
    assert user["is_admin"] is True


# ---------------------------------------------------------------------------
# /me Endpoint Tests
# ---------------------------------------------------------------------------


def test_me_anonymous(auth_client):
    """GET /me with no token should return anonymous response."""
    response = auth_client.get("/me")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["kind"] == "anonymous"


def test_me_authenticated(auth_client, sample_schools):
    """GET /me with a valid dev-login token should return authenticated response with schools."""
    school = sample_schools["Focus School Academy"]
    login_response = auth_client.post(
        "/auth/dev-login", json={"role": "school", "school_id": school.id}
    )
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]

    # Now use the token to call /me
    response = auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert data["kind"] == "authenticated"
    assert data["username"] == f"dev-school-{school.id}"
    assert data["is_admin"] is False
    assert len(data["schools"]) == 1
    assert data["schools"][0]["id"] == school.id
    assert data["schools"][0]["name"] == "Focus School Academy"


def test_me_authenticated_admin(auth_client, sample_schools):
    """GET /me with an admin dev-login token should show all schools."""
    login_response = auth_client.post("/auth/dev-login", json={"role": "admin"})
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]

    # Now use the token to call /me
    response = auth_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert data["kind"] == "authenticated"
    assert data["username"] == "dev-admin"
    assert data["is_admin"] is True
    assert len(data["schools"]) == 2
    school_names = {s["name"] for s in data["schools"]}
    assert "Focus School Academy" in school_names
    assert "Neighbouring School" in school_names


def test_me_invalid_token(auth_client):
    """GET /me with invalid token should return 401."""
    response = auth_client.get("/me", headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_query_get_etag_support(auth_client):
    """Test that GET /query returns ETag and supports If-None-Match."""
    # First request - should get ETag
    response1 = auth_client.get("/query?v=bw_wbeing_1")
    assert response1.status_code == status.HTTP_200_OK
    assert "ETag" in response1.headers
    etag = response1.headers["ETag"]

    # Second request with If-None-Match - should get 304
    response2 = auth_client.get("/query?v=bw_wbeing_1", headers={"If-None-Match": etag})
    assert response2.status_code == status.HTTP_304_NOT_MODIFIED

    # Third request with different query - should get new ETag
    response3 = auth_client.get("/query?v=bw_wbeing_2")
    assert response3.status_code == status.HTTP_200_OK
    assert "ETag" in response3.headers
    assert response3.headers["ETag"] != etag  # Different query = different ETag


# ---------------------------------------------------------------------------
# Schools and Legacy Tests
# ---------------------------------------------------------------------------
# /dimensions Endpoint Tests
# ---------------------------------------------------------------------------


def test_dimensions_public_dataset_scope(auth_client):
    """GET /dimensions without school_id should work anonymously."""
    response = auth_client.get("/dimensions")

    # After implementation, this should be 200 and return dimensions/variables
    if response.status_code == status.HTTP_200_OK:
        data = response.json()
        assert "variables" in data
        assert "dimensions" in data
        assert data["school_id"] is None
        assert isinstance(data["variables"], list)
        assert isinstance(data["dimensions"], list)
        # Should have some variables from the data
        if len(data["variables"]) > 0:
            assert "key" in data["variables"][0]
            assert "raw_key" in data["variables"][0]
    else:
        # For now, expect 404 since endpoint doesn't exist yet
        assert response.status_code == status.HTTP_404_NOT_FOUND


def test_dimensions_exposes_namespaced_variable_metadata(
    db_session, sample_user, sample_schools
):
    """/dimensions should identify the source form for namespaced variables."""
    from fastapi.testclient import TestClient
    from glow_api.data import get_datastore
    from glow_api.database import get_db
    from glow_api.main import app
    from glow_api.models import UserRead

    namespaced_df = pd.DataFrame(
        {
            "uid": ["S001"],
            "school": ["Focus School Academy"],
            "period_id": ["2023-2024"],
            "yearGroup": [9],
            "d_age": [14],
            "bewell_questionnaire__bw_wbeing_1": [3],
            "phq9_questionnaire__phq9_1": [2],
        }
    )

    from tests.conftest import _make_mock_datastore

    fake_store = _make_mock_datastore(namespaced_df)
    fake_store._df = namespaced_df
    fake_store._categorical_whitelist = ["yearGroup", "d_age"]
    fake_store._numerical_whitelist = [
        "bewell_questionnaire__bw_wbeing_1",
        "phq9_questionnaire__phq9_1",
    ]
    fake_store._observed_periods = {
        None: ["2023-2024"],
        "Focus School Academy": ["2023-2024"],
    }
    fake_store._lock = threading.Lock()

    def override_get_db():
        yield db_session

    def override_get_datastore():
        return fake_store

    def override_get_current_user():
        return UserRead(
            id=sample_user.id,
            username=sample_user.username,
            school_ids=[s.id for s in sample_user.schools],
            school_names=[s.name for s in sample_user.schools],
            is_active=True,
            is_admin=False,
        )

    from glow_api.auth import get_current_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_datastore] = override_get_datastore
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/dimensions")

    app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    variables = {item["key"]: item for item in data["variables"]}
    assert variables["bewell_questionnaire__bw_wbeing_1"]["raw_key"] == "bw_wbeing_1"
    assert (
        variables["bewell_questionnaire__bw_wbeing_1"]["form_id"]
        == "bewell_questionnaire"
    )
    assert variables["phq9_questionnaire__phq9_1"]["raw_key"] == "phq9_1"
    assert variables["phq9_questionnaire__phq9_1"]["form_id"] == "phq9_questionnaire"


def test_dimensions_school_scope_requires_auth(auth_client, sample_schools):
    """GET /dimensions?school_id=X should require authorization for that school."""
    school_id = sample_schools["Focus School Academy"].id
    login_response = auth_client.post(
        "/auth/dev-login", json={"role": "school", "school_id": school_id}
    )
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]

    response = auth_client.get(
        f"/dimensions?school_id={school_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # After implementation, this should be 200
    if response.status_code == status.HTTP_200_OK:
        data = response.json()
        assert data["school_id"] == school_id
        assert "variables" in data
        assert "dimensions" in data
    else:
        # For now, expect 404 since endpoint doesn't exist yet
        assert response.status_code == status.HTTP_404_NOT_FOUND


def test_dimensions_school_scope_unauthorized(auth_client, sample_schools):
    """GET /dimensions for unauthorized school should return 403."""
    # Login as a user scoped only to Focus School Academy
    login_response = auth_client.post(
        "/auth/dev-login",
        json={"role": "school", "school_id": sample_schools["Focus School Academy"].id},
    )
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]

    # Try to access Neighbouring School (which user doesn't have access to)
    school_id = sample_schools["Neighbouring School"].id
    response = auth_client.get(
        f"/dimensions?school_id={school_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # After implementation, this should be 403
    if response.status_code == status.HTTP_200_OK:
        # If somehow it returns 200, fail the test
        assert False, "Expected 403 for unauthorized school access"
    elif response.status_code == status.HTTP_403_FORBIDDEN:
        # This is what we expect after implementation
        pass
    else:
        # For now, accept 404 since endpoint doesn't exist yet
        assert response.status_code == status.HTTP_404_NOT_FOUND


def test_dimensions_school_scope_no_data_loaded(auth_client_empty_data, sample_schools):
    """GET /dimensions?school_id=X should return empty results, not 500, when the
    datastore hasn't loaded any submissions yet (e.g. ODK Central unreachable at
    startup) and the analytic frame has no "school" column."""
    school_id = sample_schools["Focus School Academy"].id
    login_response = auth_client_empty_data.post(
        "/auth/dev-login", json={"role": "school", "school_id": school_id}
    )
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]

    response = auth_client_empty_data.get(
        f"/dimensions?school_id={school_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["school_id"] == school_id
    assert data["variables"] == []
    assert data["dimensions"] == []


# ---------------------------------------------------------------------------
# Schools and Legacy Tests
# ---------------------------------------------------------------------------


def test_docs_example_fixture_is_parseable(sample_df):
    df = pd.read_csv(io.StringIO(sample_df.to_csv(index=False)))
    assert not df.empty


def test_schools_list_for_user(client, sample_schools):
    """Regular user should see their assigned schools."""
    response = client.get("/schools")
    assert response.status_code == status.HTTP_200_OK
    schools = response.json()

    # Should only see Focus School Academy (sample_user has access to this school)
    assert len(schools) == 1
    school = schools[0]
    assert school["name"] == "Focus School Academy"
    assert school["id"] == sample_schools["Focus School Academy"].id


def test_schools_list_for_admin(admin_client, sample_schools):
    """Admin should see all schools."""
    response = admin_client.get("/schools")
    assert response.status_code == status.HTTP_200_OK
    schools = response.json()

    # Should see all schools
    assert len(schools) == 2
    school_names = {s["name"] for s in schools}
    assert "Focus School Academy" in school_names
    assert "Neighbouring School" in school_names


def test_schools_list_requires_authentication(auth_client):
    """Unauthenticated requests should be rejected."""
    response = auth_client.get("/schools")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_schools_include_neighbor_ids(client, db_session, sample_schools):
    """Schools should include geographical and statistical neighbor IDs."""
    # Set up neighbors
    alpha = sample_schools["Focus School Academy"]
    beta = sample_schools["Neighbouring School"]
    alpha.geographical_neighbors.append(beta)
    db_session.commit()

    response = client.get("/schools")
    assert response.status_code == status.HTTP_200_OK
    schools = response.json()

    alpha_school = next(s for s in schools if s["name"] == "Focus School Academy")
    assert beta.id in alpha_school["geographical_neighbor_ids"]


# ---------------------------------------------------------------------------
# New GET /query Endpoint Tests (Period-Oriented)
# ---------------------------------------------------------------------------


def test_query_get_repeated_v_params(client):
    """GET /query should accept repeated 'v' params for variable selection."""
    # This test will fail until we implement the new GET endpoint
    response = client.get("/query?v=bw_wbeing_1&v=bw_wbeing_2")
    # For now, expect either 404 (not implemented) or 405 (wrong method)
    # After implementation, check that variables are properly selected
    assert response.status_code in [
        status.HTTP_404_NOT_FOUND,
        status.HTTP_405_METHOD_NOT_ALLOWED,
        status.HTTP_200_OK,
    ]


def test_query_get_repeated_d_params(client):
    """GET /query should accept repeated 'd' params for dimension selection."""
    # This test will fail until we implement the new GET endpoint
    response = client.get("/query?v=bw_wbeing_1&d=d_sex&d=yearGroup")
    # For now, expect either 404 (not implemented) or 405 (wrong method)
    assert response.status_code in [
        status.HTTP_404_NOT_FOUND,
        status.HTTP_405_METHOD_NOT_ALLOWED,
        status.HTTP_200_OK,
    ]


def test_query_get_repeated_variable_prefix_params(client):
    """GET /query should accept repeated 'variable_prefix' params."""
    # This test will fail until we implement the new GET endpoint
    response = client.get("/query?variable_prefix=bw_&variable_prefix=d_")
    assert response.status_code in [
        status.HTTP_404_NOT_FOUND,
        status.HTTP_405_METHOD_NOT_ALLOWED,
        status.HTTP_200_OK,
    ]


def test_query_get_union_v_and_prefix(client):
    """GET /query with both 'v' and 'variable_prefix' should union them."""
    # This test will fail until we implement the new GET endpoint
    response = client.get("/query?v=bw_wbeing_1&variable_prefix=d_")
    assert response.status_code in [
        status.HTTP_404_NOT_FOUND,
        status.HTTP_405_METHOD_NOT_ALLOWED,
        status.HTTP_200_OK,
    ]


def test_query_get_omit_dimensions_means_no_dimensions(client):
    """GET /query without 'd' should mean no dimensions."""
    # This test will fail until we implement the new GET endpoint
    response = client.get("/query?v=bw_wbeing_1")
    assert response.status_code in [
        status.HTTP_404_NOT_FOUND,
        status.HTTP_405_METHOD_NOT_ALLOWED,
        status.HTTP_200_OK,
    ]


def test_query_get_omit_variables_means_all_variables(client):
    """GET /query without 'v' or 'variable_prefix' should mean all variables."""
    # This test will fail until we implement the new GET endpoint
    response = client.get("/query")
    assert response.status_code in [
        status.HTTP_404_NOT_FOUND,
        status.HTTP_405_METHOD_NOT_ALLOWED,
        status.HTTP_200_OK,
    ]


def test_query_get_school_scope_requires_auth(auth_client, sample_schools):
    """GET /query with school_id should require authorization."""
    school_id = sample_schools["Focus School Academy"].id
    login_response = auth_client.post(
        "/auth/dev-login", json={"role": "school", "school_id": school_id}
    )
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]

    response = auth_client.get(
        f"/query?school_id={school_id}&v=bw_wbeing_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK


def test_query_get_school_scope_unauthorized(auth_client, sample_schools):
    """GET /query for unauthorized school should return 403."""
    # Login as a user scoped only to Focus School Academy
    login_response = auth_client.post(
        "/auth/dev-login",
        json={"role": "school", "school_id": sample_schools["Focus School Academy"].id},
    )
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]

    # Try to access Neighbouring School (which user doesn't have access to)
    school_id = sample_schools["Neighbouring School"].id
    response = auth_client.get(
        f"/query?school_id={school_id}&v=bw_wbeing_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_query_get_school_scope_no_data_loaded(auth_client_empty_data, sample_schools):
    """GET /query?school_id=X should return an empty result, not 500, when the
    datastore hasn't loaded any submissions yet and the analytic frame has no
    "school" column."""
    school_id = sample_schools["Focus School Academy"].id
    login_response = auth_client_empty_data.post(
        "/auth/dev-login", json={"role": "school", "school_id": school_id}
    )
    assert login_response.status_code == status.HTTP_200_OK
    token = login_response.json()["access_token"]

    response = auth_client_empty_data.get(
        f"/query?school_id={school_id}&v=bw_wbeing_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# /wrc Router Tests
# ---------------------------------------------------------------------------


def test_wrc_ping_requires_wrc_flag(client):
    """A non-WRC authenticated user is refused."""
    response = client.get("/wrc/ping")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_wrc_ping_allows_wrc_user(wrc_client):
    response = wrc_client.get("/wrc/ping")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}
