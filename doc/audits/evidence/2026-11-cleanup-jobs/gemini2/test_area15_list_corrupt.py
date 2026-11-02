#!/usr/bin/env python3
"""Test Area 15: --list on corrupt / edge stores."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area15")

def setup_store():
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)
    return store, store_jobs

def run_list(store: Path, json_mode: bool = False) -> tuple[int, str, str]:
    args = [
        sys.executable,
        "-m", "agy_mcp.cleanup_jobs",
        "--store", str(store),
        "--list",
    ]
    if json_mode:
        args.append("--json")
    env = get_env(SCRATCH)
    env["PYTHONPATH"] = str(REPO_SRC)
    res = subprocess.run(args, capture_output=True, text=True, env=env)
    return res.returncode, res.stdout, res.stderr

print("Area 15: --list testing")

# Case 1: Corrupt job.json in store
store, store_jobs = setup_store()
bad_job = store_jobs / "00000000-0000-0000-0000-000000000001"
bad_job.mkdir()
(bad_job / "job.json").write_text("{corrupt json", encoding="utf-8")

rc, out, err = run_list(store, json_mode=False)
print("Case 1 (human corrupt):", rc, "err:", err.strip(), "out:", out[:100].strip())
if rc != 0 or "Traceback" in err or "Traceback" in out:
    print("FAILED CASE 1")

rc_j, out_j, err_j = run_list(store, json_mode=True)
print("Case 1 (json corrupt):", rc_j, "err:", err_j.strip())
try:
    data = json.loads(out_j)
    print("  json warnings:", data.get("warnings"))
except Exception as e:
    print("  json parse failed:", e)

# Case 2: Oversized job.json
store, store_jobs = setup_store()
big_job = store_jobs / "00000000-0000-0000-0000-000000000002"
big_job.mkdir()
(big_job / "job.json").write_text('{"job_id": "' + "0"*1050000 + '"}')
rc, out, err = run_list(store, json_mode=True)
print("Case 2 (oversized):", rc, "warnings:", json.loads(out).get("warnings"))

# Case 3: Duplicate keys in job.json
store, store_jobs = setup_store()
dup_job = store_jobs / "00000000-0000-0000-0000-000000000003"
dup_job.mkdir()
(dup_job / "job.json").write_text('{"job_id": "00000000-0000-0000-0000-000000000003", "repo_path": "/a", "repo_path": "/b"}')
rc, out, err = run_list(store, json_mode=True)
print("Case 3 (duplicate keys):", rc, "warnings:", json.loads(out).get("warnings"))
