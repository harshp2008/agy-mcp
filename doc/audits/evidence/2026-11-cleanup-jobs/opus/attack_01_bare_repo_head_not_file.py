#!/usr/bin/env python3
"""Attack 1: bare repo where HEAD is a directory, not a file.

Theory: If repo_path has no .git, and (repo_path / "HEAD") is NOT a file
(e.g., it's a directory), the code at cleanup_jobs.py L176-179 will NOT add
repo_path/"worktrees" to candidate_wt_roots. The code then falls through to
the candidate_wt_roots loop (L326) which has nothing to iterate, and returns
(False, None) at L439 — marking the worktree as NOT registered.

If git still considers the worktree registered (which it does, because the
bare repo is valid and has the worktree entry), this is FAIL-OPEN.

However, note: HEAD is fundamental to git; if HEAD is replaced by a directory,
git itself likely won't work. So git_works=False and FAIL-OPEN is if the
cleaner says deletable when git can't read the repo.

Actually, let me think more carefully. The attack is:
1. Create a bare repo with a worktree pointing into our job dir.
2. REPLACE repo's HEAD with a directory.
3. Check: does git -C <repo> worktree list work? (Probably not → git_works=False)
4. Check: does _check_worktree_status return (False, None)?

If cleaner says (False, None) → is_candidate=True, and git_works=False,
that IS FAIL-OPEN per the harness definition.

But wait - there's another check first: work_dir/.git exists check (lines 136-146).
If we make work/.git NOT exist, we bypass that. And worktree_path is checked too
(lines 148-161). Let's set worktree_path = None in job.json.

The key question: what does the cleaner do when the repo is a bare repo
and HEAD has been corrupted to not be a file?
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_SRC = Path("~/Documents/GITHUB PROJECTS/agy-mcp/src")
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

SCRATCH = Path("~/agy-review/opus/scratch_01")

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

def setup():
    if SCRATCH.exists():
        import shutil
        # Fix permissions first
        for root, dirs, files in os.walk(SCRATCH, topdown=False):
            for f in files:
                try: os.chmod(Path(root)/f, 0o600)
                except: pass
            for d in dirs:
                try: os.chmod(Path(root)/d, 0o700)
                except: pass
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    
    env = get_safe_git_env(SCRATCH)
    
    # Create a bare repo
    bare_repo = SCRATCH / "bare.git"
    subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True, env=env)
    
    # Create an initial commit
    tree = subprocess.run(
        ["git", "write-tree"], cwd=str(bare_repo), check=True, capture_output=True, text=True, env=env
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", "init"], cwd=str(bare_repo), check=True, capture_output=True, text=True, env=env
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/heads/main", commit], cwd=str(bare_repo), check=True, capture_output=True, env=env
    )
    
    # Create store and job
    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)
    
    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir()
    work = job_dir / "work"
    
    # Create worktree
    subprocess.run(
        ["git", "worktree", "add", str(work), "main"], cwd=str(bare_repo), check=True, capture_output=True, env=env
    )
    
    # Verify git sees it
    res = subprocess.run(
        ["git", "-C", str(bare_repo), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, env=env
    )
    print(f"Before mutation, git worktree list:\n{res.stdout}")
    
    # Remove work/.git (so the first check in _check_worktree_status passes through)
    (work / ".git").unlink()
    
    # Write job.json
    job_data = {
        "job_id": job_id,
        "title": "attack job",
        "state": "done",
        "repo_path": str(bare_repo),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")
    
    return bare_repo, store, job_dir, job_id, work, env

def attack():
    bare_repo, store, job_dir, job_id, work, env = setup()
    
    # MUTATION: replace HEAD with a directory
    head_file = bare_repo / "HEAD"
    head_file.unlink()
    head_file.mkdir()
    (head_file / "dummy").write_text("x\n")
    
    # Check git ground truth
    res = subprocess.run(
        ["git", "-C", str(bare_repo), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, env=env
    )
    git_works = res.returncode == 0
    print(f"After mutation: git returncode={res.returncode}")
    print(f"  stdout: {res.stdout}")
    print(f"  stderr: {res.stderr}")
    
    # Check git worktree list
    work_resolved = str(work.resolve(strict=False))
    found = False
    if git_works:
        for line in res.stdout.splitlines():
            if line.startswith("worktree "):
                wt = line[len("worktree "):].strip()
                if str(Path(wt).resolve(strict=False)) == work_resolved:
                    found = True
    
    print(f"git_works={git_works}, git_registered={found}")
    
    # Check cleaner
    from agy_mcp.cleanup_jobs import _check_worktree_status, run_cleanup
    
    is_reg, reason = _check_worktree_status(job_dir, {"repo_path": str(bare_repo)})
    print(f"cleaner: is_registered={is_reg}, reason={reason}")
    
    try:
        result = run_cleanup(store, project=str(bare_repo), dry_run=True, older_than_days=0.0)
        cand_ids = [c["job_id"] for c in result.get("candidates", [])]
        is_candidate = job_id in cand_ids
        print(f"run_cleanup: is_candidate={is_candidate}")
        print(f"  candidates: {cand_ids}")
        kept = result.get("kept", [])
        for k in kept:
            if k["job_id"] == job_id:
                print(f"  kept reasons: {k['reasons']}")
    except Exception as exc:
        print(f"run_cleanup CRASHED: {exc}")
        is_candidate = False
    
    # Verdict
    if is_candidate and (not git_works or found):
        print("\n*** FAIL-OPEN: cleaner says deletable, but git can't read repo or still lists worktree ***")
    elif is_candidate and git_works and not found:
        print("\n--- OK: cleaner says deletable, git agrees worktree is not registered ---")
    else:
        print("\n--- OK: cleaner says kept (fail-closed or correctly blocked) ---")

if __name__ == "__main__":
    attack()
