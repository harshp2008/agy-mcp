"""Instrumented reproduction for F22 Part A.

Runs the ``success_simple`` fake-agy scenario through ``JobManager`` exactly
like ``tests/test_f22.py::test_record_usage_written_on_job_completion`` does,
but polls with a 1 ms cadence and records timestamps:

* when ``job.json`` first reports a non-running/non-queued state (DONE),
* whether ``usage.jsonl`` already existed at that moment (what the test
  asserts),
* when ``usage.jsonl`` appeared afterwards.

Usage: .venv/bin/python docs/reports/installer-fixes/f22_instrument.py [N]

Exits 0 always; the printed RACE_OBSERVED lines are the evidence.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from agy_mcp.config import Config  # noqa: E402
from agy_mcp.jobs import get_job  # noqa: E402
from agy_mcp.manager import JobManager  # noqa: E402
from agy_mcp.models import JobState  # noqa: E402
from agy_mcp.runner import LocalProcessRunner  # noqa: E402
from tests.helpers.make_repo import get_safe_git_env, make_repo  # noqa: E402

RUNNING_STATES = (JobState.RUNNING, JobState.QUEUED)


def one_run(index: int) -> bool:
    tmp = Path(tempfile.mkdtemp(prefix=f"f22-instrument-{index}-"))
    home = tmp / "home"
    home.mkdir(parents=True, exist_ok=True)
    state_dir = tmp / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = home / "repo"
    make_repo(repo_dir, initial_commit=True, env=get_safe_git_env(home))

    runner = LocalProcessRunner(executable=str(REPO / "tests" / "fake_agy" / "agy"))
    mgr = JobManager(
        runner=runner,
        config=Config(),
        base_state_dir=state_dir,
        home=home,
        settle_window_seconds=0.05,
    )
    env = mgr.start(
        prompt="__SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Usage Metrics Test Title",
    )
    job_id = env.job_id
    usage_path = state_dir / "usage.jsonl"

    state = None
    t_start = time.monotonic()
    while time.monotonic() - t_start < 20.0:
        job = get_job(job_id, mgr.state_dir)
        state = job.state
        if state not in RUNNING_STATES:
            break
        time.sleep(0.001)
    t_done = time.monotonic()
    usage_at_done = usage_path.is_file()
    while not usage_path.is_file() and time.monotonic() - t_done < 20.0:
        time.sleep(0.001)
    t_usage = time.monotonic()

    delta_ms = (t_usage - t_done) * 1000.0
    race = not usage_at_done
    print(
        f"run {index:02d}: state_at_break={state.value} "
        f"usage_exists_at_done={usage_at_done} "
        f"usage_appeared_{delta_ms:8.3f}ms_after_done race_observed={race}"
    )
    if race:
        print(
            f"           RACE_OBSERVED run {index:02d}: job.json said "
            f"{state.value} {delta_ms:.3f} ms before usage.jsonl existed"
        )
    try:
        mgr.shutdown(timeout=5.0)
    except Exception as exc:  # pragma: no cover - best effort cleanup
        print(f"           shutdown warning: {exc}")
    return race


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    print(f"instrumented F22 reproduction: {count} runs, 1 ms polling")
    races = 0
    for index in range(1, count + 1):
        if one_run(index):
            races += 1
    print(f"SUMMARY: {races}/{count} runs observed DONE before usage.jsonl existed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
