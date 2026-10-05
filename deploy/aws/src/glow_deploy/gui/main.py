"""PyInstaller entry point: start the local server and open a browser tab.

A frozen build has no visible console — this is also how the app is run from
source for local dev/manual smoke-testing (`uv run glow-deploy-gui`).

There's no native window here, just a browser tab pointed at a local server,
so two lifecycle gaps need covering by hand:

- Relaunching while an instance is already running used to just exit
  silently (the single-instance lock refused the bind, the print went
  nowhere in a frozen build). Now the running instance's port is read back
  out of a state file and the browser is reopened at it instead.
- Closing the browser tab left the server running forever with no UI to
  bring it back. A heartbeat pinged from every page (see heartbeat.ts) lets
  a watcher thread notice the tab is gone and quit — but never while a
  provision/update job is still running (see jobs.JobManager), since that's
  a `terraform apply` a hard kill would leave state-locked.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from glow_deploy.gui.app import create_app
from glow_deploy.gui.paths import log_file

_HOST = "127.0.0.1"
# Arbitrary fixed port bound purely as a single-instance mutex — the actual
# server below still runs on a separate, freshly allocated port.
_SINGLE_INSTANCE_LOCK_PORT = 47632

_STATE_FILE = Path(tempfile.gettempdir()) / "glow-deploy-gui-state.json"

# How long without a heartbeat before the watcher treats the tab as closed.
# Pings arrive every 3s (heartbeat.ts) — this tolerates a couple of missed
# pings (page navigation, brief system hiccup) without quitting early.
_HEARTBEAT_TIMEOUT = 10.0
_WATCHER_INTERVAL = 2.0


def _acquire_single_instance_lock() -> socket.socket | None:
    """Bind a fixed local port as a mutex; a second launch fails here.

    ponytail: a bound-socket mutex, not a PID-file with staleness handling —
    good enough for a single local desktop app; revisit if a crashed process
    ever needs to release this cleanly on its own.
    """
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind((_HOST, _SINGLE_INSTANCE_LOCK_PORT))
    except OSError:
        lock_socket.close()
        return None
    return lock_socket


def _reopen_running_instance() -> None:
    try:
        state = json.loads(_STATE_FILE.read_text())
        webbrowser.open(f"http://{_HOST}:{state['port']}")
    except (OSError, ValueError, KeyError):
        print(
            "Glow Deploy is already running, but its window could not be found. "
            "Check your task manager / activity monitor for a stuck 'glow-deploy' "
            "process and close it manually.",
            file=sys.stderr,
        )
        sys.exit(1)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((_HOST, 0))
        return probe.getsockname()[1]


def _open_browser_when_ready(
    host: str, port: int, url: str, timeout: float = 10.0
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    webbrowser.open(url)


def _watch_for_closed_tab(app, server: uvicorn.Server) -> None:
    while not server.should_exit:
        time.sleep(_WATCHER_INTERVAL)
        idle_for = time.time() - app.state.last_heartbeat
        if (
            idle_for > _HEARTBEAT_TIMEOUT
            and not app.state.job_manager.has_running_jobs()
        ):
            server.should_exit = True


def main() -> None:
    # A frozen build has no visible console (see module docstring), so
    # errors logged from route handlers would otherwise go to a stderr
    # nobody can see. File the same messages here instead.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        filename=log_file(),
    )

    lock_socket = _acquire_single_instance_lock()
    if lock_socket is None:
        _reopen_running_instance()
        return

    try:
        port = _free_port()
        url = f"http://{_HOST}:{port}"
        _STATE_FILE.write_text(json.dumps({"port": port}))
        app = create_app()

        config = uvicorn.Config(app, host=_HOST, port=port, log_level="info")
        server = uvicorn.Server(config)

        threading.Thread(
            target=_open_browser_when_ready, args=(_HOST, port, url), daemon=True
        ).start()
        threading.Thread(
            target=_watch_for_closed_tab, args=(app, server), daemon=True
        ).start()

        server.run()
    finally:
        _STATE_FILE.unlink(missing_ok=True)
        lock_socket.close()


if __name__ == "__main__":
    main()
