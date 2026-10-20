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
        if not src.startswith("/srv/glow/"):
            bad.append(f"{name}: {src}")
if bad:
    sys.exit("check_compose: state not under GLOW_STATE_DIR: " + "; ".join(bad))
'
echo "check_compose: OK"
