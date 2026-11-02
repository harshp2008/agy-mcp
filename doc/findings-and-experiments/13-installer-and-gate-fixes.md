# 13 — Installer, Verification Loop, and Wrapper Commit Gate Investigations

Status: current
Audience: maintainers, contributors

Consolidated empirical investigations covering four system hardening efforts:
resolving the F22 terminal state usage recording flake, defining the orchestrator
workflow selection guidance within size budgets, reviewing the verification and
auto-fix loop execution context, and implementing wrapper-owned commit subject
sanitization with repository hook execution and per-job verify timeouts.

## Question

What mechanisms caused race conditions and flakes in installer test gates and
verify commands, how does the wrapper ensure strict size and process isolation
across orchestration workflows, and how can wrapper-created commits satisfy
repository hooks without violating commit message rules?

## Method

Investigated across four targeted experimental initiatives:
1. **Installer F22 Usage Ordering Investigation (2026-10-26):**
   Investigated on branch `exp/installer-fixes` (forked from `main` at `07d706a`).
   Analyzed intermittent failures in `tests/test_disk_limits_retention.py::test_record_usage_written_on_job_completion`
   using pytest `--collect-only`, isolated execution loops, and a high-resolution
   instrumentation script (`f22_instrument.py`) polling at 1 ms intervals.
2. **Orchestrator Workflow Guidance Budgeting (2026-10-27):**
   Conducted on branch `exp/orchestrator-workflows` (from `main` at `5380b95`).
   Authored workflow selection rules in `installer/agents_block.md` and `installer/install.sh`,
   enforcing strict line count constraints (net growth limit +28 lines, total cap <= 62 lines)
   and byte-identical synchronization.
3. **Verification Loop Execution Review (2026-10-25):**
   Conducted under `docs/reports/verify-loop/` and `docs/reports/verify-fixes/`.
   Analyzed invocation sequence, working directory resolution, process group isolation,
   sanitized environment generation (`env_allowlist`), and executable resolution under
   MCP client PATHs.
4. **Wrapper Verify Timeout and Commit Subject Hardening (2026-10-29):**
   Conducted on branch `fix/wrapper-verify-commit`. Evaluated 14 targeted regression
   tests in `tests/test_wrapper_verify_commit_fix.py` covering commit subject sanitization,
   repository hook enforcement, and process group timeout termination across 5 sequential
   full-suite test runs.

## Result

### 1. Installer F22 Usage Record Ordering (D68, D76, D77)

The shipped test `tests/test_disk_limits_retention.py::test_record_usage_written_on_job_completion` failed intermittently (3/20 runs in loop 1, 1/20 runs in loop 2).
- **Not test interference:** Collection order confirmed `tests/test_disk_limits_retention.py` at items 170–175 while `test_installer_chapter.py` was collected at items 269+, proving installer tests executed after F22.
- **Race mechanism:** `Supervisor._finalize_terminated_process` called `transition_job_state` to write the terminal state into `job.json`, and only then called `usage.record_usage` to append to `usage.jsonl`.
- **Instrumentation proof:** In 10/10 pre-fix runs (`f22-instrument-before.txt`), the terminal state appeared in `job.json` 4.274–7.057 ms before `usage.jsonl` existed. Readers polling with 50 ms sleeps intermittently landed in this window.
- **Fix:** In commit `03d4833` under [D68](../design/decisions.md#d68-usage-record-is-appended-before-the-terminal-state-becomes-observable-f22), `Supervisor._finalize_terminated_process` was modified to accumulate execution time and append `usage.jsonl` *before* persisting the terminal state. Post-fix instrumentation confirmed 0/10 races (`f22-instrument-after.txt`), and the full suite passed 632/632 tests.
- **Extensions:** Under [D76](../design/decisions.md#d76-the-verify-not-found-flake-is-the-auto-fix-observation-window-not-a-persistence-race-c3), the verify-not-found flake was traced to sampling during active auto-fix windows, resolved via `manager.wait`. Under [D77](../design/decisions.md#d77-usage-ordering-on-every-terminalneeds_review-path-and-no-double-counting-across-a-crash-c4), usage-before-state ordering was extended across watcher, tick-failure, and recovery paths.

### 2. Orchestrator Workflow Selection and Budgeting (D79)

The orchestrator guidance in `installer/agents_block.md` was expanded under [D79](../design/decisions.md#d79-workflow-selection-is-a-short-block-section-examples-live-in-orchestrationmd) to formalize workflow selection:
- **Block size constraint:** Net line growth was +9 lines (34 to 43 lines), comfortably below the +28 net-growth ceiling and the 62-line hard cap enforced by `test_agents_block_fits_the_workflow_budget`.
- **Four defined workflows:**
  1. `GATED`: Default for spec-driven development. Orchestrator writes `tests/acceptance/` first, runs one build job, blocks modification of acceptance tests, reviews diff, and merges into `integration/<topic>`.
  2. `MICRO`: 3–5 small chunk jobs with review, diff inspection, and cleanup after each.
  3. `RESEARCH`: Exploratory sub-topics; claims accepted only when marked `VERIFIED` with citations.
  4. `DIRECT`: Tiny edits executed without invoking `agy`.
- **Synchronization:** Verified byte-identical parity between `installer/agents_block.md` and the heredoc in `installer/install.sh` (`sync-check.txt`). Checklist step range was standardized to 3–10 steps, and commit subjects capped at <= 72 characters. The full suite passed 632 tests in 137.95s.

### 3. Verification Loop and PATH Resolution (D52, D53)

Analysis of the verification loop in `docs/reports/verify-loop/verify-flow.md`:
- **Execution lifecycle:** Verification runs strictly once per turn in `JobSupervisor._finalize_terminated_process` after the `agy` process has completely terminated with exit code 0, post-completion security audit has passed, and parsed status is `DONE`.
- **Working directory & isolation:** Executes in `worktree_path.resolve()` under a sanitized environment (`LANG=C.UTF-8`, `GIT_CONFIG_COUNT=3`, `core.fsmonitor=false`, `core.hooksPath=/dev/null`, `core.pager=cat`, `start_new_session=True`).
- **MCP Client PATH divergence:** MCP clients (e.g. `dsh`) execute with default host `PATH` (`/usr/local/bin:/usr/bin:/bin:~/.local/bin`), omitting project virtualenv bin directories. A bare command such as `"pytest"` fails with `FileNotFoundError` (rc 127).
- **Resolution under D53:** Supported configuration allows passing interpreter-qualified verify commands (e.g. `["/path/to/venv/bin/python", "-m", "pytest"]`), resolving the python binary directly and avoiding ambient PATH dependencies.

### 4. Wrapper Commit Subjects, Repo Hooks, and Verify Timeout (D80)

Hardening conducted under `fix/wrapper-verify-commit` and recorded in [D80](../design/decisions.md#d80-wrapper-owned-commit-subjects-repo-hooks-and-per-job-verify-timeout):
- **Commit subject sanitization:** `gitops.sanitize_commit_subject()` enforces printable ASCII (NFKD transliteration), strips control characters, removes trailing periods and whitespace, enforces a 72-character maximum, and falls back to safe non-empty subjects. Checkpoint commits format as `agy: checkpoint <UTC ISO seconds>Z`.
- **Repository hooks enforcement:** Wrapper commits execute with `commit_with_retry(run_hooks=True)` using `GIT_CONFIG_COUNT=2` without `--no-verify`. Hook rejections transition to `needs_review(reason="invalid_commit_message")` with hook stderr captured, preventing silent bypass of local repo policies.
- **Configurable per-job verify timeout:** Replaced the hard-coded 900s timeout with `verify_timeout_seconds` in `agy_start`, ensuring hung test commands kill the entire process group including live grandchild processes.
- **Suite stability:** All 5 sequential full-suite runs passed with 674 tests each (`full-run-1.txt` through `full-run-5.txt`).

## Conclusion

These investigations eliminated critical race conditions, standardized workflow instructions within strict token budgets, established safe command resolution for MCP environments, and aligned wrapper commits with git repository security policies. Supported by decisions [D68](../design/decisions.md#d68-usage-record-is-appended-before-the-terminal-state-becomes-observable-f22), [D76](../design/decisions.md#d76-the-verify-not-found-flake-is-the-auto-fix-observation-window-not-a-persistence-race-c3), [D77](../design/decisions.md#d77-usage-ordering-on-every-terminalneeds_review-path-and-no-double-counting-across-a-crash-c4), [D79](../design/decisions.md#d79-workflow-selection-is-a-short-block-section-examples-live-in-orchestrationmd), and [D80](../design/decisions.md#d80-wrapper-owned-commit-subjects-repo-hooks-and-per-job-verify-timeout).

## Evidence

| Claim | Repo file and line or report path |
|---|---|
| F22 test failure rates in isolated stress loops (3/20 and 1/20 runs) | `docs/reports/installer-fixes/REPORT.md:18` |
| Pytest collection order for `tests/test_disk_limits_retention.py` (items 170–175) and `test_installer_chapter.py` (items 269+) | `docs/reports/installer-fixes/REPORT.md:9-10` |
| Baseline full test suite pass count (622 passed) | `docs/reports/installer-fixes/REPORT.md:14` |
| Deselected baseline pass count (599 passed, 23 deselected) | `docs/reports/installer-fixes/REPORT.md:16-17` |
| Shipped test polling sleep duration (50 ms) | `docs/reports/installer-fixes/REPORT.md:27-28` |
| Instrumented harness polling interval (1 ms) | `docs/reports/installer-fixes/REPORT.md:25` |
| Pre-fix usage inversion window across runs (4.274–7.057 ms in 10/10 runs) | `docs/reports/installer-fixes/REPORT.md:26-27` |
| Post-fix race occurrence rate (0/10 races) | `docs/reports/installer-fixes/REPORT.md:33` |
| F22 failing test commit `6a9140b` and fix commit `03d4833` | `docs/reports/installer-fixes/REPORT.md:35-36` |
| Final post-fix test suite pass count (632 passed) | `docs/reports/installer-fixes/REPORT.md:37` |
| Orchestrator workflow guidance line growth (34 -> 43 lines, +9 net) | `docs/reports/orchestrator-workflows/REPORT.md:12` |
| Agents block test budget ceiling (<= 62 lines) | `docs/reports/orchestrator-workflows/REPORT.md:65`, `doc/design/decisions.md:493` |
| Commit subject maximum length limit (72 characters) | `docs/reports/orchestrator-workflows/REPORT.md:33`, `doc/design/decisions.md:491` |
| Checklist range adjustment in guidance (3–8 to 3–10 steps) | `docs/reports/orchestrator-workflows/REPORT.md:40-41` |
| Full suite pass count on orchestrator workflows (632 passed in 137.95s) | `docs/reports/orchestrator-workflows/final-full-suite.txt:1` |
| Bare command lookup failure exit code in MCP environment (rc 127) | `docs/reports/verify-loop/verify-flow.md:41` |
| Wrapper verify commit full-suite sequential runs (5 runs, 674 passed each) | `docs/reports/wrapper-verify-commit/README.md:10` |
| Number of regression tests in `test_wrapper_verify_commit_fix.py` (14 tests) | `docs/reports/wrapper-verify-commit/README.md:11` |
| Hard-coded default verify timeout (900s) | `docs/reports/wrapper-verify-commit/README.md:11`, `doc/design/decisions.md:497` |
| Installer chapter test definitions | `tests/test_installer_chapter.py` |
| Wrapper verify and commit fix regression tests | `tests/test_wrapper_verify_commit_fix.py` |

## How to reproduce

Execute the installer and wrapper verify regression test suites:
```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_installer_chapter.py tests/test_wrapper_verify_commit_fix.py -q
```

## Sources

- `docs/reports/installer-fixes/REPORT.md`
- `docs/reports/orchestrator-workflows/REPORT.md`
- `docs/reports/verify-loop/verify-flow.md`
- `docs/reports/wrapper-verify-commit/README.md`
- `tests/test_installer_chapter.py`
- `tests/test_wrapper_verify_commit_fix.py`
- `doc/design/decisions.md`
