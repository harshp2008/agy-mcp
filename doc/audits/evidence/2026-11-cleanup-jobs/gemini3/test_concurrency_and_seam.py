#!/usr/bin/env python3
"""Adversarial test for:
1. Two cleaners running concurrently on the same scratch store with --yes.
2. before_delete seam applying mutations between classify and delete.
"""

import concurrent.futures
import json
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path("~/Documents/GITHUB PROJECTS/agy-mcp")
PYTHON_EXE = REPO_ROOT / ".venv" / "bin" / "python"
SCRATCH = Path("~/agy-review/gemini3/scratch_concurrency")

from agy_mcp.cleanup_jobs import run_cleanup

def reset_scratch():
    if SCRATCH.exists():
        for root, dirs, files in os.walk(SCRATCH, topdown=False):
            for f in files:
                try: os.chmod(Path(root) / f, 0o600); (Path(root) / f).unlink()
                except OSError: pass
            for d in dirs:
                try: os.chmod(Path(root) / d, 0o700); (Path(root) / d).rmdir()
                except OSError: pass
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Test 1: Concurrent cleaners on same store with --yes
# --------------------------------------------------------------------------- #
def test_concurrent_cleaners():
    print("\n=== Running Test 1: Concurrent cleaners with --yes ===")
    reset_scratch()
    env = os.environ.copy()
    env["HOME"] = str(SCRATCH)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"

    repo = SCRATCH / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init", "-q"], env=env, check=True)

    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)

    # Create 20 eligible candidate jobs
    job_ids = []
    now = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    for _ in range(20):
        jid = str(uuid.uuid4())
        job_ids.append(jid)
        jdir = store_jobs / jid
        jdir.mkdir()
        (jdir / "job.json").write_text(json.dumps({
            "job_id": jid,
            "repo_path": str(repo),
            "state": "done",
            "created_at": now,
            "updated_at": now,
        }), encoding="utf-8")

    cmd = [
        str(PYTHON_EXE), "-m", "agy_mcp.cleanup_jobs",
        "--store", str(store),
        "--project", str(repo),
        "--older-than-days", "0",
        "--yes",
        "--json"
    ]

    # Run two cleaners concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(subprocess.run, cmd, capture_output=True, text=True, env=env)
        f2 = executor.submit(subprocess.run, cmd, capture_output=True, text=True, env=env)
        res1 = f1.result()
        res2 = f2.result()

    print(f"Cleaner 1 rc: {res1.returncode}, stderr: {res1.stderr.strip()[:100]}")
    print(f"Cleaner 2 rc: {res2.returncode}, stderr: {res2.stderr.strip()[:100]}")

    crashed = False
    if res1.returncode not in (0, 1, 2) or "Traceback" in res1.stderr:
        print("[!] Cleaner 1 CRASHED")
        crashed = True
    if res2.returncode not in (0, 1, 2) or "Traceback" in res2.stderr:
        print("[!] Cleaner 2 CRASHED")
        crashed = True

    # Check remaining jobs on disk
    remaining = [p.name for p in store_jobs.iterdir() if not p.name.startswith(".cleanup-manifest-")]
    print(f"Remaining jobs in store: {len(remaining)} (expected 0)")

    if not crashed and len(remaining) == 0:
        print("Concurrent cleaners test PASSED (no crash, safe completion)")
    else:
        print(f"Concurrent cleaners test FAILED: crashed={crashed}, remaining={len(remaining)}")

# --------------------------------------------------------------------------- #
# Test 2: before_delete seam mutations
# --------------------------------------------------------------------------- #
def test_before_delete_seam():
    print("\n=== Running Test 2: before_delete seam mutations ===")
    reset_scratch()
    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)
    repo = SCRATCH / "repo"
    repo.mkdir(parents=True)

    rng = random.Random(42)
    mutations_tested = [
        "corrupt_json",
        "delete_job_dir",
        "symlink_job_dir",
        "change_to_running",
        "change_repo_path",
        "add_work_git",
        "fifo_job_json",
        "chmod_000_job_dir",
    ]

    for m in mutations_tested:
        jid = str(uuid.uuid4())
        jdir = store_jobs / jid
        jdir.mkdir(exist_ok=True)
        now = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        (jdir / "job.json").write_text(json.dumps({
            "job_id": jid,
            "repo_path": str(repo),
            "state": "done",
            "created_at": now,
            "updated_at": now,
        }), encoding="utf-8")

        def make_seam(mutation_name):
            def seam(target_id: str):
                target_dir = store_jobs / target_id
                target_json = target_dir / "job.json"
                if mutation_name == "corrupt_json":
                    target_json.write_text("{invalid json", encoding="utf-8")
                elif mutation_name == "delete_job_dir":
                    shutil.rmtree(target_dir)
                elif mutation_name == "symlink_job_dir":
                    shutil.rmtree(target_dir)
                    target_dir.symlink_to(Path("/dev/null"))
                elif mutation_name == "change_to_running":
                    target_json.write_text(json.dumps({
                        "job_id": target_id,
                        "repo_path": str(repo),
                        "state": "running",
                        "created_at": now,
                        "updated_at": now,
                    }), encoding="utf-8")
                elif mutation_name == "change_repo_path":
                    target_json.write_text(json.dumps({
                        "job_id": target_id,
                        "repo_path": str(SCRATCH / "other_repo"),
                        "state": "done",
                        "created_at": now,
                        "updated_at": now,
                    }), encoding="utf-8")
                elif mutation_name == "add_work_git":
                    (target_dir / "work" / ".git").mkdir(parents=True, exist_ok=True)
                elif mutation_name == "fifo_job_json":
                    target_json.unlink()
                    os.mkfifo(target_json)
                elif mutation_name == "chmod_000_job_dir":
                    os.chmod(target_dir, 0o000)
            return seam

        print(f"Testing before_delete seam with mutation: {m}...")
        try:
            report = run_cleanup(
                store,
                project=str(repo),
                older_than_days=0,
                dry_run=False,
                before_delete=make_seam(m),
            )
            # Must not be in deleted_ids
            assert jid not in report["deleted_ids"], f"Mutated job {jid} was deleted under {m}!"
            assert jid in report["failed_ids"], f"Mutated job {jid} was not reported in failed_ids under {m}!"
            print(f"  [PASS] {m}: correctly retained/failed (failed_ids={report['failed_ids']})")
        except Exception as exc:
            print(f"  [CRASH] {m} raised exception: {exc}")
        finally:
            try:
                os.chmod(jdir, 0o700)
                shutil.rmtree(jdir, ignore_errors=True)
            except Exception:
                pass

if __name__ == "__main__":
    test_concurrent_cleaners()
    test_before_delete_seam()
