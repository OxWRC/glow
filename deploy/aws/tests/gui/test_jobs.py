"""JobManager: background execution, progress-line capture, and error surfacing."""

from __future__ import annotations

import threading
import time

from glow_deploy.errors import DeployError
from glow_deploy.gui.jobs import JobManager


def _wait_until_terminal(manager: JobManager, job_id: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = manager.get(job_id)
        if job.status not in ("pending", "running"):
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish in time")


def test_submit_runs_fn_and_records_success_and_meta():
    manager = JobManager()

    def fn():
        from glow_deploy import core

        core.write_line("step 1")
        core.write_inline("spinner...")
        core.write_inline("spinner done")
        return 42

    job_id = manager.submit(fn, meta={"kind": "test"})
    job = _wait_until_terminal(manager, job_id)

    assert job.status == "succeeded"
    assert job.result == 42
    assert job.meta == {"kind": "test"}
    # the two inline updates collapse into the same line, not two separate lines
    assert job.lines == ["step 1", "spinner done"]


def test_detail_lines_fold_into_one_block_between_step_lines():
    manager = JobManager()

    def fn():
        from glow_deploy import core

        core.write_line("step 1")
        core.write_line("sub-log a", detail=True)
        core.write_line("sub-log b", detail=True)
        core.write_line("step 2")
        return None

    job_id = manager.submit(fn)
    job = _wait_until_terminal(manager, job_id)

    assert job.status == "succeeded"
    assert len(job.lines) == 3
    assert job.lines[0] == "step 1"
    assert "<details" in job.lines[1]
    assert " open" in job.lines[1]  # expanded by default, no click needed
    assert "sub-log a" in job.lines[1] and "sub-log b" in job.lines[1]
    assert job.lines[2] == "step 2"


def test_detail_lines_survive_interleaved_spinner_heartbeats():
    """Mirrors wait_with_spinner: detail lines from on_tick() interleaved with
    the spinner's own write_inline() redraw. The heartbeat must not fragment
    the sub-log into a new block on every tick."""
    manager = JobManager()

    def fn():
        from glow_deploy import core

        core.write_line("step 1")
        core.write_line("sub-log a", detail=True)
        core.write_inline("step 1 | (1s)")
        core.write_line("sub-log b", detail=True)
        core.write_inline("step 1 | (2s)")
        core.write_line("step 1 done")
        return None

    job_id = manager.submit(fn)
    job = _wait_until_terminal(manager, job_id)

    assert job.status == "succeeded"
    detail_blocks = [line for line in job.lines if "<details" in line]
    assert len(detail_blocks) == 1
    assert "sub-log a" in detail_blocks[0]
    assert "sub-log b" in detail_blocks[0]


def test_submit_records_deploy_error_as_failure():
    manager = JobManager()

    def fn():
        raise DeployError("boom")

    job_id = manager.submit(fn)
    job = _wait_until_terminal(manager, job_id)

    assert job.status == "failed"
    assert job.error == "boom"


def test_get_returns_none_for_unknown_job_id():
    manager = JobManager()
    assert manager.get("does-not-exist") is None


def test_has_running_jobs_true_while_a_job_is_in_flight():
    manager = JobManager()
    started = threading.Event()
    release = threading.Event()

    def fn():
        started.set()
        release.wait(timeout=2.0)

    job_id = manager.submit(fn)
    started.wait(timeout=2.0)
    assert manager.has_running_jobs() is True

    release.set()
    _wait_until_terminal(manager, job_id)
    assert manager.has_running_jobs() is False


def test_has_running_jobs_false_with_no_jobs():
    manager = JobManager()
    assert manager.has_running_jobs() is False
