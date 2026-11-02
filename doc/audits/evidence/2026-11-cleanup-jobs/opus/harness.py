#!/usr/bin/env python3
"""Shared harness for opus review attacks."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_SRC = Path("~/Documents/GITHUB PROJECTS/agy-mcp/src")
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from agy_mcp.cleanup_jobs import _check_worktree_status, run_cleanup


def get_env(home_dir: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home_dir),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "author@example.com",
        "GIT_COMMITTER_NAME": "Test Committer",
        "GIT_COMMITTER_EMAIL": "committer@example.com",
        "LANG": "C.UTF-8",
    }


def safe_rmtree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            try: os.chmod(Path(root) / f, 0o600)
            except OSError: pass
        for d in dirs:
            try: os.chmod(Path(root) / d, 0o700)
            except OSError: pass
    shutil.rmtree(path)


def make_normal_repo_job(scratch: Path):
    """Create a normal repo with a worktree in a job dir. Returns (repo, store, job_dir, job_id, work, env)."""
    safe_rmtree(scratch)
    scratch.mkdir(parents=True)
    env = get_env(scratch)

    repo = scratch / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                    cwd=str(repo), check=True, capture_output=True, env=env)

    store = scratch / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)

    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir()
    work = job_dir / "work"

    subprocess.run(["git", "worktree", "add", str(work), "-b", "wt_a"],
                    cwd=str(repo), check=True, capture_output=True, env=env)
    (work / ".git").unlink()

    job_data = {
        "job_id": job_id,
        "title": "attack job",
        "state": "done",
        "repo_path": str(repo),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")
    return repo, store, job_dir, job_id, work, env


def make_bare_repo_job(scratch: Path):
    """Create a bare repo with a worktree in a job dir."""
    safe_rmtree(scratch)
    scratch.mkdir(parents=True)
    env = get_env(scratch)

    bare = scratch / "bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, env=env)
    tree = subprocess.run(["git", "write-tree"], cwd=str(bare), check=True,
                          capture_output=True, text=True, env=env).stdout.strip()
    commit = subprocess.run(["git", "commit-tree", tree, "-m", "init"], cwd=str(bare),
                            check=True, capture_output=True, text=True, env=env).stdout.strip()
    subprocess.run(["git", "update-ref", "refs/heads/main", commit],
                    cwd=str(bare), check=True, capture_output=True, env=env)

    store = scratch / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)

    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir()
    work = job_dir / "work"

    subprocess.run(["git", "worktree", "add", str(work), "main"],
                    cwd=str(bare), check=True, capture_output=True, env=env)
    (work / ".git").unlink()

    job_data = {
        "job_id": job_id,
        "title": "attack job",
        "state": "done",
        "repo_path": str(bare),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")
    return bare, store, job_dir, job_id, work, env


def check_git(repo: Path, work: Path, env: dict) -> tuple:
    """Returns (git_works, git_registered, git_output)."""
    res = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, errors="replace", env=env,
    )
    git_works = res.returncode == 0
    work_resolved = str(work.resolve(strict=False))
    found = False
    if git_works:
        for line in res.stdout.splitlines():
            if line.startswith("worktree "):
                wt = line[len("worktree "):].strip()
                try:
                    if str(Path(wt).resolve(strict=False)) == work_resolved:
                        found = True
                except Exception:
                    pass
    return git_works, found, res.stdout + res.stderr


def check_cleaner(store: Path, repo: Path, job_dir: Path, job_id: str,
                   data: dict = None) -> tuple:
    """Returns (is_registered, reg_reason, is_candidate, crash_msg)."""
    if data is None:
        data = {"repo_path": str(repo)}
    is_reg, reason = _check_worktree_status(job_dir, data)
    try:
        result = run_cleanup(store, project=str(repo), dry_run=True, older_than_days=0.0)
        cand_ids = [c["job_id"] for c in result.get("candidates", [])]
        return is_reg, reason, job_id in cand_ids, None
    except Exception as exc:
        return is_reg, reason, False, str(exc)


def verdict(git_works, git_reg, is_candidate, crash_msg) -> str:
    if crash_msg:
        return f"CRASH: {crash_msg}"
    if is_candidate and (not git_works or git_reg):
        return "FAIL-OPEN"
    if is_candidate:
        return "OK (deletable, git agrees)"
    return "OK (kept)"
