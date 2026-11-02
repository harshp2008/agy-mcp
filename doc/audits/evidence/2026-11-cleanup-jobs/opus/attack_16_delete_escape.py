#!/usr/bin/env python3
"""Attack 16: Does _safe_delete_job_dir actually confine itself to the job dir?

The deletion function at cleanup_fixtures.py L336-363 does:
1. Checks job_dir.is_symlink() → refuse
2. Checks job_dir.resolve() == expected_root → refuse if different
3. Walks bottom-up, for each file/dir:
   - Checks is_symlink() → refuse
   - Checks resolve() is under expected_root → refuse if not
   - Then unlinks/rmdirs

The question: if a file INSIDE the job dir has been replaced with a
symlink to something outside (e.g., to $HOME/.bashrc), does the walker
catch it?

Actually, the walker is in _safe_delete_job_dir in cleanup_fixtures.py.
But wait — _has_symlinks() is checked BEFORE classification (L470-471
in cleanup_jobs.py). If any symlink exists inside, the job is KEPT.

But there's a TOCTOU: _has_symlinks() runs during classification, then
_safe_delete_job_dir runs during deletion. If a symlink is created between
those two calls...

Actually, _safe_delete_job_dir itself checks for symlinks during the walk.
So even if one is created between classify and delete, the delete will
catch it and refuse. Good.

Let me verify this defense works.
"""
import json
import os
import shutil
import sys
sys.path.insert(0, "~/Documents/GITHUB PROJECTS/agy-mcp/src")
from pathlib import Path
from agy_mcp.cleanup_jobs import run_cleanup
from agy_mcp.cleanup_fixtures import _safe_delete_job_dir

SCRATCH = Path("~/agy-review/opus/scratch_16")
if SCRATCH.exists():
    for root, dirs, files in os.walk(SCRATCH, topdown=False):
        for f in files:
            try: os.chmod(Path(root)/f, 0o600)
            except: pass
        for d in dirs:
            try: os.chmod(Path(root)/d, 0o700)
            except: pass
    shutil.rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)

print("Attack 16: _safe_delete_job_dir symlink escape defense")

store = SCRATCH / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True)

job_id = "11111111-1111-1111-1111-111111111111"
job_dir = store_jobs / job_id
job_dir.mkdir()

# Create a target file that SHOULD NOT be deleted
safe_target = SCRATCH / "precious_file.txt"
safe_target.write_text("DO NOT DELETE\n")

# Create files inside job dir, one of which is a symlink to the safe target
(job_dir / "normal_file.txt").write_text("normal\n")
(job_dir / "evil_symlink").symlink_to(str(safe_target))

print(f"  Before: precious_file exists = {safe_target.exists()}")
print(f"  Before: evil_symlink target = {os.readlink(job_dir / 'evil_symlink')}")

try:
    _safe_delete_job_dir(job_dir, job_id, store_jobs)
    print("  _safe_delete_job_dir: COMPLETED (unexpected!)")
    print(f"  After: precious_file exists = {safe_target.exists()}")
except RuntimeError as e:
    print(f"  _safe_delete_job_dir: REFUSED: {e}")
    print(f"  After: precious_file exists = {safe_target.exists()}")
    print("  --- OK: symlink escape blocked ---")

# Test 2: TOCTOU - before_delete creates symlink
print("\nAttack 16b: TOCTOU - before_delete creates symlink inside job dir")
if SCRATCH.exists():
    for root, dirs, files in os.walk(SCRATCH, topdown=False):
        for f in files:
            try: os.chmod(Path(root)/f, 0o600)
            except: pass
        for d in dirs:
            try: os.chmod(Path(root)/d, 0o700)
            except: pass
    shutil.rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)

store = SCRATCH / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True)

repo = SCRATCH / "repo"
repo.mkdir()
(repo / ".git").mkdir()
(repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

job_id = "11111111-1111-1111-1111-111111111111"
job_dir = store_jobs / job_id
job_dir.mkdir()
(job_dir / "normal.txt").write_text("normal\n")

safe_target = SCRATCH / "precious.txt"
safe_target.write_text("PRECIOUS\n")

(job_dir / "job.json").write_text(json.dumps({
    "job_id": job_id, "state": "done", "repo_path": str(repo),
    "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z",
}))

def create_symlink_toctou(jid):
    """Before delete callback: create a symlink to precious file."""
    target_dir = store_jobs / jid
    symlink_path = target_dir / "evil_link"
    if not symlink_path.exists():
        symlink_path.symlink_to(str(safe_target))

result = run_cleanup(store, project=str(repo), dry_run=False, older_than_days=0.0,
                     before_delete=create_symlink_toctou)
print(f"  deleted_ids: {result['deleted_ids']}")
print(f"  failed_ids: {result['failed_ids']}")
print(f"  precious_file exists: {safe_target.exists()}")
print(f"  precious_file content: {safe_target.read_text()!r}")
print(f"  job_dir exists: {job_dir.exists()}")

if safe_target.exists() and safe_target.read_text() == "PRECIOUS\n":
    print("  --- OK: precious file was NOT deleted ---")
else:
    print("  *** OUT-OF-SCOPE-DELETE: precious file was deleted or modified! ***")

if job_id in result["failed_ids"]:
    print("  --- Deletion was blocked by re-check (_has_symlinks) ---")
elif job_id in result["deleted_ids"]:
    print("  Note: job was deleted, but precious file was preserved by _safe_delete_job_dir")
