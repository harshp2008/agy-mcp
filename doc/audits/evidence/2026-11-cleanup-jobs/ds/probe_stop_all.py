"""Task 3 probe: stop-all fail-closed pre-delete rule, and per-row failure isolation.

Scenario A: two eligible candidates + one kept (running) row. Through the
before_delete hook the KEPT row is mutated into duplicate-key JSON before the
first deletion. Expectation per SPEC section 23: all deletion stops, NO row is
deleted, both candidates land in failed_ids with a blocker reason.

Scenario B: two eligible candidates, the first is made undeletable (chmod 0500
plus an inner file) before its deletion. Expectation: first fails, second is
still deleted (one bad row never stops the rest), exit path reports partial.
"""

import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path.home() / "Documents/GITHUB PROJECTS/agy-mcp"
SD = Path.home() / "agy-review" / "ds" / "stop_all_probe"
sys.path.insert(0, str(REPO / "src"))
from agy_mcp.cleanup_jobs import run_cleanup  # noqa: E402

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
K = "33333333-3333-3333-3333-333333333333"


def write_row(store: Path, jid: str, state: str, repo: Path, extra: dict | None = None) -> None:
    d = store / "jobs" / jid
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "job_id": jid,
        "state": state,
        "repo_path": str(repo),
        "created_at": "2020-01-01T00:00:00+00:00",
        "updated_at": "2020-01-01T00:00:00+00:00",
    }
    if extra:
        data.update(extra)
    (d / "job.json").write_text(json.dumps(data), encoding="utf-8")


def scenario_a() -> int:
    store = SD / "stop_all"
    if store.exists():
        shutil.rmtree(store)
    repo = SD / "repo_a"
    repo.mkdir(parents=True, exist_ok=True)
    write_row(store, A, "done", repo)
    write_row(store, B, "done", repo)
    write_row(store, K, "running", repo)

    def mutate_kept(job_id: str) -> None:
        # On the first deletion attempt, corrupt the kept row with a duplicate key.
        k_file = store / "jobs" / K / "job.json"
        text = k_file.read_text(encoding="utf-8")
        k_file.write_text(text.replace('"state": "running"', '"state": "running", "state": "running"'), encoding="utf-8")

    report = run_cleanup(store, project=repo, dry_run=False, before_delete=mutate_kept)
    print("=== Scenario A: kept row becomes duplicate-key JSON mid-deletion")
    print("deleted_count:", report["deleted_count"], "deleted_ids:", report["deleted_ids"])
    print("failed_ids:", report["failed_ids"])
    for s in report["skipped"]:
        print("skipped:", s["job_id"], "->", s["reasons"][0][:160])
    kept_a = (store / "jobs" / A).is_dir()
    kept_b = (store / "jobs" / B).is_dir()
    print(f"A on disk: {kept_a}, B on disk: {kept_b}")
    ok = (
        report["deleted_count"] == 0
        and set(report["failed_ids"]) == {A, B}
        and kept_a
        and kept_b
        and all("duplicate key" in s["reasons"][0] for s in report["skipped"])
        and "manifest" in report
    )
    print("RESULT:", "PASS (stop-all honored)" if ok else "FAIL")
    return 0 if ok else 1


def scenario_b() -> int:
    store = SD / "row_isolation"
    if store.exists():
        shutil.rmtree(store)
    os.chmod(store, 0o700) if store.exists() else None
    repo = SD / "repo_b"
    repo.mkdir(parents=True, exist_ok=True)
    write_row(store, A, "done", repo)
    write_row(store, B, "done", repo)

    def chmod_a(job_id: str) -> None:
        if job_id == A:
            (store / "jobs" / A / "job.json").chmod(0o400)
            (store / "jobs" / A).chmod(0o500)

    report = run_cleanup(store, project=repo, dry_run=False, before_delete=chmod_a)
    print("=== Scenario B: first candidate directory becomes undeletable")
    print("deleted_count:", report["deleted_count"], "deleted_ids:", report["deleted_ids"])
    print("failed_ids:", report["failed_ids"])
    for s in report["skipped"]:
        print("skipped:", s["job_id"], "->", s["reasons"][0][:120])
    a_exists = (store / "jobs" / A).is_dir()
    b_exists = (store / "jobs" / B).is_dir()
    print(f"A on disk: {a_exists}, B on disk: {b_exists}")
    try:
        (store / "jobs" / A).chmod(0o700)
    except OSError:
        pass
    ok = (
        report["deleted_count"] == 1
        and A in report["failed_ids"]
        and not b_exists
        and a_exists
    )
    print("RESULT:", "PASS (per-row isolation)" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    SD.mkdir(parents=True, exist_ok=True)
    return max(scenario_a(), scenario_b())


if __name__ == "__main__":
    sys.exit(main())
