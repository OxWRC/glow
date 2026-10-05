"""Single-instance guard, free-port allocation, and the closed-tab watcher
for the packaged app's entry point (gui/main.py) — the parts that don't
require actually starting uvicorn or opening a browser.
"""

from __future__ import annotations

import json
import socket
import time
from types import SimpleNamespace

from glow_deploy.gui import main


def test_free_port_returns_a_bindable_port():
    port = main._free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((main._HOST, port))  # must not raise


def test_single_instance_lock_returns_none_on_a_second_acquire(monkeypatch):
    monkeypatch.setattr(main, "_SINGLE_INSTANCE_LOCK_PORT", 0)
    first = main._acquire_single_instance_lock()
    monkeypatch.setattr(main, "_SINGLE_INSTANCE_LOCK_PORT", first.getsockname()[1])
    try:
        assert main._acquire_single_instance_lock() is None
    finally:
        first.close()


def test_single_instance_lock_can_be_reacquired_after_release(monkeypatch):
    monkeypatch.setattr(main, "_SINGLE_INSTANCE_LOCK_PORT", 0)
    first = main._acquire_single_instance_lock()
    port = first.getsockname()[1]
    first.close()

    monkeypatch.setattr(main, "_SINGLE_INSTANCE_LOCK_PORT", port)
    second = main._acquire_single_instance_lock()
    assert second is not None
    second.close()


def test_reopen_running_instance_opens_browser_at_state_file_port(
    monkeypatch, tmp_path
):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"port": 54321}))
    monkeypatch.setattr(main, "_STATE_FILE", state_file)

    opened = []
    monkeypatch.setattr(main.webbrowser, "open", opened.append)

    main._reopen_running_instance()

    assert opened == ["http://127.0.0.1:54321"]


def test_reopen_running_instance_exits_when_state_file_is_missing(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(main, "_STATE_FILE", tmp_path / "does-not-exist.json")

    try:
        main._reopen_running_instance()
        raised = False
    except SystemExit:
        raised = True
    assert raised


class _FakeServer:
    def __init__(self):
        self.should_exit = False


def test_watcher_quits_after_heartbeat_times_out_with_no_running_jobs(monkeypatch):
    monkeypatch.setattr(main, "_HEARTBEAT_TIMEOUT", 0.05)
    monkeypatch.setattr(main, "_WATCHER_INTERVAL", 0.01)

    app = SimpleNamespace(
        state=SimpleNamespace(
            last_heartbeat=time.time() - 1.0,
            job_manager=SimpleNamespace(has_running_jobs=lambda: False),
        )
    )
    server = _FakeServer()

    main._watch_for_closed_tab(app, server)

    assert server.should_exit is True


def test_watcher_does_not_quit_while_a_job_is_running(monkeypatch):
    monkeypatch.setattr(main, "_HEARTBEAT_TIMEOUT", 0.05)
    monkeypatch.setattr(main, "_WATCHER_INTERVAL", 0.01)

    calls = {"n": 0}

    def has_running_jobs():
        calls["n"] += 1
        # let the watcher loop run a couple of times, then stop it ourselves
        if calls["n"] >= 3:
            server.should_exit = True
        return True

    app = SimpleNamespace(
        state=SimpleNamespace(
            last_heartbeat=time.time() - 1.0,
            job_manager=SimpleNamespace(has_running_jobs=has_running_jobs),
        )
    )
    server = _FakeServer()

    main._watch_for_closed_tab(app, server)

    assert calls["n"] >= 3
