#!/usr/bin/env python3
"""Instrumented replica of tests/test_f1.py::test_f1_invalid_commit_msg_kills_process_and_resumes.

Diagnostic only. It runs the same steps as the real test but:
  * wraps agy_mcp.jobs.save_job to record which thread wrote which job state;
  * polls job.json in a side thread and records every observed state change;
  * snapshots the live job state at the moment manager.wait returns.

Not part of the test suite and not imported by src/.
"""

import json
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

import agy_mcp.jobs as jobs_mod  # noqa: E402

EVENTS: list[tuple[float, str, str]] = []
LOCK = threading.Lock()
T0 = time.monotonic()


def rec(kind: str, text: str) -> None:
    with LOCK:
        EVENTS.append((time.monotonic() - T0, kind, text))


_orig_save_job = jobs_mod.save_job


def _wrapped_save_job(job, base_state_dir=None):
    frames = traceback.extract_stack()[-6:-1]
    compact = " <- ".join(f"{f.name}:{f.lineno}" for f in reversed(frames))
    rec(
        "save_job",
        f"thread={threading.current_thread().name} state={job.state.value} "
        f"reason={job.review_reason} gen={job.run_generation} pid={job.pid} "
        f"conv={job.conversation_id} stack={compact}",
    )
    return _orig_save_job(job, base_state_dir)


jobs_mod.save_job = _wrapped_save_job

# Import after patching so manager/supervisor/watcher bind the wrapped function.
from agy_mcp.jobs import get_job  # noqa: E402
from agy_mcp.models import JobState  # noqa: E402
from tests.test_f1 import _is_alive, _setup_manager_and_repo  # noqa: E402

OBSERVER_STOP = threading.Event()


def _observer(state_dir: Path, job_id: str) -> None:
    last = None
    path = state_dir / "jobs" / job_id / "job.json"
    while not OBSERVER_STOP.is_set():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cur = (
                data.get("state"),
                data.get("review_reason"),
                data.get("run_generation"),
                data.get("pid"),
                data.get("conversation_id"),
            )
            if cur != last:
                rec(
                    "observe",
                    f"state={cur[0]} reason={cur[1]} gen={cur[2]} pid={cur[3]} conv={cur[4]}",
                )
                last = cur
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(0.003)


def _dump(root: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for job_dir in sorted(root.glob("**/state/jobs/*")):
        if not job_dir.is_dir():
            continue
        dest = out / "job"
        shutil.copytree(job_dir, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("tmp"))
        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        rec(
            "final",
            f"job_json state={job.get('state')} reason={job.get('review_reason')} "
            f"gen={job.get('run_generation')} pid={job.get('pid')} "
            f"conv={job.get('conversation_id')}",
        )
    with (out / "timeline.txt").open("w", encoding="utf-8") as f:
        for dt, kind, text in EVENTS:
            f.write(f"+{dt:8.3f}s {kind:10s} {text}\n")


def main() -> int:
    attempts = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    base = Path("/tmp/flaky-f1-live")
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    failures = 0
    for attempt in range(1, attempts + 1):
        root = base / f"attempt-{attempt}"
        root.mkdir()
        rec("start", f"attempt={attempt}")
        manager, repo_dir = _setup_manager_and_repo(root)
        env = manager.start(
            prompt="Work on task __SCENARIO:hang_after_invalid_commit_msg__",
            repo_path=str(repo_dir),
            title="F1 Invalid Commit Msg Test",
        )
        job_id = env.job_id
        obs = threading.Thread(
            target=_observer, args=(manager.state_dir, job_id), name="observer", daemon=True
        )
        obs.start()
        try:
            deadline = time.time() + 10.0
            job = None
            while time.time() < deadline:
                st = manager.status(job_id)
                if st.state == JobState.NEEDS_REVIEW:
                    job = get_job(job_id, manager.state_dir)
                    break
                time.sleep(0.1)

            assert job is not None
            assert job.state == JobState.NEEDS_REVIEW
            assert job.review_reason == "invalid_commit_message"
            assert job.pid is not None

            kill_deadline = time.time() + 5.0
            while time.time() < kill_deadline and _is_alive(job.pid):
                time.sleep(0.05)
            assert not _is_alive(job.pid), f"PID {job.pid} still alive"

            poll_deadline = time.time() + 5.0
            while time.time() < poll_deadline:
                job = get_job(job_id, manager.state_dir)
                if job.conversation_id is not None:
                    break
                time.sleep(0.05)
            assert job.conversation_id is not None
            assert "conv-" in job.conversation_id

            rec("resume-call", f"job_id={job_id}")
            res = manager.resume(job_id, "Fixed commit message format")
            rec("resume-return", f"state={res.state.value}")
            assert res.state in (JobState.RUNNING, JobState.DONE)
            final_env = manager.wait(job_id, seconds=5)
            waited_job = get_job(job_id, manager.state_dir)
            rec(
                "wait-return",
                f"state={final_env.state.value} job_state={waited_job.state.value} "
                f"reason={waited_job.review_reason}",
            )
            if final_env.state != JobState.DONE:
                failures += 1
                out = REPO / "docs/reports/flaky-f1/step-a" / f"instrumented-failure-{failures}"
                _dump(root, out)
                print(
                    f"attempt {attempt}: FAILURE final_state={final_env.state.value} "
                    f"reason={waited_job.review_reason} -> {out}"
                )
            else:
                print(f"attempt {attempt}: pass")
        except Exception as exc:  # noqa: BLE001 - diagnostic harness
            failures += 1
            out = REPO / "docs/reports/flaky-f1/step-a" / f"instrumented-error-{failures}"
            rec("exception", f"{type(exc).__name__}: {exc}")
            _dump(root, out)
            print(f"attempt {attempt}: ERROR {type(exc).__name__}: {exc} -> {out}")
        finally:
            OBSERVER_STOP.set()
            obs.join(timeout=1.0)
            OBSERVER_STOP.clear()
            try:
                manager.shutdown(timeout=1.0)
            except Exception:
                pass
        if failures >= 2:
            print(f"captured {failures} failures after {attempt} attempts")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
