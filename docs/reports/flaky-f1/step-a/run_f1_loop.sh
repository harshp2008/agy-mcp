#!/usr/bin/env bash
# STEP A evidence loop: run the single f1 test 30 times and keep per-run state.
# Read-only w.r.t. the repository; writes evidence under docs/reports/flaky-f1/step-a.
set -u
REPO="~/Documents/GITHUB PROJECTS/agy-mcp"
OUT="$REPO/docs/reports/flaky-f1/step-a"
BASE="/tmp/flaky-f1-runs"
PYTHON="$REPO/.venv/bin/python"
TEST="tests/test_f1.py::test_f1_invalid_commit_msg_kills_process_and_resumes"
mkdir -p "$OUT" "$BASE"
RESULTS="$OUT/results.txt"
: > "$RESULTS"
fails=0
for i in $(seq 1 30); do
  run_dir="$BASE/run-$i"
  rm -rf "$run_dir"
  mkdir -p "$run_dir"
  start=$(date -Is)
  env -u PYTHONPATH timeout 120 "$PYTHON" -m pytest "$TEST" -q \
    --basetemp="$run_dir" > "$OUT/run-$i-pytest.txt" 2>&1
  rc=$?
  end=$(date -Is)
  if [ "$rc" -eq 0 ]; then
    status="PASS"
  else
    status="FAIL"
    fails=$((fails + 1))
    fail_dir="$OUT/failure-$i"
    rm -rf "$fail_dir"
    mkdir -p "$fail_dir"
    cp -a "$OUT/run-$i-pytest.txt" "$fail_dir/pytest.txt"
    # Copy only the job metadata + scratchpad, never the whole git repo.
    find "$run_dir" -path "*/state/jobs/*" -maxdepth 8 -type d -name "work" -prune -o \
      -path "*/state/jobs/*" -maxdepth 8 -type f -print 2>/dev/null | while read -r f; do
        rel=${f#"$run_dir"/}
        mkdir -p "$fail_dir/artifacts/$(dirname "$rel")"
        cp -a "$f" "$fail_dir/artifacts/$rel"
      done
    env -u PYTHONPATH "$PYTHON" "$OUT/dump_job_state.py" "$run_dir" \
      > "$fail_dir/job-state.txt" 2>&1
  fi
  echo "run=$i status=$status rc=$rc start=$start end=$end" | tee -a "$RESULTS"
done
echo "TOTAL_FAILURES=$fails/30" | tee -a "$RESULTS"
