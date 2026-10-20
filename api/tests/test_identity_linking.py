"""Email-based identity linking: pre-registered users bind to an OIDC sub on
first verified login. GLOW never auto-creates users."""

import pytest

from glow_api.auth import create_access_token
from glow_api.database import create_user, get_user_by_username
from glow_api.metadata_models import User

EMAIL = "teacher@school.org"


def _get(client, path, claims, **kw):
    token = create_access_token(claims)
    return client.get(path, headers={"Authorization": f"Bearer {token}"}, **kw)


def _me(client, claims):
    return _get(client, "/me", claims)


@pytest.fixture
def pre(db_session, sample_schools):
    return create_user(
        db_session,
        "pre",
        email=EMAIL,
        school_ids=[sample_schools["Focus School Academy"].id],
    )


def _row(db_session, username="pre"):
    db_session.expire_all()
    return get_user_by_username(db_session, username)


def test_first_login_links_preregistered_email(auth_client, db_session, pre):
    r = _me(auth_client, {"sub": "s1", "email": EMAIL, "email_verified": True})
    assert r.status_code == 200
    assert _row(db_session).oidc_sub == "s1"


def test_email_verified_string_true_links(auth_client, db_session, pre):
    r = _me(auth_client, {"sub": "s1", "email": EMAIL, "email_verified": "true"})
    assert r.status_code == 200
    assert _row(db_session).oidc_sub == "s1"


@pytest.mark.parametrize(
    "extra", [{}, {"email_verified": False}, {"email_verified": "false"}]
)
def test_unverified_email_is_no_access(auth_client, db_session, pre, extra):
    r = _me(auth_client, {"sub": "s1", "email": EMAIL, **extra})
    assert r.status_code == 403
    assert r.json() == {"detail": "no_access"}
    assert _row(db_session).oidc_sub is None


def test_email_match_is_case_and_space_insensitive(auth_client, db_session, pre):
    r = _me(
        auth_client,
        {"sub": "s1", "email": " Teacher@School.org", "email_verified": True},
    )
    assert r.status_code == 200
    assert _row(db_session).oidc_sub == "s1"


def test_stored_email_is_normalised(db_session):
    u = create_user(db_session, "n", email="  Mixed@Case.ORG ")
    assert u.email == "mixed@case.org"


def test_unknown_email_is_no_access_and_creates_nothing(auth_client, db_session, pre):
    before = db_session.query(User).count()
    r = _me(
        auth_client,
        {"sub": "s9", "email": "who@else.org", "email_verified": True},
    )
    assert r.status_code == 403
    assert db_session.query(User).count() == before


def test_second_sub_with_linked_email_is_no_access(auth_client, db_session, pre):
    claims = {"email": EMAIL, "email_verified": True}
    assert _me(auth_client, {"sub": "s1", **claims}).status_code == 200
    assert _me(auth_client, {"sub": "s2", **claims}).status_code == 403
    assert _row(db_session).oidc_sub == "s1"


def test_linked_user_found_by_sub_even_if_email_changes(auth_client, db_session, pre):
    claims = {"email": EMAIL, "email_verified": True}
    assert _me(auth_client, {"sub": "s1", **claims}).status_code == 200
    assert _me(auth_client, {"sub": "s1", "email": "new@x.org"}).status_code == 200


def test_inactive_user_is_rejected(auth_client, db_session, pre):
    claims = {"email": EMAIL, "email_verified": True}
    assert _me(auth_client, {"sub": "s1", **claims}).status_code == 200
    _row(db_session).is_active = False
    db_session.commit()
    assert _me(auth_client, {"sub": "s1", **claims}).status_code == 403


def test_inactive_unlinked_user_is_not_linked(auth_client, db_session, pre):
    pre.is_active = False
    db_session.commit()
    r = _me(auth_client, {"sub": "s1", "email": EMAIL, "email_verified": True})
    assert r.status_code == 403
    assert _row(db_session).oidc_sub is None


def test_school_scoped_query_uses_same_resolver(
    auth_client, db_session, pre, sample_schools
):
    sid = sample_schools["Focus School Academy"].id
    r = _get(
        auth_client,
        "/query/counts",
        {"sub": "s1", "email": EMAIL, "email_verified": True},
        params={"school_id": sid},
    )
    assert r.status_code == 200
    assert _row(db_session).oidc_sub == "s1"


def test_school_scoped_query_unverified_not_linked(
    auth_client, db_session, pre, sample_schools
):
    sid = sample_schools["Focus School Academy"].id
    r = _get(
        auth_client,
        "/query/counts",
        {"sub": "s1", "email": EMAIL},
        params={"school_id": sid},
    )
    assert r.status_code in (401, 403)
    assert _row(db_session).oidc_sub is None
