"""glow-api api-keys: CLI issuance/listing/revocation, each audited."""

import json

import pytest
from click.testing import CliRunner
from sqlalchemy.orm import sessionmaker

import glow_api.cli as cli_module
from glow_api.metadata_models import ApiKey, AuditEvent


@pytest.fixture()
def cli_env(monkeypatch, db_engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)
    return Session


def _audit_events(Session) -> list[AuditEvent]:
    with Session() as db:
        return db.query(AuditEvent).order_by(AuditEvent.id).all()


def test_create_prints_key_once_and_audits(cli_env):
    Session = cli_env
    result = CliRunner().invoke(
        cli_module.cli,
        ["api-keys", "create", "--name", "WRC", "--expires-in-days", "30"],
    )
    assert result.exit_code == 0, result.output
    raw = result.output.strip().splitlines()[-1]
    assert raw.startswith("glow_")
    with Session() as db:
        record = db.query(ApiKey).one()
        assert record.created_by_user_id is None
        assert raw not in (record.key_hash, record.prefix)
    [event] = _audit_events(Session)
    assert event.actor == "cli"
    assert event.action == "api_key.create"
    assert event.details["api_key_id"] == record.id
    assert raw not in json.dumps(event.details)


def test_create_rejects_out_of_range_expiry(cli_env):
    result = CliRunner().invoke(
        cli_module.cli, ["api-keys", "create", "--name", "x", "--expires-in-days", "0"]
    )
    assert result.exit_code != 0


def test_list_shows_status_without_secret(cli_env):
    runner = CliRunner()
    created = runner.invoke(cli_module.cli, ["api-keys", "create", "--name", "WRC"])
    raw = created.output.strip().splitlines()[-1]
    result = runner.invoke(cli_module.cli, ["api-keys", "list"])
    assert result.exit_code == 0
    assert "WRC" in result.output
    assert "active" in result.output
    assert raw not in result.output


def test_revoke_marks_revoked_and_audits(cli_env):
    Session = cli_env
    runner = CliRunner()
    runner.invoke(cli_module.cli, ["api-keys", "create", "--name", "WRC"])
    with Session() as db:
        key_id = db.query(ApiKey).one().id
    result = runner.invoke(cli_module.cli, ["api-keys", "revoke", str(key_id)])
    assert result.exit_code == 0
    with Session() as db:
        assert db.query(ApiKey).one().revoked_at is not None
    assert _audit_events(Session)[-1].action == "api_key.revoke"
    assert "revoked" in runner.invoke(cli_module.cli, ["api-keys", "list"]).output


def test_revoke_unknown_key_fails(cli_env):
    result = CliRunner().invoke(cli_module.cli, ["api-keys", "revoke", "999"])
    assert result.exit_code == 1
    assert "not found" in result.output
