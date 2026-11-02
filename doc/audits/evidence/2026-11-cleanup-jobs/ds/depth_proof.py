"""D100 proof: depth protection at the DEFAULT recursion limit (1000).

Builds 100000-level nested job.json files with raw string concatenation (no
Python recursion in the builder), then exercises every CLI face in a fresh
subprocess with a timeout. Asserts: no traceback, no signal/segfault, exit code
in {0,1,2}, and the corrupt row kept fail-closed (never deleted).

Run: env -u PYTHONPATH .venv/bin/python ~/agy-review/ds/depth_proof.py
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path("~/Documents/GITHUB PROJECTS/agy-mcp")
SCRATCH = Path.home() / "agy-review" / "ds" / "depth_proof"
CHILD = Path.home() / "agy-review" / "ds" / "depth_child.py"
DEPTH = 100_000
CAND = "11111111-1111-1111-1111-111111111111"
KEPT = "22222222-2222-2222-2222-222222222222"

BOMB = '{"k":' * DEPTH + '"leaf"' + '}' * DEPTH

failures: list[str] = []


def job_text(jid: str, state: str, repo: str, bomb: bool = True, extra: str = "") -> str:
    nested = BOMB if bomb else '"ok"'
    return (
        '{"job_id":"%s","state":"%s","repo_path":"%s",'
        '"created_at":"2020-01-01T00:00:00+00:00"%s,"nested":%s}'
    ) % (jid, state, repo, extra, nested)


def make_store(name: str, rows: list[tuple[str, str]]) -> Path:
    store = SCRATCH / name
    if store.exists():
        shutil.rmtree(store)
    jobs = store / "jobs"
    jobs.mkdir(parents=True)
    for jid, text in rows:
        d = jobs / jid
        d.mkdir()
        (d / "job.json").write_text(text, encoding="utf-8")
    return store


def base_env(pytest_tmp: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src")
    env["HOME"] = str(Path.home())
    env["TMPDIR"] = str(pytest_tmp)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def run(label: str, module: str, args: list[object], env: dict[str, str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", module, *map(str, args)]
    proc = subprocess.run(
        cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=120
    )
    tb = "Traceback (most recent call last):" in proc.stderr
    sig = proc.returncode < 0
    if tb or sig or proc.returncode not in (0, 1, 2):
        failures.append(f"{label}: exit={proc.returncode} traceback={tb} signal={sig}")
    print(f"--- {label}")
    print(f"cmd: {' '.join(map(str, cmd))}")
    print(f"exit: {proc.returncode} | traceback: {tb} | signal(segfault): {sig}")
    out_tail = proc.stdout.strip().splitlines()[-3:] or ["<empty>"]
    err_tail = proc.stderr.strip().splitlines()[-2:] or ["<empty>"]
    print("stdout tail: " + " | ".join(out_tail))
    print("stderr tail: " + " | ".join(err_tail))
    return proc


def require(label: str, cond: bool, detail: str) -> None:
    if not cond:
        failures.append(f"{label}: {detail}")
    print(f"    {'PASS' if cond else 'FAIL'}: {detail}")


def check_fail_closed(label: str, store: Path, jid: str) -> None:
    exists = (store / "jobs" / jid).is_dir()
    require(label, exists, f"row {jid} still on disk (fail-closed)")


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    tmp_root = SCRATCH / "tmp"
    pytest_repo = tmp_root / "pytest-of-proof" / "pytest-0" / "test_depthproof0" / "repo"
    pytest_repo.mkdir(parents=True, exist_ok=True)
    repo = SCRATCH / "repo"
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir()
    env = base_env(tmp_root)

    print(f"parent python: {sys.executable}")
    print(f"parent recursion limit: {sys.getrecursionlimit()}")
    print(f"nested depth: {DEPTH} levels, job.json size: {len(BOMB)} bytes")
    print(f"pytest-style repo: {pytest_repo}")

    # 1. Candidate in project mode (dry run) -- project face.
    store = make_store(
        "cand_project",
        [(CAND, job_text(CAND, "done", str(repo)))],
    )
    p = run("project dry-run (candidate)", "agy_mcp.cleanup_jobs",
            ["--store", store, "--project", repo], env)
    require("project dry-run (candidate)", "Candidates (0):" in p.stdout, "candidate kept, Candidates (0)")
    check_fail_closed("project dry-run (candidate)", store, CAND)

    # 2. Candidate in project mode under --yes (nothing may be deleted).
    store = make_store(
        "cand_project_yes",
        [(CAND, job_text(CAND, "done", str(repo)))],
    )
    p = run("project --yes (candidate)", "agy_mcp.cleanup_jobs",
            ["--store", store, "--project", repo, "--yes"], env)
    check_fail_closed("project --yes (candidate)", store, CAND)
    require("project --yes (candidate)", "Deleted: 0" in p.stdout, "Deleted: 0")

    # 3. Kept neighbor (running job with bomb) keeps the normal candidate fail-closed.
    store = make_store(
        "kept_neighbor",
        [
            (CAND, job_text(CAND, "done", str(repo), bomb=False)),
            (KEPT, job_text(KEPT, "running", str(repo))),
        ],
    )
    p = run("project dry-run (kept neighbor)", "agy_mcp.cleanup_jobs",
            ["--store", store, "--project", repo], env)
    require("project dry-run (kept neighbor)", "Candidates (0):" in p.stdout,
            "normal candidate kept because a kept record is corrupt depth bomb")
    check_fail_closed("project dry-run (kept neighbor)", store, CAND)

    # 4. --list face with a bomb row.
    store = make_store(
        "list_mode",
        [
            (CAND, job_text(CAND, "done", str(repo))),
            (KEPT, job_text(KEPT, "running", str(repo))),
        ],
    )
    p = run("--list (bomb rows)", "agy_mcp.cleanup_jobs",
            ["--store", store, "--list"], env)
    require("--list (bomb rows)", "Traceback" not in p.stdout, "no traceback on stdout")
    require("--list (bomb rows)", "Warning:" in p.stdout, "corrupt-record warning printed")

    # 5. --job face with a bomb row: dry-run and --yes.
    store = make_store(
        "job_mode",
        [(CAND, job_text(CAND, "done", str(repo)))],
    )
    p = run("--job dry-run (bomb row)", "agy_mcp.cleanup_jobs",
            ["--store", store, "--job", CAND], env)
    require("--job dry-run (bomb row)", "Candidates (0):" in p.stdout, "kept, Candidates (0)")
    check_fail_closed("--job dry-run (bomb row)", store, CAND)
    p = run("--job --yes (bomb row)", "agy_mcp.cleanup_jobs",
            ["--store", store, "--job", CAND, "--yes"], env)
    check_fail_closed("--job --yes (bomb row)", store, CAND)

    # 6. cleanup_fixtures CLI face (dry run and --yes).
    store = make_store(
        "fixtures_mode",
        [(CAND, job_text(CAND, "done", str(pytest_repo)))],
    )
    p = run("fixtures dry-run (bomb row)", "agy_mcp.cleanup_fixtures",
            ["--store", store], env)
    require("fixtures dry-run (bomb row)", "Candidates (0):" in p.stdout, "kept, Candidates (0)")
    check_fail_closed("fixtures dry-run (bomb row)", store, CAND)
    p = run("fixtures --yes (bomb row)", "agy_mcp.cleanup_fixtures",
            ["--store", store, "--yes"], env)
    check_fail_closed("fixtures --yes (bomb row)", store, CAND)

    # 7. Pre-delete recheck, cleanup_jobs: normal candidate mutates into bomb at delete time.
    store = make_store(
        "predelete_jobs",
        [(CAND, job_text(CAND, "done", str(repo), bomb=False))],
    )
    cmd = [sys.executable, str(CHILD), str(REPO / "src"), str(store), str(repo), "jobs"]
    proc = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    tb = "Traceback (most recent call last):" in proc.stderr
    print("--- pre-delete recheck (cleanup_jobs)")
    print(f"cmd: {' '.join(cmd)}")
    print(f"exit: {proc.returncode} | traceback: {tb}")
    print(f"stdout: {proc.stdout.strip()}")
    if tb or proc.returncode != 0:
        failures.append(f"pre-delete recheck (cleanup_jobs): exit={proc.returncode} tb={tb}")
    if proc.stdout.strip():
        rep = json.loads(proc.stdout)
        require("pre-delete recheck (cleanup_jobs)", rep["recursion_limit"] == 1000,
                f"child ran at default limit ({rep['recursion_limit']})")
        require("pre-delete recheck (cleanup_jobs)", rep["deleted_count"] == 0,
                f"deleted_count == 0 ({rep['deleted_count']})")
        check_fail_closed("pre-delete recheck (cleanup_jobs)", store, CAND)

    # 8. Pre-delete recheck, cleanup_fixtures.
    store = make_store(
        "predelete_fixtures",
        [(CAND, job_text(CAND, "done", str(pytest_repo), bomb=False))],
    )
    cmd = [sys.executable, str(CHILD), str(REPO / "src"), str(store), str(pytest_repo), "fixtures"]
    proc = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    tb = "Traceback (most recent call last):" in proc.stderr
    print("--- pre-delete recheck (cleanup_fixtures)")
    print(f"cmd: {' '.join(cmd)}")
    print(f"exit: {proc.returncode} | traceback: {tb}")
    print(f"stdout: {proc.stdout.strip()}")
    if tb or proc.returncode != 0:
        failures.append(f"pre-delete recheck (cleanup_fixtures): exit={proc.returncode} tb={tb}")
    if proc.stdout.strip():
        rep = json.loads(proc.stdout)
        require("pre-delete recheck (cleanup_fixtures)", rep["deleted_count"] == 0,
                f"deleted_count == 0 ({rep['deleted_count']})")
        check_fail_closed("pre-delete recheck (cleanup_fixtures)", store, CAND)

    # leftovers from recheck stores must not have been deleted either
    print()
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS (no traceback, no segfault, exit codes 0/1/2, all rows kept)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
