"""Task 3 probes for the new pgid liveness check.

Level 1: _check_pgid_status directly (1, own group, root-owned group,
         zombie-only group, huge, invalid, monkeypatched PermissionError).
Level 2: CLI classification via cleanup_jobs --project for the same values.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path.home() / "Documents/GITHUB PROJECTS/agy-mcp"
SD = Path.home() / "agy-review" / "ds" / "pgid_probe"
CAND = "11111111-1111-1111-1111-111111111111"

sys.path.insert(0, str(REPO / "src"))
from agy_mcp.cleanup_fixtures import (  # noqa: E402
    _check_pgid_status,
    _get_kernel_pid_max,
)


def show(label: str, value: object) -> None:
    keep, reason = _check_pgid_status(value)
    print(f"{label:<42} keep={keep!s:<5} reason={reason!r}")


def root_pgid() -> int | None:
    """Find a process group owned by root (non-1) to elicit PermissionError."""
    out = subprocess.run(
        ["ps", "-eo", "pgid,user"], capture_output=True, text=True, timeout=15
    ).stdout
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == "root":
            try:
                pg = int(parts[0])
            except ValueError:
                continue
            if pg > 1:
                return pg
    return None


def build_store(name: str, pgid: object, repo: Path) -> Path:
    store = SD / name
    if store.exists():
        shutil.rmtree(store)
    jd = store / "jobs" / CAND
    jd.mkdir(parents=True)
    (jd / "job.json").write_text(
        json.dumps(
            {
                "job_id": CAND,
                "state": "done",
                "repo_path": str(repo),
                "created_at": "2020-01-01T00:00:00+00:00",
                "updated_at": "2020-01-01T00:00:00+00:00",
                "pgid": pgid,
            }
        ),
        encoding="utf-8",
    )
    return store


def run_cli(label: str, store: Path, repo: Path, env: dict[str, str]) -> str:
    p = subprocess.run(
        [sys.executable, "-m", "agy_mcp.cleanup_jobs", "--store", str(store), "--project", str(repo)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    text = p.stdout + p.stderr
    verdict = "kept" if "Candidates (0):" in p.stdout else "CANDIDATE"
    print(f"=== CLI {label}: exit={p.returncode} -> {verdict}")
    for line in p.stdout.strip().splitlines():
        if "process group" in line or "Kept" in line or "Candidates" in line:
            print("   ", line)
    return verdict


def spawn_group(script: str) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
    return proc


def main() -> int:
    SD.mkdir(parents=True, exist_ok=True)
    repo = SD / "repo"
    repo.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src")
    env["HOME"] = str(SD)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    print("own pid:", os.getpid(), "own pgid:", os.getpgid(0), "pid_max:", _get_kernel_pid_max())
    print()

    print("--- _check_pgid_status direct")
    show("pgid 1", 1)
    show("own pgid", os.getpgid(0))
    rp = root_pgid()
    print("root-owned pgid candidate:", rp)
    if rp is not None:
        show("root-owned pgid", rp)
    show("huge (pid_max + 1)", _get_kernel_pid_max() + 1)
    show("huge (10**12)", 10**12)
    show("0", 0)
    show("-1", -1)
    show("True", True)
    show('"12345"', "12345")
    show("1.5", 1.5)

    # PermissionError branch via monkeypatch (cannot create a foreign-user group here).
    real_killpg = os.killpg
    try:
        def fake_killpg(pg, sig):
            raise PermissionError(1, "Operation not permitted")
        os.killpg = fake_killpg  # type: ignore[assignment]
        show("monkeypatched PermissionError", 123456)
    finally:
        os.killpg = real_killpg
    print()

    print("--- CLI classification (cleanup_jobs --project dry-run)")
    verdicts: dict[str, str] = {}

    own = os.getpgid(0)
    for label, value in [
        ("pgid 1", 1),
        ("own pgid", own),
        ("huge pid_max+1", _get_kernel_pid_max() + 1),
        ("invalid 0", 0),
        ("invalid -1", -1),
        ("invalid bool", True),
        ("invalid str", "12345"),
        ("invalid float", 1.5),
    ]:
        store = build_store("cli_" + label.replace(" ", "_").replace("+", "p"), value, repo)
        verdicts[label] = run_cli(label, store, repo, env)

    # dead group: leader dead, no members
    store = build_store("cli_dead_group", 4194300, repo)
    verdicts["dead group"] = run_cli("dead group", store, repo, env)

    # leader dead, grandchild alive in the same group
    script = """
import os, sys, time
pid = os.fork()
if pid == 0:
    gpid = os.fork()
    if gpid == 0:
        time.sleep(20)
        sys.exit(0)
    sys.exit(0)
sys.exit(0)
"""
    proc = spawn_group(script)
    proc.wait(timeout=10)
    group_pgid = proc.pid
    time.sleep(1)
    alive = False
    try:
        os.killpg(group_pgid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    print(f"dead-leader group {group_pgid} still alive: {alive}")
    store = build_store("cli_dead_leader", group_pgid, repo)
    try:
        verdicts["dead leader, children alive"] = run_cli(
            "dead leader, children alive", store, repo, env
        )
    finally:
        try:
            os.killpg(group_pgid, 9)
        except OSError:
            pass

    # zombie child in a live-leader group (leader stays alive, child is a zombie)
    script2 = """
import os, sys, time
pid = os.fork()
if pid == 0:
    sys.exit(0)   # child exits immediately -> zombie under live leader
time.sleep(20)
sys.exit(0)
"""
    proc2 = spawn_group(script2)
    time.sleep(1)
    zp_pgid = proc2.pid
    try:
        os.killpg(zp_pgid, 0)
        print(f"zombie-member group {zp_pgid} alive: True")
    except ProcessLookupError:
        print(f"zombie-member group {zp_pgid} alive: False")
    store = build_store("cli_zombie", zp_pgid, repo)
    try:
        verdicts["zombie child in live group"] = run_cli(
            "zombie child in live group", store, repo, env
        )
    finally:
        proc2.kill()
        try:
            os.killpg(zp_pgid, 9)
        except OSError:
            pass
        proc2.wait(timeout=10)

    print()
    bad = [k for k, v in verdicts.items() if v != "kept"]
    if bad:
        print("PROBE RESULT: unexpected candidate classification for:", bad)
        return 1
    print("PROBE RESULT: all probed pgid values kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
