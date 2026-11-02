#!/usr/bin/env python3
"""Test Area 14: --job and --project mode with odd ids and paths."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area14")

def setup_store():
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)
    
    # Valid job
    job_id = "00000000-0000-0000-0000-000000000001"
    jdir = store_jobs / job_id
    jdir.mkdir()
    jdata = {
        "job_id": job_id,
        "title": "valid job",
        "state": "done",
        "repo_path": str(SCRATCH / "repo"),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (jdir / "job.json").write_text(json.dumps(jdata))
    return store, job_id

def run_cli(*args):
    cmd = [
        sys.executable,
        "-m", "agy_mcp.cleanup_jobs",
    ] + list(args)
    env = get_env(SCRATCH)
    env["PYTHONPATH"] = str(REPO_SRC)
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return res.returncode, res.stdout, res.stderr

print("Area 14: Odd IDs and Paths")

store, valid_id = setup_store()

odd_job_cases = [
    ("empty string", ""),
    ("uppercase UUID", "00000000-0000-0000-0000-00000000000A"),
    ("whitespace UUID", " 00000000-0000-0000-0000-000000000001 "),
    ("path traversal ..", "../jobs"),
    ("slash in ID", "00000000/0000-0000-0000-000000000001"),
    ("nonexistent UUID", "11111111-1111-1111-1111-111111111111"),
]

for name, odd_id in odd_job_cases:
    rc, out, err = run_cli("--store", str(store), "--job", odd_id)
    print(f"  --job ({name}): rc={rc}, err={err.strip()[:80]}")
    if rc != 2:
        print(f"    UNEXPECTED RC: expected 2, got {rc}")
        record_finding(
            repro_script=f"test_area14_odd_ids.py ({name})",
            command=f"python -m agy_mcp.cleanup_jobs --store ... --job '{odd_id}'",
            cleaner_said=f"rc={rc}, out={out}, err={err}",
            git_said="N/A",
            severity="WRONG-EXIT-CODE",
            details=f"Expected exit code 2 for odd job id '{name}'"
        )

# Test duplicate job ids: valid_id twice
rc, out, err = run_cli("--store", str(store), "--job", valid_id, "--job", valid_id)
print(f"  --job (duplicate valid_id dry-run): rc={rc}, err={err.strip()[:80]}")
if rc != 0:
    print(f"    UNEXPECTED RC for duplicate valid_id dry-run: {rc}")

odd_project_cases = [
    ("empty string", ""),
    ("whitespace", "   "),
    ("nonexistent path", str(SCRATCH / "nonexistent")),
    ("file instead of dir", str(store / "jobs" / valid_id / "job.json")),
    ("trailing slashes", str(SCRATCH / "repo") + "///"),
    ("dot components", str(SCRATCH / "repo" / "." / ".." / "repo")),
]

for name, odd_proj in odd_project_cases:
    rc, out, err = run_cli("--store", str(store), "--project", odd_proj)
    print(f"  --project ({name}): rc={rc}, err={err.strip()[:80]}")
    if rc != 0:
        print(f"    UNEXPECTED RC for project dry-run: {rc}")

