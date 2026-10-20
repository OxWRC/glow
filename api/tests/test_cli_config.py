"""glow-api config check / suppression-hash (run as the real console script)."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from glow_api.cli import hash_suppression_file
from glow_api.suppression import RULES_PATH

GLOW_API = Path(sys.executable).parent / "glow-api"


def run(tmp_path, args, **env):
    full_env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "TZ": "BST-1",
        **env,
    }
    return subprocess.run(
        [str(GLOW_API), *args],
        env=full_env,
        cwd=tmp_path,  # no stray .env
        capture_output=True,
        text=True,
    )


def out(p):
    return p.stdout + p.stderr


def test_config_check_ok_in_demo_mode(tmp_path):
    p = run(tmp_path, ["config", "check"], GLOW_DEMO_MODE="true")
    assert p.returncode == 0, out(p)
    assert p.stdout.strip() == "Config OK"


def test_config_check_reports_bad_type(tmp_path):
    p = run(tmp_path, ["config", "check"], GLOW_DEMO_MODE="true", GLOW_MIN_N="abc")
    assert p.returncode == 1
    assert "GLOW_MIN_N" in out(p)
    assert "Traceback" not in out(p)


def test_config_check_reports_auth_rule(tmp_path):
    p = run(
        tmp_path,
        ["config", "check"],
        GLOW_DEMO_MODE="true",
        GLOW_OIDC_ISSUER="https://idp.example",
        GLOW_OIDC_CLIENT_ID="x",
    )
    assert p.returncode == 1
    assert "GLOW_OIDC_ISSUER" in out(p)
    assert "Traceback" not in out(p)


def test_config_check_requires_auth_when_not_demo(tmp_path):
    p = run(tmp_path, ["config", "check"])
    assert p.returncode == 1
    assert "GLOW_OIDC_ISSUER" in out(p)
    assert "Traceback" not in out(p)


def canonical(rules):
    blob = json.dumps(rules, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def test_suppression_hash_matches_canonical_rules(tmp_path):
    p = run(tmp_path, ["suppression-hash"], GLOW_DEMO_MODE="true")
    assert p.returncode == 0, out(p)
    expected = canonical(yaml.safe_load(RULES_PATH.read_text()))
    assert p.stdout.strip() == expected


def test_suppression_hash_ignores_comments(tmp_path):
    base = tmp_path / "a.yaml"
    base.write_text("a: 1\nb:\n  - 2\n  - 3\n")
    commented = tmp_path / "b.yaml"
    commented.write_text("# note\nb: [2, 3]  # inline\n\na: 1\n")
    changed = tmp_path / "c.yaml"
    changed.write_text("a: 1\nb:\n  - 2\n  - 4\n")
    assert hash_suppression_file(base) == hash_suppression_file(commented)
    assert hash_suppression_file(base) != hash_suppression_file(changed)
