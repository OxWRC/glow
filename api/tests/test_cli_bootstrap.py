"""Tests for `glow-api users create --bootstrap` (Task 5: Cognito bootstrap).

boto3 is mocked directly (no moto in this project) by monkeypatching
`cli_module._get_cognito_client` to return a fake client whose
`admin_create_user`/`admin_get_user`/`admin_set_user_password` behave like
the real Cognito API, including `exceptions.UsernameExistsException`.
"""

from click.testing import CliRunner
from sqlalchemy.orm import sessionmaker

import glow_api.cli as cli_module
from glow_api.metadata_models import User


class _UsernameExistsException(Exception):
    pass


class FakeCognitoClient:
    """Stands in for a boto3 cognito-idp client.

    `existing_subs` simulates usernames that already exist in the user pool
    (AdminCreateUser raises, AdminGetUser must be used instead).
    """

    def __init__(self, existing: dict[str, str] | None = None):
        self.existing = dict(existing or {})
        self.exceptions = type("Exceptions", (), {"UsernameExistsException": _UsernameExistsException})
        self.create_calls = []
        self.set_password_calls = []
        self._next_sub_id = 1

    def admin_create_user(self, UserPoolId, Username, MessageAction=None):
        self.create_calls.append(Username)
        if Username in self.existing:
            raise self.exceptions.UsernameExistsException(Username)
        sub = f"sub-{self._next_sub_id}"
        self._next_sub_id += 1
        self.existing[Username] = sub
        return {"User": {"Attributes": [{"Name": "sub", "Value": sub}]}}

    def admin_get_user(self, UserPoolId, Username):
        sub = self.existing[Username]
        return {"UserAttributes": [{"Name": "sub", "Value": sub}]}

    def admin_set_user_password(self, UserPoolId, Username, Password, Permanent):
        self.set_password_calls.append(
            {"Username": Username, "Password": Password, "Permanent": Permanent}
        )


def _run_bootstrap(runner, args, monkeypatch, db_engine, fake_client, pool_id="us-east-1_test"):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)
    monkeypatch.setattr(cli_module.settings, "COGNITO_USER_POOL_ID", pool_id)
    monkeypatch.setattr(cli_module, "_get_cognito_client", lambda: fake_client)
    return runner.invoke(cli_module.cli, args), Session


def test_bootstrap_creates_cognito_user_and_local_row(monkeypatch, db_engine):
    fake_client = FakeCognitoClient()
    runner = CliRunner()
    result, Session = _run_bootstrap(
        runner,
        ["users", "create", "alice", "--password", "TempPass123!", "--bootstrap", "--admin"],
        monkeypatch,
        db_engine,
        fake_client,
    )

    assert result.exit_code == 0, result.output
    assert fake_client.create_calls == ["alice"]
    assert fake_client.set_password_calls == [
        {"Username": "alice", "Password": "TempPass123!", "Permanent": False}
    ]

    with Session() as session:
        user = session.query(User).filter_by(username="alice").one()
        assert user.cognito_sub == "sub-1"
        assert user.is_admin is True
        # Regression guard: upsert_user_by_sub doesn't set is_active
        # explicitly, relying on the column's default=True - if that ever
        # changes, a bootstrapped admin would be silently locked out by
        # get_current_user's `not user.is_active` check.
        assert user.is_active is True


def test_bootstrap_permanent_password_flag_sets_permanent_true(monkeypatch, db_engine):
    fake_client = FakeCognitoClient()
    runner = CliRunner()
    result, _ = _run_bootstrap(
        runner,
        [
            "users",
            "create",
            "demo",
            "--password",
            "DemoPass123!",
            "--bootstrap",
            "--permanent-password",
        ],
        monkeypatch,
        db_engine,
        fake_client,
    )

    assert result.exit_code == 0, result.output
    assert fake_client.set_password_calls[0]["Permanent"] is True


def test_bootstrap_idempotent_when_cognito_user_already_exists(monkeypatch, db_engine):
    """UsernameExistsException falls back to AdminGetUser, and the local row
    (missing here) is still created rather than the command failing."""
    fake_client = FakeCognitoClient(existing={"bob": "sub-existing"})
    runner = CliRunner()
    result, Session = _run_bootstrap(
        runner,
        ["users", "create", "bob", "--password", "TempPass123!", "--bootstrap", "--wrc"],
        monkeypatch,
        db_engine,
        fake_client,
    )

    assert result.exit_code == 0, result.output
    with Session() as session:
        user = session.query(User).filter_by(username="bob").one()
        assert user.cognito_sub == "sub-existing"
        assert user.is_wrc is True
    # AdminSetUserPassword still runs so a re-run also resets the password.
    assert fake_client.set_password_calls[0]["Username"] == "bob"


def test_bootstrap_rerun_upserts_rather_than_duplicating(monkeypatch, db_engine):
    fake_client = FakeCognitoClient()
    runner = CliRunner()

    args = ["users", "create", "carol", "--password", "TempPass123!", "--bootstrap"]
    result1, Session = _run_bootstrap(runner, args, monkeypatch, db_engine, fake_client)
    assert result1.exit_code == 0, result1.output

    # Second run: same username now exists in Cognito -> UsernameExistsException path.
    result2, _ = _run_bootstrap(runner, args, monkeypatch, db_engine, fake_client)
    assert result2.exit_code == 0, result2.output

    with Session() as session:
        users = session.query(User).filter_by(username="carol").all()
        assert len(users) == 1


def test_bootstrap_refuses_when_cognito_not_configured(monkeypatch, db_engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)
    monkeypatch.setattr(cli_module.settings, "COGNITO_USER_POOL_ID", None)

    def _boom():
        raise AssertionError("should not construct a Cognito client when unconfigured")

    monkeypatch.setattr(cli_module, "_get_cognito_client", _boom)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.cli,
        ["users", "create", "dana", "--password", "TempPass123!", "--bootstrap"],
    )

    assert result.exit_code != 0
    assert "GLOW_COGNITO_USER_POOL_ID" in result.output


def test_permanent_password_without_bootstrap_errors(monkeypatch, db_engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.cli,
        ["users", "create", "eve", "--password", "TempPass123!", "--permanent-password"],
    )

    assert result.exit_code != 0
    assert "--permanent-password only applies with --bootstrap" in result.output
