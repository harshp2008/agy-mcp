import json
import os
import shutil
import sys
from pathlib import Path

store_dir = Path(os.path.expanduser("~/agy-review/gemini4/store_depth_bomb"))
if store_dir.exists():
    shutil.rmtree(store_dir)
jobs_dir = store_dir / "jobs"
jobs_dir.mkdir(parents=True, exist_ok=True)

cand_id = "11111111-1111-1111-1111-111111111111"
kept_id = "22222222-2222-2222-2222-222222222222"

(jobs_dir / cand_id).mkdir(exist_ok=True)
(jobs_dir / kept_id).mkdir(exist_ok=True)

# Candidate with nested JSON (5KB total size, well below 1 MiB limit)
depth = 1100
bomb = "{\"k\":" * depth + "1" + "}" * depth
cand_json = json.loads(bomb)
cand_json["job_id"] = cand_id
cand_json["state"] = "done"
cand_json["repo_path"] = "/tmp/repo"
cand_json["created_at"] = "2020-01-01T00:00:00+00:00"

(jobs_dir / cand_id / "job.json").write_text(json.dumps(cand_json), encoding="utf-8")

# Kept job that references candidate ID, triggering candidate transition to kept status
kept_json = {
    "job_id": kept_id,
    "state": "running",
    "repo_path": "/tmp/repo",
    "created_at": "2020-01-01T00:00:00+00:00",
    "ref": cand_id
}
(jobs_dir / kept_id / "job.json").write_text(json.dumps(kept_json), encoding="utf-8")
print(f"Store prepared at {store_dir}")
