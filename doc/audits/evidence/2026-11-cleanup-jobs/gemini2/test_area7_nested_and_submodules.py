#!/usr/bin/env python3
"""Test Area 7: Nested worktrees, worktree of a worktree, and submodule in worktree."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area7")

def test_worktree_of_worktree():
    """Case: WT1 is worktree of main repo. WT2 is created from WT1.
    Job 2 has repo_path = WT1.
    """
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    env = get_env(SCRATCH)

    main_repo = SCRATCH / "main_repo"
    subprocess.run(["git", "init", str(main_repo)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(main_repo), check=True, capture_output=True, env=env)

    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)

    job1_id = "00000000-0000-0000-0000-000000000001"
    job1_dir = store_jobs / job1_id
    job1_dir.mkdir(parents=True, exist_ok=True)
    wt1 = job1_dir / "work"
    subprocess.run(["git", "worktree", "add", str(wt1), "-b", "b1"], cwd=str(main_repo), check=True, capture_output=True, env=env)

    job2_id = "00000000-0000-0000-0000-000000000002"
    job2_dir = store_jobs / job2_id
    job2_dir.mkdir(parents=True, exist_ok=True)
    wt2 = job2_dir / "work"

    # Create WT2 from WT1
    subprocess.run(["git", "worktree", "add", str(wt2), "-b", "b2"], cwd=str(wt1), check=True, capture_output=True, env=env)

    # Now unlink .git inside wt2 to test registration lookup
    (wt2 / ".git").unlink()

    job2_data = {
        "job_id": job2_id,
        "title": "wt2 job with repo_path=wt1",
        "state": "done",
        "repo_path": str(wt1),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job2_dir / "job.json").write_text(json.dumps(job2_data, indent=2), encoding="utf-8")

    gw, gr, go = check_git(wt1, wt2, env)
    ir, rr, ic, cm = check_cleaner(store, wt1, job2_dir, job2_id)
    v = verdict(gw, gr, ic, cm)
    print(f"worktree of worktree (repo_path=wt1): git_works={gw} git_reg={gr} cleaner_reg={ir} candidate={ic} → {v}")
    if "FAIL-OPEN" in v or "CRASH" in v:
        print(f"  git out: {go[:200]}")
        print(f"  cleaner reason: {rr}")
        record_finding(
            repro_script="test_area7_nested_and_submodules.py (wt_of_wt)",
            command="env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini2/test_area7_nested_and_submodules.py",
            cleaner_said=f"candidate={ic}, reason={rr}",
            git_said=f"git_works={gw}, git_reg={gr}, out={go.strip()}",
            severity="FAIL-OPEN" if "FAIL-OPEN" in v else "CRASH",
            details="Worktree of worktree with repo_path=wt1"
        )


def test_nested_worktree_inside_worktree():
    """Case: WT2 is physically located inside WT1: wt1/nested/work."""
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    env = get_env(SCRATCH)

    main_repo = SCRATCH / "main_repo"
    subprocess.run(["git", "init", str(main_repo)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(main_repo), check=True, capture_output=True, env=env)

    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)

    job1_id = "00000000-0000-0000-0000-000000000001"
    job1_dir = store_jobs / job1_id
    job1_dir.mkdir(parents=True, exist_ok=True)
    wt1 = job1_dir / "work"
    subprocess.run(["git", "worktree", "add", str(wt1), "-b", "b1"], cwd=str(main_repo), check=True, capture_output=True, env=env)

    nested_wt = wt1 / "nested_wt"
    subprocess.run(["git", "worktree", "add", str(nested_wt), "-b", "b_nested"], cwd=str(main_repo), check=True, capture_output=True, env=env)

    (wt1 / ".git").unlink()
    (nested_wt / ".git").unlink()

    job1_data = {
        "job_id": job1_id,
        "title": "job1 with nested wt inside work",
        "state": "done",
        "repo_path": str(main_repo),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job1_dir / "job.json").write_text(json.dumps(job1_data, indent=2), encoding="utf-8")

    gw, gr, go = check_git(main_repo, wt1, env)
    ir, rr, ic, cm = check_cleaner(store, main_repo, job1_dir, job1_id)
    v = verdict(gw, gr, ic, cm)
    print(f"nested worktree inside wt: git_works={gw} git_reg={gr} cleaner_reg={ir} candidate={ic} → {v}")
    if "FAIL-OPEN" in v or "CRASH" in v:
        print(f"  git out: {go[:200]}")
        print(f"  cleaner reason: {rr}")
        record_finding(
            repro_script="test_area7_nested_and_submodules.py (nested_wt)",
            command="env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini2/test_area7_nested_and_submodules.py",
            cleaner_said=f"candidate={ic}, reason={rr}",
            git_said=f"git_works={gw}, git_reg={gr}, out={go.strip()}",
            severity="FAIL-OPEN" if "FAIL-OPEN" in v else "CRASH",
            details="Nested worktree inside wt1"
        )


def test_submodule_in_worktree():
    """Case: main_repo has a submodule. A worktree of main_repo is created.
    The worktree inits the submodule.
    What happens if job has repo_path = submodule inside worktree?
    """
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    env = get_env(SCRATCH)

    sub_repo = SCRATCH / "sub_repo"
    subprocess.run(["git", "init", str(sub_repo)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "sub_init"], cwd=str(sub_repo), check=True, capture_output=True, env=env)

    main_repo = SCRATCH / "main_repo"
    subprocess.run(["git", "init", str(main_repo)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(main_repo), check=True, capture_output=True, env=env)
    subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub_repo), "sub"], cwd=str(main_repo), check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "add sub"], cwd=str(main_repo), check=True, capture_output=True, env=env)

    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)

    job1_id = "00000000-0000-0000-0000-000000000001"
    job1_dir = store_jobs / job1_id
    job1_dir.mkdir(parents=True, exist_ok=True)
    wt1 = job1_dir / "work"
    subprocess.run(["git", "worktree", "add", str(wt1), "-b", "b1"], cwd=str(main_repo), check=True, capture_output=True, env=env)
    subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "update", "--init"], cwd=str(wt1), check=True, capture_output=True, env=env)

    # wt1's submodule has a worktree added to job2
    sub_in_wt = wt1 / "sub"
    job2_id = "00000000-0000-0000-0000-000000000002"
    job2_dir = store_jobs / job2_id
    job2_dir.mkdir(parents=True, exist_ok=True)
    wt2 = job2_dir / "work"
    subprocess.run(["git", "worktree", "add", str(wt2), "-b", "sub_wt"], cwd=str(sub_in_wt), check=True, capture_output=True, env=env)

    (wt2 / ".git").unlink()

    job2_data = {
        "job_id": job2_id,
        "title": "submodule worktree job",
        "state": "done",
        "repo_path": str(sub_in_wt),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job2_dir / "job.json").write_text(json.dumps(job2_data, indent=2), encoding="utf-8")

    gw, gr, go = check_git(sub_in_wt, wt2, env)
    ir, rr, ic, cm = check_cleaner(store, sub_in_wt, job2_dir, job2_id)
    v = verdict(gw, gr, ic, cm)
    print(f"submodule in worktree: git_works={gw} git_reg={gr} cleaner_reg={ir} candidate={ic} → {v}")
    if "FAIL-OPEN" in v or "CRASH" in v:
        print(f"  git out: {go[:200]}")
        print(f"  cleaner reason: {rr}")
        record_finding(
            repro_script="test_area7_nested_and_submodules.py (sub_in_wt)",
            command="env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini2/test_area7_nested_and_submodules.py",
            cleaner_said=f"candidate={ic}, reason={rr}",
            git_said=f"git_works={gw}, git_reg={gr}, out={go.strip()}",
            severity="FAIL-OPEN" if "FAIL-OPEN" in v else "CRASH",
            details="Submodule inside worktree has worktree in job2"
        )

if __name__ == "__main__":
    test_worktree_of_worktree()
    test_nested_worktree_inside_worktree()
    test_submodule_in_worktree()
