# 11 — Investigation of Flaky F1 Resume Race and SIGKILL Readiness

Status: current
Audience: maintainers, contributors

Empirical investigation into an intermittent test failure in `tests/test_watcher_needs_review_resume.py`
where resuming a job after an invalid commit message unexpectedly reverted to
`needs_review`, identifying an inter-thread race during supervisor hand-off,
proving the failure deterministically, and resolving runner SIGKILL timing flakes.

## Question

Why did `tests/test_watcher_needs_review_resume.py::test_f1_invalid_commit_msg_kills_process_and_resumes`
intermittently fail with `assert NEEDS_REVIEW == DONE`, how did the prior supervisor's
finalization clobber the resumed generation, and what mechanism ensures safe process
termination escalation to SIGKILL?

## Method

Investigated on 2026-10-26 (04:06:32 to 04:09:23+05:30) on branch `exp/flaky-f1`
(forked from `main`). All tests used `tests/fake_agy/agy`; real `agy` was not run.

1. **STEP 0 Baseline:** Ran the full test suite (`pytest -q`, 900s timeout). All 591 tests passed in 136.84s (0 failures), confirming the flake is intermittent.
2. **STEP A Stress Loop:** Executed 30 sequential runs of the isolated F1 test (`tests/test_watcher_needs_review_resume.py::test_f1_invalid_commit_msg_kills_process_and_resumes`) with isolated basetemp directories (`/tmp/flaky-f1-runs/run-N`). Captured job state, `audit.jsonl`, and `cli.log` upon failure.
3. **STEP B Root Cause Proof:** Traced `save_job` call stacks across threads to demonstrate the asynchronous overwrite.
4. **Deterministic Reproduction and Mutation:** Created a test-first deterministic reproduction in `tests/test_watcher_needs_review_resume.py`, patched the race in `JobManager.resume`, executed a mutation check, and validated with a post-fix 30-run loop.
5. **Runner Hardening:** Evaluated `tests/test_runner.py::test_runner_kill_escalates_to_sigkill` readiness race under SIGTERM ignores.

## Result

### 1. Stress Loop Reproductions (5 Failures in 30 Runs)

In 30 runs of the single F1 test, 5 failures occurred (runs 1, 7, 8, 23, and 26):
- **Run 1 Duration:** 4.69s
- **Run 7 Duration:** 4.72s
- **Failure Assertion (`tests/test_watcher_needs_review_resume.py:166`):**
  ```text
  >       assert final_env.state == JobState.DONE
  E       AssertionError: assert <JobState.NEEDS_REVIEW: 'needs_review'> == <JobState.DONE: 'done'>
  ```

Captured job state at failure:
- `state`: `needs_review`
- `review_reason`: `invalid_commit_message`
- `run_generation`: `1`
- `conversation_id`: `conv-invalid-msg-001`
- `stale COMMIT_MSG present`: `True`

Captured `audit.jsonl` event sequence:
1. `job_created`
2. `state_transition queued -> running`
3. `state_transition running -> needs_review` ("Malformed .agy/COMMIT_MSG after retries", `invalid_commit_message`)
4. `state_transition needs_review -> running` ("Resumed with answer: Fixed commit message format")
5. `process_terminated state=needs_review` (old supervisor generation, PGID 113456)
6. `state_transition running -> done` ("Process terminated with rc 0")

Although the resumed runner ran and reached `done` (event 6), the persisted job state observed by `agy_wait` reverted to `needs_review` with generation 1.

### 2. Root Cause: Supervisor Hand-Off Race

The failure was a race between two supervisor threads across generation hand-off:
1. Generation 1 entered `_finalize_terminated_process` after the malformed `COMMIT_MSG` kill and transitioned `job.json` to `needs_review`.
2. `manager.resume()` was called, acquiring the job lock, incrementing `run_generation` to 2, writing `state=running`, spawning the Generation 2 supervisor thread, and registering it in `self._supervisors[job_id]`.
3. Meanwhile, Generation 1's `_run_supervisor` finally block resumed:
   - Line 547: Read `refreshed = get_job(job.job_id)` (reading `needs_review` before generation 2's write).
   - Line 548: Called `self._retain_scratchpad(refreshed)`, which called `save_job(job)` at line 619, overwriting the new `running` state with the stale `needs_review` record (~4 ms later).
   - Line 557-558: Unconditionally deleted `self._supervisors[job.job_id]`, evicting the newly spawned Generation 2 supervisor.
4. `manager.wait()` observed no registered supervisor in `_supervisors` and read the clobbered `job.json`, returning transient `needs_review` instead of waiting for Generation 2's completion.

### 3. Fix and Deterministic Verification

The fix was applied in commit `76a917b` under [D65](../design/decisions.md#d65-resume-serialization-against-the-prior-supervisors-finalization-f1):
1. **Thread Join on Resume:** `JobManager.resume` calls `self._wait_for_prior_supervisor(job_id)` before taking the job lock, joining the prior supervisor thread with a 30s deadline (failing closed with `ManagerError(code="job_finalizing")` on timeout).
2. **Guarded Unregistration:** `_run_supervisor` unregisters from `_supervisors` only if `self._supervisors[job_id]` is still that exact supervisor instance.
3. **Deterministic Test:** `tests/test_watcher_needs_review_resume.py::test_f1_resume_serializes_with_previous_supervisor_finalization` artificially delayed the prior supervisor in `_retain_scratchpad`. It failed 5/5 before the fix (`6c2b797`) and passed 5/5 after the fix.
4. **Stress Loop Validation:** The original F1 test ran 30/30 passes (0 failures in 30 runs).
5. **Mutation Check:** Removing `_wait_for_prior_supervisor` immediately reproduced the failure in 4.88s (`AssertionError: previous supervisor clobbered the resumed generation`).

### 4. Runner SIGKILL Readiness

An intermittent assertion failure (`assert -15 == -SIGKILL`) in `tests/test_runner.py::test_runner_kill_escalates_to_sigkill` occurred when `time.sleep(0.05)` expired before the child process installed its `SIG_IGN` signal handler for `SIGTERM`.
Under commit `28476e1`:
- `tests/fake_agy/agy` writes `$TMPDIR/.fake_agy_sigterm_ready` immediately after installing `SIG_IGN`.
- `test_runner_kill_escalates_to_sigkill` polls for that file with a 5s deadline before issuing signals.
- Post-hardening loop: 30/30 runs passed.

### 5. Stale COMMIT_MSG Discard Policy

D65 resolved the supervisor thread race but left the invalid `.agy/COMMIT_MSG` in the worktree, meaning a resumed run started with a fresh watcher counter and re-validated the invalid file.
This was settled by [D66](../design/decisions.md#d66-discard-the-invalid-commit_msg-on-needs_reviewinvalid_commit_message): on transition to `needs_review(invalid_commit_message)`, the wrapper atomically renames the offending message to `.agy/COMMIT_MSG.invalid.<n>` (bounded to 20 slots), ensuring resumed runs do not re-encounter stale invalid inputs.

## Conclusion

Inter-generation lifecycle transitions cannot rely on atomic disk replacement alone when multiple supervisor threads overlap. Under [D65](../design/decisions.md#d65-resume-serialization-against-the-prior-supervisors-finalization-f1) and [D66](../design/decisions.md#d66-discard-the-invalid-commit_msg-on-needs_reviewinvalid_commit_message):
1. `JobManager.resume` strictly joins the previous supervisor thread before initiating a new run generation.
2. Supervisor unregistration is guarded by object identity.
3. Malformed commit messages are moved to bounded invalid backup slots upon review transition.

## Evidence

| Claim | Repo file and line or report path |
|---|---|
| Investigation start and STEP 0 timestamp (`2026-10-26T04:06:32+05:30`) | `docs/reports/flaky-f1/REPORT.md:9` |
| STEP 0 baseline pytest pass count (591 passed in 136.84s, 0 failures) | `docs/reports/flaky-f1/REPORT.md:18,20` |
| STEP A 30-run loop failure count (5 failures in 30 runs: 1, 7, 8, 23, 26) | `docs/reports/flaky-f1/REPORT.md:25-34` |
| Run 1 failure assertion and duration (4.69s) | `docs/reports/flaky-f1/REPORT.md:46,52` |
| Run 7 failure assertion and duration (4.72s) | `docs/reports/flaky-f1/REPORT.md:84,90` |
| Captured job state run generation at failure (`run_generation: 1`) | `docs/reports/flaky-f1/REPORT.md:59` |
| Captured job conversation ID (`conv-invalid-msg-001`) | `docs/reports/flaky-f1/REPORT.md:60` |
| Audit event sequence count on failed run (6 events) | `docs/reports/flaky-f1/REPORT.md:65-74` |
| Supervisor scratchpad overwrite race window (~4 ms) | `docs/reports/flaky-f1/REPORT.md:117`, `doc/design/decisions.md:369` |
| Deterministic failing test commit `6c2b797` (5/5 failed) | `docs/reports/flaky-f1/REPORT.md:143` |
| Hand-off fix commit `76a917b` | `docs/reports/flaky-f1/REPORT.md:143` |
| Supervisor thread join timeout deadline (30s) | `docs/reports/flaky-f1/REPORT.md:124`, `doc/design/decisions.md:371` |
| Deterministic test post-fix pass count (5/5 passed) | `docs/reports/flaky-f1/REPORT.md:129` |
| Post-fix original test stress loop result (30/30 passed) | `docs/reports/flaky-f1/REPORT.md:131,143` |
| Mutation check failure duration (1 failed in 4.88s) | `docs/reports/flaky-f1/REPORT.md:139,143` |
| Runner SIGKILL readiness hardening commit `28476e1` | `docs/reports/flaky-f1/REPORT.md:134,143` |
| Runner readiness poll deadline (5s) vs replaced sleep (50ms) | `docs/reports/flaky-f1/REPORT.md:136,149` |
| Runner post-hardening loop result (30/30 passed) | `docs/reports/flaky-f1/REPORT.md:138,143` |
| Discard slots cap for invalid commit messages (20 slots) | `doc/design/decisions.md:379` |
| Deterministic hand-off test definition | `tests/test_watcher_needs_review_resume.py:173` |
| Comprehensive resume and commit message test suite | `tests/test_resume_and_commitmsg.py` |

## How to reproduce

Run the deterministic resume hand-off test and runner escalation test:
```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_watcher_needs_review_resume.py::test_f1_resume_serializes_with_previous_supervisor_finalization tests/test_runner.py::test_runner_kill_escalates_to_sigkill -q
```

## Sources

- `docs/reports/flaky-f1/REPORT.md`
- `tests/test_watcher_needs_review_resume.py`
- `tests/test_resume_and_commitmsg.py`
- `src/agy_mcp/manager.py`
- `doc/design/decisions.md`
