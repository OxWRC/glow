import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import render_release as rr  # noqa: E402

H = "a" * 64
IMG_API = "${GLOW_REGISTRY:-ghcr.io/oxwrc}/glow-api:${GLOW_VERSION:-dev}"
DIGESTS = {
    IMG_API: f"ghcr.io/oxwrc/glow-api@sha256:{H}",
    "caddy:2.10.2": f"caddy@sha256:{'b' * 64}",
}


def compose(stage):
    s = str(stage)
    return {
        "name": "stage",
        "services": {
            "api": {
                "image": IMG_API,
                "build": {"context": f"{s}/api", "dockerfile": "Dockerfile"},
                "environment": {"D": "${GLOW_DOMAIN:-localhost}"},
                "volumes": [
                    {"type": "volume", "source": "${GLOW_STATE_DIR:-./docker-mount-data}/x",
                     "target": "/x", "volume": {}},
                    {"type": "bind", "source": f"{s}/odk-central/local-glow.json",
                     "target": "/c.json", "read_only": True, "bind": {"create_host_path": True}},
                ],
            },
            "proxy": {
                "image": "caddy:2.10.2",
                "networks": {"public": {"aliases": ["${GLOW_DOMAIN:-localhost}"]}},
                # short syntax, as `config --no-interpolate` emits it
                "ports": ["${GLOW_HTTPS_PORT:-443}:443", "${GLOW_HTTP_PORT:-80}:80"],
                "volumes": [{"type": "bind", "source": f"{s}/proxy/Caddyfile.${{GLOW_TLS_MODE:-acme}}",
                             "target": "/etc/caddy/Caddyfile", "read_only": True}],
            },
        },
        "networks": {"public": {"name": "stage_public"}},
    }


@pytest.fixture
def stage(tmp_path):
    (tmp_path / "odk-central").mkdir()
    (tmp_path / "odk-central/local-glow.json").write_text("{}")
    (tmp_path / "proxy").mkdir()
    (tmp_path / "proxy/Caddyfile.acme").write_text("")
    return tmp_path


def render(stage, digests=DIGESTS, c=None):
    return rr.render(c or compose(stage), digests, stage)


def test_replaces_images_with_digests_and_drops_build(stage):
    out = render(stage)
    assert out["services"]["api"]["image"] == DIGESTS[IMG_API]
    assert out["services"]["proxy"]["image"] == DIGESTS["caddy:2.10.2"]
    assert "build" not in out["services"]["api"]
    assert "name" not in out


def test_project_name_not_baked_into_network_names(stage):
    out = render(stage)
    assert out["networks"] == {"public": None}


def test_missing_digest_fails_naming_image(stage):
    with pytest.raises(rr.RenderError, match="caddy:2.10.2"):
        render(stage, {IMG_API: DIGESTS[IMG_API]})


@pytest.mark.parametrize("bad", ["repo:tag", "repo@sha256:abc", f"repo@sha256:{'G' * 64}"])
def test_malformed_digest_fails(stage, bad):
    with pytest.raises(rr.RenderError, match="malformed"):
        render(stage, {**DIGESTS, "caddy:2.10.2": bad})


def test_missing_bind_source_fails_naming_path(stage):
    (stage / "odk-central/local-glow.json").unlink()
    with pytest.raises(rr.RenderError, match="local-glow.json"):
        render(stage)


def test_present_bind_sources_pass(stage):
    out = render(stage)
    assert "./odk-central/local-glow.json:/c.json:ro" in out["services"]["api"]["volumes"]
    # state-dir volume skipped by the check, kept verbatim
    assert "${GLOW_STATE_DIR:-./docker-mount-data}/x:/x" in out["services"]["api"]["volumes"]


def test_variables_are_preserved(stage):
    text = yaml.safe_dump(render(stage))
    assert "${GLOW_DOMAIN:-localhost}" in text
    assert "./proxy/Caddyfile.${GLOW_TLS_MODE:-acme}:/etc/caddy/Caddyfile:ro" in text


def test_proxy_extends_reinserted(stage):
    proxy = render(stage)["services"]["proxy"]
    assert proxy["extends"] == {
        "file": "proxy/compose.${GLOW_TLS_MODE:-acme}.yml",
        "service": "proxy-networks",
    }
    assert "networks" not in proxy


def test_image_lookup_with_set_vars(stage):
    c = compose(stage)
    d = {"ghcr.io/oxwrc/glow-api:v1": DIGESTS[IMG_API], "caddy:2.10.2": DIGESTS["caddy:2.10.2"]}
    out = rr.render(c, d, stage, variables={"GLOW_VERSION": "v1"})
    assert out["services"]["api"]["image"] == DIGESTS[IMG_API]


def test_cli(stage, tmp_path):
    cj, dj, out = tmp_path / "c.json", tmp_path / "d.json", tmp_path / "out.yml"
    cj.write_text(json.dumps(compose(stage)))
    dj.write_text(json.dumps(DIGESTS))
    r = subprocess.run([sys.executable, str(Path(rr.__file__)), "--compose-json", str(cj),
                        "--digests", str(dj), "--stage", str(stage), "--out", str(out)])
    assert r.returncode == 0 and "services" in yaml.safe_load(out.read_text())
    dj.write_text("{}")
    r = subprocess.run([sys.executable, str(Path(rr.__file__)), "--compose-json", str(cj),
                        "--digests", str(dj), "--stage", str(stage), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "glow-api" in r.stderr


def test_ports_inlined_from_extends_file_are_stripped(stage):
    proxy = render(stage)["services"]["proxy"]
    assert proxy["ports"] == ["${GLOW_HTTP_PORT:-80}:80"]


def test_long_syntax_ports_stripped_too(stage):
    c = compose(stage)
    c["services"]["proxy"]["ports"] = [{"target": 443, "published": "443"}, {"target": 80, "published": "80"}]
    assert [p["target"] for p in render(stage, c=c)["services"]["proxy"]["ports"]] == [80]
