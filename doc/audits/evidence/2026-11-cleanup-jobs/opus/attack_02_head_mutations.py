#!/usr/bin/env python3
"""Attack 2: Normal repo where .git/HEAD is deleted or replaced.

Theory: For a normal git repository (.git is a directory), cleanup_jobs.py L302-312:
  elif git_dir.is_dir():
      if not (git_dir / "HEAD").is_file():
          return (True, "worktree registrations unreadable ...")
      candidate_wt_roots.append(git_dir / "worktrees")

So if .git/HEAD is missing, it should fail-closed (return True).
But what about the normal (non-bare) case where .git/HEAD is deleted?
Let's test: deleting HEAD, replacing it with symlink, directory, etc.
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

SCRATCH = Path("~/agy-review/opus/scratch_02")

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

def setup():
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
    
    # Remove work/.git to bypass the trivial check
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

def test_mutation(mutation_name, mutate_fn):
    repo, store, job_dir, job_id, work, env = setup()
    
    mutate_fn(repo)
    
    # Check git
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
    
    # Check cleaner
    from agy_mcp.cleanup_jobs import _check_worktree_status, run_cleanup
    
    is_reg, reason = _check_worktree_status(job_dir, {"repo_path": str(repo)})
    
    try:
        result = run_cleanup(store, project=str(repo), dry_run=True, older_than_days=0.0)
        cand_ids = [c["job_id"] for c in result.get("candidates", [])]
        is_candidate = job_id in cand_ids
    except Exception as exc:
        print(f"  CRASH: {exc}")
        is_candidate = False
    
    verdict = ""
    if is_candidate and (not git_works or found):
        verdict = "FAIL-OPEN"
    elif not is_reg and not is_candidate:
        # Hmm, cleaner says not registered but run_cleanup didn't mark as candidate?
        # This could happen if some other gate stopped it
        verdict = "OK (other gate stopped it)"
    elif is_reg:
        verdict = "OK (fail-closed)"
    else:
        verdict = "OK"
    
    print(f"  {mutation_name}: git_works={git_works}, git_reg={found}, cleaner_reg={is_reg}, "
          f"is_candidate={is_candidate}, reason={reason} → {verdict}")
    
    return verdict

def attack():
    mutations = {
        "HEAD deleted": lambda repo: (repo / ".git" / "HEAD").unlink(),
        "HEAD replaced with dir": lambda repo: ((repo / ".git" / "HEAD").unlink(), (repo / ".git" / "HEAD").mkdir()),
        "HEAD replaced with symlink to nonexistent": lambda repo: ((repo / ".git" / "HEAD").unlink(), (repo / ".git" / "HEAD").symlink_to("nonexistent")),
        "HEAD replaced with empty file": lambda repo: (repo / ".git" / "HEAD").write_text(""),
        "HEAD with BOM": lambda repo: (repo / ".git" / "HEAD").write_bytes(b"\xef\xbb\xbf" + (repo / ".git" / "HEAD").read_bytes()),
        ".git/worktrees deleted": lambda repo: shutil.rmtree(repo / ".git" / "worktrees"),
        ".git/worktrees replaced with file": lambda repo: (shutil.rmtree(repo / ".git" / "worktrees"), (repo / ".git" / "worktrees").write_text("file\n")),
        ".git dir chmod 000": lambda repo: os.chmod(repo / ".git", 0o000),
    }
    
    print("Attack 2: Normal repo HEAD/structure mutations")
    for name, fn in mutations.items():
        test_mutation(name, fn)

if __name__ == "__main__":
    attack()
