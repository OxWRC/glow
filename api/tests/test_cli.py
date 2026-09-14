"""Tests for the glow-api admin CLI."""

import threading

from click.testing import CliRunner
from sqlalchemy.orm import sessionmaker

import glow_api.cli as cli_module
from glow_api.data import DataStore
from glow_api.metadata_models import User


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
            "alice",
            "--schools",
            "Focus School Academy",
            "--admin",
        ],
    )

    assert create_result.exit_code == 0
    assert "User 'alice' created" in create_result.output

    with Session() as session:
        user = session.query(User).filter_by(username="alice").one()
        assert user.is_admin is True
        assert len(user.schools) == 1
        assert user.schools[0].name == "Focus School Academy"

    list_result = runner.invoke(cli_module.cli, ["users", "list"])
    assert list_result.exit_code == 0
    assert "alice" in list_result.output


def test_users_create_wrc_clears_schools(monkeypatch, db_engine, sample_schools):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    runner = CliRunner()
    create_result = runner.invoke(
        cli_module.cli,
        [
            "users",
            "create",
            "wrcalice",
            "--schools",
            "Focus School Academy",
            "--wrc",
        ],
    )

    assert create_result.exit_code == 0
    assert "[WRC]" in create_result.output

    with Session() as session:
        user = session.query(User).filter_by(username="wrcalice").one()
        assert user.is_wrc is True
        assert user.schools == []


def test_users_update_wrc_flag(monkeypatch, db_engine, sample_schools):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    runner = CliRunner()
    runner.invoke(
        cli_module.cli,
        ["users", "create", "bob"],
    )

    update_result = runner.invoke(
        cli_module.cli,
        ["users", "update", "bob", "--wrc"],
    )
    assert update_result.exit_code == 0

    with Session() as session:
        user = session.query(User).filter_by(username="bob").one()
        assert user.is_wrc is True


def test_users_update_wrc_flag_clears_existing_schools_without_schools_flag(
    monkeypatch, db_engine, sample_schools
):
    """`users update USERNAME --wrc` with no `--schools` must still clear any
    schools the user already had (regression: the core enforcement guard
    used to only fire when --schools was also passed in the same call)."""
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    runner = CliRunner()
    runner.invoke(
        cli_module.cli,
        [
            "users",
            "create",
            "carol",
            "--schools",
            "Focus School Academy",
        ],
    )

    with Session() as session:
        user = session.query(User).filter_by(username="carol").one()
        assert len(user.schools) == 1

    update_result = runner.invoke(
        cli_module.cli,
        ["users", "update", "carol", "--wrc"],
    )
    assert update_result.exit_code == 0

    with Session() as session:
        user = session.query(User).filter_by(username="carol").one()
        assert user.is_wrc is True
        assert user.schools == []


def test_users_delete(monkeypatch, db_engine, sample_schools):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    runner = CliRunner()
    runner.invoke(
        cli_module.cli,
        ["users", "create", "alice"],
    )

    delete_result = runner.invoke(
        cli_module.cli,
        ["users", "delete", "alice"],
        input="y\n",
    )

    assert delete_result.exit_code == 0
    assert "User 'alice' deleted." in delete_result.output

    with Session() as session:
        assert session.query(User).filter_by(username="alice").first() is None


def test_schools_sync_extracts_creates_and_grants_access(
    monkeypatch, db_engine, sample_df
):
    """Test that schools sync command extracts schools, creates neighbors, and grants admin access."""
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    # Create a fake datastore with sample data
    fake_store = DataStore.__new__(DataStore)
    fake_store._df = fake_store._process_loaded_data(sample_df)
    fake_store._metadata = {}
    fake_store._lock = threading.Lock()
    fake_store.startup = lambda: None
    fake_store.shutdown = lambda: None

    def override_get_datastore():
        return fake_store

    # Patch get_datastore in cli module
    import glow_api.data

    original_get_datastore = glow_api.data.get_datastore
    glow_api.data.get_datastore = override_get_datastore

    try:
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

    finally:
        glow_api.data.get_datastore = original_get_datastore


def test_schools_sync_is_idempotent(monkeypatch, db_engine, sample_df):
    """Test that running schools sync multiple times doesn't create duplicates."""
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    # Create a fake datastore with sample data
    fake_store = DataStore.__new__(DataStore)
    fake_store._df = fake_store._process_loaded_data(sample_df)
    fake_store._metadata = {}
    fake_store._lock = threading.Lock()
    fake_store.startup = lambda: None
    fake_store.shutdown = lambda: None

    def override_get_datastore():
        return fake_store

    # Patch get_datastore in cli module
    import glow_api.data

    original_get_datastore = glow_api.data.get_datastore
    glow_api.data.get_datastore = override_get_datastore

    try:
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

    finally:
        glow_api.data.get_datastore = original_get_datastore
