#!/usr/bin/env python3
"""Render a release compose file from `docker compose config --no-interpolate --format json`.

Drops `build:`, pins every `image:` to a digest ref, relativises bind sources
against --stage (the dir compose ran in), verifies they exist, and re-inserts
the proxy `extends` that compose resolved away.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

DIGEST_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
VAR_RE = re.compile(r"\$\{(\w+):-([^}]*)\}")
# compose resolves `extends` inline; put it back (the file is shipped in proxy/).
EXTENDS = {
    "proxy": {"file": "proxy/compose.${GLOW_TLS_MODE:-acme}.yml", "service": "proxy-networks"},
}
# Container ports whose mapping lives in the extends file (compose inlines it).
EXTENDS_PORTS = {"proxy": {443}}


class RenderError(Exception):
    pass


def _expand(ref, variables):
    return VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(2)), ref)


def _digest_for(image, digests, variables):
    for key in (image, _expand(image, variables)):
        if key in digests:
            d = digests[key]
            if not DIGEST_RE.match(d):
                raise RenderError(f"malformed digest for {key}: {d!r}")
            return d
    raise RenderError(f"no digest for image {image}")


def _target(port):
    """Container port of a short ("host:80/tcp") or long-syntax port entry."""
    if isinstance(port, dict):
        return port.get("target")
    tail = str(port).rsplit(":", 1)[-1].split("/")[0]
    return int(tail) if tail.isdigit() else None


def _volume(v, stage):
    if not isinstance(v, dict):
        return v
    src, tgt, typ = v.get("source", ""), v.get("target"), v.get("type")
    if typ not in ("bind", "volume") or src is None:
        return v
    if typ == "volume" and "${" not in src:
        return v  # named volume, keep long form
    if src.startswith(str(stage) + "/"):
        src = src[len(str(stage)) + 1:]
        # a source that starts with a variable (e.g. ${GLOW_STATE_DIR:-...}) is a
        # deploy-time path, possibly absolute: don't anchor it to the stage dir
        src = src if src.startswith("${") else "./" + src
    if typ == "bind" and not src.startswith("${"):
        # check existence using the variable defaults (e.g. Caddyfile.${GLOW_TLS_MODE:-acme})
        check = Path(_expand(src, {}))
        if not check.is_absolute():
            check = stage / check
        if not check.exists():
            raise RenderError(f"bind mount source missing: {src} (expected {check})")
    return f"{src}:{tgt}" + (":ro" if v.get("read_only") else "")


def render(compose, digests, stage, variables=None):
    variables = variables or {}
    stage = Path(stage).resolve()
    out = {k: v for k, v in compose.items() if k != "name"}
    # compose bakes the project name into resolved network/volume names; drop
    # them so the installer's project name applies.
    project = compose.get("name")
    for kind in ("networks", "volumes"):
        for key, body in (out.get(kind) or {}).items():
            if body and not body.get("external") and body.get("name") == f"{project}_{key}":
                del body["name"]
        if kind in out:
            out[kind] = {k: (v or None) for k, v in out[kind].items()}
    for name, svc in out.get("services", {}).items():
        svc.pop("build", None)
        if "image" not in svc:
            raise RenderError(f"service {name} has no image")
        svc["image"] = _digest_for(svc["image"], digests, variables)
        if "volumes" in svc:
            svc["volumes"] = [_volume(v, stage) for v in svc["volumes"]]
        if name in EXTENDS:
            svc.pop("networks", None)
            if "ports" in svc:
                svc["ports"] = [p for p in svc["ports"] if _target(p) not in EXTENDS_PORTS[name]]
            svc["extends"] = dict(EXTENDS[name])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compose-json", required=True)
    ap.add_argument("--digests", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="variable used to resolve ${NAME:-default} in image refs for digest lookup")
    a = ap.parse_args()
    try:
        compose = json.load(open(a.compose_json))
        digests = json.load(open(a.digests))
        out = render(compose, digests, a.stage, dict(s.split("=", 1) for s in a.set))
    except RenderError as e:
        sys.exit(f"render_release: {e}")
    with open(a.out, "w") as f:
        yaml.safe_dump(out, f, sort_keys=False, width=1000)


if __name__ == "__main__":
    main()
