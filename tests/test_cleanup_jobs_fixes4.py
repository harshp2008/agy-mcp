"""FROZEN acceptance tests for cleanup_jobs review findings (fix round 4).

Committed before the implementation. Do not edit src/ or fix anything in this chat.

Test coverage for round 4:
1. Multi-line .git file must fail closed:
   - test_multiline_git_file_with_comment_fails_closed:
     repo_path/.git is a regular file with a valid first line 'gitdir: <path>'
     followed by a comment ('# comment'). The repo has a real registered worktree
     for the job (built with real git: git worktree add).
     Expected: the job is kept, reason says the .git file is invalid or registrations
     unreadable (mention of the .git file), nothing deleted with --yes, dry-run exit 0,
     no traceback.
   - test_multiline_git_file_with_second_gitdir_fails_closed:
     repo_path/.git is a regular file with a valid first line followed by a second
     'gitdir: <other>' line. The repo has a real registered worktree for the job.
     Expected: kept fail-closed, nothing deleted with --yes.
   - test_valid_single_line_git_file_crlf_registered_worktree_kept_contrast:
     Contrast test: a valid single-line pointer with CRLF ending and a registered
     worktree is kept with reason 'worktree registered'.
   - test_valid_single_line_git_file_no_worktree_stays_deletable_contrast:
     Contrast test: a valid single-line pointer with no registered worktree stays
     deletable (candidate on dry-run, removed with --yes).
2. Missing or regular-file .git target must fail closed:
   - test_git_file_target_nonexistent_fails_closed:
     A valid-looking 'gitdir: <path>' line whose target does not exist.
     Expected: the job is kept with a reason naming the .git file target as missing/invalid,
     nothing deleted with --yes, dry-run exit 0.
   - test_git_file_target_regular_file_fails_closed:
     A valid-looking 'gitdir: <path>' line whose target exists but is a regular file.
     Expected: the job is kept with a reason naming the .git file target as missing/invalid,
     nothing deleted with --yes, dry-run exit 0.
"""

from __future__ import annotations

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

from tests.helpers.make_repo import get_safe_git_env, make_repo

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


def _ids(data: dict[str, Any], key: str) -> list[str]:
    """Extract list of job_id strings from a candidates/kept list."""
    items = data.get(key, [])
    return [item["job_id"] for item in items if isinstance(item, dict) and "job_id" in item]


@pytest.fixture
def store() -> Path:
    """Create a temporary store inside session HOME for testing."""
    root = Path(os.environ["HOME"]) / "cleanup-jobs-fixes4-tests" / uuid.uuid4().hex
    (root / "jobs").mkdir(parents=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _old_time(days: float = 10.0) -> datetime:
    """A timestamp in the past, older than the default 7-day threshold."""
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
        "title": "Cleanup Test Job",
        "repo_path": repo_val,
        "branch": f"agy/cleanup-{jid[:8]}",
        "state": state,
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
    }
    if pid is not None:
        data["pid"] = pid
    if extra:
        data.update(extra)
    (job_dir / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return jid


# --------------------------------------------------------------------------- #
# Item 1: Multi-line .git file must fail closed
# --------------------------------------------------------------------------- #


def test_multiline_git_file_with_comment_fails_closed(store: Path, tmp_path: Path) -> None:
    """repo_path/.git with valid gitdir followed by comment fails closed: job kept."""
    real_repo = make_repo(tmp_path / "real_repo_1")
    target_repo = tmp_path / "target_repo_1"
    target_repo.mkdir(parents=True, exist_ok=True)

    jid = _write_job(store, repo=target_repo, state="done", updated_at=_old_time())
    worktree_path = store / "jobs" / jid / "work"

    subprocess.run(
        [
            "git",
            "-C",
            str(real_repo),
            "worktree",
            "add",
            str(worktree_path),
            "-b",
            f"agy/wt-{jid[:8]}",
            "HEAD",
        ],
        env=get_safe_git_env(tmp_path),
        check=True,
        capture_output=True,
    )
    (worktree_path / ".git").unlink()

    # Multi-line .git file: valid first line, followed by comment
    (target_repo / ".git").write_text(
        f"gitdir: {real_repo / '.git'}\n# comment\n", encoding="utf-8"
    )

    # Dry-run: job must be kept, 0 candidates
    res = _run_json("--store", store, "--project", target_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ".git" in r.lower() and ("invalid" in r.lower() or "unreadable" in r.lower())
        for r in reasons
    ), f"reason must mention .git file is invalid or unreadable: {reasons}"

    # Dry-run human output: exit 0, no traceback
    proc = _run("--store", store, "--project", target_repo, expect=0)
    assert "Traceback" not in proc.stderr

    # With --yes: nothing deleted
    _run("--store", store, "--project", target_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on fail-closed .git file"


def test_multiline_git_file_with_second_gitdir_fails_closed(store: Path, tmp_path: Path) -> None:
    """repo_path/.git with valid gitdir followed by second gitdir fails closed: job kept."""
    real_repo = make_repo(tmp_path / "real_repo_2")
    target_repo = tmp_path / "target_repo_2"
    target_repo.mkdir(parents=True, exist_ok=True)

    jid = _write_job(store, repo=target_repo, state="done", updated_at=_old_time())
    worktree_path = store / "jobs" / jid / "work"

    subprocess.run(
        [
            "git",
            "-C",
            str(real_repo),
            "worktree",
            "add",
            str(worktree_path),
            "-b",
            f"agy/wt-{jid[:8]}",
            "HEAD",
        ],
        env=get_safe_git_env(tmp_path),
        check=True,
        capture_output=True,
    )
    (worktree_path / ".git").unlink()

    # Multi-line .git file: valid first line, followed by second gitdir line
    other_dir = tmp_path / "other_gitdir"
    (target_repo / ".git").write_text(
        f"gitdir: {real_repo / '.git'}\ngitdir: {other_dir}\n", encoding="utf-8"
    )

    # Dry-run: job must be kept, 0 candidates
    res = _run_json("--store", store, "--project", target_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ".git" in r.lower() and ("invalid" in r.lower() or "unreadable" in r.lower())
        for r in reasons
    ), f"reason must mention .git file is invalid or unreadable: {reasons}"

    # Dry-run human output: exit 0, no traceback
    proc = _run("--store", store, "--project", target_repo, expect=0)
    assert "Traceback" not in proc.stderr

    # With --yes: nothing deleted
    _run("--store", store, "--project", target_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on fail-closed .git file"


def test_valid_single_line_git_file_crlf_registered_worktree_kept(
    store: Path, tmp_path: Path
) -> None:
    """Valid single-line pointer with CRLF and registered worktree is kept."""
    real_repo = make_repo(tmp_path / "real_repo_crlf")
    target_repo = tmp_path / "target_repo_crlf"
    target_repo.mkdir(parents=True, exist_ok=True)

    jid = _write_job(store, repo=target_repo, state="done", updated_at=_old_time())
    worktree_path = store / "jobs" / jid / "work"

    subprocess.run(
        [
            "git",
            "-C",
            str(real_repo),
            "worktree",
            "add",
            str(worktree_path),
            "-b",
            f"agy/wt-{jid[:8]}",
            "HEAD",
        ],
        env=get_safe_git_env(tmp_path),
        check=True,
        capture_output=True,
    )
    (worktree_path / ".git").unlink()

    # Valid single-line pointer with CRLF ending
    (target_repo / ".git").write_bytes(f"gitdir: {real_repo / '.git'}\r\n".encode("utf-8"))

    res = _run_json("--store", store, "--project", target_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any("worktree registered" in r.lower() for r in reasons), (
        f"reason must say 'worktree registered': {reasons}"
    )

    proc = _run("--store", store, "--project", target_repo, expect=0)
    assert "Traceback" not in proc.stderr
    assert "worktree prune" in proc.stdout or "worktree prune" in proc.stderr

    _run("--store", store, "--project", target_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "registered worktree job must NOT be deleted"


def test_valid_single_line_git_file_no_worktree_deletable(store: Path, tmp_path: Path) -> None:
    """Valid single-line pointer with no registered worktree stays deletable."""
    real_repo = make_repo(tmp_path / "real_repo_nowt")
    target_repo = tmp_path / "target_repo_nowt"
    target_repo.mkdir(parents=True, exist_ok=True)

    (target_repo / ".git").write_bytes(f"gitdir: {real_repo / '.git'}\r\n".encode("utf-8"))
    jid = _write_job(store, repo=target_repo, state="done", updated_at=_old_time())

    # Dry-run: candidate
    res = _run_json("--store", store, "--project", target_repo, expect=0)
    assert res["candidate_count"] == 1
    assert res["candidates"][0]["job_id"] == jid
    assert res["kept_count"] == 0

    proc = _run("--store", store, "--project", target_repo, expect=0)
    assert "Traceback" not in proc.stderr

    # With --yes: successfully deleted
    _run("--store", store, "--project", target_repo, "--yes", expect=0)
    assert not (store / "jobs" / jid).exists(), "job with no registered worktree should be deleted"


# --------------------------------------------------------------------------- #
# Item 2: Missing or regular-file .git target must fail closed
# --------------------------------------------------------------------------- #


def test_git_file_target_nonexistent_fails_closed(store: Path, tmp_path: Path) -> None:
    """gitdir pointing to nonexistent target fails closed: job kept, reason names target."""
    target_repo = tmp_path / "target_repo_no_target"
    target_repo.mkdir(parents=True, exist_ok=True)

    nonexistent = tmp_path / "nonexistent_target_gitdir"
    (target_repo / ".git").write_text(f"gitdir: {nonexistent}\n", encoding="utf-8")

    jid = _write_job(store, repo=target_repo, state="done", updated_at=_old_time())

    # Dry-run: kept fail-closed, 0 candidates
    res = _run_json("--store", store, "--project", target_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        (".git" in r.lower() or "gitdir" in r.lower() or "target" in r.lower())
        and ("missing" in r.lower() or "invalid" in r.lower() or "not exist" in r.lower())
        for r in reasons
    ), f"reason must name .git file target as missing/invalid: {reasons}"

    # Dry-run human output: exit 0, no traceback
    proc = _run("--store", store, "--project", target_repo, expect=0)
    assert "Traceback" not in proc.stderr

    # With --yes: nothing deleted
    _run("--store", store, "--project", target_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted when target does not exist"


def test_git_file_target_regular_file_fails_closed(store: Path, tmp_path: Path) -> None:
    """gitdir pointing to a regular file fails closed: job kept, reason names target."""
    target_repo = tmp_path / "target_repo_file_target"
    target_repo.mkdir(parents=True, exist_ok=True)

    reg_file = tmp_path / "target_file.txt"
    reg_file.write_text("regular file, not directory\n", encoding="utf-8")
    (target_repo / ".git").write_text(f"gitdir: {reg_file}\n", encoding="utf-8")

    jid = _write_job(store, repo=target_repo, state="done", updated_at=_old_time())

    # Dry-run: kept fail-closed, 0 candidates
    res = _run_json("--store", store, "--project", target_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        (".git" in r.lower() or "gitdir" in r.lower() or "target" in r.lower())
        and (
            "missing" in r.lower()
            or "invalid" in r.lower()
            or "not a directory" in r.lower()
            or "regular file" in r.lower()
        )
        for r in reasons
    ), f"reason must name .git file target as missing/invalid: {reasons}"

    # Dry-run human output: exit 0, no traceback
    proc = _run("--store", store, "--project", target_repo, expect=0)
    assert "Traceback" not in proc.stderr

    # With --yes: nothing deleted
    _run("--store", store, "--project", target_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted when target is a regular file"
