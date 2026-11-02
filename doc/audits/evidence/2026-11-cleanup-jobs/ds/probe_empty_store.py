"""Task 3 probe: is an EMPTY --store refused, or silently normalized to CWD?

Both CLIs guard with `if not str(store_path).strip()`, but argparse's type=Path
turns "" into Path("."), whose str is "." -- so from inside a store directory
the empty argument silently targets that store instead of being refused.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path.home() / "Documents/GITHUB PROJECTS/agy-mcp"
SD = Path.home() / "agy-review" / "ds" / "empty_store_repro"
CAND = "11111111-1111-1111-1111-111111111111"


def build(store: Path, repo: str) -> None:
    if store.exists():
        shutil.rmtree(store)
    jd = store / "jobs" / CAND
    jd.mkdir(parents=True)
    (jd / "job.json").write_text(
        json.dumps(
            {
                "job_id": CAND,
                "state": "done",
                "repo_path": repo,
                "created_at": "2020-01-01T00:00:00+00:00",
                "updated_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def run(label: str, args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", *args]
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
    print(f"=== {label}")
    print("cmd:", " ".join(cmd))
    print("cwd:", cwd)
    print("exit:", p.returncode)
    print("stdout:", p.stdout.strip() or "<empty>")
    print("stderr:", p.stderr.strip() or "<empty>")
    return p


def main() -> int:
    SD.mkdir(parents=True, exist_ok=True)
    tmp_root = SD / "tmp"
    pytest_repo = tmp_root / "pytest-of-proof" / "pytest-0" / "test_emptystore0" / "repo"
    pytest_repo.mkdir(parents=True, exist_ok=True)
    plain_repo = SD / "repo"
    plain_repo.mkdir(exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src")
    env["HOME"] = str(SD)
    env["TMPDIR"] = str(tmp_root)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    # 1. cleanup_jobs --store "" --list, run from inside a store directory.
    store_jobs = SD / "store_jobs"
    build(store_jobs, str(plain_repo))
    p1 = run(
        "cleanup_jobs --store '' --list (cwd = store)",
        ["agy_mcp.cleanup_jobs", "--store", "", "--list"],
        cwd=store_jobs,
        env=env,
    )

    # 2. cleanup_fixtures --store "" dry run, run from inside a fixture store.
    store_fix = SD / "store_fixtures"
    build(store_fix, str(pytest_repo))
    p2 = run(
        "cleanup_fixtures --store '' (cwd = store)",
        ["agy_mcp.cleanup_fixtures", "--store", ""],
        cwd=store_fix,
        env=env,
    )

    # 3. cleanup_fixtures --store "" --yes: does it delete the CWD store's row?
    store_del = SD / "store_delete"
    build(store_del, str(pytest_repo))
    p3 = run(
        "cleanup_fixtures --store '' --yes (cwd = store)",
        ["agy_mcp.cleanup_fixtures", "--store", "", "--yes"],
        cwd=store_del,
        env=env,
    )
    still_there = (store_del / "jobs" / CAND).is_dir()
    print(f"=== after --yes: row still on disk: {still_there}")
    if still_there and p3.returncode == 2 and "Error:" in p3.stderr:
        print("RESULT: empty --store refused (no finding)")
        return 0
    print("RESULT: empty --store was NOT refused; it operated on CWD store")
    return 1


if __name__ == "__main__":
    sys.exit(main())
