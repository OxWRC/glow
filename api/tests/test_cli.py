"""Tests for the glow-api admin CLI."""

from click.testing import CliRunner
from sqlalchemy.orm import sessionmaker

import glow_api.cli as cli_module
import glow_api.counts_store as counts_module
from glow_api.metadata_models import User
from tests.conftest import _make_counts_store


def test_db_init_runs_migrations(monkeypatch):
    called = {"value": False}

    def fake_run_migrations():
        called["value"] = True

    monkeypatch.setattr(cli_module, "run_migrations", fake_run_migrations)

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["db", "init"])

    assert result.exit_code == 0
    assert called["value"] is True
    assert "Database initialised." in result.output


def test_users_create_and_list(monkeypatch, db_engine, sample_schools):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    runner = CliRunner()
    create_result = runner.invoke(
        cli_module.cli,
        [
            "users",
            "create",
            "alice@x.org",
            "--schools",
            "Focus School Academy",
            "--admin",
        ],
    )

    assert create_result.exit_code == 0
    assert "User 'alice@x.org' created" in create_result.output

    with Session() as session:
        user = session.query(User).filter_by(username="alice@x.org").one()
        assert user.is_admin is True
        assert len(user.schools) == 1
        assert user.schools[0].name == "Focus School Academy"

    list_result = runner.invoke(cli_module.cli, ["users", "list"])
    assert list_result.exit_code == 0
    assert "alice@x.org" in list_result.output


def test_users_delete(monkeypatch, db_engine, sample_schools):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    runner = CliRunner()
    runner.invoke(
        cli_module.cli,
        ["users", "create", "alice@x.org"],
    )

    delete_result = runner.invoke(
        cli_module.cli,
        ["users", "delete", "alice@x.org"],
        input="y\n",
    )

    assert delete_result.exit_code == 0
    assert "User 'alice@x.org' deleted." in delete_result.output

    with Session() as session:
        assert session.query(User).filter_by(username="alice@x.org").first() is None


def test_schools_sync_extracts_creates_and_grants_access(
    monkeypatch, db_engine, sample_df
):
    """Test that schools sync command extracts schools, creates neighbors, and grants admin access."""
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    # Schools come from the slim counts data (school names only).
    fake_counts = _make_counts_store(sample_df)
    monkeypatch.setattr(counts_module, "get_counts_store", lambda: fake_counts)

    # Create an admin user first
    with Session() as session:
        from glow_api.database import create_user

        admin = create_user(
            session,
            username="admin",
            is_admin=True,
        )
        assert len(admin.schools) == 0

    # Run the sync command
    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["schools", "sync"])

    assert result.exit_code == 0, f"Command failed: {result.output}"
    assert "School synchronization completed successfully!" in result.output
    assert "Focus School Academy" in result.output
    assert "Neighbouring School" in result.output

    # Verify schools were created
    with Session() as session:
        from glow_api.database import list_schools

        schools = list_schools(session)
        assert len(schools) == 2
        school_names = {s.name for s in schools}
        assert "Focus School Academy" in school_names
        assert "Neighbouring School" in school_names

        # Verify each school has neighbors
        for school in schools:
            # With only 2 schools, each should have the other as a neighbor
            assert len(school.geographical_neighbors) == 1
            assert len(school.statistical_neighbors) == 1

        # Verify admin has access to all schools
        admin = session.query(User).filter_by(username="admin").one()
        assert len(admin.schools) == 2
        admin_school_names = {s.name for s in admin.schools}
        assert admin_school_names == school_names


def test_schools_sync_is_idempotent(monkeypatch, db_engine, sample_df):
    """Test that running schools sync multiple times doesn't create duplicates."""
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    # Schools come from the slim counts data (school names only).
    fake_counts = _make_counts_store(sample_df)
    monkeypatch.setattr(counts_module, "get_counts_store", lambda: fake_counts)

    runner = CliRunner()

    # Run sync twice
    result1 = runner.invoke(cli_module.cli, ["schools", "sync"])
    assert result1.exit_code == 0

    result2 = runner.invoke(cli_module.cli, ["schools", "sync"])
    assert result2.exit_code == 0

    # Verify we still have only 2 schools
    with Session() as session:
        from glow_api.database import list_schools

        schools = list_schools(session)
        assert len(schools) == 2


def _session(monkeypatch, db_engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)
    return Session


def test_cli_users_create_by_email(monkeypatch, db_engine):
    Session = _session(monkeypatch, db_engine)
    result = CliRunner().invoke(cli_module.cli, ["users", "create", " Bob@Y.org "])
    assert result.exit_code == 0, result.output
    with Session() as s:
        user = s.query(User).filter_by(email="bob@y.org").one()
        assert user.username == "bob@y.org"
        assert user.oidc_sub is None


def test_cli_users_create_rejects_invalid_email(monkeypatch, db_engine):
    _session(monkeypatch, db_engine)
    result = CliRunner().invoke(cli_module.cli, ["users", "create", "alice"])
    assert result.exit_code != 0


def test_cli_users_update_admin_flag(monkeypatch, db_engine):
    Session = _session(monkeypatch, db_engine)
    runner = CliRunner()
    runner.invoke(cli_module.cli, ["users", "create", "c@y.org"])
    for flag, expected in (("--admin", True), ("--no-admin", False)):
        result = runner.invoke(cli_module.cli, ["users", "update", "c@y.org", flag])
        assert result.exit_code == 0, result.output
        with Session() as s:
            assert s.query(User).filter_by(email="c@y.org").one().is_admin is expected


def test_cli_has_no_bootstrap_option(monkeypatch, db_engine):
    _session(monkeypatch, db_engine)
    result = CliRunner().invoke(
        cli_module.cli, ["users", "create", "x@y.org", "--bootstrap"]
    )
    assert result.exit_code != 0
    assert "No such option" in result.output


def test_cli_users_update_and_delete_normalise_email_argument(monkeypatch, db_engine):
    Session = _session(monkeypatch, db_engine)
    runner = CliRunner()
    runner.invoke(cli_module.cli, ["users", "create", "c@y.org"])
    result = runner.invoke(cli_module.cli, ["users", "update", " C@Y.org ", "--admin"])
    assert result.exit_code == 0, result.output
    with Session() as s:
        assert s.query(User).filter_by(email="c@y.org").one().is_admin is True
    result = runner.invoke(
        cli_module.cli, ["users", "delete", " C@Y.org "], input="y\n"
    )
    assert result.exit_code == 0, result.output
    with Session() as s:
        assert s.query(User).filter_by(email="c@y.org").first() is None
