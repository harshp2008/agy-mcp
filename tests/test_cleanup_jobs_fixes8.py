"""FROZEN acceptance tests for cleanup_jobs round 9 (git metadata validation, R10-R12).

Committed before implementation. Do not edit src/ or fix anything in Phase 1.
Oracle: real git. FAIL-OPEN = cleaner says deletable while git still lists the worktree,
OR git cannot read the repo. Keeping a row git does not list (OVER-BLOCK) is allowed.

Covers:
1. The 6 F3 variants from repro_f3_git_metadata_nul_bom.py:
   - Normal repo config NUL
   - Normal repo HEAD NUL
   - Normal repo HEAD BOM
   - Bare repo config NUL
   - Bare repo HEAD NUL
   - Bare repo HEAD BOM
2. Full R10 matrix:
   repo kind (normal, linked_wt, common, bare, submodule, pointer)
   x metadata files (HEAD, config, packed-refs, commondir, gitdir, locked, .git pointer)
   x mutations (nul_byte, bom, crlf, non_utf8, trunc_half, trunc_empty, garbage,
                trailing_spaces, extra_lines).
3. FIFO-as-metadata cases with a hard timeout (HEAD, config, packed-refs,
   commondir, gitdir, pointer file as FIFO) asserting prompt return and fail-closed.
4. Controls: healthy repos of every kind still behave as before (registered -> kept,
   unregistered -> deletable; plain non-git directory -> deletable).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_TIMEOUT_SECONDS = 15


def _cli_env(home_dir: Path) -> dict[str, str]:
    """Run worktree code with HOME confined strictly to scratch tempdir."""
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
    if worktree_path is not None:
        data["worktree_path"] = str(worktree_path)
    if extra:
        data.update(extra)
    (job_dir / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return jid


def _init_git_repo(path: Path, env: dict[str, str], *, bare: bool = False) -> None:
    """Initialize a git repository with an initial commit and packed-refs."""
    if bare:
        subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True, env=env, timeout=10)
        tree = subprocess.run(
            ["git", "write-tree"],
            cwd=str(path),
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "commit-tree", tree, "-m", "init"],
            cwd=str(path),
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", "refs/heads/main", commit],
            cwd=str(path),
            check=True,
            capture_output=True,
            env=env,
            timeout=10,
        )
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
            cwd=str(path),
            check=True,
            capture_output=True,
            env=env,
            timeout=10,
        )
        subprocess.run(
            ["git", "pack-refs", "--all"],
            cwd=str(path),
            check=True,
            capture_output=True,
            env=env,
            timeout=10,
        )
    else:
        subprocess.run(["git", "init", "-q", str(path)], check=True, env=env, timeout=10)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=str(path),
            check=True,
            env=env,
            timeout=10,
        )
        subprocess.run(
            ["git", "pack-refs", "--all"],
            cwd=str(path),
            check=True,
            capture_output=True,
            env=env,
            timeout=10,
        )


def _setup_repo_kind(
    tmp_path: Path,
    repo_kind: str,
    prefix: str,
    store: Path,
    *,
    worktree_registered: bool = False,
) -> tuple[Path, Path, str, Path]:
    """Set up repository kind and job.

    Returns: (repo_path_for_cleaner, target_dir_to_mutate, job_id, worktree_path).
    """
    env = _cli_env(tmp_path)
    if repo_kind == "normal":
        repo = tmp_path / f"{prefix}_normal"
        _init_git_repo(repo, env, bare=False)
        jid = _write_job(store, repo=repo, state="done", updated_at=_old_time())
        wt_path = store / "jobs" / jid / "work"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt_path), "-b", f"b_{prefix}"],
            cwd=str(repo),
            check=True,
            env=env,
            timeout=10,
        )
        if not worktree_registered:
            (wt_path / ".git").unlink()
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(repo),
                check=True,
                env=env,
                timeout=10,
            )
        return repo, repo / ".git", jid, wt_path

    elif repo_kind == "linked_wt":
        main_repo = tmp_path / f"{prefix}_main"
        _init_git_repo(main_repo, env, bare=False)
        linked_wt = tmp_path / f"{prefix}_linked_wt"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(linked_wt), "-b", f"b_l_{prefix}"],
            cwd=str(main_repo),
            check=True,
            env=env,
            timeout=10,
        )
        jid = _write_job(store, repo=linked_wt, state="done", updated_at=_old_time())
        wt_path = store / "jobs" / jid / "work"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt_path), "-b", f"b_j_{prefix}"],
            cwd=str(linked_wt),
            check=True,
            env=env,
            timeout=10,
        )
        if not worktree_registered:
            (wt_path / ".git").unlink()
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(linked_wt),
                check=True,
                env=env,
                timeout=10,
            )
        priv_dir = main_repo / ".git" / "worktrees" / linked_wt.name
        # Create locked file if needed
        subprocess.run(
            ["git", "worktree", "lock", "--reason", "test lock", str(linked_wt)],
            cwd=str(main_repo),
            check=True,
            env=env,
            timeout=10,
        )
        return linked_wt, priv_dir, jid, wt_path

    elif repo_kind == "common":
        main_repo = tmp_path / f"{prefix}_common_main"
        _init_git_repo(main_repo, env, bare=False)
        linked_wt = tmp_path / f"{prefix}_common_wt"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(linked_wt), "-b", f"b_c_{prefix}"],
            cwd=str(main_repo),
            check=True,
            env=env,
            timeout=10,
        )
        jid = _write_job(store, repo=linked_wt, state="done", updated_at=_old_time())
        wt_path = store / "jobs" / jid / "work"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt_path), "-b", f"b_cw_{prefix}"],
            cwd=str(linked_wt),
            check=True,
            env=env,
            timeout=10,
        )
        if not worktree_registered:
            (wt_path / ".git").unlink()
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(linked_wt),
                check=True,
                env=env,
                timeout=10,
            )
        return linked_wt, main_repo / ".git", jid, wt_path

    elif repo_kind == "bare":
        bare_repo = tmp_path / f"{prefix}_bare.git"
        _init_git_repo(bare_repo, env, bare=True)
        jid = _write_job(store, repo=bare_repo, state="done", updated_at=_old_time())
        wt_path = store / "jobs" / jid / "work"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt_path), "-b", f"b_b_{prefix}", "main"],
            cwd=str(bare_repo),
            check=True,
            env=env,
            timeout=10,
        )
        if not worktree_registered:
            (wt_path / ".git").unlink()
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(bare_repo),
                check=True,
                env=env,
                timeout=10,
            )
        return bare_repo, bare_repo, jid, wt_path

    elif repo_kind == "submodule":
        sub_repo = tmp_path / f"{prefix}_sub"
        _init_git_repo(sub_repo, env, bare=False)
        super_repo = tmp_path / f"{prefix}_super"
        _init_git_repo(super_repo, env, bare=False)
        subprocess.run(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "-q",
                "add",
                str(sub_repo),
                "mysub",
            ],
            cwd=str(super_repo),
            check=True,
            env=env,
            timeout=10,
        )
        mysub_path = super_repo / "mysub"
        jid = _write_job(store, repo=mysub_path, state="done", updated_at=_old_time())
        wt_path = store / "jobs" / jid / "work"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt_path), "-b", f"b_s_{prefix}"],
            cwd=str(mysub_path),
            check=True,
            env=env,
            timeout=10,
        )
        if not worktree_registered:
            (wt_path / ".git").unlink()
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(mysub_path),
                check=True,
                env=env,
                timeout=10,
            )
        mod_dir = super_repo / ".git" / "modules" / "mysub"
        subprocess.run(
            ["git", "pack-refs", "--all"], cwd=str(mod_dir), check=True, env=env, timeout=10
        )
        return mysub_path, mod_dir, jid, wt_path

    elif repo_kind == "pointer":
        main_repo = tmp_path / f"{prefix}_p_main"
        _init_git_repo(main_repo, env, bare=False)
        linked_wt = tmp_path / f"{prefix}_p_wt"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(linked_wt), "-b", f"b_p_{prefix}"],
            cwd=str(main_repo),
            check=True,
            env=env,
            timeout=10,
        )
        jid = _write_job(store, repo=linked_wt, state="done", updated_at=_old_time())
        wt_path = store / "jobs" / jid / "work"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt_path), "-b", f"b_pw_{prefix}"],
            cwd=str(linked_wt),
            check=True,
            env=env,
            timeout=10,
        )
        if not worktree_registered:
            (wt_path / ".git").unlink()
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(linked_wt),
                check=True,
                env=env,
                timeout=10,
            )
        return linked_wt, linked_wt, jid, wt_path

    raise ValueError(f"unknown repo_kind: {repo_kind}")


def _apply_mutation(target: Path, mutation: str) -> None:
    """Mutate target file according to test mutation name."""
    orig = target.read_bytes() if target.exists() else b""

    if mutation == "nul_byte":
        target.write_bytes(b"\x00\x00" + orig)
    elif mutation == "bom":
        target.write_bytes(b"\xef\xbb\xbf" + orig)
    elif mutation == "crlf":
        target.write_bytes(orig.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    elif mutation == "non_utf8":
        target.write_bytes(b"\xff\xfe\x80\x90" + orig)
    elif mutation == "trunc_half":
        target.write_bytes(orig[: len(orig) // 2])
    elif mutation == "trunc_empty":
        target.write_bytes(b"")
    elif mutation == "garbage":
        target.write_bytes(b"malformed_syntax_garbage_content_12345\n")
    elif mutation == "trailing_spaces":
        target.write_bytes(orig.rstrip(b"\r\n") + b"    \r\n")
    elif mutation == "extra_lines":
        target.write_bytes(orig.rstrip(b"\r\n") + b"\nextra line after content\n")
    else:
        raise ValueError(f"unknown mutation: {mutation}")


# --------------------------------------------------------------------------- #
# 1. Confirmed F3 Reproductions (from repro_f3_git_metadata_nul_bom.py)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,rel_path,corruption,is_bare",
    [
        (
            "Normal_repo_config_NUL",
            ".git/config",
            b"\x00\x00[core]\n\trepositoryformatversion = 0\n",
            False,
        ),
        ("Normal_repo_HEAD_NUL", ".git/HEAD", b"\x00\x00ref: refs/heads/main\n", False),
        ("Normal_repo_HEAD_BOM", ".git/HEAD", b"\xef\xbb\xbfref: refs/heads/main\n", False),
        (
            "Bare_repo_config_NUL",
            "config",
            b"\x00\x00[core]\n\trepositoryformatversion = 0\n",
            True,
        ),
        ("Bare_repo_HEAD_NUL", "HEAD", b"\x00\x00ref: refs/heads/main\n", True),
        ("Bare_repo_HEAD_BOM", "HEAD", b"\xef\xbb\xbfref: refs/heads/main\n", True),
    ],
)
def test_f3_variant_confirmed_fail_open_prevented(
    tmp_path: Path,
    name: str,
    rel_path: str,
    corruption: bytes,
    is_bare: bool,
) -> None:
    """Verify that corrupting HEAD or config with NUL/BOM does not FAIL-OPEN."""
    env = _cli_env(tmp_path)
    repo = tmp_path / ("bare.git" if is_bare else "repo")
    _init_git_repo(repo, env, bare=is_bare)

    store = tmp_path / "store"
    jid = _write_job(store, repo=repo, state="done", updated_at=_old_time())
    jdir = store / "jobs" / jid

    target_file = repo / rel_path
    target_file.write_bytes(corruption)

    # Git oracle must fail
    git_status = subprocess.run(
        ["git", "-C", str(repo), "status"],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    git_wt = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list"],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert git_status.returncode != 0, f"expected git status to fail on {name}"
    assert git_wt.returncode != 0, f"expected git worktree list to fail on {name}"

    # Cleaner must not delete the job
    res = _run_json(
        tmp_path,
        "--store",
        store,
        "--project",
        repo,
        "--older-than-days",
        "0",
        "--yes",
        expect=None,
    )
    deleted_ids = res.get("deleted_ids", [])
    assert jid not in deleted_ids, f"FAIL-OPEN: cleaner deleted job {jid} on {name}"
    assert jdir.is_dir(), f"FAIL-OPEN: job directory {jdir} was deleted on disk for {name}"


# --------------------------------------------------------------------------- #
# 2. Full R10 Matrix: repo kind x metadata file x mutation
# --------------------------------------------------------------------------- #

_MATRIX_COMBOS = [
    # Normal repo
    ("normal", "HEAD"),
    ("normal", "config"),
    ("normal", "packed-refs"),
    # Linked worktree private dir
    ("linked_wt", "HEAD"),
    ("linked_wt", "commondir"),
    ("linked_wt", "gitdir"),
    ("linked_wt", "locked"),
    # Common dir
    ("common", "HEAD"),
    ("common", "config"),
    ("common", "packed-refs"),
    # Bare repo
    ("bare", "HEAD"),
    ("bare", "config"),
    ("bare", "packed-refs"),
    # Submodule modules dir
    ("submodule", "HEAD"),
    ("submodule", "config"),
    ("submodule", "packed-refs"),
    # .git pointer file
    ("pointer", ".git"),
]

_MUTATIONS = [
    "nul_byte",
    "bom",
    "crlf",
    "non_utf8",
    "trunc_half",
    "trunc_empty",
    "garbage",
    "trailing_spaces",
    "extra_lines",
]


@pytest.mark.parametrize(
    "repo_kind,file_name",
    _MATRIX_COMBOS,
)
@pytest.mark.parametrize(
    "mutation",
    _MUTATIONS,
)
def test_r10_matrix_git_oracle_differential(
    tmp_path: Path,
    repo_kind: str,
    file_name: str,
    mutation: str,
) -> None:
    """Every matrix cell: git oracle vs cleaner must never FAIL-OPEN."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    prefix = f"{repo_kind}_{file_name}_{mutation}".replace(".", "_")
    repo, target_dir, jid, wt_path = _setup_repo_kind(
        tmp_path, repo_kind, prefix, store, worktree_registered=False
    )

    target = target_dir / file_name
    _apply_mutation(target, mutation)

    env = _cli_env(tmp_path)
    git_status_ok = False
    git_wt_ok = False
    git_registered = False

    try:
        p_status = subprocess.run(
            ["git", "-C", str(repo), "status"],
            capture_output=True,
            text=True,
            env=env,
            timeout=2.0,
            errors="replace",
        )
        git_status_ok = (p_status.returncode == 0)
    except subprocess.TimeoutExpired:
        git_status_ok = False

    try:
        p_wt = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            env=env,
            timeout=2.0,
            errors="replace",
        )
        git_wt_ok = (p_wt.returncode == 0)
        git_registered = git_wt_ok and (
            str(wt_path) in p_wt.stdout or str(wt_path.resolve(strict=False)) in p_wt.stdout
        )
    except subprocess.TimeoutExpired:
        git_wt_ok = False
        git_registered = False

    git_works = git_status_ok or git_wt_ok

    res = _run_json(
        tmp_path,
        "--store",
        store,
        "--project",
        repo,
        "--older-than-days",
        "0",
        expect=None,
        timeout=10.0,
    )
    cands = [c["job_id"] for c in res.get("candidates", [])]
    is_candidate = jid in cands

    if (not git_works) or git_registered:
        # Git oracle cannot read repo or still registers worktree: MUST FAIL-CLOSED.
        assert not is_candidate, (
            f"FAIL-OPEN on {repo_kind}/{file_name}/{mutation}: "
            f"git_works={git_works}, git_registered={git_registered}, "
            f"but cleaner returned candidate for {jid}"
        )
        _run(
            tmp_path,
            "--store",
            store,
            "--project",
            repo,
            "--older-than-days",
            "0",
            "--yes",
            expect=None,
            timeout=10.0,
        )
        assert (store / "jobs" / jid).is_dir(), (
            f"FAIL-OPEN on {repo_kind}/{file_name}/{mutation}: "
            f"job was deleted under --yes"
        )


# --------------------------------------------------------------------------- #
# 3. FIFO-as-metadata cases with a hard timeout (R11 prompt return check)
# --------------------------------------------------------------------------- #

_FIFO_CASES = [
    ("normal", "HEAD"),
    ("normal", "config"),
    ("normal", "packed-refs"),
    ("linked_wt", "commondir"),
    ("linked_wt", "gitdir"),
    ("pointer", ".git"),
]


@pytest.mark.parametrize(
    "repo_kind,file_name",
    _FIFO_CASES,
)
def test_fifo_metadata_hard_timeout_and_fail_closed(
    tmp_path: Path,
    repo_kind: str,
    file_name: str,
) -> None:
    """Cleaner must never hang on FIFO metadata file; must return promptly and fail closed."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    prefix = f"fifo_{repo_kind}_{file_name}".replace(".", "_")
    repo, target_dir, jid, _ = _setup_repo_kind(
        tmp_path, repo_kind, prefix, store, worktree_registered=False
    )

    target = target_dir / file_name
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        os.mkfifo(str(target))
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("FIFO creation not supported on platform")

    # Hard timeout assertion: cleaner must return promptly (< 3.0 seconds)
    t0 = time.monotonic()
    res = _run_json(
        tmp_path,
        "--store",
        store,
        "--project",
        repo,
        "--older-than-days",
        "0",
        expect=None,
        timeout=5.0,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0, f"cleaner took {elapsed:.2f}s on FIFO {file_name}, expected < 3.0s"

    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands, f"cleaner returned deletable candidate on FIFO {file_name}"

    _run(
        tmp_path,
        "--store",
        store,
        "--project",
        repo,
        "--older-than-days",
        "0",
        "--yes",
        expect=None,
        timeout=5.0,
    )
    assert (store / "jobs" / jid).is_dir(), f"cleaner deleted job on FIFO {file_name} under --yes"


# --------------------------------------------------------------------------- #
# 4. Controls: Healthy repos (registered vs unregistered) & plain non-git
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "repo_kind",
    ["normal", "linked_wt", "common", "bare", "submodule"],
)
def test_control_healthy_repos_registered_worktree_kept(
    tmp_path: Path,
    repo_kind: str,
) -> None:
    """Healthy repos with worktree still registered must be kept."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    prefix = f"ctrl_reg_{repo_kind}"
    repo, _, jid, _ = _setup_repo_kind(
        tmp_path, repo_kind, prefix, store, worktree_registered=True
    )

    res = _run_json(
        tmp_path,
        "--store",
        store,
        "--project",
        repo,
        "--older-than-days",
        "0",
        expect=0,
    )
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands, f"healthy registered job was marked candidate for {repo_kind}"

    _run(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0", "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), f"healthy registered job was deleted for {repo_kind}"


@pytest.mark.parametrize(
    "repo_kind",
    ["normal", "linked_wt", "common", "bare", "submodule"],
)
def test_control_healthy_repos_unregistered_worktree_deletable(
    tmp_path: Path,
    repo_kind: str,
) -> None:
    """Healthy repos with worktree pruned/unregistered must be deletable."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    prefix = f"ctrl_unreg_{repo_kind}"
    repo, _, jid, _ = _setup_repo_kind(
        tmp_path, repo_kind, prefix, store, worktree_registered=False
    )

    res = _run_json(
        tmp_path,
        "--store",
        store,
        "--project",
        repo,
        "--older-than-days",
        "0",
        expect=0,
    )
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid in cands, f"healthy unregistered job was not marked candidate for {repo_kind}"

    _run(
        tmp_path,
        "--store",
        store,
        "--project",
        repo,
        "--older-than-days",
        "0",
        "--yes",
        expect=0,
    )
    assert not (store / "jobs" / jid).exists(), (
        f"healthy unregistered job was not deleted for {repo_kind}"
    )


def test_control_non_git_repo_path_deletable(tmp_path: Path) -> None:
    """Plain non-git repo_path is deletable."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    non_git_dir = tmp_path / "plain_dir"
    non_git_dir.mkdir()
    (non_git_dir / "somefile.txt").write_text("hello")

    jid = _write_job(store, repo=non_git_dir, state="done", updated_at=_old_time())
    res = _run_json(
        tmp_path,
        "--store",
        store,
        "--project",
        non_git_dir,
        "--older-than-days",
        "0",
        expect=0,
    )
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid in cands

    _run(
        tmp_path,
        "--store",
        store,
        "--project",
        non_git_dir,
        "--older-than-days",
        "0",
        "--yes",
        expect=0,
    )
    assert not (store / "jobs" / jid).exists()
