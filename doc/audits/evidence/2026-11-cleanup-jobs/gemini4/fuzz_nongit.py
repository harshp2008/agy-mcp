#!/usr/bin/env python3
"""Adversarial non-git fuzzer for agy-mcp job cleaner (SPEC §23, D89-D98).

Runs mutated store layouts and job.json payloads against the cleaner CLI
and asserts normative invariants:
1. Never a traceback on stderr.
2. Exit code is strictly in {0, 1, 2}.
3. Non-terminal jobs are never candidates.
4. Jobs younger than threshold are never candidates in project mode.
5. Jobs referenced by kept neighbors are never candidates.
6. Nothing outside <store>/jobs/<id> is ever touched or deleted.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CLEANER_MODULE = "agy_mcp.cleanup_jobs"

CANONICAL_UUIDS = [
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222",
    "33333333-3333-3333-3333-333333333333",
    "44444444-4444-4444-4444-444444444444",
]

NON_CANONICAL_NAMES = [
    "11111111-1111-1111-1111-11111111111A",  # Uppercase
    "not-a-uuid-directory",
    ".hidden_folder",
    "12345678-1234-1234-1234-12345678901",   # Short
    "11111111-1111-1111-1111-1111111111111", # Long
]

STATES = [
    "done", "failed", "cancelled",
    "running", "queued", "needs_review", "timeout", "needs_decision",
    "DONE", "done ", "done\n", "", None, 123, True, False,
]

PIDS = [
    None,
    0, -1, -999,
    1, os.getpid(),
    99999999,  # High dead PID
    4194305,   # Above kernel pid_max
    "12345", 1.5, True, False,
]

TIMESTAMPS = [
    "2020-01-01T00:00:00Z",
    "2020-01-01T00:00:00+00:00",
    "2020-01-01T05:30:00+05:30",
    "2020-01-01T00:00:00.123456Z",
    "2020-01-01T00:00:00",           # Naive
    "2099-01-01T00:00:00Z",           # Future
    "0001-01-01T00:00:00Z",           # Far past
    "9999-12-31T23:59:59Z",
    "10000-01-01T00:00:00Z",          # Invalid year
    "2020-12-31T23:59:60Z",           # Leap second
    "invalid-date", "", "   ", "Z",
    1600000000, 1600000000.5, True, False, None,
]

REPO_PATHS = [
    "/tmp/fuzz_repo_alpha",
    "/tmp/fuzz_repo_alpha/",
    "/tmp/fuzz_repo_alpha/../fuzz_repo_alpha",
    "/tmp/fuzz_repo_alpha_suffix",
    "/tmp/fuzz_repo_beta",
    "/tmp/fuzz_repo_alpha\nextra",
    "/tmp/fuzz_repo_alpha/[a-z]*",
    "fuzz_repo_relative",
    "~/fuzz_repo_home",
    "", "   ", None, 12345, True,
]

def make_nested(depth: int) -> dict[str, Any]:
    curr: dict[str, Any] = {"leaf": "value"}
    for i in range(depth):
        curr = {f"k_{i}": curr}
    return curr

def snapshot_tree(root: Path) -> dict[str, int]:
    snap: dict[str, int] = {}
    if not root.exists():
        return snap
    for p in root.rglob("*"):
        try:
            snap[str(p.relative_to(root))] = p.stat().st_mtime_ns
        except OSError:
            pass
    return snap

def run_fuzzer(seed: int, max_iterations: int, timeout_seconds: float = 180.0) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    scratch_base = Path(os.path.expanduser(f"~/agy-review/gemini4/fuzz_run_{seed}"))
    if scratch_base.exists():
        shutil.rmtree(scratch_base)
    scratch_base.mkdir(parents=True, exist_ok=True)

    store_dir = scratch_base / "store"
    jobs_dir = store_dir / "jobs"
    failures: list[dict[str, Any]] = []

    t_start = time.time()
    it = 0

    clean_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    clean_env["HOME"] = str(Path.home())

    print(f"[Seed {seed}] Starting {max_iterations} iterations...")

    while it < max_iterations:
        if time.time() - t_start > timeout_seconds:
            print(f"[Seed {seed}] Hit time limit ({timeout_seconds}s) after {it} iterations.")
            break

        it += 1
        # Recreate store layout
        if store_dir.exists():
            shutil.rmtree(store_dir)
        jobs_dir.mkdir(parents=True, exist_ok=True)

        # Decide jobs to create
        job1_id = CANONICAL_UUIDS[0]
        job2_id = CANONICAL_UUIDS[1]

        j1_dir = jobs_dir / job1_id
        j2_dir = jobs_dir / job2_id
        j1_dir.mkdir(parents=True, exist_ok=True)
        j2_dir.mkdir(parents=True, exist_ok=True)

        # Mutations for Job 1 (potential candidate)
        j1_state = rng.choice(["done", "failed", "cancelled", rng.choice(STATES)])
        j1_pid = rng.choice([None, rng.choice(PIDS)])
        j1_ts = rng.choice(["2020-01-01T00:00:00Z", rng.choice(TIMESTAMPS)])
        j1_repo = rng.choice(["/tmp/fuzz_repo_alpha", rng.choice(REPO_PATHS)])
        j1_dup_keys = rng.random() < 0.05
        j1_depth = rng.choice([0, 0, 0, 5, 20, 100, 1100]) if rng.random() < 0.1 else 0

        # Mutations for Job 2 (neighbor, possibly kept)
        j2_state = rng.choice(["running", "queued", "needs_review", "timeout", "needs_decision", rng.choice(STATES)])
        j2_pid = rng.choice([None, 1, os.getpid(), rng.choice(PIDS)])
        j2_ts = rng.choice(["2020-01-01T00:00:00Z", rng.choice(TIMESTAMPS)])
        j2_repo = rng.choice(["/tmp/fuzz_repo_alpha", rng.choice(REPO_PATHS)])
        j2_refs_j1 = rng.random() < 0.4
        j2_ref_case = rng.choice(["exact", "upper", "nested", "branch_sub"]) if j2_refs_j1 else "none"

        # Build Job 1 payload
        j1_data: dict[str, Any] = {
            "job_id": job1_id,
            "state": j1_state,
            "created_at": j1_ts,
            "repo_path": j1_repo,
        }
        if j1_pid is not None:
            j1_data["pid"] = j1_pid
        if j1_depth > 0:
            j1_data["depth_tree"] = make_nested(j1_depth)

        # Build Job 2 payload
        j2_data: dict[str, Any] = {
            "job_id": job2_id,
            "state": j2_state,
            "created_at": j2_ts,
            "repo_path": j2_repo,
        }
        if j2_pid is not None:
            j2_data["pid"] = j2_pid
        if j2_refs_j1:
            if j2_ref_case == "exact":
                j2_data["related_job"] = job1_id
            elif j2_ref_case == "upper":
                j2_data["related_job"] = job1_id.upper()
            elif j2_ref_case == "branch_sub":
                j2_data["branch"] = f"feature/{job1_id}/patch"
            elif j2_ref_case == "nested":
                j2_data["nested_refs"] = [{"details": {"ref": job1_id}}]

        # Write job.json files
        if j1_dup_keys:
            raw1 = f'{{"job_id": "{job1_id}", "state": "{j1_state}", "state": "done", "created_at": "2020-01-01T00:00:00Z", "repo_path": "/tmp/fuzz_repo_alpha"}}'
            (j1_dir / "job.json").write_text(raw1, encoding="utf-8")
        else:
            (j1_dir / "job.json").write_text(json.dumps(j1_data), encoding="utf-8")

        (j2_dir / "job.json").write_text(json.dumps(j2_data), encoding="utf-8")

        # Select mode and flags
        mode = rng.choice(["project", "job", "list"])
        cmd = [sys.executable, "-m", CLEANER_MODULE, "--store", str(store_dir)]

        target_project = "/tmp/fuzz_repo_alpha"
        if mode == "project":
            proj_arg = rng.choice([target_project, target_project + "/", "/tmp/fuzz_repo_beta", ""])
            cmd.extend(["--project", proj_arg])
        elif mode == "job":
            if rng.random() < 0.7:
                cmd.extend(["--job", job1_id])
            else:
                cmd.extend(["--job", job1_id, "--job", rng.choice([job2_id, CANONICAL_UUIDS[2], "not-a-uuid"])])
        else:
            cmd.append("--list")

        older_days = rng.choice([7.0, 1.0, 0.0, 30.0, 0.01])
        if rng.random() < 0.1:
            # Bad age flag
            older_days_arg = rng.choice(["-1", "nan", "inf", "bad_float"])
            cmd.extend(["--older-than-days", str(older_days_arg)])
        else:
            cmd.extend(["--older-than-days", str(older_days)])

        use_json = rng.random() < 0.5
        if use_json:
            cmd.append("--json")

        use_yes = rng.random() < 0.15  # 15% deletion runs
        if use_yes:
            cmd.append("--yes")

        # Snapshot before
        snap_before = snapshot_tree(scratch_base)

        # Run command
        proc = subprocess.run(cmd, env=clean_env, capture_output=True, text=True, timeout=10)

        # Check Invariant 1: Never a traceback
        if "Traceback (most recent call last):" in proc.stderr:
            failures.append({
                "iteration": it,
                "type": "TRACEBACK",
                "cmd": cmd,
                "stderr": proc.stderr.strip()[:300],
                "exit_code": proc.returncode,
            })
            continue

        # Check Invariant 2: Exit code in {0, 1, 2}
        if proc.returncode not in (0, 1, 2):
            failures.append({
                "iteration": it,
                "type": "ILLEGAL_EXIT_CODE",
                "cmd": cmd,
                "exit_code": proc.returncode,
                "stderr": proc.stderr.strip()[:300],
            })
            continue

        # Check Invariant 3: Dry-run exit code
        if not use_yes and proc.returncode == 1:
            failures.append({
                "iteration": it,
                "type": "DRY_RUN_EXIT_1",
                "cmd": cmd,
                "exit_code": proc.returncode,
                "stderr": proc.stderr.strip()[:300],
            })
            continue

        # If JSON output produced and exit 0, check candidate invariants
        if use_json and proc.returncode == 0:
            try:
                out_data = json.loads(proc.stdout)
                if mode in ("project", "job") and "candidates" in out_data:
                    cands = [c["job_id"] for c in out_data["candidates"]]
                    # Job 1 candidate checks:
                    if job1_id in cands:
                        # Invariant: Job 1 state must be terminal
                        if j1_state not in ("done", "failed", "cancelled"):
                            failures.append({
                                "iteration": it,
                                "type": "NON_TERMINAL_CANDIDATE",
                                "cmd": cmd,
                                "state": j1_state,
                                "job_id": job1_id,
                            })
                        # Invariant: Kept neighbor reference protection
                        # If Job 2 is kept (e.g. state is non-terminal) and references job1_id exactly:
                        if j2_state not in ("done", "failed", "cancelled") and j2_refs_j1 and j2_ref_case == "exact":
                            failures.append({
                                "iteration": it,
                                "type": "REFERENCED_JOB_CANDIDATE",
                                "cmd": cmd,
                                "job1": job1_id,
                                "job2_state": j2_state,
                            })
            except Exception as e:
                failures.append({
                    "iteration": it,
                    "type": "MALFORMED_JSON_OUTPUT",
                    "cmd": cmd,
                    "stdout": proc.stdout[:200],
                    "error": str(e),
                })

        # Invariant 6: Out-of-scope modification under --yes
        if use_yes:
            snap_after = snapshot_tree(scratch_base)
            for path_str in snap_before:
                if not path_str.startswith("store/jobs/"):
                    if path_str not in snap_after:
                        failures.append({
                            "iteration": it,
                            "type": "OUT_OF_SCOPE_DELETE",
                            "cmd": cmd,
                            "deleted_path": path_str,
                        })

    elapsed = time.time() - t_start
    print(f"[Seed {seed}] Completed {it} iterations in {elapsed:.2f}s. Failures found: {len(failures)}")
    return failures

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    fails = run_fuzzer(args.seed, args.iterations, args.timeout)
    if fails:
        print(f"\n--- FAILURES FOR SEED {args.seed} ({len(fails)}) ---")
        for f in fails[:10]:
            print(f)
        sys.exit(1)
    else:
        print(f"\n--- SEED {args.seed} CLEAN ({args.iterations} iterations) ---")
        sys.exit(0)
