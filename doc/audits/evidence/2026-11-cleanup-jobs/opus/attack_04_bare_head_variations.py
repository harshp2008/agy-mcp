#!/usr/bin/env python3
"""Attack 4: Variations of the bare repo HEAD corruption.

Attack 1 confirmed: bare repo + HEAD replaced with directory = FAIL-OPEN.
Now testing more bare repo HEAD variations to see if the pattern is broader:
- HEAD is a symlink (valid, dangling)
- HEAD is a FIFO/pipe
- HEAD is a device node (skip if not root)
- HEAD is a socket
- HEAD missing entirely
- HEAD is a broken symlink
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

SCRATCH = Path("~/agy-review/opus/scratch_04")

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

def make_bare_repo_with_worktree():
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    env = get_safe_git_env(SCRATCH)
    
    bare_repo = SCRATCH / "bare.git"
    subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True, env=env)
    tree = subprocess.run(
        ["git", "write-tree"], cwd=str(bare_repo), check=True, capture_output=True, text=True, env=env
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", "init"], cwd=str(bare_repo), check=True, capture_output=True, text=True, env=env
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/heads/main", commit], cwd=str(bare_repo), check=True, capture_output=True, env=env
    )
    
    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)
    
    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir()
    work = job_dir / "work"
    
    subprocess.run(
        ["git", "worktree", "add", str(work), "main"], cwd=str(bare_repo), check=True, capture_output=True, env=env
    )
    (work / ".git").unlink()
    
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

def test_mutation(name, mutate_fn):
    bare_repo, store, job_dir, job_id, work, env = make_bare_repo_with_worktree()
    
    mutate_fn(bare_repo)
    
    res = subprocess.run(
        ["git", "-C", str(bare_repo), "worktree", "list", "--porcelain"],
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
    
    from agy_mcp.cleanup_jobs import _check_worktree_status, run_cleanup
    
    is_reg, reason = _check_worktree_status(job_dir, {"repo_path": str(bare_repo)})
    
    try:
        result = run_cleanup(store, project=str(bare_repo), dry_run=True, older_than_days=0.0)
        cand_ids = [c["job_id"] for c in result.get("candidates", [])]
        is_candidate = job_id in cand_ids
        crash = False
    except Exception as exc:
        print(f"  CRASH: {exc}")
        is_candidate = False
        crash = True
    
    verdict = ""
    if crash:
        verdict = "CRASH"
    elif is_candidate and (not git_works or found):
        verdict = "FAIL-OPEN"
    elif is_candidate:
        verdict = "OK"
    else:
        verdict = "OK (fail-closed)"
    
    print(f"  {name}: git_works={git_works}, git_reg={found}, "
          f"cleaner_reg={is_reg}, candidate={is_candidate} → {verdict}")

def attack():
    print("Attack 4: Bare repo HEAD variations")
    
    mutations = {
        "HEAD → directory": lambda r: ((r/"HEAD").unlink(), (r/"HEAD").mkdir()),
        "HEAD → dangling symlink": lambda r: ((r/"HEAD").unlink(), (r/"HEAD").symlink_to("nonexistent")),
        "HEAD → symlink to valid file": lambda r: ((r/"HEAD").rename(r/"HEAD.bak"), (r/"HEAD").symlink_to("HEAD.bak")),
        "HEAD deleted": lambda r: (r/"HEAD").unlink(),
        "HEAD → empty file": lambda r: (r/"HEAD").write_bytes(b""),
        "HEAD → FIFO": lambda r: ((r/"HEAD").unlink(), os.mkfifo(r/"HEAD")),
        "HEAD chmod 000": lambda r: os.chmod(r/"HEAD", 0o000),
        "HEAD → file with BOM": lambda r: (r/"HEAD").write_bytes(b"\xef\xbb\xbf" + (r/"HEAD").read_bytes()),
        "HEAD → file with NUL": lambda r: (r/"HEAD").write_bytes((r/"HEAD").read_bytes()[:5] + b"\x00"),
    }
    
    for name, fn in mutations.items():
        if name == "HEAD → FIFO":
            # FIFO could hang, skip
            print(f"  {name}: SKIPPED (could hang)")
            continue
        test_mutation(name, fn)

if __name__ == "__main__":
    attack()
