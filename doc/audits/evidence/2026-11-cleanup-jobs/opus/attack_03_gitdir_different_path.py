#!/usr/bin/env python3
"""Attack 3: gitdir pointing to a DIFFERENT valid repo.

Theory: If worktree entry's gitdir file points to a path that is a valid
job_dir or work_dir from a DIFFERENT job, could we fool the cleaner into
thinking THIS job's worktree is still registered when it's actually referring
to another job?

This is the OVER-BLOCK direction, which is fine. But the inverse is interesting:
What if a gitdir file in the worktrees/ directory points AWAY from the job_dir
(to some other directory), meaning the cleaner doesn't find a match and marks
the job as deletable, while git itself still considers the worktree registered?

Actually, the key attack: The gitdir is corrupted to point somewhere else.
Git still lists the worktree (because git reads its own copy of the entry).
But the cleaner reads the corrupted gitdir and doesn't match → FAIL-OPEN.

Wait, but the differential harness already tests mutations of gitdir files.
Let me check if they cover the case where gitdir is rewritten to point to
a different VALID path (not garbage, not truncated, not BOM — but a real path
that just doesn't match).
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

SCRATCH = Path("~/agy-review/opus/scratch_03")

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
    
    # Normal repo
    repo = SCRATCH / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(repo), check=True, capture_output=True, env=env
    )
    
    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)
    
    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir()
    work = job_dir / "work"
    
    subprocess.run(
        ["git", "worktree", "add", str(work), "-b", "wt_a"],
        cwd=str(repo), check=True, capture_output=True, env=env
    )
    
    # Remove work/.git
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
    
    # MUTATION: rewrite the gitdir entry to point to /tmp (a valid, existing path
    # that is NOT the job_dir)
    entry_dir = repo / ".git" / "worktrees" / "work"
    gitdir_file = entry_dir / "gitdir"
    
    print(f"Original gitdir content: {gitdir_file.read_text()!r}")
    
    # Point to a different directory entirely
    gitdir_file.write_text("/tmp\n", encoding="utf-8")
    
    # Check git ground truth
    res = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, env=env
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
                except: pass
    
    print(f"git_works={git_works}, git_registered={found}")
    print(f"  git output: {res.stdout.strip()}")
    
    # Check cleaner
    from agy_mcp.cleanup_jobs import _check_worktree_status, run_cleanup
    
    is_reg, reason = _check_worktree_status(job_dir, {"repo_path": str(repo)})
    print(f"cleaner: is_registered={is_reg}, reason={reason}")
    
    try:
        result = run_cleanup(store, project=str(repo), dry_run=True, older_than_days=0.0)
        cand_ids = [c["job_id"] for c in result.get("candidates", [])]
        is_candidate = job_id in cand_ids
        print(f"run_cleanup: is_candidate={is_candidate}")
    except Exception as exc:
        print(f"run_cleanup CRASHED: {exc}")
        is_candidate = False
    
    if is_candidate and (not git_works or found):
        print("\n*** FAIL-OPEN ***")
    elif is_candidate:
        print("\n--- OK: git doesn't see it as registered either ---")
    else:
        print("\n--- OK: fail-closed ---")

if __name__ == "__main__":
    attack()
