"""FROZEN acceptance tests for cleanup_jobs review findings (fix round 7 bare-repo cases).

Committed before the implementation. Do not edit src/ or fix anything in Phase 1.

Test coverage for round 7:
1. Bare repo where HEAD is mutated (F1: FAIL-OPEN in stock code):
   - test_bare_repo_head_is_directory_fails_closed: HEAD replaced with directory.
   - test_bare_repo_head_is_dangling_symlink_fails_closed: HEAD is dangling symlink.
   - test_bare_repo_head_deleted_fails_closed: HEAD deleted entirely.
   - test_bare_repo_head_is_fifo_fails_closed: HEAD replaced with FIFO pipe.
2. Bare-repo linked worktree where backing repo HEAD is mutated:
   - test_bare_worktree_backing_head_is_directory_fails_closed.
   - test_bare_worktree_backing_head_is_dangling_symlink_fails_closed.
   - test_bare_worktree_backing_head_deleted_fails_closed.
   - test_bare_worktree_backing_head_is_fifo_fails_closed.
3. Bare repo with a .git DIRECTORY inside (F2: FAIL-OPEN in stock code):
   - test_bare_repo_with_valid_dotgit_dir_still_blocks_registered_worktree.
   - test_bare_repo_with_invalid_dotgit_dir_without_head_fails_closed.
   - test_bare_repo_with_invalid_dotgit_dir_head_is_directory_fails_closed.
4. repo_path with only some git markers and NO HEAD (R6b):
   - test_repo_marker_worktrees_only_no_head_fails_closed.
   - test_repo_marker_objects_only_no_head_fails_closed.
   - test_repo_marker_config_only_no_head_fails_closed.
   - test_repo_marker_refs_only_no_head_fails_closed.
   - test_repo_marker_packed_refs_only_no_head_fails_closed.
   - test_repo_marker_commondir_only_no_head_fails_closed.
5. HEAD mutations on commondir target, pointer target, and submodule modules dir (R6c):
   - test_pointer_target_head_is_directory_fails_closed.
   - test_pointer_target_head_is_dangling_symlink_fails_closed.
   - test_pointer_target_head_deleted_fails_closed.
   - test_pointer_target_head_is_fifo_fails_closed.
   - test_commondir_target_head_is_directory_fails_closed.
   - test_commondir_target_head_is_dangling_symlink_fails_closed.
   - test_commondir_target_head_deleted_fails_closed.
   - test_commondir_target_head_is_fifo_fails_closed.
   - test_submodule_modules_dir_head_is_directory_fails_closed.
   - test_submodule_modules_dir_head_deleted_fails_closed.
6. Controls (must pass and stay passing):
   - test_control_plain_non_git_repo_path_stays_deletable.
   - test_control_healthy_bare_repo_blocks_registered_worktree.
   - test_control_healthy_bare_repo_without_registered_worktree_deletable.
   - test_control_healthy_bare_repo_with_dotgit_dir_blocks.
7. Loose ends:
   - test_loose_end_1_worktree_path_escape_does_not_delete_outside_store.
   - test_loose_end_2_job_id_uppercase_hex_kept.
   - test_loose_end_2_folder_name_uppercase_hex_kept.
   - test_loose_end_2_cli_job_uppercase_hex_rejected.
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
    root = Path(os.environ["HOME"]) / "cleanup-jobs-fixes6-tests" / uuid.uuid4().hex
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


def _setup_bare_repo_and_job(
    store: Path,
    tmp_path: Path,
    *,
    prefix: str,
    register_job_worktree: bool = True,
) -> tuple[Path, str, Path | None]:
    """Build a bare git repo topology with an optional registered worktree:

    - bare_repo: bare git repository with initial commit on main
    - jid: job ID written to store with repo_path=bare_repo
    - job_worktree_entry: <bare_repo>/worktrees/<entry> for the job's worktree

    Returns (bare_repo, jid, job_worktree_entry).
    """
    git_env = get_safe_git_env(tmp_path)
    bare_repo = tmp_path / f"{prefix}_bare.git"
    subprocess.run(
        ["git", "init", "--bare", str(bare_repo)],
        check=True,
        capture_output=True,
        env=git_env,
    )
    tree = subprocess.run(
        ["git", "write-tree"],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", "init"],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/heads/main", commit],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    jid = _write_job(store, repo=bare_repo, state="done", updated_at=_old_time())
    worktree_path = store / "jobs" / jid / "work"

    job_entry: Path | None = None
    if register_job_worktree:
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                str(worktree_path),
                "-b",
                f"agy/wt-{jid[:8]}",
                "main",
            ],
            cwd=str(bare_repo),
            env=git_env,
            check=True,
            capture_output=True,
        )
        (worktree_path / ".git").unlink()

        wt_root = bare_repo / "worktrees"
        for entry in wt_root.iterdir():
            gd = entry / "gitdir"
            if gd.is_file() and str(worktree_path) in gd.read_text(encoding="utf-8"):
                job_entry = entry
                break
        assert job_entry is not None, f"job worktree registration not found in {wt_root}"

    return bare_repo, jid, job_entry


def _setup_linked_worktree_backed_by_bare_repo(
    store: Path,
    tmp_path: Path,
    *,
    prefix: str,
) -> tuple[Path, Path, str]:
    """Build a bare repo with a linked worktree (used as repo_path) and a job worktree."""
    git_env = get_safe_git_env(tmp_path)
    bare_repo = tmp_path / f"{prefix}_backing_bare.git"
    subprocess.run(
        ["git", "init", "--bare", str(bare_repo)],
        check=True,
        capture_output=True,
        env=git_env,
    )
    tree = subprocess.run(
        ["git", "write-tree"],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", "init"],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/heads/main", commit],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    linked_repo = tmp_path / f"{prefix}_linked_wt"
    subprocess.run(
        ["git", "worktree", "add", str(linked_repo), "-b", f"agy/linked-{prefix}", "main"],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())
    worktree_path = store / "jobs" / jid / "work"
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "-b", f"agy/job-{jid[:8]}", "main"],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    (worktree_path / ".git").unlink()

    return bare_repo, linked_repo, jid


# --------------------------------------------------------------------------- #
# Item 1: Bare repo where HEAD is mutated (F1: FAIL-OPEN on stock code)
# --------------------------------------------------------------------------- #


def test_bare_repo_head_is_directory_fails_closed(store: Path, tmp_path: Path) -> None:
    """Bare repo HEAD replaced with a directory must fail closed (kept), never candidate."""
    bare_repo, jid, _ = _setup_bare_repo_and_job(store, tmp_path, prefix="bare_head_dir")

    head = bare_repo / "HEAD"
    head.unlink()
    head.mkdir()

    res = _run_json("--store", store, "--project", bare_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any("worktree" in r.lower() or "git" in r.lower() for r in reasons)

    _run("--store", store, "--project", bare_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted when repo HEAD is a directory"


def test_bare_repo_head_is_dangling_symlink_fails_closed(store: Path, tmp_path: Path) -> None:
    """Bare repo HEAD replaced with a dangling symlink must fail closed (kept)."""
    bare_repo, jid, _ = _setup_bare_repo_and_job(store, tmp_path, prefix="bare_head_symlink")

    head = bare_repo / "HEAD"
    head.unlink()
    head.symlink_to("nonexistent_target")

    res = _run_json("--store", store, "--project", bare_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", bare_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on dangling symlink HEAD"


def test_bare_repo_head_deleted_fails_closed(store: Path, tmp_path: Path) -> None:
    """Bare repo HEAD deleted entirely must fail closed (kept)."""
    bare_repo, jid, _ = _setup_bare_repo_and_job(store, tmp_path, prefix="bare_head_deleted")

    (bare_repo / "HEAD").unlink()

    res = _run_json("--store", store, "--project", bare_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", bare_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on deleted HEAD"


def test_bare_repo_head_is_fifo_fails_closed(store: Path, tmp_path: Path) -> None:
    """Bare repo HEAD replaced with a FIFO pipe must fail closed (kept)."""
    bare_repo, jid, _ = _setup_bare_repo_and_job(store, tmp_path, prefix="bare_head_fifo")

    head = bare_repo / "HEAD"
    head.unlink()
    try:
        os.mkfifo(head)
    except (AttributeError, OSError):
        pytest.skip("FIFO not supported on this platform/filesystem")

    res = _run_json("--store", store, "--project", bare_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", bare_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted on FIFO HEAD"


# --------------------------------------------------------------------------- #
# Item 2: Bare-repo linked worktree where backing repo HEAD is mutated
# --------------------------------------------------------------------------- #


def test_bare_worktree_backing_head_is_directory_fails_closed(
    store: Path, tmp_path: Path
) -> None:
    """Linked worktree backed by bare repo whose HEAD is a directory fails closed."""
    bare_repo, linked_repo, jid = _setup_linked_worktree_backed_by_bare_repo(
        store, tmp_path, prefix="bw_head_dir"
    )

    head = bare_repo / "HEAD"
    head.unlink()
    head.mkdir()

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_bare_worktree_backing_head_is_dangling_symlink_fails_closed(
    store: Path, tmp_path: Path
) -> None:
    """Linked worktree backed by bare repo whose HEAD is dangling symlink fails closed."""
    bare_repo, linked_repo, jid = _setup_linked_worktree_backed_by_bare_repo(
        store, tmp_path, prefix="bw_head_symlink"
    )

    head = bare_repo / "HEAD"
    head.unlink()
    head.symlink_to("nonexistent_ref")

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_bare_worktree_backing_head_deleted_fails_closed(store: Path, tmp_path: Path) -> None:
    """Linked worktree backed by bare repo whose HEAD is deleted fails closed."""
    bare_repo, linked_repo, jid = _setup_linked_worktree_backed_by_bare_repo(
        store, tmp_path, prefix="bw_head_del"
    )

    (bare_repo / "HEAD").unlink()

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_bare_worktree_backing_head_is_fifo_fails_closed(store: Path, tmp_path: Path) -> None:
    """Linked worktree backed by bare repo whose HEAD is a FIFO fails closed."""
    bare_repo, linked_repo, jid = _setup_linked_worktree_backed_by_bare_repo(
        store, tmp_path, prefix="bw_head_fifo"
    )

    head = bare_repo / "HEAD"
    head.unlink()
    try:
        os.mkfifo(head)
    except (AttributeError, OSError):
        pytest.skip("FIFO not supported on this platform/filesystem")

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


# --------------------------------------------------------------------------- #
# Item 3: Bare repo with a .git DIRECTORY inside (F2: FAIL-OPEN on stock code)
# --------------------------------------------------------------------------- #


def test_bare_repo_with_valid_dotgit_dir_still_blocks_registered_worktree(
    store: Path, tmp_path: Path
) -> None:
    """Bare repo with .git directory inside must still check bare worktrees (no shadowing)."""
    bare_repo, jid, _ = _setup_bare_repo_and_job(store, tmp_path, prefix="bare_dotgit_valid")

    dotgit = bare_repo / ".git"
    dotgit.mkdir()
    (dotgit / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    res = _run_json("--store", store, "--project", bare_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any("worktree registered" in r.lower() for r in reasons)

    _run("--store", store, "--project", bare_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job must not be deleted when worktree registered in bare repo"


def test_bare_repo_with_invalid_dotgit_dir_without_head_fails_closed(
    store: Path, tmp_path: Path
) -> None:
    """Bare repo with .git directory lacking HEAD must fail closed (kept)."""
    bare_repo, jid, _ = _setup_bare_repo_and_job(store, tmp_path, prefix="bare_dotgit_nohead")

    dotgit = bare_repo / ".git"
    dotgit.mkdir()

    res = _run_json("--store", store, "--project", bare_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", bare_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_bare_repo_with_invalid_dotgit_dir_head_is_directory_fails_closed(
    store: Path, tmp_path: Path
) -> None:
    """Bare repo with .git directory whose HEAD is a directory must fail closed."""
    bare_repo, jid, _ = _setup_bare_repo_and_job(store, tmp_path, prefix="bare_dotgit_headdir")

    dotgit = bare_repo / ".git"
    dotgit.mkdir()
    (dotgit / "HEAD").mkdir()

    res = _run_json("--store", store, "--project", bare_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", bare_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


# --------------------------------------------------------------------------- #
# Item 4: repo_path with only some git markers and NO HEAD (R6b)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "marker_name,is_dir",
    [
        ("worktrees", True),
        ("objects", True),
        ("refs", True),
        ("config", False),
        ("packed-refs", False),
        ("commondir", False),
    ],
)
def test_repo_marker_only_no_head_fails_closed(
    store: Path, tmp_path: Path, marker_name: str, is_dir: bool
) -> None:
    """repo_path with git marker but no HEAD looks like a corrupted git repo and must fail closed."""
    fake_repo = tmp_path / f"repo_marker_{marker_name}"
    fake_repo.mkdir()
    target = fake_repo / marker_name
    if is_dir:
        target.mkdir()
    else:
        target.write_text("marker content\n", encoding="utf-8")

    jid = _write_job(store, repo=fake_repo, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", fake_repo, expect=0)
    assert res["candidate_count"] == 0, f"must not be candidate when marker {marker_name} is present without HEAD"
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", fake_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), f"job must not be deleted when marker {marker_name} is present"


# --------------------------------------------------------------------------- #
# Item 5: HEAD mutations on commondir target, pointer target, and submodule
# --------------------------------------------------------------------------- #


def test_pointer_target_head_is_directory_fails_closed(store: Path, tmp_path: Path) -> None:
    """.git file pointing to a gitdir whose HEAD is a directory fails closed."""
    main_repo = make_repo(tmp_path / "ptr_head_dir_main")
    linked_repo = tmp_path / "ptr_head_dir_linked"
    git_env = get_safe_git_env(tmp_path)
    subprocess.run(
        ["git", "-C", str(main_repo), "worktree", "add", str(linked_repo), "-b", "b1", "HEAD"],
        check=True,
        capture_output=True,
        env=git_env,
    )
    raw_git = (linked_repo / ".git").read_text(encoding="utf-8").strip()
    target_gitdir = Path(raw_git[len("gitdir:") :].strip()).resolve()

    head = target_gitdir / "HEAD"
    head.unlink()
    head.mkdir()

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_pointer_target_head_is_dangling_symlink_fails_closed(
    store: Path, tmp_path: Path
) -> None:
    """.git file pointing to a gitdir whose HEAD is a dangling symlink fails closed."""
    main_repo = make_repo(tmp_path / "ptr_head_sym_main")
    linked_repo = tmp_path / "ptr_head_sym_linked"
    git_env = get_safe_git_env(tmp_path)
    subprocess.run(
        ["git", "-C", str(main_repo), "worktree", "add", str(linked_repo), "-b", "b1", "HEAD"],
        check=True,
        capture_output=True,
        env=git_env,
    )
    raw_git = (linked_repo / ".git").read_text(encoding="utf-8").strip()
    target_gitdir = Path(raw_git[len("gitdir:") :].strip()).resolve()

    head = target_gitdir / "HEAD"
    head.unlink()
    head.symlink_to("missing_ref")

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_pointer_target_head_deleted_fails_closed(store: Path, tmp_path: Path) -> None:
    """.git file pointing to a gitdir whose HEAD is deleted fails closed."""
    main_repo = make_repo(tmp_path / "ptr_head_del_main")
    linked_repo = tmp_path / "ptr_head_del_linked"
    git_env = get_safe_git_env(tmp_path)
    subprocess.run(
        ["git", "-C", str(main_repo), "worktree", "add", str(linked_repo), "-b", "b1", "HEAD"],
        check=True,
        capture_output=True,
        env=git_env,
    )
    raw_git = (linked_repo / ".git").read_text(encoding="utf-8").strip()
    target_gitdir = Path(raw_git[len("gitdir:") :].strip()).resolve()

    (target_gitdir / "HEAD").unlink()

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_pointer_target_head_is_fifo_fails_closed(store: Path, tmp_path: Path) -> None:
    """.git file pointing to a gitdir whose HEAD is a FIFO fails closed."""
    main_repo = make_repo(tmp_path / "ptr_head_fifo_main")
    linked_repo = tmp_path / "ptr_head_fifo_linked"
    git_env = get_safe_git_env(tmp_path)
    subprocess.run(
        ["git", "-C", str(main_repo), "worktree", "add", str(linked_repo), "-b", "b1", "HEAD"],
        check=True,
        capture_output=True,
        env=git_env,
    )
    raw_git = (linked_repo / ".git").read_text(encoding="utf-8").strip()
    target_gitdir = Path(raw_git[len("gitdir:") :].strip()).resolve()

    head = target_gitdir / "HEAD"
    head.unlink()
    try:
        os.mkfifo(head)
    except (AttributeError, OSError):
        pytest.skip("FIFO not supported on this platform/filesystem")

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_commondir_target_head_is_directory_fails_closed(store: Path, tmp_path: Path) -> None:
    """commondir target whose HEAD is a directory fails closed."""
    main_repo = make_repo(tmp_path / "cmn_head_dir_main")
    linked_repo = tmp_path / "cmn_head_dir_linked"
    git_env = get_safe_git_env(tmp_path)
    subprocess.run(
        ["git", "-C", str(main_repo), "worktree", "add", str(linked_repo), "-b", "b1", "HEAD"],
        check=True,
        capture_output=True,
        env=git_env,
    )
    head = main_repo / ".git" / "HEAD"
    head.unlink()
    head.mkdir()

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_commondir_target_head_is_dangling_symlink_fails_closed(
    store: Path, tmp_path: Path
) -> None:
    """commondir target whose HEAD is dangling symlink fails closed."""
    main_repo = make_repo(tmp_path / "cmn_head_sym_main")
    linked_repo = tmp_path / "cmn_head_sym_linked"
    git_env = get_safe_git_env(tmp_path)
    subprocess.run(
        ["git", "-C", str(main_repo), "worktree", "add", str(linked_repo), "-b", "b1", "HEAD"],
        check=True,
        capture_output=True,
        env=git_env,
    )
    head = main_repo / ".git" / "HEAD"
    head.unlink()
    head.symlink_to("missing")

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_commondir_target_head_deleted_fails_closed(store: Path, tmp_path: Path) -> None:
    """commondir target whose HEAD is deleted fails closed."""
    main_repo = make_repo(tmp_path / "cmn_head_del_main")
    linked_repo = tmp_path / "cmn_head_del_linked"
    git_env = get_safe_git_env(tmp_path)
    subprocess.run(
        ["git", "-C", str(main_repo), "worktree", "add", str(linked_repo), "-b", "b1", "HEAD"],
        check=True,
        capture_output=True,
        env=git_env,
    )
    (main_repo / ".git" / "HEAD").unlink()

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_commondir_target_head_is_fifo_fails_closed(store: Path, tmp_path: Path) -> None:
    """commondir target whose HEAD is a FIFO fails closed."""
    main_repo = make_repo(tmp_path / "cmn_head_fifo_main")
    linked_repo = tmp_path / "cmn_head_fifo_linked"
    git_env = get_safe_git_env(tmp_path)
    subprocess.run(
        ["git", "-C", str(main_repo), "worktree", "add", str(linked_repo), "-b", "b1", "HEAD"],
        check=True,
        capture_output=True,
        env=git_env,
    )
    head = main_repo / ".git" / "HEAD"
    head.unlink()
    try:
        os.mkfifo(head)
    except (AttributeError, OSError):
        pytest.skip("FIFO not supported on this platform/filesystem")

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_submodule_modules_dir_head_is_directory_fails_closed(
    store: Path, tmp_path: Path
) -> None:
    """Submodule whose modules directory HEAD is a directory fails closed."""
    git_env = get_safe_git_env(tmp_path)
    sub_origin = make_repo(tmp_path / "sub_origin")
    super_repo = make_repo(tmp_path / "super_repo")
    subprocess.run(
        [
            "git",
            "-C",
            str(super_repo),
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(sub_origin),
            "sub",
        ],
        check=True,
        capture_output=True,
        env=git_env,
    )
    subprocess.run(
        ["git", "-C", str(super_repo), "commit", "-m", "add submodule"],
        check=True,
        capture_output=True,
        env=git_env,
    )

    sub_checkout = super_repo / "sub"
    raw_sub_git = (sub_checkout / ".git").read_text(encoding="utf-8").strip()
    sub_modules_dir = Path(
        super_repo / "sub" / raw_sub_git[len("gitdir:") :].strip()
    ).resolve()
    assert sub_modules_dir.is_dir()

    head = sub_modules_dir / "HEAD"
    head.unlink()
    head.mkdir()

    jid = _write_job(store, repo=sub_checkout, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", sub_checkout, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", sub_checkout, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_submodule_modules_dir_head_deleted_fails_closed(
    store: Path, tmp_path: Path
) -> None:
    """Submodule whose modules directory HEAD is deleted fails closed."""
    git_env = get_safe_git_env(tmp_path)
    sub_origin = make_repo(tmp_path / "sub_origin_del")
    super_repo = make_repo(tmp_path / "super_repo_del")
    subprocess.run(
        [
            "git",
            "-C",
            str(super_repo),
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(sub_origin),
            "sub",
        ],
        check=True,
        capture_output=True,
        env=git_env,
    )
    subprocess.run(
        ["git", "-C", str(super_repo), "commit", "-m", "add submodule"],
        check=True,
        capture_output=True,
        env=git_env,
    )

    sub_checkout = super_repo / "sub"
    raw_sub_git = (sub_checkout / ".git").read_text(encoding="utf-8").strip()
    sub_modules_dir = Path(
        super_repo / "sub" / raw_sub_git[len("gitdir:") :].strip()
    ).resolve()
    assert sub_modules_dir.is_dir()

    (sub_modules_dir / "HEAD").unlink()

    jid = _write_job(store, repo=sub_checkout, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", sub_checkout, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", sub_checkout, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


# --------------------------------------------------------------------------- #
# Item 6: Controls (healthy repos and non-git dirs)
# --------------------------------------------------------------------------- #


def test_control_plain_non_git_repo_path_stays_deletable(
    store: Path, tmp_path: Path
) -> None:
    """Plain non-git repo_path directory (no git markers) stays deletable."""
    plain_dir = tmp_path / "plain_dir"
    plain_dir.mkdir()
    (plain_dir / "some_code.py").write_text("print('hello')\n", encoding="utf-8")

    jid = _write_job(store, repo=plain_dir, state="done", updated_at=_old_time())

    res = _run_json("--store", store, "--project", plain_dir, expect=0)
    assert res["candidate_count"] == 1
    assert res["candidates"][0]["job_id"] == jid
    assert res["kept_count"] == 0

    _run("--store", store, "--project", plain_dir, "--yes", expect=0)
    assert not (store / "jobs" / jid).exists()


def test_control_healthy_bare_repo_blocks_registered_worktree(
    store: Path, tmp_path: Path
) -> None:
    """Healthy bare repo correctly blocks a registered worktree."""
    bare_repo, jid, _ = _setup_bare_repo_and_job(
        store, tmp_path, prefix="ctrl_bare_reg", register_job_worktree=True
    )

    res = _run_json("--store", store, "--project", bare_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid
    reasons = res["kept"][0]["reasons"]
    assert any("worktree registered" in r.lower() for r in reasons)

    _run("--store", store, "--project", bare_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


def test_control_healthy_bare_repo_without_registered_worktree_deletable(
    store: Path, tmp_path: Path
) -> None:
    """Healthy bare repo without a registered worktree leaves job deletable."""
    bare_repo, jid, _ = _setup_bare_repo_and_job(
        store, tmp_path, prefix="ctrl_bare_noreg", register_job_worktree=False
    )

    res = _run_json("--store", store, "--project", bare_repo, expect=0)
    assert res["candidate_count"] == 1
    assert res["candidates"][0]["job_id"] == jid
    assert res["kept_count"] == 0

    _run("--store", store, "--project", bare_repo, "--yes", expect=0)
    assert not (store / "jobs" / jid).exists()


def test_control_healthy_bare_repo_with_dotgit_dir_blocks(
    store: Path, tmp_path: Path
) -> None:
    """Healthy bare repo with a .git directory inside still blocks registered worktree."""
    bare_repo, jid, _ = _setup_bare_repo_and_job(
        store, tmp_path, prefix="ctrl_bare_dotgit", register_job_worktree=True
    )
    dotgit = bare_repo / ".git"
    dotgit.mkdir()
    (dotgit / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    res = _run_json("--store", store, "--project", bare_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid

    _run("--store", store, "--project", bare_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir()


# --------------------------------------------------------------------------- #
# Item 7: Loose ends tests
# --------------------------------------------------------------------------- #


def test_loose_end_1_worktree_path_escape_does_not_delete_outside_store(
    store: Path, tmp_path: Path
) -> None:
    """Cleaner never deletes anything outside store_jobs, even if worktree_path points outside."""
    plain_repo = tmp_path / "plain_repo"
    plain_repo.mkdir()

    outside_scratch = tmp_path / "precious_workspace"
    outside_scratch.mkdir()
    precious_file = outside_scratch / "precious.txt"
    precious_file.write_text("PRECIOUS USER DATA\n", encoding="utf-8")

    jid = _write_job(
        store,
        repo=plain_repo,
        state="done",
        updated_at=_old_time(),
        extra={"worktree_path": str(outside_scratch)},
    )

    res = _run_json("--store", store, "--project", plain_repo, expect=0)
    assert res["candidate_count"] == 1
    assert res["candidates"][0]["job_id"] == jid

    _run("--store", store, "--project", plain_repo, "--yes", expect=0)
    assert not (store / "jobs" / jid).exists(), "job in store must be deleted"
    assert precious_file.exists(), "file outside store must NEVER be deleted"
    assert precious_file.read_text(encoding="utf-8") == "PRECIOUS USER DATA\n"
    assert outside_scratch.is_dir(), "directory outside store must NEVER be deleted"


def test_loose_end_2_job_id_uppercase_hex_kept(store: Path, tmp_path: Path) -> None:
    """Uppercase UUID in job.json mismatching lowercase folder name is kept."""
    plain_repo = tmp_path / "plain_repo_uuid"
    plain_repo.mkdir()

    jid_lower = "abcdef01-2345-6789-abcd-ef0123456789"
    jid_upper = jid_lower.upper()

    job_dir = store / "jobs" / jid_lower
    job_dir.mkdir(parents=True)
    job_data = {
        "schema_version": 1,
        "job_id": jid_upper,
        "title": "UUID case mismatch job",
        "repo_path": str(plain_repo),
        "state": "done",
        "created_at": _old_time().isoformat(),
        "updated_at": _old_time().isoformat(),
    }
    (job_dir / "job.json").write_text(json.dumps(job_data), encoding="utf-8")

    res = _run_json("--store", store, "--project", plain_repo, expect=0)
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid_lower
    reasons = res["kept"][0]["reasons"]
    assert any("does not match directory name" in r for r in reasons)


def test_loose_end_2_folder_name_uppercase_hex_kept(store: Path, tmp_path: Path) -> None:
    """Uppercase folder name is kept / not deletable because it is not a canonical lowercase UUID."""
    from agy_mcp.cleanup_jobs import _classify_job_for_cleanup

    plain_repo = tmp_path / "plain_repo_uuid_folder"
    plain_repo.mkdir()

    jid_upper = "ABCDEF01-2345-6789-ABCD-EF0123456789"
    job_dir = store / "jobs" / jid_upper
    job_dir.mkdir(parents=True)
    job_data = {
        "schema_version": 1,
        "job_id": jid_upper,
        "title": "Uppercase folder job",
        "repo_path": str(plain_repo),
        "state": "done",
        "created_at": _old_time().isoformat(),
        "updated_at": _old_time().isoformat(),
    }
    (job_dir / "job.json").write_text(json.dumps(job_data), encoding="utf-8")

    # In project scan, non-canonical UUID folder is never a candidate
    res = _run_json("--store", store, "--project", plain_repo, expect=0)
    assert res["candidate_count"] == 0
    assert jid_upper not in [c["job_id"] for c in res.get("candidates", [])]

    # Classification directly verifies the fail-closed reason
    now = datetime.now(timezone.utc)
    is_cand, reasons, _, _ = _classify_job_for_cleanup(
        job_dir, store, check_age=True, older_than_days=7.0, now=now
    )
    assert not is_cand
    assert any("canonical uuid" in r.lower() for r in reasons)

    # Deletion never touches the folder
    _run("--store", store, "--project", plain_repo, "--yes", expect=0)
    assert job_dir.is_dir()


def test_loose_end_2_cli_job_uppercase_hex_rejected(store: Path) -> None:
    """CLI --job rejects uppercase UUID with exit code 2."""
    proc = _run(
        "--store",
        store,
        "--job",
        "ABCDEF01-2345-6789-ABCD-EF0123456789",
        expect=2,
    )
    assert "malformed uuid" in proc.stderr.lower()
