"""glow-api vintages: CLI cut/list, audited, same shape as api-keys CLI."""

import json
import threading

import boto3
import pytest
from click.testing import CliRunner
from moto import mock_aws
from sqlalchemy.orm import sessionmaker

import glow_api.cli as cli_module
import glow_api.data
from glow_api import audit as audit_module
from glow_api.data import DataStore
from glow_api.settings import settings

BUCKET = "test-glow-cli-vintages"


@pytest.fixture()
def cli_env(monkeypatch, db_engine, tmp_path):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(cli_module, "SessionLocal", Session)
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "AUDIT_LOG_PATH", str(audit_path))
    monkeypatch.setattr(settings, "VINTAGES_S3_BUCKET", BUCKET)
    audit_module.reset_audit_sink()

    from glow_api import vintages as vintages_module

    vintages_module._latest_prefix_cache.invalidate()
    vintages_module._vintage_cache.clear()

    fake_store = DataStore.__new__(DataStore)
    fake_store._df = fake_store._process_loaded_data(_small_df())
    fake_store._metadata = {}
    fake_store._lock = threading.Lock()
    fake_store.startup = lambda: None
    fake_store.shutdown = lambda: None

    original_get_datastore = glow_api.data.get_datastore
    glow_api.data.get_datastore = lambda: fake_store

    with mock_aws():
        client = boto3.client("s3", region_name="eu-west-2")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
        )
        yield Session, audit_path

    glow_api.data.get_datastore = original_get_datastore
    audit_module.reset_audit_sink()
    vintages_module._latest_prefix_cache.invalidate()
    vintages_module._vintage_cache.clear()


def _small_df():
    import pandas as pd

    return pd.DataFrame(
        {
            "uid": ["S001", "S002"],
            "school": ["Focus School Academy", "Focus School Academy"],
            "bw_wbeing_1": [3, 4],
        }
    )


def _audit_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_cut_writes_vintage_and_audits(cli_env):
    _, audit_path = cli_env

    result = CliRunner().invoke(cli_module.cli, ["vintages", "cut"])

    assert result.exit_code == 0, result.output
    assert "Cut vintage" in result.output
    [line] = _audit_lines(audit_path)
    assert line["actor"] == "cli"
    assert line["action"] == "vintage_cut"
    assert "prefix" in line


def test_list_shows_cut_vintages(cli_env):
    runner = CliRunner()
    cut = runner.invoke(cli_module.cli, ["vintages", "cut"])
    assert cut.exit_code == 0, cut.output

    listed = runner.invoke(cli_module.cli, ["vintages", "list"])

    assert listed.exit_code == 0, listed.output
    assert "created" in listed.output
    assert "scheduler" in listed.output  # CLI-created cuts have no admin user


def test_list_with_no_vintages_says_so(cli_env):
    result = CliRunner().invoke(cli_module.cli, ["vintages", "list"])

    assert result.exit_code == 0
    assert "No vintages." in result.output
