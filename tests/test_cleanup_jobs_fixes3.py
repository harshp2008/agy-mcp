"""Acceptance tests for cleanup_jobs review findings (fix round 3).

FROZEN tests committed before implementation.
Do not edit src/ or fix anything in this chat.

Test coverage for the review findings:
1. Skipped-candidate corruption must stop deletion:
   - test_skipped_candidate_corrupt_json_stops_deletion:
     Candidates C1, C2, C3 in --project mode with --yes and before_delete callback.
     When called for C1, rewrites C1 to running (skipped/kept).
     When called for C2, rewrites C1 to invalid JSON.
     Expected: C2 and C3 are NOT deleted and stay on disk; failed_ids contains
     C2 and C3 with a reason naming the unreadable/corrupt record of C1;
     manifest status "partial".
   - test_skipped_candidate_non_object_stops_deletion:
     Variant: C1/job.json becomes a JSON non-object.
   - test_skipped_candidate_chmod_000_stops_deletion:
     Variant: C1/job.json chmod 0o000.
2. A .git FILE that is not a valid gitdir pointer must fail closed:
   - test_invalid_git_file_empty_fails_closed: repo_path/.git is empty file.
   - test_invalid_git_file_garbage_fails_closed: repo_path/.git has garbage text.
   - test_invalid_git_file_no_colon_fails_closed: repo_path/.git has "gitdir" with no colon.
   - test_invalid_git_file_truncated_gitdi_fails_closed: repo_path/.git has truncated "gitdi: ...".
   - test_valid_git_file_no_registered_worktree_deletable_contrast:
     Contrast: valid gitdir pointer with no registered worktree remains deletable.
3. Edge collection from rows that become kept must not swallow errors:
   - Documented: no public seam can trigger corruption of candidate between initial
     read and edge-collection loop without monkeypatching or concurrent threads.
     Protected by test 8 (structural guard).
4. --job dry run reporting:
   - test_job_dry_run_eligible_job_reported_as_candidate:
     --job <eligible_id> without --yes reports id as candidate; exit 0, failed_ids empty,
     skipped empty in human and --json output.
   - test_job_dry_run_mixed_reports_only_running_as_skipped_or_failed:
     Mixed dry run (one eligible, one running) reports only running id as skipped/failed.
   - test_job_yes_contrast_mixed:
     Real --yes deletes eligible and keeps running, exit 1.
5. NUL byte in repo_path:
   - test_nul_byte_in_repo_path_list_mode:
     --list shows row with NUL in repo_path under "(unknown)" with eligible_count 0.
   - test_nul_byte_in_repo_path_project_mode:
     --project keeps row with invalid repo_path reason; exits 0 on dry run.
   - test_nul_byte_in_repo_path_job_mode_deletes_when_eligible:
     --job deletes eligible row despite NUL in repo_path (repo_path ignored).
6. Unreadable store/jobs directory:
   - test_unreadable_store_jobs_list_mode_exits_2:
     chmod 0o000 on store/jobs exits 2 on --list with one-line stderr, no traceback.
   - test_unreadable_store_jobs_project_mode_exits_2:
     chmod 0o000 on store/jobs exits 2 on --project dry run with one-line stderr.
   - test_unreadable_store_jobs_job_mode_exits_2:
     chmod 0o000 on store/jobs exits 2 on --job dry run with one-line stderr.
7. Document only:
   - Note on O(projects x jobs) scaling in --list mode.
8. STRUCTURAL GUARD:
   - test_no_swallowed_exceptions_structural_guard:
     AST inspection verifying that no broad exception handler swallows exceptions with pass.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agy_mcp.cleanup_jobs import run_cleanup
from tests.helpers.make_repo import make_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_TIMEOUT_SECONDS = 120


# --------------------------------------------------------------------------- #
# CLI test helpers
# --------------------------------------------------------------------------- #


def _cli_env() -> dict[str, str]:
    """Run the worktree's code, not an editable install from another checkout."""
    env = os.environ.copy()
    prefix = str(REPO_ROOT / "src") + os.pathsep + str(REPO_ROOT)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = prefix + (os.pathsep + existing if existing else "")
    return env


def _run(*args: object, expect: int = 0) -> subprocess.CompletedProcess[str]:
    """Execute the cleanup_jobs CLI entrypoint and assert expected exit code."""
    cmd = [sys.executable, "-m", "agy_mcp.cleanup_jobs", *[str(a) for a in args]]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=_cli_env(),
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
    )
    assert proc.returncode == expect, (
        f"expected rc {expect}, got {proc.returncode}\n"
        f"cmd: {cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc


def _run_json(*args: object, expect: int = 0) -> dict[str, Any]:
    """Execute the cleanup_jobs CLI with --json and return parsed JSON object."""
    proc = _run("--json", *args, expect=expect)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"--json did not print valid JSON: {exc}\n{proc.stdout}") from exc


@pytest.fixture
def store() -> Path:
    """Create a temporary store inside session HOME for testing."""
    root = Path(os.environ["HOME"]) / "cleanup-jobs-fixes3-tests" / uuid.uuid4().hex
    (root / "jobs").mkdir(parents=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _old_time(days: float = 10.0) -> datetime:
    """A timestamp in the past, older than the default 7-day threshold."""
    return datetime.now(timezone.utc) - timedelta(days=days)


def _recent_time(days: float = 2.0) -> datetime:
    """A timestamp in the past, fresher than the default 7-day threshold."""
    return datetime.now(timezone.utc) - timedelta(days=days)


def _write_job(
    store: Path,
    *,
    job_id: str | None = None,
    state: str = "done",
    repo: Path | str | None = None,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    pid: int | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Write a valid job directory and job.json inside the store."""
    jid = job_id or str(uuid.uuid4())
    job_dir = store / "jobs" / jid
    job_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    created = created_at or now
    updated = updated_at or created
    repo_val = str(repo) if repo is not None else str(Path(os.environ["HOME"]) / "default-project")
    data: dict[str, Any] = {
        "schema_version": 1,
        "job_id": jid,
        "title": "Cleanup Fixes 3 Test Job",
        "repo_path": repo_val,
        "branch": f"agy/cleanup-{jid[:8]}",
        "base_commit": "0" * 40,
        "head_commit": "",
        "state": state,
        "model": "gemini-3.8-flash-medium",
        "model_resolved": None,
        "max_minutes": 240,
        "verify": None,
        "verify_timeout_seconds": 900,
        "conversation_id": None,
        "pid": pid,
        "pgid": None,
        "start_time": None,
        "worktree_path": str(job_dir / "work"),
        "commit_msg_generation": 0,
        "review_reason": None,
        "source_dirty": False,
        "recovered": False,
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
    }
    if extra:
        data.update(extra)
    (job_dir / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return jid


# --------------------------------------------------------------------------- #
# Finding 1: Skipped-candidate corruption must stop deletion
# --------------------------------------------------------------------------- #


def test_skipped_candidate_corrupt_json_stops_deletion(store: Path) -> None:
    """When a skipped candidate becomes corrupt, remaining candidates are NOT deleted."""
    repo = Path(os.environ["HOME"]) / "d1_skipped_corrupt_proj"
    repo.mkdir(parents=True, exist_ok=True)

    c1 = "00000000-0000-4000-8000-000000000001"
    c2 = "00000000-0000-4000-8000-000000000002"
    c3 = "00000000-0000-4000-8000-000000000003"

    _write_job(store, job_id=c1, repo=repo, state="done", updated_at=_old_time())
    _write_job(store, job_id=c2, repo=repo, state="done", updated_at=_old_time())
    _write_job(store, job_id=c3, repo=repo, state="done", updated_at=_old_time())

    def before_delete_cb(cid: str) -> None:
        if cid == c1:
            jfile = store / "jobs" / c1 / "job.json"
            data = json.loads(jfile.read_text(encoding="utf-8"))
            data["state"] = "running"
            jfile.write_text(json.dumps(data, indent=2), encoding="utf-8")
        elif cid == c2:
            jfile = store / "jobs" / c1 / "job.json"
            jfile.write_text(f"{{ corrupt: json for {c1}", encoding="utf-8")

    res = run_cleanup(store, project=repo, dry_run=False, before_delete=before_delete_cb)

    # C2 and C3 must NOT be deleted and stay on disk
    assert (store / "jobs" / c2).is_dir(), "candidate C2 must stay on disk when C1 is corrupt"
    assert (store / "jobs" / c3).is_dir(), "candidate C3 must stay on disk when C1 is corrupt"
    assert (store / "jobs" / c1).is_dir(), "skipped row C1 must stay on disk"

    assert c2 not in res.get("deleted_ids", []), "C2 must NOT be in deleted_ids"
    assert c3 not in res.get("deleted_ids", []), "C3 must NOT be in deleted_ids"
    assert c2 in res.get("failed_ids", []), "C2 must be in failed_ids"
    assert c3 in res.get("failed_ids", []), "C3 must be in failed_ids"

    s2 = next((s for s in res.get("skipped", []) if s.get("job_id") == c2), None)
    s3 = next((s for s in res.get("skipped", []) if s.get("job_id") == c3), None)
    assert s2 is not None, f"C2 must be reported in skipped: {res}"
    assert s3 is not None, f"C3 must be reported in skipped: {res}"

    r2 = s2.get("reasons", [])
    r3 = s3.get("reasons", [])
    assert any(c1 in r and ("corrupt" in r.lower() or "unreadable" in r.lower()) for r in r2), (
        f"C2 reason must name corrupt/unreadable record of C1: {r2}"
    )
    assert any(c1 in r and ("corrupt" in r.lower() or "unreadable" in r.lower()) for r in r3), (
        f"C3 reason must name corrupt/unreadable record of C1: {r3}"
    )

    assert res.get("manifest") is not None
    mdata = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
    assert mdata.get("status") == "partial", f"manifest status must be 'partial': {mdata}"
    assert c2 not in mdata.get("removed_ids", []), "C2 must not be in manifest removed_ids"
    assert c3 not in mdata.get("removed_ids", []), "C3 must not be in manifest removed_ids"
    assert c2 in mdata.get("failed_ids", []), "C2 must be in manifest failed_ids"
    assert c3 in mdata.get("failed_ids", []), "C3 must be in manifest failed_ids"


def test_skipped_candidate_non_object_stops_deletion(store: Path) -> None:
    """When skipped candidate becomes a JSON non-object, remaining candidates are NOT deleted."""
    repo = Path(os.environ["HOME"]) / "d1_skipped_nonobj_proj"
    repo.mkdir(parents=True, exist_ok=True)

    c1 = "00000000-0000-4000-8000-000000000001"
    c2 = "00000000-0000-4000-8000-000000000002"
    c3 = "00000000-0000-4000-8000-000000000003"

    _write_job(store, job_id=c1, repo=repo, state="done", updated_at=_old_time())
    _write_job(store, job_id=c2, repo=repo, state="done", updated_at=_old_time())
    _write_job(store, job_id=c3, repo=repo, state="done", updated_at=_old_time())

    def before_delete_cb(cid: str) -> None:
        if cid == c1:
            jfile = store / "jobs" / c1 / "job.json"
            data = json.loads(jfile.read_text(encoding="utf-8"))
            data["state"] = "running"
            jfile.write_text(json.dumps(data, indent=2), encoding="utf-8")
        elif cid == c2:
            jfile = store / "jobs" / c1 / "job.json"
            jfile.write_text(f'["not", "a", "dict", "{c1}"]\n', encoding="utf-8")

    res = run_cleanup(store, project=repo, dry_run=False, before_delete=before_delete_cb)

    assert (store / "jobs" / c2).is_dir(), "candidate C2 must stay on disk when C1 is non-object"
    assert (store / "jobs" / c3).is_dir(), "candidate C3 must stay on disk when C1 is non-object"
    assert c2 not in res.get("deleted_ids", [])
    assert c3 not in res.get("deleted_ids", [])
    assert c2 in res.get("failed_ids", [])
    assert c3 in res.get("failed_ids", [])

    s2 = next((s for s in res.get("skipped", []) if s.get("job_id") == c2), None)
    s3 = next((s for s in res.get("skipped", []) if s.get("job_id") == c3), None)
    assert s2 is not None
    assert s3 is not None
    r2 = s2.get("reasons", [])
    r3 = s3.get("reasons", [])
    assert any(
        c1 in r
        and ("non-object" in r.lower() or "corrupt" in r.lower() or "unreadable" in r.lower())
        for r in r2
    ), f"C2 reason must name non-object record of C1: {r2}"
    assert any(
        c1 in r
        and ("non-object" in r.lower() or "corrupt" in r.lower() or "unreadable" in r.lower())
        for r in r3
    ), f"C3 reason must name non-object record of C1: {r3}"

    assert res.get("manifest") is not None
    mdata = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
    assert mdata.get("status") == "partial"
    assert c2 not in mdata.get("removed_ids", [])
    assert c3 not in mdata.get("removed_ids", [])


def test_skipped_candidate_chmod_000_stops_deletion(store: Path) -> None:
    """When skipped candidate becomes unreadable (chmod 0o000), deletion stops fail-closed."""
    if os.geteuid() == 0:
        pytest.skip("cannot test chmod-based denial as root")

    repo = Path(os.environ["HOME"]) / "d1_skipped_chmod_proj"
    repo.mkdir(parents=True, exist_ok=True)

    c1 = "00000000-0000-4000-8000-000000000001"
    c2 = "00000000-0000-4000-8000-000000000002"
    c3 = "00000000-0000-4000-8000-000000000003"

    _write_job(store, job_id=c1, repo=repo, state="done", updated_at=_old_time())
    _write_job(store, job_id=c2, repo=repo, state="done", updated_at=_old_time())
    _write_job(store, job_id=c3, repo=repo, state="done", updated_at=_old_time())

    c1_file = store / "jobs" / c1 / "job.json"

    def before_delete_cb(cid: str) -> None:
        if cid == c1:
            data = json.loads(c1_file.read_text(encoding="utf-8"))
            data["state"] = "running"
            c1_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        elif cid == c2:
            os.chmod(c1_file, 0o000)

    try:
        res = run_cleanup(store, project=repo, dry_run=False, before_delete=before_delete_cb)
        assert (store / "jobs" / c2).is_dir(), "candidate C2 must stay on disk"
        assert (store / "jobs" / c3).is_dir(), "candidate C3 must stay on disk"
        assert c2 not in res.get("deleted_ids", [])
        assert c3 not in res.get("deleted_ids", [])
        assert c2 in res.get("failed_ids", [])
        assert c3 in res.get("failed_ids", [])

        s2 = next((s for s in res.get("skipped", []) if s.get("job_id") == c2), None)
        assert s2 is not None
        r2 = s2.get("reasons", [])
        assert any(c1 in r and ("unreadable" in r.lower() or "corrupt" in r.lower()) for r in r2), (
            f"C2 reason must mention unreadable record of C1: {r2}"
        )

        assert res.get("manifest") is not None
        mdata = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
        assert mdata.get("status") == "partial"
    finally:
        os.chmod(c1_file, 0o600)


# --------------------------------------------------------------------------- #
# Finding 2: A .git FILE that is not a valid gitdir pointer must fail closed
# --------------------------------------------------------------------------- #


def test_invalid_git_file_empty_fails_closed(store: Path, tmp_path: Path) -> None:
    """repo_path/.git as an empty file must fail closed: kept with clear reason, not deleted."""
    repo_path = tmp_path / "repo_git_empty"
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / ".git").write_text("", encoding="utf-8")

    jid = _write_job(store, repo=repo_path, state="done", updated_at=_old_time())

    # Dry-run JSON: kept, not candidate, reason names .git file unreadable or invalid
    res = _run_json("--store", store, "--project", repo_path, expect=0)
    assert res["candidate_count"] == 0, f"empty .git file must not be candidate: {res}"
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ".git" in r.lower() and ("unreadable" in r.lower() or "invalid" in r.lower())
        for r in reasons
    ), f"reason must mention .git file unreadable/invalid: {reasons}"

    # Dry-run human: exit 0, no traceback
    proc = _run("--store", store, "--project", repo_path, expect=0)
    assert "Traceback" not in proc.stderr

    # Deletion with --yes: nothing deleted
    proc_yes = _run("--store", store, "--project", repo_path, "--yes", expect=0)
    assert "Traceback" not in proc_yes.stderr
    assert (store / "jobs" / jid).is_dir(), "job must NOT be deleted with invalid .git file"


def test_invalid_git_file_garbage_fails_closed(store: Path, tmp_path: Path) -> None:
    """repo_path/.git with garbage text must fail closed: kept with clear reason, not deleted."""
    repo_path = tmp_path / "repo_git_garbage"
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / ".git").write_text("not a valid gitdir line at all\n", encoding="utf-8")

    jid = _write_job(store, repo=repo_path, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", repo_path, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ".git" in r.lower() and ("unreadable" in r.lower() or "invalid" in r.lower())
        for r in reasons
    ), f"reason must mention .git file unreadable/invalid: {reasons}"

    proc = _run("--store", store, "--project", repo_path, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", repo_path, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_invalid_git_file_no_colon_fails_closed(store: Path, tmp_path: Path) -> None:
    """repo_path/.git with 'gitdir' without colon must fail closed: kept, not deleted."""
    repo_path = tmp_path / "repo_git_no_colon"
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / ".git").write_text("gitdir /path/to/somewhere\n", encoding="utf-8")

    jid = _write_job(store, repo=repo_path, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", repo_path, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ".git" in r.lower() and ("unreadable" in r.lower() or "invalid" in r.lower())
        for r in reasons
    ), f"reason must mention .git file unreadable/invalid: {reasons}"

    proc = _run("--store", store, "--project", repo_path, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", repo_path, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_invalid_git_file_truncated_gitdi_fails_closed(store: Path, tmp_path: Path) -> None:
    """repo_path/.git with truncated 'gitdi' must fail closed: kept, not deleted."""
    repo_path = tmp_path / "repo_git_truncated"
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / ".git").write_text("gitdi: /path/to/somewhere\n", encoding="utf-8")

    jid = _write_job(store, repo=repo_path, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", repo_path, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ".git" in r.lower() and ("unreadable" in r.lower() or "invalid" in r.lower())
        for r in reasons
    ), f"reason must mention .git file unreadable/invalid: {reasons}"

    proc = _run("--store", store, "--project", repo_path, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", repo_path, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_valid_git_file_no_registered_worktree_deletable_contrast(
    store: Path, tmp_path: Path
) -> None:
    """Contrast: valid gitdir pointer with no registered worktree remains deletable."""
    real_repo = make_repo(tmp_path / "real_git_dir")
    linked_repo = tmp_path / "linked_worktree_repo"
    linked_repo.mkdir(parents=True, exist_ok=True)
    (linked_repo / ".git").write_text(f"gitdir: {real_repo / '.git'}\n", encoding="utf-8")

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())

    # Dry-run: candidate
    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 1
    assert res["candidates"][0]["job_id"] == jid
    assert res["kept_count"] == 0

    # With --yes: successfully deleted
    proc = _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert "Traceback" not in proc.stderr
    assert not (store / "jobs" / jid).exists(), "job with valid .git file should be deleted"


# --------------------------------------------------------------------------- #
# Finding 3: Edge collection from rows that become kept must not swallow errors
# --------------------------------------------------------------------------- #
# Note: In run_cleanup, candidates are validated as JSON dicts in the initial scan
# before edge collection runs. Between initial scan and edge collection in the same
# synchronous function, no public seam (callbacks/hooks) exists to corrupt a row
# that transitions from candidate to kept without concurrent threads or monkeypatching.
# As instructed by prompt finding 3, we do not monkeypatch and rely on the structural
# guard (test 8) to verify that edge-collection error handlers do not swallow errors.


# --------------------------------------------------------------------------- #
# Finding 4: --job dry run reporting
# --------------------------------------------------------------------------- #


def test_job_dry_run_eligible_job_reported_as_candidate(store: Path) -> None:
    """--job <eligible_id> in dry run: exit 0, failed_ids and skipped empty, candidate."""
    e = _write_job(store, state="done", updated_at=_old_time())

    # JSON output
    report = _run_json("--store", store, "--job", e, expect=0)
    assert report["candidate_count"] == 1
    assert report["candidates"][0]["job_id"] == e
    assert report.get("failed_ids", []) == [], f"failed_ids must be empty: {report}"
    assert report.get("skipped", []) == [], f"skipped must be empty: {report}"
    assert report["skipped_count"] == 0
    assert report.get("deleted_ids", []) == []

    # Human output
    proc = _run("--store", store, "--job", e, expect=0)
    assert "Traceback" not in proc.stderr
    assert "Skipped/Failed" not in proc.stdout, f"Skipped/Failed should not appear: {proc.stdout}"
    assert "Candidates (1):" in proc.stdout
    assert e in proc.stdout

    # Directory untouched
    assert (store / "jobs" / e).is_dir()


def test_job_dry_run_mixed_reports_only_running_as_skipped_or_failed(store: Path) -> None:
    """Mixed dry run (one eligible, one running) reports only running id as skipped/failed."""
    e = _write_job(store, state="done", updated_at=_old_time())
    r = _write_job(store, state="running", updated_at=_recent_time())

    report = _run_json("--store", store, "--job", e, "--job", r, expect=0)
    assert report["candidate_count"] == 1
    assert report["candidates"][0]["job_id"] == e
    assert e not in report.get("failed_ids", []), f"eligible job {e} must not be in failed_ids"
    assert e not in [s.get("job_id") for s in report.get("skipped", [])]

    assert r in report.get("failed_ids", []), f"running job {r} must be in failed_ids"
    skipped_ids = [s.get("job_id") for s in report.get("skipped", [])]
    assert skipped_ids == [r], f"only running job {r} must be in skipped: {report}"

    s_r = next(s for s in report.get("skipped", []) if s.get("job_id") == r)
    reasons = s_r.get("reasons", [])
    assert any("running" in reason.lower() or "terminal" in reason.lower() for reason in reasons)

    proc = _run("--store", store, "--job", e, "--job", r, expect=0)
    assert "Traceback" not in proc.stderr
    assert "Candidates (1):" in proc.stdout
    assert e in proc.stdout

    assert (store / "jobs" / e).is_dir()
    assert (store / "jobs" / r).is_dir()


def test_job_yes_contrast_mixed(store: Path) -> None:
    """Contrast: with --yes, mixed eligible and running deletes eligible and exits 1."""
    e = _write_job(store, state="done", updated_at=_old_time())
    r = _write_job(store, state="running", updated_at=_recent_time())

    proc = _run("--store", store, "--job", e, "--job", r, "--yes", expect=1)
    assert "Traceback" not in proc.stderr
    assert not (store / "jobs" / e).exists(), "eligible job E must be deleted"
    assert (store / "jobs" / r).is_dir(), "running job R must be kept"


# --------------------------------------------------------------------------- #
# Finding 5: NUL byte in repo_path
# --------------------------------------------------------------------------- #


def test_nul_byte_in_repo_path_list_mode(store: Path) -> None:
    """A row with NUL in repo_path must show under (unknown) with eligible_count 0 in --list."""
    _write_job(store, repo="/some/path\x00bad", state="done", updated_at=_old_time())

    # JSON mode
    report = _run_json("--store", store, "--list", expect=0)
    projects = report.get("projects", [])
    unknown_proj = next((p for p in projects if p.get("repo_path") == "(unknown)"), None)
    assert unknown_proj is not None, f"expected '(unknown)' project in {projects}"
    assert unknown_proj.get("eligible_count") == 0

    # Human mode
    proc = _run("--store", store, "--list", expect=0)
    assert "Traceback" not in proc.stderr
    assert "(unknown)" in proc.stdout


def test_nul_byte_in_repo_path_project_mode(store: Path) -> None:
    """--project keeps row with NUL in repo_path; dry run exits 0 with no traceback."""
    jid = _write_job(store, repo="/some/path\x00bad", state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", "/some/path", expect=0)
    assert jid not in [c["job_id"] for c in res.get("candidates", [])]
    kept = res.get("kept", [])
    j_kept = next((k for k in kept if k.get("job_id") == jid), None)
    assert j_kept is not None, f"job {jid} must be kept: {res}"
    reasons = j_kept.get("reasons", [])
    assert any("repo_path" in r.lower() or "invalid" in r.lower() for r in reasons), (
        f"reason must mention repo_path invalid: {reasons}"
    )

    proc = _run("--store", store, "--project", "/some/path", expect=0)
    assert "Traceback" not in proc.stderr


def test_nul_byte_in_repo_path_job_mode_deletes_when_eligible(store: Path) -> None:
    """--job mode deletes job with NUL in repo_path when otherwise eligible (repo_path ignored)."""
    jid = _write_job(store, repo="/some/path\x00bad", state="done", updated_at=_old_time())

    proc = _run("--store", store, "--job", jid, "--yes", expect=0)
    assert "Traceback" not in proc.stderr
    assert not (store / "jobs" / jid).exists(), "job must be deleted in --job mode"


# --------------------------------------------------------------------------- #
# Finding 6: Unreadable store/jobs directory
# --------------------------------------------------------------------------- #


def test_unreadable_store_jobs_list_mode_exits_2(store: Path) -> None:
    """chmod 0o000 on store/jobs: --list exits 2 with one-line stderr, no traceback."""
    if os.geteuid() == 0:
        pytest.skip("cannot test chmod-based denial as root")

    jobs_dir = store / "jobs"
    os.chmod(jobs_dir, 0o000)
    try:
        proc = _run("--store", store, "--list", expect=2)
        assert "Traceback" not in proc.stderr
        lines = [line for line in proc.stderr.splitlines() if line.strip()]
        assert len(lines) == 1, f"stderr must be a single line, got:\n{proc.stderr}"
        err = lines[0].lower()
        assert "jobs" in err or "unreadable" in err or "permission" in err
    finally:
        os.chmod(jobs_dir, 0o700)


def test_unreadable_store_jobs_project_mode_exits_2(store: Path) -> None:
    """chmod 0o000 on store/jobs: --project dry run exits 2 with one-line stderr, no traceback."""
    if os.geteuid() == 0:
        pytest.skip("cannot test chmod-based denial as root")

    jobs_dir = store / "jobs"
    os.chmod(jobs_dir, 0o000)
    try:
        proc = _run("--store", store, "--project", "/some/path", expect=2)
        assert "Traceback" not in proc.stderr
        lines = [line for line in proc.stderr.splitlines() if line.strip()]
        assert len(lines) == 1, f"stderr must be a single line, got:\n{proc.stderr}"
        err = lines[0].lower()
        assert "jobs" in err or "unreadable" in err or "permission" in err
    finally:
        os.chmod(jobs_dir, 0o700)


def test_unreadable_store_jobs_job_mode_exits_2(store: Path) -> None:
    """chmod 0o000 on store/jobs: --job dry run exits 2 with one-line stderr, no traceback."""
    if os.geteuid() == 0:
        pytest.skip("cannot test chmod-based denial as root")

    jobs_dir = store / "jobs"
    jid = "00000000-0000-4000-8000-000000000001"
    os.chmod(jobs_dir, 0o000)
    try:
        proc = _run("--store", store, "--job", jid, expect=2)
        assert "Traceback" not in proc.stderr
        lines = [line for line in proc.stderr.splitlines() if line.strip()]
        assert len(lines) == 1, f"stderr must be a single line, got:\n{proc.stderr}"
        err = lines[0].lower()
        assert "jobs" in err or "unreadable" in err or "permission" in err
    finally:
        os.chmod(jobs_dir, 0o700)


# --------------------------------------------------------------------------- #
# Finding 7: Documentation only
# --------------------------------------------------------------------------- #
# --list mode scales O(projects x jobs) because for each distinct project found,
# it runs run_cleanup dry run to compute eligible_count, re-scanning the store.


# --------------------------------------------------------------------------- #
# Finding 8: STRUCTURAL GUARD (No swallowed exceptions)
# --------------------------------------------------------------------------- #


def find_offending_handlers(source: str) -> list[tuple[int, str]]:
    """Check Python source for offending exception handlers per the structural guard rule.

    The rule: every handler for Exception, BaseException or a bare except,
    INCLUDING when the type is a tuple that contains either, must contain a
    raise, or must record a failure reason (a return or assignment that includes
    the caught exception variable or a reason string/list, or a call to something
    whose name contains fail, block, warn, reason, append or write). A handler
    whose body is only pass, continue, break, "...", a bare constant return
    (return, return None, return False, return True, return 0), or only a logging
    call is offending. Also flag any use of contextlib.suppress(Exception/BaseException)
    and a "return" inside a "finally" block.
    """
    text = source.strip()
    if text == "try/finally with return in finally":
        text = "try:\n    pass\nfinally:\n    return"
    elif text.startswith("bare "):
        text = text[5:].strip()
    if text.startswith("except"):
        text = f"try:\n    pass\n{text}"

    tree = ast.parse(text)
    flagged: list[tuple[int, str]] = []

    def is_broad_type(t: ast.AST | None) -> bool:
        if t is None:
            return True
        if isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"):
            return True
        if isinstance(t, ast.Tuple):
            return any(is_broad_type(elt) for elt in t.elts)
        return False

    def is_bare_constant(val: ast.AST | None) -> bool:
        if val is None:
            return True
        if isinstance(val, ast.Constant) and val.value in (None, False, True, 0):
            return True
        return False

    def is_logging_call(call: ast.Call) -> bool:
        func = call.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in ("logger", "logging", "log"):
                return True
        elif isinstance(func, ast.Name) and func.id in (
            "log",
            "debug",
            "info",
            "warning",
            "error",
            "exception",
        ):
            return True
        return False

    def has_reason_recording(node_list: list[ast.stmt], exc_var: str | None) -> bool:
        for stmt in node_list:
            for n in ast.walk(stmt):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(n, ast.Raise):
                    return True
                if isinstance(n, ast.Call):
                    if not is_logging_call(n):
                        name = ""
                        if isinstance(n.func, ast.Name):
                            name = n.func.id
                        elif isinstance(n.func, ast.Attribute):
                            name = n.func.attr
                        name = name.lower()
                        if any(
                            kw in name
                            for kw in ("fail", "block", "warn", "reason", "append", "write")
                        ):
                            return True
                if isinstance(n, (ast.Return, ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    if exc_var is not None:
                        for sub in ast.walk(n):
                            if isinstance(sub, ast.Name) and sub.id == exc_var:
                                return True
                    for sub in ast.walk(n):
                        if isinstance(sub, (ast.Constant, ast.JoinedStr)):
                            if (
                                isinstance(sub, ast.Constant)
                                and isinstance(sub.value, str)
                                and sub.value.strip()
                            ):
                                return True
                            if isinstance(sub, ast.JoinedStr):
                                return True
                        if isinstance(sub, ast.Name):
                            sub_id = sub.id.lower()
                            if any(
                                kw in sub_id for kw in ("reason", "fail", "warn", "block", "err")
                            ):
                                return True
        return False

    # 1. Flag use of contextlib.suppress(Exception/BaseException)
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call):
                    f = item.context_expr.func
                    is_suppress = False
                    if isinstance(f, ast.Attribute) and f.attr == "suppress":
                        is_suppress = True
                    elif isinstance(f, ast.Name) and f.id == "suppress":
                        is_suppress = True
                    if is_suppress and any(is_broad_type(a) for a in item.context_expr.args):
                        flagged.append((node.lineno, ast.unparse(node).strip()))

        # 2. Flag return inside finally block
        if isinstance(node, ast.Try) and node.finalbody:
            for stmt in node.finalbody:
                for sub in ast.walk(stmt):
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if isinstance(sub, ast.Return):
                        flagged.append((node.lineno, ast.unparse(node).strip()))
                        break

        # 3. Check broad exception handlers
        if isinstance(node, ast.ExceptHandler) and is_broad_type(node.type):
            exc_var = node.name
            body = node.body
            if not body:
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif len(body) == 1 and isinstance(body[0], ast.Pass):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif len(body) == 1 and isinstance(body[0], ast.Continue):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif len(body) == 1 and isinstance(body[0], ast.Break):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif (
                len(body) == 1
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and body[0].value.value is ...
            ):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif (
                len(body) == 1
                and isinstance(body[0], ast.Return)
                and is_bare_constant(body[0].value)
            ):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif (
                len(body) == 1
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Call)
                and is_logging_call(body[0].value)
            ):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif not has_reason_recording(body, exc_var):
                flagged.append((node.lineno, ast.unparse(node).strip()))

    return flagged


def test_no_swallowed_exceptions_checker_self_test() -> None:
    """Self-test for find_offending_handlers on synthetic source strings."""
    flagged_samples = [
        "except Exception: pass",
        "except (Exception,): pass",
        "except (OSError, Exception): continue",
        "except Exception: return False",
        "except BaseException: pass",
        "bare except: pass",
        "with contextlib.suppress(Exception): ...",
        "try/finally with return in finally",
        "except Exception: logger.debug(x)",
    ]
    for sample in flagged_samples:
        offenders = find_offending_handlers(sample)
        assert offenders, f"Expected {sample!r} to be flagged by structural guard checker"

    unflagged_samples = [
        "except OSError as exc: return (True, f'x {exc}')",
        "except json.JSONDecodeError: return False, ['job.json is not valid JSON']",
        "except Exception as exc: failed_ids.append(cid)",
        "except (OSError, ValueError) as exc: raise RuntimeError() from exc",
    ]
    for sample in unflagged_samples:
        offenders = find_offending_handlers(sample)
        assert not offenders, (
            f"Expected {sample!r} NOT to be flagged by structural guard checker, got {offenders}"
        )


def test_no_swallowed_exceptions_structural_guard() -> None:
    """AST guard: broad exception handlers in cleanup_jobs.py must satisfy the rule.

    The rule: every handler for Exception, BaseException or a bare except,
    INCLUDING when the type is a tuple that contains either, must contain a
    raise, or must record a failure reason (a return or assignment that includes
    the caught exception variable or a reason string/list, or a call to something
    whose name contains fail, block, warn, reason, append or write). A handler
    whose body is only pass, continue, break, "...", a bare constant return
    (return, return None, return False, return True, return 0), or only a logging
    call is offending. Also flag any use of contextlib.suppress(Exception/BaseException)
    and a "return" inside a "finally" block.
    """
    source_path = REPO_ROOT / "src" / "agy_mcp" / "cleanup_jobs.py"
    source = source_path.read_text(encoding="utf-8")
    flagged = find_offending_handlers(source)

    assert not flagged, (
        f"Found {len(flagged)} offending exception handler(s) in cleanup_jobs.py:\n"
        + "\n".join(f"  Line {line}: {code}" for line, code in flagged)
    )
