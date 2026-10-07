from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from glow_api.cli import cli
from glow_api.database import (
    create_api_key,
    create_user,
    delete_school,
    get_user_by_username,
    list_schools,
    list_users,
    seed_demo,
)
from glow_api.metadata_models import ApiKey
from glow_api.settings import settings


def test_seed_demo_wipes_users_keys_and_restores_schools(db_session, sample_df):
    seed_demo(db_session, sample_df)
    school_names = {s.name for s in list_schools(db_session)}
    visitor = create_user(db_session, username="visitor", school_ids=[], is_admin=False)
    create_api_key(
        db_session,
        user_id=visitor.id,
        name="k",
        key_hash="h",
        prefix="glow_x",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    delete_school(db_session, list_schools(db_session)[0])

    seed_demo(db_session, sample_df)

    assert {s.name for s in list_schools(db_session)} == school_names
    assert [u.username for u in list_users(db_session)] == ["admin"]
    assert db_session.query(ApiKey).count() == 0


def test_seed_demo_neighbours_are_reproducible(db_session, sample_df):
    def snapshot():
        return {
            s.name: (
                sorted(n.name for n in s.geographical_neighbors),
                sorted(n.name for n in s.statistical_neighbors),
            )
            for s in list_schools(db_session)
        }

    seed_demo(db_session, sample_df)
    first = snapshot()
    seed_demo(db_session, sample_df)
    assert snapshot() == first


def test_admin_gets_all_schools_after_seed(db_session, sample_df):
    seed_demo(db_session, sample_df)
    admin = get_user_by_username(db_session, "admin")
    assert admin.is_admin
    assert {s.id for s in admin.schools} == {s.id for s in list_schools(db_session)}


def test_demo_reset_cli_refuses_when_demo_mode_off(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    result = CliRunner().invoke(cli, ["demo", "reset"])
    assert result.exit_code == 1
    assert "GLOW_DEMO_MODE is off" in result.output


def test_seed_demo_failure_rolls_back_everything(db_session, sample_df):
    seed_demo(db_session, sample_df)
    schools = {s.name for s in list_schools(db_session)}

    with pytest.raises(ValueError):
        seed_demo(db_session, sample_df.drop(columns=["school"]))

    assert {s.name for s in list_schools(db_session)} == schools
    assert [u.username for u in list_users(db_session)] == ["admin"]
