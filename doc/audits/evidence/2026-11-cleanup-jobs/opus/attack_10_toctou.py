#!/usr/bin/env python3
"""Attack 10: TOCTOU through the before_delete seam.

Theory: Between classify (dry-run phase) and delete (--yes phase),
a worktree could be added. The code re-checks classification before
each delete (line 821-831), but does the re-check fully re-verify
worktree status? Let's trace the code:

Line 821: is_cand, reasons, _, _ = _classify_job_for_cleanup(...)
Inside _classify_job_for_cleanup (line 501):
    is_wt, wt_reason = _check_worktree_status(child, data)
    if is_wt: return False, ...

So YES, the re-check does re-verify worktree status. But there's a
window between the re-check (line 821) and the actual delete (line 900).
If a worktree is created in that window, the delete proceeds.

The before_delete callback fires at line 812, BEFORE the re-check at 821.
So we can't use before_delete to create a worktree AFTER the re-check.

But what about between the _classify_job_for_cleanup re-check (line 821)
and the _safe_delete_job_dir (line 900)? There are:
- project re-check (line 834-870)
- dynamic edge protection (line 876-896)
- then delete (line 900)

If a worktree is created between line 821 and line 900, it would be missed.
This is a genuine TOCTOU window, but it requires another process to create
the worktree in that exact window.

Let's instead test: what if the before_delete callback creates work/.git?
That would mean the job now has a worktree, but the re-check at line 821
runs AFTER before_delete (line 812). So the re-check SHOULD catch it.

Actually wait - let me re-read:
Line 810: for idx, cid in enumerate(planned_ids):
Line 811:     if before_delete is not None:
Line 812:         before_delete(cid)
Line 814:     c_dir = store_jobs / cid
...
Line 821:     is_cand, reasons, _, _ = _classify_job_for_cleanup(...)

So before_delete fires, THEN re-check happens. If before_delete creates
work/.git, the re-check will see it and skip. Good.

But what if before_delete modifies a DIFFERENT job's data to reference
this candidate? The edge protection re-check at line 876-896 should
catch it. Let's verify.

Actually, the most interesting TOCTOU: what if between the dynamic edge
protection check (line 876-896) and the actual delete (line 900), a new
job appears that references this candidate? This is a real TOCTOU but
requires filesystem-level race condition. Can't easily repro.

Let me instead focus on testing the before_delete seam behavior.
"""
import json
import os
import subprocess
import sys
sys.path.insert(0, "~/agy-review/opus")
sys.path.insert(0, "~/Documents/GITHUB PROJECTS/agy-mcp/src")
from pathlib import Path
from harness import safe_rmtree, get_env
from agy_mcp.cleanup_jobs import run_cleanup

SCRATCH = Path("~/agy-review/opus/scratch_10")

print("Attack 10: TOCTOU - before_delete creates work/.git")

safe_rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)
env = get_env(SCRATCH)

repo = SCRATCH / "repo"
subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=env)
subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                cwd=str(repo), check=True, capture_output=True, env=env)

store = SCRATCH / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True)

job_id = "00000000-0000-0000-0000-000000000001"
job_dir = store_jobs / job_id
job_dir.mkdir()
work = job_dir / "work"
work.mkdir(exist_ok=True)

job_data = {
    "job_id": job_id,
    "title": "attack job",
    "state": "done",
    "repo_path": str(repo),
    "created_at": "2020-01-01T00:00:00Z",
    "updated_at": "2020-01-01T00:00:00Z",
}
(job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")

# First verify it's a candidate in dry-run
result_dry = run_cleanup(store, project=str(repo), dry_run=True, older_than_days=0.0)
cand_ids = [c["job_id"] for c in result_dry.get("candidates", [])]
print(f"  Dry run: is_candidate={job_id in cand_ids}")

# Now run with --yes, but the before_delete callback creates work/.git
# This simulates a worktree being re-created between plan and execute
def create_worktree(jid):
    print(f"  before_delete({jid}): creating work/.git")
    work_dir = store_jobs / jid / "work"
    work_dir.mkdir(exist_ok=True)
    # Create a .git file (simulating worktree registration)
    (work_dir / ".git").write_text("gitdir: /tmp/fake\n", encoding="utf-8")

result = run_cleanup(store, project=str(repo), dry_run=False, older_than_days=0.0,
                     before_delete=create_worktree)
print(f"  Deleted: {result['deleted_ids']}")
print(f"  Failed: {result['failed_ids']}")
print(f"  Skipped: {result['skipped']}")
print(f"  Job dir exists: {job_dir.exists()}")

if job_id in result['deleted_ids']:
    print("\n  *** FAIL-OPEN: worktree created via before_delete was not caught! ***")
elif job_id in result['failed_ids']:
    print("\n  --- OK: before_delete worktree creation was caught by re-check ---")
else:
    print("\n  --- Unexpected result ---")

# Test 2: before_delete modifies ANOTHER kept job to reference this candidate
print("\nAttack 10b: before_delete modifies neighbor to reference candidate")
safe_rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)

repo2 = SCRATCH / "repo2"
subprocess.run(["git", "init", str(repo2)], check=True, capture_output=True, env=env)
subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                cwd=str(repo2), check=True, capture_output=True, env=env)

store2 = SCRATCH / "store2"
store_jobs2 = store2 / "jobs"
store_jobs2.mkdir(parents=True)

cand_id = "11111111-1111-1111-1111-111111111111"
cand_dir = store_jobs2 / cand_id
cand_dir.mkdir()
(cand_dir / "job.json").write_text(json.dumps({
    "job_id": cand_id, "state": "done", "repo_path": str(repo2),
    "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z",
}, indent=2), encoding="utf-8")

# Kept neighbor (running state)
kept_id = "22222222-2222-2222-2222-222222222222"
kept_dir = store_jobs2 / kept_id
kept_dir.mkdir()
(kept_dir / "job.json").write_text(json.dumps({
    "job_id": kept_id, "state": "running", "repo_path": str(repo2),
    "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z",
}, indent=2), encoding="utf-8")

def modify_neighbor(jid):
    print(f"  before_delete({jid}): adding candidate ID to neighbor's job.json")
    kept_data = json.loads((kept_dir / "job.json").read_text())
    kept_data["related_job"] = cand_id  # Now the kept job references the candidate
    (kept_dir / "job.json").write_text(json.dumps(kept_data, indent=2), encoding="utf-8")

result2 = run_cleanup(store2, project=str(repo2), dry_run=False, older_than_days=0.0,
                      before_delete=modify_neighbor)
print(f"  Deleted: {result2['deleted_ids']}")
print(f"  Failed: {result2['failed_ids']}")
print(f"  Skipped: {result2['skipped']}")

if cand_id in result2['deleted_ids']:
    print("\n  *** POTENTIAL ISSUE: candidate deleted despite neighbor now referencing it ***")
    print("  (But dynamic edge recheck at L876 re-reads all kept jobs, so it SHOULD catch this)")
elif cand_id in result2['failed_ids']:
    print("\n  --- OK: dynamic edge recheck caught the new reference ---")
