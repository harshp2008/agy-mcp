#!/usr/bin/env bash
# F22 Part A reproduction (D68).
#
# The shipped test tests/test_f22.py::test_record_usage_written_on_job_completion
# is timing-dependent: it polls job.json with a 50 ms sleep and asserts that
# usage.jsonl exists the moment the state is no longer running. The supervisor
# runs on a background thread and used to persist the terminal state BEFORE
# appending usage.jsonl (a ~5 ms window), so the test fails intermittently.
#
# This script runs the test repeatedly and stops at the first failure, printing
# the raw pytest output. Run it with the documented environment:
#
#   env -u PYTHONPATH .venv/bin/python -m pytest ...   # (single run)
#   bash docs/reports/installer-fixes/repro_f22.sh 25   # (loop)
#
# The deterministic proof is the instrumented harness:
#   env -u PYTHONPATH .venv/bin/python docs/reports/installer-fixes/f22_instrument.py 10
set -u

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." >/dev/null 2>&1 && pwd)"
RUNS="${1:-25}"
TARGET="tests/test_f22.py::test_record_usage_written_on_job_completion"
failures=0

cd "$REPO_ROOT" || exit 1
printf 'repro_f22: %s runs of %s in %s\n' "$RUNS" "$TARGET" "$REPO_ROOT"
for i in $(seq 1 "$RUNS"); do
    out="$(env -u PYTHONPATH .venv/bin/python -m pytest "$TARGET" -q 2>&1)"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        printf '=== run %s: FAILED (rc=%s) ===\n%s\n' "$i" "$rc" "$out"
        failures=$((failures + 1))
        break
    fi
    printf '=== run %s: passed ===\n' "$i"
done
printf 'repro_f22: %s failure(s) in %s run(s)\n' "$failures" "$i"
exit 0
