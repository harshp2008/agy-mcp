"""FROZEN acceptance tests for cleanup_jobs round 8 (linked-worktree cases, R7-R9).

Committed before the implementation. Do not edit src/ or fix anything in Phase 1.
Oracle: real git. FAIL-OPEN = cleaner says deletable while git still lists the worktree,
OR git cannot read the repo. Keeping a row git does not list (OVER-BLOCK) is allowed.

Covers:
1. Bug A repros: repo_path is a linked worktree, private dir missing commondir/HEAD/gitdir.
2. Bug B repro: job.json has custom worktree_path outside job folder registered in git.
3. Worktree path variants: exists, missing, relative, symlinked, equal to work,
   trailing slash, dotdot components.
4. Controls: healthy linked worktree blocks registered; unregistered deletable;
   plain non-git repo_path deletable.
5. Full R7 matrix: repo kind x file x mutation (missing, dir, dangling symlink,
   fifo, empty, chmod 000).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_TIMEOUT_SECONDS = 60


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


def _run(home_dir: Path, *args: object, expect: int = 0) -> subprocess.CompletedProcess[str]:
    """Execute cleanup_jobs CLI entrypoint and assert expected exit code."""
    cmd = [sys.executable, "-m", "agy_mcp.cleanup_jobs", *[str(a) for a in args]]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=_cli_env(home_dir),
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
    )
    assert proc.returncode == expect, (
        f"expected rc {expect}, got {proc.returncode}\n"
        f"cmd: {cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc


def _run_json(home_dir: Path, *args: object, expect: int = 0) -> dict[str, Any]:
    """Execute cleanup_jobs CLI with --json and return parsed JSON object."""
    proc = _run(home_dir, "--json", *args, expect=expect)
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
    """Initialize a git repository with an initial commit."""
    if bare:
        subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True, env=env)
        tree = subprocess.run(
            ["git", "write-tree"],
            cwd=str(path),
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "commit-tree", tree, "-m", "init"],
            cwd=str(path),
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", "refs/heads/main", commit],
            cwd=str(path),
            check=True,
            capture_output=True,
            env=env,
        )
    else:
        subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=str(path),
            check=True,
            env=env,
        )


def _setup_repo_kind(
    tmp_path: Path,
    repo_kind: str,
    prefix: str,
    store: Path,
) -> tuple[Path, Path, str, Path]:
    """Set up repository kind and job with worktree registered.

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
        )
        (wt_path / ".git").unlink()
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
        )
        jid = _write_job(store, repo=linked_wt, state="done", updated_at=_old_time())
        wt_path = store / "jobs" / jid / "work"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt_path), "-b", f"b_j_{prefix}"],
            cwd=str(linked_wt),
            check=True,
            env=env,
        )
        (wt_path / ".git").unlink()
        priv_dir = main_repo / ".git" / "worktrees" / linked_wt.name
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
        )
        jid = _write_job(store, repo=linked_wt, state="done", updated_at=_old_time())
        wt_path = store / "jobs" / jid / "work"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt_path), "-b", f"b_cw_{prefix}"],
            cwd=str(linked_wt),
            check=True,
            env=env,
        )
        (wt_path / ".git").unlink()
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
        )
        (wt_path / ".git").unlink()
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
        )
        mysub_path = super_repo / "mysub"
        jid = _write_job(store, repo=mysub_path, state="done", updated_at=_old_time())
        wt_path = store / "jobs" / jid / "work"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt_path), "-b", f"b_s_{prefix}"],
            cwd=str(mysub_path),
            check=True,
            env=env,
        )
        (wt_path / ".git").unlink()
        mod_dir = super_repo / ".git" / "modules" / "mysub"
        return mysub_path, mod_dir, jid, wt_path

    raise ValueError(f"unknown repo_kind: {repo_kind}")


def _apply_mutation(target: Path, mutation: str) -> None:
    """Mutate target file/dir according to test mutation name."""
    if mutation == "missing":
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()

    elif mutation == "dir":
        if target.is_dir() and not target.is_symlink():
            # Directory mutated to regular file
            shutil.rmtree(target)
            target.write_text("not_a_directory")
        else:
            if target.exists() or target.is_symlink():
                target.unlink()
            target.mkdir()

    elif mutation == "dangling_symlink":
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        target.symlink_to(target.parent / "nonexistent_target")

    elif mutation == "fifo":
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        try:
            os.mkfifo(str(target))
        except (OSError, NotImplementedError, AttributeError):
            pytest.skip("FIFO creation not supported on platform")

    elif mutation == "empty":
        if target.is_dir() and not target.is_symlink():
            for item in list(target.iterdir()):
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        else:
            target.write_text("")

    elif mutation == "chmod000":
        try:
            target.chmod(0)
            if os.access(str(target), os.R_OK):
                pytest.skip("cannot revoke read permission (running as root)")
        except OSError:
            pytest.skip("chmod 000 not supported on platform")

    else:
        raise ValueError(f"unknown mutation: {mutation}")


# --------------------------------------------------------------------------- #
# 1. Bug A Repros & Variants (linked worktree private dir mutations)
# --------------------------------------------------------------------------- #


def test_bug_a_linked_worktree_commondir_missing(tmp_path: Path) -> None:
    """Bug A: repo_path is linked worktree; commondir missing causes fail-closed."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo, priv_dir, jid, _ = _setup_repo_kind(tmp_path, "linked_wt", "bug_a_commondir", store)

    commondir = priv_dir / "commondir"
    assert commondir.is_file()
    commondir.unlink()

    # Real git oracle fails: git -C repo cannot read repo (rc=128)
    env = _cli_env(tmp_path)
    res_git = subprocess.run(["git", "-C", str(repo), "status"], capture_output=True, env=env)
    assert res_git.returncode != 0, "git should fail with rc=128 when commondir is missing"

    # Cleaner must FAIL-CLOSED (keep job, never candidate)
    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands, (
        f"FAIL-OPEN: cleaner marked job {jid} deletable when commondir is missing"
    )

    # Deletion under --yes must keep job dir intact
    _run(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0", "--yes")
    assert (store / "jobs" / jid).is_dir(), "job was deleted under --yes"


def test_bug_a_linked_worktree_head_missing(tmp_path: Path) -> None:
    """Bug A variant: linked worktree private dir missing HEAD causes fail-closed."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo, priv_dir, jid, _ = _setup_repo_kind(tmp_path, "linked_wt", "bug_a_head", store)

    head = priv_dir / "HEAD"
    assert head.is_file()
    head.unlink()

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands, "FAIL-OPEN: cleaner marked job deletable when HEAD is missing"

    _run(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0", "--yes")
    assert (store / "jobs" / jid).is_dir()


def test_bug_a_linked_worktree_gitdir_missing(tmp_path: Path) -> None:
    """Bug A variant: linked worktree private dir missing gitdir causes fail-closed."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo, priv_dir, jid, _ = _setup_repo_kind(tmp_path, "linked_wt", "bug_a_gitdir", store)

    gitdir = priv_dir / "gitdir"
    assert gitdir.is_file()
    gitdir.unlink()

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands, "FAIL-OPEN: cleaner marked job deletable when gitdir is missing"

    _run(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0", "--yes")
    assert (store / "jobs" / jid).is_dir()


# --------------------------------------------------------------------------- #
# 2. Bug B Repro (custom worktree_path unregistered match)
# --------------------------------------------------------------------------- #


def test_bug_b_custom_worktree_path_unregistered(tmp_path: Path) -> None:
    """Bug B: job has custom worktree_path registered in repo; cleaner must block deletion."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    env = _cli_env(tmp_path)

    repo = tmp_path / "bug_b_repo"
    _init_git_repo(repo, env, bare=False)

    ext_worktree = tmp_path / "custom_worktree"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(ext_worktree), "-b", "ext_branch"],
        cwd=str(repo),
        check=True,
        env=env,
    )

    jid = _write_job(
        store,
        repo=repo,
        worktree_path=ext_worktree,
        state="done",
        updated_at=_old_time(),
    )

    # Simulate worktree cleanup prior to git prune
    (ext_worktree / ".git").unlink()

    # Real git lists worktree as registered (prunable)
    res_git = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert str(ext_worktree.resolve(strict=False)) in res_git.stdout

    # Cleaner must detect registration and NOT mark as candidate
    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands, (
        f"FAIL-OPEN: cleaner marked job {jid} deletable while git lists {ext_worktree}"
    )

    _run(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0", "--yes")
    assert (store / "jobs" / jid).is_dir(), "job was deleted under --yes"


# --------------------------------------------------------------------------- #
# 3. Worktree Path Variants
# --------------------------------------------------------------------------- #


def test_worktree_path_variant_exists_on_disk(tmp_path: Path) -> None:
    """worktree_path and its .git file exist on disk: cleaner blocks deletion."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    env = _cli_env(tmp_path)
    repo = tmp_path / "wt_var_exists"
    _init_git_repo(repo, env, bare=False)

    custom_wt = tmp_path / "custom_wt_exists"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(custom_wt), "-b", "b_exists"],
        cwd=str(repo),
        check=True,
        env=env,
    )
    jid = _write_job(
        store,
        repo=repo,
        worktree_path=custom_wt,
        state="done",
        updated_at=_old_time(),
    )

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands


def test_worktree_path_variant_missing_on_disk(tmp_path: Path) -> None:
    """worktree_path deleted completely from disk, still registered in git: cleaner blocks."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    env = _cli_env(tmp_path)
    repo = tmp_path / "wt_var_missing"
    _init_git_repo(repo, env, bare=False)

    custom_wt = tmp_path / "custom_wt_missing"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(custom_wt), "-b", "b_missing"],
        cwd=str(repo),
        check=True,
        env=env,
    )
    jid = _write_job(
        store,
        repo=repo,
        worktree_path=custom_wt,
        state="done",
        updated_at=_old_time(),
    )
    shutil.rmtree(custom_wt)

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands


def test_worktree_path_variant_relative_in_job_json(tmp_path: Path) -> None:
    """job.json has relative worktree_path: cleaner normalizes and blocks."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    env = _cli_env(tmp_path)
    repo = tmp_path / "wt_var_rel"
    _init_git_repo(repo, env, bare=False)

    custom_wt = tmp_path / "custom_wt_rel"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(custom_wt), "-b", "b_rel"],
        cwd=str(repo),
        check=True,
        env=env,
    )
    (custom_wt / ".git").unlink()

    # Use path relative to repo or cwd
    rel_path = os.path.relpath(custom_wt, REPO_ROOT)
    jid = _write_job(
        store,
        repo=repo,
        worktree_path=rel_path,
        state="done",
        updated_at=_old_time(),
    )

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands


def test_worktree_path_variant_symlinked(tmp_path: Path) -> None:
    """worktree_path is a symlink path: cleaner resolves and blocks."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    env = _cli_env(tmp_path)
    repo = tmp_path / "wt_var_symlink"
    _init_git_repo(repo, env, bare=False)

    real_wt = tmp_path / "real_wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(real_wt), "-b", "b_sym"],
        cwd=str(repo),
        check=True,
        env=env,
    )
    (real_wt / ".git").unlink()

    sym_wt = tmp_path / "sym_wt"
    sym_wt.symlink_to(real_wt)

    jid = _write_job(
        store,
        repo=repo,
        worktree_path=sym_wt,
        state="done",
        updated_at=_old_time(),
    )

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands


def test_worktree_path_variant_equal_to_job_work_dir(tmp_path: Path) -> None:
    """worktree_path equals job work dir: cleaner blocks."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    env = _cli_env(tmp_path)
    repo = tmp_path / "wt_var_equal"
    _init_git_repo(repo, env, bare=False)

    jid = _write_job(store, repo=repo, state="done", updated_at=_old_time())
    work_dir = store / "jobs" / jid / "work"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(work_dir), "-b", "b_eq"],
        cwd=str(repo),
        check=True,
        env=env,
    )
    (work_dir / ".git").unlink()

    # Update job.json to explicitly set worktree_path=work_dir
    jdata = json.loads((store / "jobs" / jid / "job.json").read_text(encoding="utf-8"))
    jdata["worktree_path"] = str(work_dir)
    (store / "jobs" / jid / "job.json").write_text(json.dumps(jdata), encoding="utf-8")

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands


def test_worktree_path_variant_registered_trailing_slash(tmp_path: Path) -> None:
    """Worktree gitdir contains trailing slash: cleaner resolves and blocks."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    env = _cli_env(tmp_path)
    repo = tmp_path / "wt_var_trailing"
    _init_git_repo(repo, env, bare=False)

    custom_wt = tmp_path / "custom_wt_trailing"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(custom_wt), "-b", "b_trail"],
        cwd=str(repo),
        check=True,
        env=env,
    )
    (custom_wt / ".git").unlink()

    # Modify gitdir file to have trailing slash
    priv = repo / ".git" / "worktrees" / custom_wt.name
    gd = priv / "gitdir"
    target = gd.read_text(encoding="utf-8").strip()
    gd.write_text(target + "/\n", encoding="utf-8")

    jid = _write_job(
        store,
        repo=repo,
        worktree_path=custom_wt,
        state="done",
        updated_at=_old_time(),
    )

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands


def test_worktree_path_variant_registered_dotdot_components(tmp_path: Path) -> None:
    """Worktree gitdir contains dotdot components: cleaner resolves and blocks."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    env = _cli_env(tmp_path)
    repo = tmp_path / "wt_var_dotdot"
    _init_git_repo(repo, env, bare=False)

    custom_wt = tmp_path / "custom_wt_dotdot"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(custom_wt), "-b", "b_dotdot"],
        cwd=str(repo),
        check=True,
        env=env,
    )
    (custom_wt / ".git").unlink()

    # Modify gitdir file with dotdot component
    priv = repo / ".git" / "worktrees" / custom_wt.name
    gd = priv / "gitdir"
    gd.write_text(f"{repo}/../{custom_wt.name}/.git\n", encoding="utf-8")

    jid = _write_job(
        store,
        repo=repo,
        worktree_path=custom_wt,
        state="done",
        updated_at=_old_time(),
    )

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    cands = [c["job_id"] for c in res.get("candidates", [])]
    assert jid not in cands


# --------------------------------------------------------------------------- #
# 4. Controls
# --------------------------------------------------------------------------- #


def test_control_healthy_linked_worktree_with_registered_job_blocks(tmp_path: Path) -> None:
    """Healthy linked worktree with registered worktree blocks deletion."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    repo, _, jid, _ = _setup_repo_kind(tmp_path, "linked_wt", "ctrl_healthy_linked", store)

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    assert res["candidate_count"] == 0
    assert res["kept_count"] == 1
    assert res["kept"][0]["job_id"] == jid


def test_control_healthy_linked_worktree_unregistered_job_deletable(tmp_path: Path) -> None:
    """Healthy linked worktree without registered worktree is deletable."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    env = _cli_env(tmp_path)

    main_repo = tmp_path / "ctrl_unreg_main"
    _init_git_repo(main_repo, env, bare=False)
    linked_wt = tmp_path / "ctrl_unreg_linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(linked_wt), "-b", "b_unreg"],
        cwd=str(main_repo),
        check=True,
        env=env,
    )
    jid = _write_job(store, repo=linked_wt, state="done", updated_at=_old_time())

    res = _run_json(tmp_path, "--store", store, "--project", linked_wt, "--older-than-days", "0")
    assert res["candidate_count"] == 1
    assert res["candidates"][0]["job_id"] == jid

    _run(tmp_path, "--store", store, "--project", linked_wt, "--older-than-days", "0", "--yes")
    assert not (store / "jobs" / jid).exists()


def test_control_unregistered_job_with_healthy_repo_is_deletable(tmp_path: Path) -> None:
    """Healthy normal repo without registered worktree is deletable."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    env = _cli_env(tmp_path)

    repo = tmp_path / "ctrl_healthy_normal"
    _init_git_repo(repo, env, bare=False)
    jid = _write_job(store, repo=repo, state="done", updated_at=_old_time())

    res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
    assert res["candidate_count"] == 1
    assert res["candidates"][0]["job_id"] == jid

    _run(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0", "--yes")
    assert not (store / "jobs" / jid).exists()


def test_control_plain_non_git_repo_path_stays_deletable(tmp_path: Path) -> None:
    """Plain directory without git files is deletable."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)

    plain_dir = tmp_path / "plain_dir"
    plain_dir.mkdir()
    jid = _write_job(store, repo=plain_dir, state="done", updated_at=_old_time())

    res = _run_json(tmp_path, "--store", store, "--project", plain_dir, "--older-than-days", "0")
    assert res["candidate_count"] == 1
    assert res["candidates"][0]["job_id"] == jid

    _run(tmp_path, "--store", store, "--project", plain_dir, "--older-than-days", "0", "--yes")
    assert not (store / "jobs" / jid).exists()


# --------------------------------------------------------------------------- #
# 5. Full R7 Matrix: repo_kind x file_name x mutation
# --------------------------------------------------------------------------- #

_MATRIX_KINDS: dict[str, list[str]] = {
    "normal": ["HEAD", "objects", "refs", "config"],
    "linked_wt": ["HEAD", "commondir", "gitdir"],
    "common": ["HEAD", "objects", "refs", "config"],
    "bare": ["HEAD", "objects", "refs", "config"],
    "submodule": ["HEAD", "objects", "refs", "config"],
}

_MUTATIONS = ["missing", "dir", "dangling_symlink", "fifo", "empty", "chmod000"]

_MATRIX_CELLS = [
    (k, f, m)
    for k, files in _MATRIX_KINDS.items()
    for f, m in product(files, _MUTATIONS)
]


@pytest.mark.parametrize("repo_kind,file_name,mutation", _MATRIX_CELLS)
def test_r7_matrix_fails_closed(
    tmp_path: Path,
    repo_kind: str,
    file_name: str,
    mutation: str,
) -> None:
    """R7 matrix cell: git oracle vs cleaner must never FAIL-OPEN."""
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    prefix = f"{repo_kind}_{file_name}_{mutation}"
    repo, target_dir, jid, wt_path = _setup_repo_kind(tmp_path, repo_kind, prefix, store)

    target = target_dir / file_name
    _apply_mutation(target, mutation)

    env = _cli_env(tmp_path)
    git_status_ok = False
    git_wt_ok = False
    git_registered = False

    try:
        try:
            p_status = subprocess.run(
                ["git", "-C", str(repo), "status"],
                capture_output=True,
                text=True,
                env=env,
                timeout=2,
            )
            git_status_ok = p_status.returncode == 0
        except subprocess.TimeoutExpired:
            git_status_ok = False

        try:
            p_wt = subprocess.run(
                ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                capture_output=True,
                text=True,
                env=env,
                timeout=2,
            )
            git_wt_ok = p_wt.returncode == 0
            git_registered = git_wt_ok and (
                str(wt_path) in p_wt.stdout
                or str(wt_path.resolve(strict=False)) in p_wt.stdout
            )
        except subprocess.TimeoutExpired:
            git_wt_ok = False
            git_registered = False

        git_works = git_status_ok or git_wt_ok

        res = _run_json(tmp_path, "--store", store, "--project", repo, "--older-than-days", "0")
        cands = [c["job_id"] for c in res.get("candidates", [])]
        is_candidate = jid in cands

        if (not git_works) or git_registered:
            # Oracle says git cannot read the repo or git still lists the worktree:
            # Cleaner MUST FAIL-CLOSED (keep the job).
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
            )
            assert (store / "jobs" / jid).is_dir(), (
                f"FAIL-OPEN on {repo_kind}/{file_name}/{mutation}: "
                f"job was deleted under --yes"
            )
    finally:
        # Restore permissions if chmod 000 was applied so tmp_path can clean up
        try:
            target.chmod(0o755)
        except OSError:
            pass
