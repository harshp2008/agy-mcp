#!/usr/bin/env python3
"""Attack 8d: Deep investigation of job_id=wrong_case being CANDIDATE.

The test used base_id.upper() as job_id in the JSON, but the directory
name is still base_id (lowercase). The code at L489:
    if data.get("job_id") != c_name:
        return False, [...]

So if JSON has uppercase and dir is lowercase, it should be KEPT.
Unless the test setup is wrong. Let me re-check.
"""
import json
import sys
sys.path.insert(0, "~/Documents/GITHUB PROJECTS/agy-mcp/src")
from pathlib import Path
from agy_mcp.cleanup_jobs import run_cleanup

scratch = Path("~/agy-review/opus/scratch_08d")
if scratch.exists():
    import shutil
    shutil.rmtree(scratch)
scratch.mkdir(parents=True)

store = scratch / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True)

base_id = "11111111-1111-1111-1111-111111111111"
job_dir = store_jobs / base_id
job_dir.mkdir()

repo = scratch / "repo"
repo.mkdir()
(repo / ".git").mkdir()
(repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

# Job JSON has UPPERCASE job_id but dir name is lowercase
data = {
    "job_id": base_id.upper(),  # UPPERCASE
    "state": "done",
    "repo_path": str(repo),
    "created_at": "2020-01-01T00:00:00Z",
    "updated_at": "2020-01-01T00:00:00Z",
}
(job_dir / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
print(f"Dir name: {base_id}")
print(f"JSON job_id: {base_id.upper()}")
print(f"Match: {base_id == base_id.upper()}")

result = run_cleanup(store, project=str(repo), dry_run=True, older_than_days=0.0)
cand_ids = [c["job_id"] for c in result.get("candidates", [])]
kept_ids = [k["job_id"] for k in result.get("kept", [])]
print(f"Candidates: {cand_ids}")
print(f"Kept: {kept_ids}")
for k in result.get("kept", []):
    if k["job_id"] == base_id:
        print(f"  Reasons: {k['reasons']}")
