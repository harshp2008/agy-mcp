#!/usr/bin/env python3
"""Test Area 19: Running two cleaners at once on the same store."""
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area19")

def test_concurrent():
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    env = get_env(SCRATCH)
    env["PYTHONPATH"] = str(REPO_SRC)

    repo = SCRATCH / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True, capture_output=True, env=env)

    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)

    # Create 50 candidate jobs
    for i in range(50):
        jid = f"00000000-0000-0000-0000-{i:012d}"
        jdir = store_jobs / jid
        jdir.mkdir()
        # Add a dummy file
        (jdir / "data.txt").write_text("hello")
        jdata = {
            "job_id": jid,
            "title": f"job {i}",
            "state": "done",
            "repo_path": str(repo),
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2020-01-01T00:00:00Z",
        }
        (jdir / "job.json").write_text(json.dumps(jdata))

    results = []

    def worker(worker_id):
        cmd = [
            sys.executable,
            "-m", "agy_mcp.cleanup_jobs",
            "--store", str(store),
            "--project", str(repo),
            "--older-than-days", "0",
            "--yes",
            "--json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        results.append((worker_id, res.returncode, res.stdout, res.stderr))

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    for wid, rc, out, err in results:
        print(f"Worker {wid}: rc={rc}, err={err.strip()}")
        if "Traceback" in err or "Traceback" in out:
            print(f"  CRASH IN WORKER {wid}!")
            record_finding(
                repro_script="test_area19_concurrent_cleaners.py",
                command="concurrent python -m agy_mcp.cleanup_jobs ... --yes",
                cleaner_said=f"rc={rc}, err={err}",
                git_said="N/A",
                severity="CRASH",
                details="Concurrent cleaner execution crashed with traceback"
            )

    # Check remaining jobs
    remaining = [p.name for p in store_jobs.iterdir() if not p.name.startswith(".cleanup-manifest-")]
    print(f"Remaining jobs count: {len(remaining)}")

if __name__ == "__main__":
    test_concurrent()
