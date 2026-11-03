#!/usr/bin/env python3
"""Run pytest per test file and monitor for orphaned fake_agy/agy_mcp processes."""

import os
import subprocess
import sys
from pathlib import Path


def get_procs():
    res = subprocess.run(
        ["pgrep", "-af", "fake_agy|agy_mcp"],
        capture_output=True,
        text=True,
    )
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    # Filter out this watcher script itself
    filtered = [l for l in lines if "watch_orphans.py" not in l and "pgrep -af" not in l]
    return filtered


def main():
    repo_root = Path(__file__).resolve().parent.parent
    test_files = sorted((repo_root / "tests").rglob("test_*.py"))
    out_file = repo_root / "docs" / "reports" / "smoke-fixes-2" / "orphans.txt"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    default_pytest = (
        str(repo_root / ".venv" / "bin" / "pytest")
        if (repo_root / ".venv" / "bin" / "pytest").is_file()
        else "pytest"
    )
    pytest_bin = os.environ.get("PYTEST_BIN", default_pytest)
    env = dict(os.environ)
    env["PYTHONPATH"] = ""

    with open(out_file, "w", encoding="utf-8") as out:
        out.write("=== ORPHAN PROCESS MONITOR LOG ===\n\n")
        print("Starting orphan watch across test files...")

        culprits = []

        for tf in test_files:
            rel_tf = tf.relative_to(repo_root)
            before = get_procs()
            out.write(f"--- File: {rel_tf} ---\n")
            out.write(f"Processes BEFORE ({len(before)}):\n")
            for p in before:
                out.write(f"  {p}\n")

            # Run pytest
            res = subprocess.run(
                [pytest_bin, str(rel_tf)],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
            )

            after = get_procs()
            out.write(f"Pytest exit code: {res.returncode}\n")
            out.write(f"Processes AFTER ({len(after)}):\n")
            for p in after:
                out.write(f"  {p}\n")

            new_orphans = [p for p in after if p not in before]
            if new_orphans:
                out.write(f"*** DETECTED ORPHANS in {rel_tf} ({len(new_orphans)}) ***\n")
                for p in new_orphans:
                    out.write(f"  ORPHAN: {p}\n")
                culprits.append((str(rel_tf), new_orphans))
                print(f"[!] ORPHAN DETECTED in {rel_tf}: {new_orphans}")
                # Clean up orphans before next test file
                for p in new_orphans:
                    try:
                        pid = int(p.split()[0])
                        os.kill(pid, 9)
                    except Exception:
                        pass
            else:
                out.write("No orphans detected.\n")
            out.write("\n")
            print(f"Finished {rel_tf}: {'CLEAN' if not new_orphans else 'LEAKED'}")

        out.write("=== SUMMARY ===\n")
        if culprits:
            out.write(f"Culprit test files ({len(culprits)}):\n")
            for tf, procs in culprits:
                out.write(f"  - {tf}: {len(procs)} processes\n")
                for p in procs:
                    out.write(f"      {p}\n")
        else:
            out.write("No culprit test files found. All test files exited cleanly.\n")

    print(f"Results written to {out_file}")


if __name__ == "__main__":
    main()
