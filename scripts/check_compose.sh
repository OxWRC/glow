#!/usr/bin/env bash
# Structural rules for the production base file (compose.yml). Fast, needs no
# containers, so CI runs it before the smoke test.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

render() { docker compose -f compose.yml config --format json 2>/dev/null; }

render | python3 -c '
import json, os, sys
services = json.load(sys.stdin)["services"]
errors = []
for name, s in services.items():
    log = s.get("logging") or {}
    opts = log.get("options") or {}
    if log.get("driver") != "json-file" or not opts.get("max-size") or not opts.get("max-file"):
        errors.append(f"{name}: needs json-file logging with max-size and max-file")
    for v in s.get("volumes") or []:
        if v.get("type") != "bind":
            continue
        src = v["source"]
        if "/deploy" in src or src.rstrip("/") == os.path.join(os.getcwd(), "data"):
            errors.append(f"{name}: forbidden bind source {src}")
published = {n: s["ports"] for n, s in services.items() if s.get("ports")}
if list(published) != ["proxy"]:
    errors.append(f"only proxy may publish ports, got {sorted(published)}")
if "mail" in services:
    errors.append("base file defines a mail service")
for name, s in services.items():
    if "mailpit" in s.get("image", ""):
        errors.append(f"{name}: mailpit image in base file")
sentry = services["odk-service"]["environment"].get("SENTRY_KEY")
if sentry:
    errors.append("odk-service SENTRY_KEY must default to empty")
if errors:
    sys.exit("check_compose: " + "; ".join(errors))
'

GLOW_STATE_DIR=/srv/glow render | python3 -c '
import json, sys, os
services = json.load(sys.stdin)["services"]
repo = os.getcwd()
bad = []
for name, s in services.items():
    for v in s.get("volumes") or []:
        src = v.get("source", "")
        if v.get("type") != "bind" or src.startswith(repo + "/"):
            continue  # repo-tracked config (Caddyfile, redis conf, templates)
        if src != "/srv/glow" and not src.startswith("/srv/glow/"):
            bad.append(f"{name}: {src}")
if bad:
    sys.exit("check_compose: state not under GLOW_STATE_DIR: " + "; ".join(bad))
'
# Proxy ports per TLS mode: HTTP always; HTTPS only when Caddy terminates TLS.
for mode in acme files off; do
  GLOW_TLS_MODE=$mode GLOW_TLS_CERT_DIR=$PWD/proxy render | MODE=$mode python3 -c '
import json, os, sys
mode = os.environ["MODE"]
svc = json.load(sys.stdin)["services"]
ports = sorted(p["target"] for s in svc.values() for p in s.get("ports") or [])
want = [80] if mode == "off" else [80, 443]
if ports != want:
    sys.exit(f"check_compose: {mode} mode publishes {ports}, want {want}")
'
done
# Image naming for publishing: built services carry GLOW_REGISTRY/GLOW_VERSION
# names; third-party images pin an explicit non-latest tag.
imgcheck() {
  GLOW_REGISTRY=r.example/x GLOW_VERSION=v9.9.9 docker compose "$@" config --format json 2>/dev/null |
    python3 -c '
import json, os, sys
services = json.load(sys.stdin)["services"]
errors = []
for name, s in services.items():
    img = s.get("image")
    if not img:
        errors.append(f"{name}: no image"); continue
    if img.startswith("r.example/x/"):
        if not img.endswith(":v9.9.9"):
            errors.append(f"{name}: bad tag {img}")
        continue
    tag = img.rsplit(":", 1)[1] if ":" in img.rsplit("/", 1)[-1] else ""
    if tag in ("", "latest"):
        errors.append(f"{name}: third-party image needs explicit tag: {img}")
want = {"api": "glow-api", "dashboard": "glow-dashboard", "enketo": "glow-enketo", "postgres14": os.environ["WANT_PG"], "backup": "glow-backup"}
for svc, base in want.items():
    if svc in services and services[svc].get("image") != f"r.example/x/{base}:v9.9.9":
        errors.append(f"{svc}: image is not {base}")
if "glow-init" in services and services["glow-init"]["image"] != services["api"]["image"]:
    errors.append("glow-init must reuse the api image")
if errors:
    sys.exit("check_compose: " + "; ".join(errors))
'
}
WANT_PG=glow-odk-postgres imgcheck -f compose.yml
WANT_PG=glow-odk-postgres-demo imgcheck -f compose.yml -f compose.demo.yml
WANT_PG=glow-odk-postgres-demo imgcheck -f compose.yml -f compose.override.yml
WANT_PG=glow-odk-postgres-demo imgcheck -f compose.yml -f compose.test.yml
# Backups run in production only; the demo overlay must not render the service.
docker compose -f compose.yml config --services 2>/dev/null | grep -qx backup ||
  { echo "check_compose: backup service missing from compose.yml" >&2; exit 1; }
if docker compose -f compose.yml -f compose.demo.yml config --services 2>/dev/null | grep -qx backup; then
  echo "check_compose: backup service present in demo overlay" >&2; exit 1
fi
echo "check_compose: OK"
