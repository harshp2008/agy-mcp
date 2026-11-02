"""FROZEN acceptance tests for cleanup_jobs round 10 (non-git hardening, R13-R16).

Committed before implementation in Phase 1. Do not edit after Phase 1 commit.

Covers:
- R13: Iterative JSON depth caps and depth bomb protection across candidate, kept neighbor,
  pre-delete recheck, --list, --job, --project, and cleanup_fixtures CLI.
- R14: Duplicate-key rejection and unreadable store/jobs handling in cleanup_fixtures CLI.
- R15: Empty/whitespace argument refusal (--project, --store, --job).
- R16: Process group (pgid) liveness checks via os.killpg mirroring pid status rules.
- Controls: Normal repo cleanup and normal fixture cleanup remain functional.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_TIMEOUT_SECONDS = 15


def _cli_env(home_dir: Path) -> dict[str, str]:
    """Run cleaner code with HOME confined strictly to scratch tempdir."""
    env = os.environ.copy()
    prefix = str(REPO_ROOT / "src") + os.pathsep + str(REPO_ROOT)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = prefix + (os.pathsep + existing if existing else "")
    env["HOME"] = str(home_dir)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_AUTHOR_NAME"] = "Test Author"
    env["GIT_AUTHOR_EMAIL"] = "author@example.com"
    env["GIT_COMMITTER_NAME"] = "Test Committer"
    env["GIT_COMMITTER_EMAIL"] = "committer@example.com"
    return env


def _run(
    home_dir: Path,
    *args: object,
    expect: int | None = 0,
    timeout: float = CLI_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Execute cleanup_jobs CLI entrypoint and optionally assert expected exit code."""
    cmd = [sys.executable, "-m", "agy_mcp.cleanup_jobs", *[str(a) for a in args]]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=_cli_env(home_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"expected rc {expect}, got {proc.returncode}\n"
            f"cmd: {cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _run_fixtures(
    home_dir: Path,
    *args: object,
    expect: int | None = 0,
    timeout: float = CLI_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Execute cleanup_fixtures CLI entrypoint and optionally assert expected exit code."""
    cmd = [sys.executable, "-m", "agy_mcp.cleanup_fixtures", *[str(a) for a in args]]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=_cli_env(home_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"expected rc {expect}, got {proc.returncode}\n"
            f"cmd: {cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _run_json(
    home_dir: Path,
    *args: object,
    expect: int | None = 0,
    timeout: float = CLI_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute cleanup_jobs CLI with --json and return parsed JSON object."""
    proc = _run(home_dir, "--json", *args, expect=expect, timeout=timeout)
    if not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"--json did not print valid JSON: {exc}\n{proc.stdout}") from exc


def _old_time(days: float = 10.0) -> datetime:
    """A timestamp in the past, older than the default 7-day threshold."""
    return datetime.now(timezone.utc) - timedelta(days=days)


def _write_job(
    store: Path,
    *,
    job_id: str | None = None,
    state: str = "done",
    repo: Path | str | None = None,
    worktree_path: Path | str | None = None,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    pid: Any = None,
    pgid: Any = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Write a valid job directory and job.json inside the store."""
    jid = job_id or str(uuid.uuid4())
    job_dir = store / "jobs" / jid
    job_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    created = created_at or now
    updated = updated_at or created
    data: dict[str, Any] = {
        "schema_version": 1,
        "job_id": jid,
        "title": "Cleanup Test Job",
        "repo_path": str(repo) if repo is not None else "/nonexistent/repo",
        "branch": f"agy/cleanup-{jid[:8]}",
        "state": state,
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
    }
    if pid is not None:
        data["pid"] = pid
    if pgid is not None:
        data["pgid"] = pgid
    if worktree_path is not None:
        data["worktree_path"] = str(worktree_path)
    if extra:
        data.update(extra)
    (job_dir / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return jid


def _make_depth_bomb_payload(depth: int = 1100, leaf_id: str | None = None) -> dict[str, Any]:
    """Construct a deeply nested dict structure up to given depth."""
    curr: dict[str, Any] = {"leaf": leaf_id or "value"}
    for i in range(depth):
        curr = {f"k_{i}": curr}
    return curr


# ==============================================================================
# R13: Depth Bomb and Iterative Walk Tests
# ==============================================================================


def test_r13_depth_bomb_extract_strings_iterative() -> None:
    """R13: _extract_strings must be iterative with a depth cap; must not hit RecursionError."""
    from agy_mcp.cleanup_fixtures import _extract_strings

    bomb = _make_depth_bomb_payload(1100, "sample_string")
    # Must raise ValueError for exceeding depth cap or extract iteratively, never RecursionError
    try:
        res = _extract_strings(bomb)
        assert isinstance(res, list)
    except ValueError as exc:
        assert "depth" in str(exc).lower()
    except RecursionError:
        pytest.fail("R13 FAIL: _extract_strings hit RecursionError on deeply nested structure")


def test_r13_depth_bomb_as_candidate_project_dry_run(tmp_path: Path) -> None:
    """R13: Candidate with depth bomb must be kept fail-closed; dry run exits 0, no traceback."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    cid = "11111111-1111-1111-1111-111111111111"
    bomb_data = _make_depth_bomb_payload(1100)
    _write_job(
        store,
        job_id=cid,
        repo=repo,
        state="done",
        updated_at=_old_time(),
        extra={"nested": bomb_data},
    )

    proc = _run(tmp_path, "--store", store, "--project", repo, expect=0)
    assert "Traceback (most recent call last):" not in proc.stderr
    assert "RecursionError" not in proc.stderr
    assert "Candidates (0):" in proc.stdout
    assert "Kept (1):" in proc.stdout


def test_r13_depth_bomb_as_candidate_project_yes(tmp_path: Path) -> None:
    """R13: A candidate with a depth bomb is never deleted under --yes; stays on disk, exit 0."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    cid = "11111111-1111-1111-1111-111111111111"
    bomb_data = _make_depth_bomb_payload(1100)
    _write_job(
        store,
        job_id=cid,
        repo=repo,
        state="done",
        updated_at=_old_time(),
        extra={"nested": bomb_data},
    )

    proc = _run(tmp_path, "--store", store, "--project", repo, "--yes", expect=0)
    assert "Traceback (most recent call last):" not in proc.stderr
    assert (store / "jobs" / cid).is_dir(), "Candidate with depth bomb must not be deleted"


def test_r13_depth_bomb_as_kept_neighbor_project_mode(tmp_path: Path) -> None:
    """R13: Kept neighbor with depth bomb keeps all candidates fail-closed; exit 0, no traceback."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    cand_id = "11111111-1111-1111-1111-111111111111"
    kept_id = "22222222-2222-2222-2222-222222222222"
    _write_job(store, job_id=cand_id, repo=repo, state="done", updated_at=_old_time())

    bomb_data = _make_depth_bomb_payload(1100)
    _write_job(
        store,
        job_id=kept_id,
        repo=repo,
        state="running",
        updated_at=_old_time(),
        extra={"nested": bomb_data},
    )

    # Dry-run: kept neighbor is corrupt/too-deep, fail-closed stops candidate cleanup
    proc = _run(tmp_path, "--store", store, "--project", repo, expect=0)
    assert "Traceback (most recent call last):" not in proc.stderr
    assert "Warning:" in proc.stdout
    assert "Candidates (0):" in proc.stdout

    # With --yes: no deletion occurs
    proc_yes = _run(tmp_path, "--store", store, "--project", repo, "--yes", expect=0)
    assert "Traceback (most recent call last):" not in proc_yes.stderr
    assert (store / "jobs" / cand_id).is_dir(), "Candidate must remain on disk fail-closed"


def test_r13_depth_bomb_in_list_mode(tmp_path: Path) -> None:
    """R13: --list mode with a depth-bomb job prints warnings without traceback and exits 0."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    jid = "11111111-1111-1111-1111-111111111111"
    bomb_data = _make_depth_bomb_payload(1100)
    _write_job(
        store,
        job_id=jid,
        repo=repo,
        state="done",
        updated_at=_old_time(),
        extra={"nested": bomb_data},
    )

    proc = _run(tmp_path, "--store", store, "--list", expect=0)
    assert "Traceback (most recent call last):" not in proc.stderr
    assert "Warning:" in proc.stdout

    data = _run_json(tmp_path, "--store", store, "--list", expect=0)
    assert "warnings" in data
    assert any(jid in w for w in data["warnings"])


def test_r13_depth_bomb_in_job_mode(tmp_path: Path) -> None:
    """R13: --job mode with depth bomb refuses under --yes with exit 2 and no traceback."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    jid = "11111111-1111-1111-1111-111111111111"
    bomb_data = _make_depth_bomb_payload(1100)
    _write_job(
        store,
        job_id=jid,
        repo=repo,
        state="done",
        updated_at=_old_time(),
        extra={"nested": bomb_data},
    )

    # Dry-run reports as kept / ineligible, exit 0
    proc = _run(tmp_path, "--store", store, "--job", jid, expect=0)
    assert "Traceback (most recent call last):" not in proc.stderr
    assert "Candidates (0):" in proc.stdout
    assert "Kept (1):" in proc.stdout

    # With --yes: all requested IDs are ineligible, exits 2
    proc_yes = _run(tmp_path, "--store", store, "--job", jid, "--yes", expect=2)
    assert "Traceback (most recent call last):" not in proc_yes.stderr
    assert (store / "jobs" / jid).is_dir()


def test_r13_depth_bomb_pre_delete_recheck(tmp_path: Path) -> None:
    """R13: Candidate mutating into depth bomb at delete time halts all deletions fail-closed."""
    from agy_mcp.cleanup_jobs import run_cleanup

    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    c1 = "11111111-1111-1111-1111-111111111111"
    c2 = "22222222-2222-2222-2222-222222222222"
    _write_job(store, job_id=c1, repo=repo, state="done", updated_at=_old_time())
    _write_job(store, job_id=c2, repo=repo, state="done", updated_at=_old_time())

    bomb = "{\"k\":" * 1100 + "1" + "}" * 1100
    bomb_json = json.loads(bomb)
    bomb_json["job_id"] = c1
    bomb_json["state"] = "done"
    bomb_json["repo_path"] = str(repo)
    bomb_json["created_at"] = "2020-01-01T00:00:00+00:00"

    def before_delete_cb(job_id: str) -> None:
        if job_id == c1:
            (store / "jobs" / c1 / "job.json").write_text(json.dumps(bomb_json), encoding="utf-8")

    res = run_cleanup(store, project=repo, dry_run=False, before_delete=before_delete_cb)
    assert (store / "jobs" / c1).is_dir(), "c1 must remain on disk"
    assert (store / "jobs" / c2).is_dir(), "c2 must remain on disk due to stop-all rule"
    assert c1 in res["failed_ids"]
    assert c2 in res["failed_ids"]
    assert len(res["deleted_ids"]) == 0


def test_r13_depth_bomb_in_fixtures_cli(tmp_path: Path) -> None:
    """R13: cleanup_fixtures CLI does not crash on depth bomb; kept fail-closed, no traceback."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    pytest_repo = "/tmp/pytest-of-user/pytest-1/test_run0/repo"

    cand_id = "11111111-1111-1111-1111-111111111111"
    kept_id = "22222222-2222-2222-2222-222222222222"
    _write_job(store, job_id=cand_id, repo=pytest_repo, state="done", updated_at=_old_time())

    bomb_data = _make_depth_bomb_payload(1100)
    _write_job(
        store,
        job_id=kept_id,
        repo="/some/other/repo",
        state="running",
        updated_at=_old_time(),
        extra={"nested": bomb_data},
    )

    proc = _run_fixtures(tmp_path, "--store", store, expect=0)
    assert "Traceback (most recent call last):" not in proc.stderr
    assert "RecursionError" not in proc.stderr

    proc_yes = _run_fixtures(tmp_path, "--store", store, "--yes", expect=0)
    assert "Traceback (most recent call last):" not in proc_yes.stderr
    assert (store / "jobs" / cand_id).is_dir()


# ==============================================================================
# R14: Fixtures CLI Duplicate Keys and Unreadable Store Tests
# ==============================================================================


def test_r14_duplicate_key_in_kept_job_fixtures_cli(tmp_path: Path) -> None:
    """R14: Kept job with duplicate key shadowing a candidate reference fails closed in fixtures."""
    store = tmp_path / "store"
    jobs_dir = store / "jobs"
    jobs_dir.mkdir(parents=True)

    cand_id = "11111111-1111-1111-1111-111111111111"
    kept_id = "22222222-2222-2222-2222-222222222222"

    (jobs_dir / cand_id).mkdir()
    (jobs_dir / kept_id).mkdir()

    pytest_repo = "/tmp/pytest-of-testuser/pytest-1/test_foo0/repo"
    cand_content = (
        f'{{"job_id": "{cand_id}", "state": "done", '
        f'"repo_path": "{pytest_repo}", "created_at": "2020-01-01T00:00:00+00:00"}}'
    )
    (jobs_dir / cand_id / "job.json").write_text(cand_content, encoding="utf-8")

    kept_content = (
        f'{{"job_id": "{kept_id}", "state": "running", "repo_path": "/some/real/repo", '
        f'"created_at": "2020-01-01T00:00:00+00:00", '
        f'"related_job": "{cand_id}", "related_job": "unrelated"}}'
    )
    (jobs_dir / kept_id / "job.json").write_text(kept_content, encoding="utf-8")

    # Under --yes, candidate must NOT be deleted because kept job has duplicate keys
    proc = _run_fixtures(tmp_path, "--store", store, "--yes", expect=0)
    assert (jobs_dir / cand_id).is_dir(), "R14 FAIL: candidate was deleted despite duplicate key"
    assert "Deleted: 0" in proc.stdout or "Deleted: 1" not in proc.stdout


def test_r14_duplicate_key_in_candidate_fixtures_cli(tmp_path: Path) -> None:
    """R14: Candidate with duplicate keys is treated as corrupt and not deleted in fixtures CLI."""
    store = tmp_path / "store"
    jobs_dir = store / "jobs"
    jobs_dir.mkdir(parents=True)

    cand_id = "11111111-1111-1111-1111-111111111111"
    (jobs_dir / cand_id).mkdir()

    pytest_repo = "/tmp/pytest-of-testuser/pytest-1/test_foo0/repo"
    cand_content = (
        f'{{"job_id": "{cand_id}", "state": "done", "state": "done", '
        f'"repo_path": "{pytest_repo}", "created_at": "2020-01-01T00:00:00+00:00"}}'
    )
    (jobs_dir / cand_id / "job.json").write_text(cand_content, encoding="utf-8")

    _run_fixtures(tmp_path, "--store", store, "--yes", expect=0)
    assert (jobs_dir / cand_id).is_dir(), "Candidate with duplicate key must not be deleted"


def test_r14_unreadable_jobs_directory_fixtures_cli(tmp_path: Path) -> None:
    """R14: cleanup_fixtures on unreadable store/jobs exits 2 with single line, no traceback."""
    store = tmp_path / "store"
    jobs_dir = store / "jobs"
    jobs_dir.mkdir(parents=True)
    jobs_dir.chmod(0o000)

    try:
        proc = _run_fixtures(tmp_path, "--store", store, expect=2)
        assert "Unexpected internal error" not in proc.stderr
        assert "Traceback" not in proc.stderr
        assert "Error: store/jobs directory unreadable" in proc.stderr
    finally:
        jobs_dir.chmod(0o700)


# ==============================================================================
# R15: Empty and Whitespace Argument Refusal Tests
# ==============================================================================


def test_r15_empty_and_whitespace_project_refused(tmp_path: Path) -> None:
    """R15: --project '' or whitespace must be refused with exit 2 and clear error."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)

    proc_empty = _run(tmp_path, "--store", store, "--project", "", expect=2)
    assert "Error:" in proc_empty.stderr
    assert "project" in proc_empty.stderr.lower()

    proc_ws = _run(tmp_path, "--store", store, "--project", "   ", expect=2)
    assert "Error:" in proc_ws.stderr
    assert "project" in proc_ws.stderr.lower()


def test_r15_empty_and_whitespace_store_refused(tmp_path: Path) -> None:
    """R15: --store '' or whitespace refused with exit 2 and clear error (already passed)."""
    # cleanup_jobs
    proc1 = _run(tmp_path, "--store", "", "--list", expect=2)
    assert "Error:" in proc1.stderr
    assert "store" in proc1.stderr.lower()

    proc2 = _run(tmp_path, "--store", "   ", "--list", expect=2)
    assert "Error:" in proc2.stderr
    assert "store" in proc2.stderr.lower()

    # cleanup_fixtures
    proc3 = _run_fixtures(tmp_path, "--store", "", expect=2)
    assert "Error:" in proc3.stderr
    assert "store" in proc3.stderr.lower()

    proc4 = _run_fixtures(tmp_path, "--store", "   ", expect=2)
    assert "Error:" in proc4.stderr
    assert "store" in proc4.stderr.lower()


def test_r15_empty_and_whitespace_job_refused(tmp_path: Path) -> None:
    """R15: --job '' or whitespace must be refused with exit 2 and clear error (already passed)."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)

    proc1 = _run(tmp_path, "--store", store, "--job", "", expect=2)
    assert "Error:" in proc1.stderr

    proc2 = _run(tmp_path, "--store", store, "--job", "   ", expect=2)
    assert "Error:" in proc2.stderr


# ==============================================================================
# R16: Process Group (pgid) Liveness Tests
# ==============================================================================


def test_r16_pgid_child_alive_leader_dead(tmp_path: Path) -> None:
    """R16: Job whose leader PID has exited but process group contains living child is KEPT."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    # Launch a process in a new session; leader forks child and exits immediately
    script = """
import os, sys, time
pid = os.fork()
if pid == 0:
    # Child sleeps 10s then exits
    time.sleep(10)
    sys.exit(0)
# Leader exits
sys.exit(0)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        start_new_session=True,
    )
    proc.wait(timeout=5)
    leader_pid = proc.pid
    pgid = leader_pid  # Process group ID equals session leader PID

    # Confirm leader is dead
    with pytest.raises(ProcessLookupError):
        os.kill(leader_pid, 0)

    # Confirm process group is still alive
    try:
        os.killpg(pgid, 0)
        pg_alive = True
    except ProcessLookupError:
        pg_alive = False

    assert pg_alive, "Test setup error: child in process group did not stay alive"

    try:
        jid = "11111111-1111-1111-1111-111111111111"
        _write_job(
            store,
            job_id=jid,
            repo=repo,
            state="done",
            updated_at=_old_time(),
            pid=leader_pid,
            pgid=pgid,
        )

        # In project mode, row must be KEPT because process group is alive
        res = _run(tmp_path, "--store", store, "--project", repo, expect=0)
        assert "Candidates (0):" in res.stdout
        assert "Kept (1):" in res.stdout
        assert "process group" in res.stdout.lower()

        # Under --yes, row must NOT be deleted
        _run(tmp_path, "--store", store, "--project", repo, "--yes", expect=0)
        assert (store / "jobs" / jid).is_dir(), "R16 FAIL: job with living pgid was deleted"
    finally:
        # Clean up child process
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass


def test_r16_pgid_dead_group_candidate_deleted(tmp_path: Path) -> None:
    """R16: Job with dead PID and dead PGID is eligible/deleted under --yes (already passed)."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    dead_pid = 4194300
    dead_pgid = 4194300
    jid = "11111111-1111-1111-1111-111111111111"
    _write_job(
        store,
        job_id=jid,
        repo=repo,
        state="done",
        updated_at=_old_time(),
        pid=dead_pid,
        pgid=dead_pgid,
    )

    proc = _run(tmp_path, "--store", store, "--project", repo, "--yes", expect=0)
    assert not (store / "jobs" / jid).exists(), "Job with dead PID/PGID must be deleted"
    assert "Deleted: 1" in proc.stdout


def test_r16_pgid_invalid_values(tmp_path: Path) -> None:
    """R16: Invalid pgid values mirror _check_pid_status and keep the row fail-closed."""
    from agy_mcp.cleanup_fixtures import _check_pgid_status

    # Mirrors _check_pid_status rules:
    # None -> (False, None)
    assert _check_pgid_status(None) == (False, None)

    # bool -> (True, ...)
    keep, reason = _check_pgid_status(True)
    assert keep and "invalid" in reason.lower()
    keep, reason = _check_pgid_status(False)
    assert keep and "invalid" in reason.lower()

    # negative / zero -> (True, ...)
    keep, reason = _check_pgid_status(0)
    assert keep and "invalid" in reason.lower()
    keep, reason = _check_pgid_status(-1)
    assert keep and "invalid" in reason.lower()

    # string / float -> (True, ...)
    keep, reason = _check_pgid_status("12345")
    assert keep and "invalid" in reason.lower()
    keep, reason = _check_pgid_status(1.5)
    assert keep and "invalid" in reason.lower()

    # exceeds pid_max -> (True, ...)
    keep, reason = _check_pgid_status(99999999)
    assert keep and "pid_max" in reason.lower()


def test_r16_pgid_fixtures_cli(tmp_path: Path) -> None:
    """R16: cleanup_fixtures keeps row with living process group even if leader PID is dead."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    pytest_repo = "/tmp/pytest-of-testuser/pytest-1/test_foo0/repo"

    script = """
import os, sys, time
pid = os.fork()
if pid == 0:
    time.sleep(10)
    sys.exit(0)
sys.exit(0)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        start_new_session=True,
    )
    proc.wait(timeout=5)
    leader_pid = proc.pid
    pgid = leader_pid

    try:
        jid = "11111111-1111-1111-1111-111111111111"
        _write_job(
            store,
            job_id=jid,
            repo=pytest_repo,
            state="done",
            updated_at=_old_time(),
            pid=leader_pid,
            pgid=pgid,
        )

        proc_dry = _run_fixtures(tmp_path, "--store", store, expect=0)
        assert "Candidates (0):" in proc_dry.stdout
        assert "Kept (1):" in proc_dry.stdout

        _run_fixtures(tmp_path, "--store", store, "--yes", expect=0)
        assert (store / "jobs" / jid).is_dir(), "Row with living pgid must not be deleted"
    finally:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass


# ==============================================================================
# Controls: Healthy Repositories and Normal Fixture Operations
# ==============================================================================


def test_control_normal_project_cleanup(tmp_path: Path) -> None:
    """Control: Normal eligible candidate in normal repository deletes cleanly under --yes."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo = tmp_path / "normal_repo"
    repo.mkdir()

    jid = "11111111-1111-1111-1111-111111111111"
    _write_job(store, job_id=jid, repo=repo, state="done", updated_at=_old_time())

    proc = _run(tmp_path, "--store", store, "--project", repo, "--yes", expect=0)
    assert not (store / "jobs" / jid).exists()
    assert "Deleted: 1" in proc.stdout


def test_control_normal_fixtures_cleanup(tmp_path: Path) -> None:
    """Control: Normal fixture candidate matching signature deletes cleanly under --yes."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    pytest_repo = "/tmp/pytest-of-testuser/pytest-1/test_control0/repo"

    jid = "11111111-1111-1111-1111-111111111111"
    _write_job(store, job_id=jid, repo=pytest_repo, state="done", updated_at=_old_time())

    proc = _run_fixtures(tmp_path, "--store", store, "--yes", expect=0)
    assert not (store / "jobs" / jid).exists()
    assert "Deleted: 1" in proc.stdout
