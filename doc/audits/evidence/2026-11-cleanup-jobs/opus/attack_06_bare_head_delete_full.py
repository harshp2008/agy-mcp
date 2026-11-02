#!/usr/bin/env python3
"""Attack 6: Test if bare repo path with worktrees/ dir present but HEAD absent
is actually reachable in the real world (not just synthetic).

Also test: what if the repo_path was a bare repo that's been partially deleted?
E.g., someone deleted HEAD but left worktrees/ around.

And test the scenario where the repo is a bare repo (no .git) with:
- repo_path/worktrees/ exists with entries
- repo_path/HEAD deleted
- git -C repo worktree list fails
- cleaner says deletable
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_SRC = Path("~/Documents/GITHUB PROJECTS/agy-mcp/src")
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

SCRATCH = Path("~/agy-review/opus/scratch_06")

def get_safe_git_env(home_dir: Path) -> dict[str, str]:
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

def safe_rmtree(path):
    if not path.exists() and not path.is_symlink():
        return
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            try: os.chmod(Path(root)/f, 0o600)
            except: pass
        for d in dirs:
            try: os.chmod(Path(root)/d, 0o700)
            except: pass
    shutil.rmtree(path)

def attack():
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    env = get_safe_git_env(SCRATCH)
    
    # Create bare repo
    bare = SCRATCH / "bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, env=env)
    tree = subprocess.run(
        ["git", "write-tree"], cwd=str(bare), check=True, capture_output=True, text=True, env=env
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", "init"], cwd=str(bare), check=True, capture_output=True, text=True, env=env
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/heads/main", commit], cwd=str(bare), check=True, capture_output=True, env=env
    )
    
    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)
    
    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir()
    work = job_dir / "work"
    
    subprocess.run(
        ["git", "worktree", "add", str(work), "main"], cwd=str(bare), check=True, capture_output=True, env=env
    )
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
    
    # Verify the worktree entry exists
    entry_dir = bare / "worktrees" / "work"
    print(f"Entry dir exists: {entry_dir.exists()}")
    print(f"Entry gitdir content: {(entry_dir / 'gitdir').read_text()!r}")
    
    # Verify git sees it
    res = subprocess.run(
        ["git", "-C", str(bare), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, env=env
    )
    print(f"Before mutation: git works={res.returncode == 0}")
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            print(f"  {line}")
    
    # ATTACK: Delete HEAD from the bare repo
    (bare / "HEAD").unlink()
    
    # Verify git fails
    res2 = subprocess.run(
        ["git", "-C", str(bare), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, env=env
    )
    print(f"\nAfter HEAD deletion: git returncode={res2.returncode}")
    print(f"  stderr: {res2.stderr.strip()}")
    
    # Check: are the worktree entries still on disk?
    print(f"\n  worktrees/ dir still exists: {(bare / 'worktrees').exists()}")
    print(f"  worktrees/work/ dir still exists: {entry_dir.exists()}")
    print(f"  worktrees/work/gitdir still exists: {(entry_dir / 'gitdir').exists()}")
    gitdir_content = (entry_dir / "gitdir").read_text()
    print(f"  worktrees/work/gitdir content: {gitdir_content!r}")
    
    # Now run the cleaner
    from agy_mcp.cleanup_jobs import _check_worktree_status, run_cleanup
    
    is_reg, reason = _check_worktree_status(job_dir, {"repo_path": str(bare)})
    print(f"\ncleaner: is_registered={is_reg}, reason={reason}")
    
    result = run_cleanup(store, project=str(bare), dry_run=True, older_than_days=0.0)
    cand_ids = [c["job_id"] for c in result.get("candidates", [])]
    is_candidate = job_id in cand_ids
    print(f"run_cleanup: is_candidate={is_candidate}")
    
    if is_candidate:
        print("\n*** CONFIRMED FAIL-OPEN: Bare repo HEAD deleted, git can't read repo,")
        print("    but worktree entries still on disk. Cleaner says deletable.")
        print("    Root cause: L176-179 in cleanup_jobs.py checks (repo_path / 'HEAD').is_file()")
        print("    and skips the entire bare repo worktree check when HEAD is not a file.")
        print("    Should instead check if worktrees/ dir exists and fail-closed.")
    
    # Also test --yes mode with a real store to ensure it would actually delete
    print("\n--- Testing with --yes against scratch store ---")
    
    # Make a copy for deletion test
    del_store = SCRATCH / "del_store"
    shutil.copytree(store, del_store)
    
    # Re-run cleanup with --yes
    del_result = run_cleanup(del_store, project=str(bare), dry_run=False, older_than_days=0.0)
    print(f"  deleted_ids: {del_result['deleted_ids']}")
    print(f"  deleted_count: {del_result['deleted_count']}")
    
    del_job_dir = del_store / "jobs" / job_id
    print(f"  job dir still exists after delete: {del_job_dir.exists()}")
    
    if not del_job_dir.exists():
        print("\n  *** CONFIRMED: Job record was actually deleted despite git not recognizing the repo. ***")

if __name__ == "__main__":
    attack()
