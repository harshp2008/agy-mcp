"""FROZEN acceptance tests for cleanup_jobs review findings (fix round 5).

Committed before the implementation. Do not edit src/ or fix anything in Phase 1.

Test coverage for round 5:
1. commondir validation in linked worktree gitdir:
   - test_commondir_extra_comment_line_fails_closed:
     commondir file has a valid first line followed by an extra comment line.
   - test_commondir_second_path_line_fails_closed:
     commondir file has a valid first line followed by a second path line.
   - test_commondir_empty_fails_closed:
     commondir file is empty.
   - test_commondir_target_nonexistent_fails_closed:
     commondir file points to a path that does not exist.
   - test_commondir_target_regular_file_fails_closed:
     commondir file points to a regular file instead of a directory.
   - test_commondir_is_directory_fails_closed:
     commondir is a directory rather than a regular file.
   - test_commondir_unreadable_chmod_000_fails_closed:
     commondir is unreadable (chmod 0o000, skipped when root).
2. Worktree entry gitdir validation (<common>/worktrees/<entry>/gitdir):
   - test_worktree_entry_gitdir_extra_comment_fails_closed:
     entry gitdir has an extra comment line after the worktree path.
   - test_worktree_entry_gitdir_empty_fails_closed:
     entry gitdir is empty.
3. Contrast cases (must pass on stock/fixed code):
   - test_commondir_stock_relative_registered_worktree_kept_contrast:
     stock relative commondir ("../..\\n") with registered worktree is kept.
   - test_commondir_stock_crlf_registered_worktree_kept_contrast:
     stock relative commondir with trailing CRLF is kept.
   - test_commondir_stock_no_registered_worktree_deletable_contrast:
     stock relative commondir with no registered worktree stays deletable.
   - test_commondir_absolute_path_registered_worktree_kept_contrast:
     absolute path commondir with registered worktree is kept.
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


@pytest.fixture
def store() -> Path:
    """Create a temporary store inside session HOME for testing."""
    root = Path(os.environ["HOME"]) / "cleanup-jobs-fixes5-tests" / uuid.uuid4().hex
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


def _setup_linked_repo_and_job(
    store: Path,
    tmp_path: Path,
    *,
    prefix: str,
    register_job_worktree: bool = True,
) -> tuple[Path, Path, Path, str, Path | None]:
    """Build real git topology:

    - main_repo: real git repository created via make_repo
    - linked_wt: linked worktree created with git worktree add (acts as repo_path)
    - commondir_file: linked_wt's gitdir commondir file
    - jid: job ID written to store with repo_path=linked_wt
    - job_worktree_entry: <main_repo>/.git/worktrees/<entry> for the job's worktree

    Returns (main_repo, linked_wt, commondir_file, jid, job_worktree_entry).
    """
    main_repo = make_repo(tmp_path / f"{prefix}_main")
    linked_wt = tmp_path / f"{prefix}_linked"
    git_env = get_safe_git_env(tmp_path)

    subprocess.run(
        [
            "git",
            "-C",
            str(main_repo),
            "worktree",
            "add",
            str(linked_wt),
            "-b",
            f"agy/linked-{prefix}",
            "HEAD",
        ],
        env=git_env,
        check=True,
        capture_output=True,
    )

    raw_git = (linked_wt / ".git").read_text(encoding="utf-8").strip()
    assert raw_git.startswith("gitdir:")
    raw_gitdir = raw_git[len("gitdir:") :].strip()
    linked_gitdir = Path(raw_gitdir).resolve()
    commondir_file = linked_gitdir / "commondir"
    assert commondir_file.is_file(), f"expected commondir file at {commondir_file}"

    jid = _write_job(store, repo=linked_wt, state="done", updated_at=_old_time())
    worktree_path = store / "jobs" / jid / "work"

    job_entry: Path | None = None
    if register_job_worktree:
        subprocess.run(
            [
                "git",
                "-C",
                str(main_repo),
                "worktree",
                "add",
                str(worktree_path),
                "-b",
                f"agy/wt-{jid[:8]}",
                "HEAD",
            ],
            env=git_env,
            check=True,
            capture_output=True,
        )
        (worktree_path / ".git").unlink()

        wt_root = main_repo / ".git" / "worktrees"
        for entry in wt_root.iterdir():
            gd = entry / "gitdir"
            if gd.is_file() and str(worktree_path) in gd.read_text(encoding="utf-8"):
                job_entry = entry
                break
        assert job_entry is not None, f"job worktree registration not found in {wt_root}"

    return main_repo, linked_wt, commondir_file, jid, job_entry


# --------------------------------------------------------------------------- #
# Item 1: Cases that must FAIL on current code (commondir validation)
# --------------------------------------------------------------------------- #


def test_commondir_extra_comment_line_fails_closed(store: Path, tmp_path: Path) -> None:
    """commondir with valid path followed by comment line fails closed: job kept."""
    _, linked_wt, commondir_file, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="comment"
    )

    commondir_file.write_text("../..\n# extra comment line\n", encoding="utf-8")

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ("commondir" in r.lower() or "registrations" in r.lower())
        and ("invalid" in r.lower() or "unreadable" in r.lower())
        for r in reasons
    ), f"reason must name commondir file as invalid or registrations unreadable: {reasons}"

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on invalid commondir"


def test_commondir_second_path_line_fails_closed(store: Path, tmp_path: Path) -> None:
    """commondir with valid path followed by second path line fails closed: job kept."""
    _, linked_wt, commondir_file, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="second_line"
    )

    other_dir = tmp_path / "other_gitdir"
    commondir_file.write_text(f"../..\n{other_dir}\n", encoding="utf-8")

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ("commondir" in r.lower() or "registrations" in r.lower())
        and ("invalid" in r.lower() or "unreadable" in r.lower())
        for r in reasons
    ), f"reason must name commondir file as invalid or registrations unreadable: {reasons}"

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on invalid commondir"


def test_commondir_empty_fails_closed(store: Path, tmp_path: Path) -> None:
    """Empty commondir file fails closed: job kept."""
    _, linked_wt, commondir_file, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="empty"
    )

    commondir_file.write_text("", encoding="utf-8")

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ("commondir" in r.lower() or "registrations" in r.lower())
        and ("invalid" in r.lower() or "unreadable" in r.lower() or "empty" in r.lower())
        for r in reasons
    ), f"reason must name commondir file as invalid or registrations unreadable: {reasons}"

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on empty commondir"


def test_commondir_target_nonexistent_fails_closed(store: Path, tmp_path: Path) -> None:
    """commondir pointing to missing target directory fails closed: job kept."""
    _, linked_wt, commondir_file, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="nonexistent"
    )

    nonexistent = tmp_path / "missing_common_gitdir"
    commondir_file.write_text(f"{nonexistent}\n", encoding="utf-8")

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ("commondir" in r.lower() or "registrations" in r.lower() or "target" in r.lower())
        and (
            "missing" in r.lower()
            or "invalid" in r.lower()
            or "not exist" in r.lower()
            or "unreadable" in r.lower()
        )
        for r in reasons
    ), f"reason must name commondir target as missing/invalid: {reasons}"

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on missing commondir target"


def test_commondir_target_regular_file_fails_closed(store: Path, tmp_path: Path) -> None:
    """commondir pointing to regular file instead of directory fails closed: job kept."""
    _, linked_wt, commondir_file, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="regfile"
    )

    reg_file = tmp_path / "commondir_target_file.txt"
    reg_file.write_text("regular file content\n", encoding="utf-8")
    commondir_file.write_text(f"{reg_file}\n", encoding="utf-8")

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ("commondir" in r.lower() or "registrations" in r.lower() or "target" in r.lower())
        and (
            "invalid" in r.lower()
            or "not a directory" in r.lower()
            or "regular file" in r.lower()
            or "unreadable" in r.lower()
        )
        for r in reasons
    ), f"reason must name commondir target as not a directory/invalid: {reasons}"

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on file commondir target"


def test_commondir_is_directory_fails_closed(store: Path, tmp_path: Path) -> None:
    """commondir being a directory instead of a file fails closed: job kept."""
    _, linked_wt, commondir_file, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="commondir_dir"
    )

    commondir_file.unlink()
    commondir_file.mkdir()

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ("commondir" in r.lower() or "registrations" in r.lower())
        and ("invalid" in r.lower() or "unreadable" in r.lower())
        for r in reasons
    ), f"reason must name commondir as invalid or registrations unreadable: {reasons}"

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted when commondir is directory"


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores filesystem read permissions",
)
def test_commondir_unreadable_chmod_000_fails_closed(store: Path, tmp_path: Path) -> None:
    """commondir file with chmod 0o000 fails closed: job kept."""
    _, linked_wt, commondir_file, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="chmod000"
    )

    os.chmod(commondir_file, 0o000)

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ("commondir" in r.lower() or "registrations" in r.lower())
        and ("unreadable" in r.lower() or "permission" in r.lower() or "invalid" in r.lower())
        for r in reasons
    ), f"reason must name commondir as unreadable: {reasons}"

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted when commondir is unreadable"


# --------------------------------------------------------------------------- #
# Item 2: Cases that must FAIL on current code (worktree entry gitdir)
# --------------------------------------------------------------------------- #


def test_worktree_entry_gitdir_extra_comment_fails_closed(
    store: Path, tmp_path: Path
) -> None:
    """Worktree entry gitdir with extra comment line fails closed: job kept."""
    _, linked_wt, _, jid, job_entry = _setup_linked_repo_and_job(
        store, tmp_path, prefix="entry_comment"
    )
    assert job_entry is not None
    entry_gitdir = job_entry / "gitdir"

    orig = entry_gitdir.read_text(encoding="utf-8").strip()
    entry_gitdir.write_text(f"{orig}\n# comment line\n", encoding="utf-8")

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ("gitdir" in r.lower() or "registrations" in r.lower() or job_entry.name in r.lower())
        and ("invalid" in r.lower() or "unreadable" in r.lower())
        for r in reasons
    ), f"reason must name entry gitdir as invalid or unreadable: {reasons}"
    assert not any(
        "worktree registered (run 'git worktree prune'" in r.lower()
        for r in reasons
    ), f"must not claim worktree is validly registered: {reasons}"

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on invalid entry gitdir"


def test_worktree_entry_gitdir_empty_fails_closed(store: Path, tmp_path: Path) -> None:
    """Worktree entry gitdir that is empty fails closed: job kept."""
    _, linked_wt, _, jid, job_entry = _setup_linked_repo_and_job(
        store, tmp_path, prefix="entry_empty"
    )
    assert job_entry is not None
    entry_gitdir = job_entry / "gitdir"

    entry_gitdir.write_text("", encoding="utf-8")

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any(
        ("gitdir" in r.lower() or "registrations" in r.lower() or job_entry.name in r.lower())
        and ("invalid" in r.lower() or "unreadable" in r.lower() or "empty" in r.lower())
        for r in reasons
    ), f"reason must name entry gitdir as invalid or unreadable: {reasons}"

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on empty entry gitdir"


# --------------------------------------------------------------------------- #
# Item 3: Contrast cases (must PASS on current code and keep passing)
# --------------------------------------------------------------------------- #


def test_commondir_stock_relative_registered_worktree_kept_contrast(
    store: Path, tmp_path: Path
) -> None:
    """Stock commondir (relative '../..\\n') with registered worktree is kept."""
    _, linked_wt, _, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="stock_rel"
    )

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any("worktree registered" in r.lower() for r in reasons), (
        f"reason must say 'worktree registered': {reasons}"
    )

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr
    assert "worktree prune" in proc.stdout or "worktree prune" in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted when worktree registered"


def test_commondir_stock_crlf_registered_worktree_kept_contrast(
    store: Path, tmp_path: Path
) -> None:
    """Stock commondir with trailing CRLF and registered worktree is kept."""
    _, linked_wt, commondir_file, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="stock_crlf"
    )

    commondir_file.write_bytes(b"../..\r\n")

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any("worktree registered" in r.lower() for r in reasons), (
        f"reason must say 'worktree registered': {reasons}"
    )

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted when worktree registered"


def test_commondir_stock_no_registered_worktree_deletable_contrast(
    store: Path, tmp_path: Path
) -> None:
    """Stock commondir with no registered worktree leaves job deletable."""
    _, linked_wt, _, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="stock_nowt", register_job_worktree=False
    )

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 1
    assert res["candidates"][0]["job_id"] == jid
    assert res["kept_count"] == 0

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert not (store / "jobs" / jid).exists(), "job without worktree must be deleted"


def test_commondir_absolute_path_registered_worktree_kept_contrast(
    store: Path, tmp_path: Path
) -> None:
    """Absolute-path commondir with registered worktree is kept."""
    main_repo, linked_wt, commondir_file, jid, _ = _setup_linked_repo_and_job(
        store, tmp_path, prefix="abs_path"
    )

    commondir_file.write_text(f"{main_repo / '.git'}\n", encoding="utf-8")

    res = _run_json("--store", store, "--project", linked_wt, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any("worktree registered" in r.lower() for r in reasons), (
        f"reason must say 'worktree registered': {reasons}"
    )

    proc = _run("--store", store, "--project", linked_wt, expect=0)
    assert "Traceback" not in proc.stderr

    _run("--store", store, "--project", linked_wt, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted when worktree registered"
