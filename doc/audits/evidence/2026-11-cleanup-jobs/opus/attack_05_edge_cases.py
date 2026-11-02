#!/usr/bin/env python3
"""Attack 5: Bare repo — HEAD absent but worktrees/ still present and valid.

Variant: git has both a HEAD file AND worktrees/ dir. We remove only HEAD.
The code at L176-179:
    if not git_dir.exists() and not git_dir.is_symlink():
        if (repo_path / "HEAD").is_file():
            candidate_wt_roots.append(repo_path / "worktrees")

If HEAD is absent → is_file() = False → candidate_wt_roots stays empty.
Then L326 loop has nothing → L439 return (False, None) → NOT registered.

Meanwhile the worktrees/ dir has a valid entry pointing at job_dir.
git -C barerepo worktree list fails because HEAD is missing.
This is FAIL-OPEN (git_works=False, cleaner says deletable).

Let's also check: what if repo_path has BOTH .git (not exists) and worktrees/?
This is the bare repo case. If HEAD is gone, the cleaner misses it.

Additionally, what about the case where:
- repo_path/.git exists as a directory (normal repo)
- repo_path/.git/HEAD IS present (passes R1 check)  
- repo_path/.git/worktrees/ IS present
- BUT worktrees/work/gitdir points to the right place
- AND we corrupt repo_path/.git in some OTHER way that makes git fail

Actually, the normal repo case handles HEAD checks. Let me focus on finding
more bare repo scenarios and also look at edge cases in the else branch L313-320.
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

SCRATCH = Path("~/agy-review/opus/scratch_05")

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
    
    # Normal repo with .git dir
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
    
    # Now the interesting test: .git IS a directory (normal case), HEAD is valid.
    # But what about .git being something else?
    
    print("Test: .git replaced with a FIFO (special file)")
    # Can't easily make .git a FIFO on a working repo. Let me instead test:
    # What if .git is a char device? Skip this — too exotic.
    
    # The interesting case: cleanup_jobs.py L313-320
    # else:
    #     return (True, "worktree registrations unreadable (.git is invalid: not a regular file or directory)")
    # This catches .git being a socket, FIFO, device, etc. Good — fail-closed.
    
    # But what about .git being a VALID symlink to a directory that contains HEAD?
    # L176: if not git_dir.exists() and not git_dir.is_symlink():
    #   → If .git is a symlink to existing dir, exists()=True, is_symlink()=True
    #   → Falls to L180: elif git_dir.is_symlink() and not git_dir.exists():
    #   → This is FALSE (symlink exists), so falls through
    #   → L189: elif git_dir.is_file(): FALSE (it's a dir via symlink)
    #   → L302: elif git_dir.is_dir(): TRUE! (resolves through symlink)
    #   → Then checks HEAD. So symlinked .git works. OK.
    
    # Now what about a symlink to a NON-directory, non-file (like a FIFO)?
    # L176: exists()=True, is_symlink()=True → skip
    # L180: is_symlink()=True, exists()=True → FALSE → skip
    # L189: is_file()=False → skip
    # L302: is_dir()=False → skip
    # L313: else: → return (True, "worktree registrations unreadable ...")
    # Good — fail-closed.
    
    # Now the REAL edge: what if the cleaner and git disagree about the repo_path?
    # The job.json says repo_path = X. The cleaner reads X/.git.
    # What if repo_path is valid but not a git repo AT ALL?
    
    # Test: repo_path points to a directory with no .git, no HEAD, no worktrees
    print("\nTest: repo_path is a plain directory (not a git repo)")
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    
    plain_dir = SCRATCH / "notarepo"
    plain_dir.mkdir()
    
    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)
    
    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir()
    
    job_data = {
        "job_id": job_id,
        "title": "attack job",
        "state": "done",
        "repo_path": str(plain_dir),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")
    
    from agy_mcp.cleanup_jobs import _check_worktree_status, run_cleanup
    
    is_reg, reason = _check_worktree_status(job_dir, {"repo_path": str(plain_dir)})
    print(f"  cleaner: is_registered={is_reg}, reason={reason}")
    
    result = run_cleanup(store, project=str(plain_dir), dry_run=True, older_than_days=0.0)
    cand_ids = [c["job_id"] for c in result.get("candidates", [])]
    print(f"  is_candidate={job_id in cand_ids}")
    # This is OK — plain dir, no worktree, cleaner says deletable. Fair.
    
    # Now: what if plain_dir has a worktrees/ directory with entries?
    # (Someone manually created it — unusual but possible)
    print("\nTest: repo_path is NOT a git repo but has worktrees/ dir with entry")
    wt_dir = plain_dir / "worktrees"
    wt_dir.mkdir()
    entry = wt_dir / "work"
    entry.mkdir()
    (entry / "gitdir").write_text(str(job_dir / "work" / ".git") + "\n", encoding="utf-8")
    
    is_reg2, reason2 = _check_worktree_status(job_dir, {"repo_path": str(plain_dir)})
    print(f"  cleaner: is_registered={is_reg2}, reason={reason2}")
    # No HEAD → is_file() = False → candidate_wt_roots stays empty → not checked
    
    # But what about adding a HEAD file?
    (plain_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    
    is_reg3, reason3 = _check_worktree_status(job_dir, {"repo_path": str(plain_dir)})
    print(f"  With HEAD file: is_registered={is_reg3}, reason={reason3}")
    
    if not is_reg2:
        print("\n  → Without HEAD: cleaner misses the worktree entry in a 'bare-like' directory.")
        print("    This is the same root cause as the bare repo HEAD corruption.")

if __name__ == "__main__":
    attack()
