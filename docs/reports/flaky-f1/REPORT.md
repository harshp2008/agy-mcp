# flaky-f1 report — invalid COMMIT_MSG resume race and SIGKILL readiness

Branch: `exp/flaky-f1` (from `main`). No branch switch, no push, no real `agy`.
All commands used `env -u PYTHONPATH .venv/bin/python -m pytest ...` and the
fake agy at `tests/fake_agy/agy`.

## Timestamps and STEP 0

- STEP 0 start: `2026-10-26T04:06:32+05:30`
- Full suite command:
  `env -u PYTHONPATH timeout 900 .venv/bin/python -m pytest -q 2>&1 | tee docs/reports/flaky-f1/baseline-pytest.txt`
- Raw tail (`docs/reports/flaky-f1/baseline-pytest.txt`):

```
........................................................................ [ 85%]
........................................................................ [ 97%]
...............                                                          [100%]
591 passed in 136.84s (0:02:16)
PYTEST_EXIT_CODE=0
```

- STEP 0 end: `2026-10-26T04:09:23+05:30`
- Failing tests in STEP 0: none (591 passed). The flake is intermittent.

## STEP A — 30 runs of the single f1 test

Command: `docs/reports/flaky-f1/step-a/run_f1_loop.sh` (one pytest process per
run, `--basetemp=/tmp/flaky-f1-runs/run-N`, per-run output saved under
`docs/reports/flaky-f1/step-a/`).

Result: **5 failures / 30 runs** (runs 1, 7, 8, 23, 26). Raw:

```
run=1 status=FAIL ... start=2026-10-26T04:09:26+05:30 end=2026-10-26T04:09:31+05:30
run=7 status=FAIL ... start=2026-10-26T04:09:58+05:30 end=2026-10-26T04:10:03+05:30
run=8 status=FAIL ... start=2026-10-26T04:10:03+05:30 end=2026-10-26T04:10:08+05:30
run=23 status=FAIL ... start=2026-10-26T04:11:26+05:30 end=2026-10-26T04:11:31+05:30
run=26 status=FAIL ... start=2026-10-26T04:11:42+05:30 end=2026-10-26T04:11:47+05:30
TOTAL_FAILURES=5/30
```

### First failure (run 1) raw assertion

```
        res = manager.resume(job.job_id, "Fixed commit message format")
        assert res.state in (JobState.RUNNING, JobState.DONE)
        final_env = manager.wait(job.job_id, seconds=5)
>       assert final_env.state == JobState.DONE
E       AssertionError: assert <JobState.NEE...needs_review'> == <JobState.DONE: 'done'>
E
E         - done
E         + needs_review

tests/test_f1.py:166: AssertionError
=========================== short test summary info ============================
FAILED tests/test_f1.py::test_f1_invalid_commit_msg_kills_process_and_resumes
1 failed in 4.69s
```

Captured job state at that failure (`failure-1/job-state.txt`):

```
state: needs_review
review_reason: invalid_commit_message
run_generation: 1
conversation_id: conv-invalid-msg-001
stale COMMIT_MSG present: True
COMMIT_MSG.seen.* files: []
```

`audit.jsonl` in order (`failure-1/job-state.txt`):

```
[1] job_created
[2] state_transition queued -> running       ("Worktree ready, process spawned & PID confirmed")
[3] state_transition running -> needs_review ("Malformed .agy/COMMIT_MSG after retries", invalid_commit_message)
[4] state_transition needs_review -> running ("Resumed with answer: Fixed commit message format")
[5] process_terminated state=needs_review    (old supervisor, pgid 113456)
[6] state_transition running -> done         ("Process terminated with rc 0")
```

The resumed fake agy ran (`cli.log`: `Resumed conversation conv-invalid-msg-001`)
and the audit contains a `running -> done` event, yet the job record read by
`agy_wait` was still `needs_review` generation 1.

### Second failure (run 7) raw assertion

```
        res = manager.resume(job.job_id, "Fixed commit message format")
        assert res.state in (JobState.RUNNING, JobState.DONE)
        final_env = manager.wait(job.job_id, seconds=5)
>       assert final_env.state == JobState.DONE
E       AssertionError: assert <JobState.NEE...needs_review'> == <JobState.DONE: 'done'>
E
E         - done
E         + needs_review

tests/test_f1.py:166: AssertionError
=========================== short test summary info ============================
FAILED tests/test_f1.py::test_f1_invalid_commit_msg_kills_process_and_resumes
1 failed in 4.72s
```

Captured job state: `state=needs_review`, `review_reason=invalid_commit_message`,
`run_generation=1`, stale `COMMIT_MSG` present, no `COMMIT_MSG.seen.*`.
Its `audit.jsonl` has the same six-event shape as run 1 (resume at
`2026-10-25T22:40:03.012837+00:00`, old `process_terminated` at `.106063`, new
`running -> done` at `.156679`).

## STEP B — root cause

**Cause: a product race between two supervisor generations. Confidence:
PROVEN by an instrumented reproduction that records every `save_job` call with
its thread and call stack.**

Evidence lines (pre-fix tree):

- `src/agy_mcp/manager.py:547` — `_run_supervisor` finally: `refreshed = get_job(job.job_id, self.state_dir)` (reads `needs_review`).
- `src/agy_mcp/manager.py:548` — `self._retain_scratchpad(refreshed)`; `manager.py:619` inside it calls `save_job(refreshed)` and writes the stale record back.
- `src/agy_mcp/manager.py:549-551` — unconditional `self._supervisors.pop(job_id)` / `self._threads.pop(job_id)`, which unregisters the successor.
- `src/agy_mcp/manager.py:1267` — `resume` had no wait for the previous supervisor; it took the job lock and launched a new generation immediately.
- `src/agy_mcp/manager.py:859-907` — `wait()` returns as soon as `state not in (queued, running)` and `has_supervisor` is false; the stale `needs_review` plus the popped registration make it return the transient state.

Instrumented replica (`docs/reports/flaky-f1/step-a/instrumented_repro.py`,
attempt 4; `instrumented-failure-1/timeline.txt`):

```
+  19.726s resume-call job_id=4e34a4c7-c0ac-43c2-bbd2-8b5d2a28204c
+  19.806s save_job   thread=MainThread state=running       gen=2 stack=transition_job_state:317 <- resume:1387
+  19.810s save_job   thread=Thread-11  state=needs_review  gen=1 stack=_retain_scratchpad:619 <- _run_supervisor:548
+  19.811s observe    state=running gen=2
+  19.814s observe    state=needs_review gen=1
+  19.863s wait-return state=needs_review job_state=needs_review reason=invalid_commit_message
```

The stale write lands **4 ms after** resume persisted `running` generation 2.
In runs where the previous thread's read/write both fall before the resume
transition, the same job ends `done` (e.g. attempt 5: stale save at `+29.714s`
before resume at `+29.727s`), which is why the failure rate is intermittent.

### The stale invalid `COMMIT_MSG`

It is left in the worktree: on every captured failure,
`stale COMMIT_MSG present: True` and `COMMIT_MSG.seen.* files: []`. The watcher
never consumes an invalid message (`src/agy_mcp/watcher.py:180-198`: first
failure returns `None`; the second transitions to `needs_review`; neither
renames the file). However, it is **not the cause of this flake**:

- The resumed fake agy exits with rc 0 in well under one supervisor tick
  (`cli.log` line `Resumed conversation ...`; `stdout.txt` has the SUCCESS
  JSON), so the fresh watcher cannot accumulate the two settled validation
  failures needed for another `invalid_commit_message` transition.
- No second `running -> needs_review` event appears after the resume in any
  captured `audit.jsonl`.
- The stale write from the old supervisor is directly observed in the
  instrumented timeline.

The stale file is a latent behavior for a *long* resumed run (the one-retry
allowance is per watcher generation), but it is not this failure.

Also ruled out: the test's own timing. The test waits for state, process death,
conversation id and (in the new deterministic test) the prior finalization
itself; the failure is a real ordering of two product threads, reproducible
without sleeps by blocking the final write.

## STEP C — fix, deterministic test, mutation

Test-first commit (fails 5/5 before the fix):

- `6c2b797 test(f1): reproduce resume overlapping prior supervisor finalization`
- Raw before-fix lines: `docs/reports/flaky-f1/step-b/test-first-before-fix.txt`
  contains five `1 failed in ...` runs, each ending with:
  `E AssertionError: previous supervisor clobbered the resumed generation: state=JobState.NEEDS_REVIEW reason=invalid_commit_message run_generation=1`

Fix commit:

- `76a917b fix(manager): serialize resume with prior supervisor finalization (D65)`
  - `resume` calls `_wait_for_prior_supervisor(job_id)` before taking the job
    lock (`src/agy_mcp/manager.py:1301`). The helper joins the previous
    supervisor thread without the job lock, so an in-thread auto-resume can
    still take it; a 30 s overrun raises
    `ManagerError(code="job_finalizing")`.
  - `_run_supervisor` only unregisters when the registered supervisor is still
    that object (`src/agy_mcp/manager.py:577`).
- After-fix deterministic test: 5/5 passed
  (`docs/reports/flaky-f1/step-b/test-first-after-fix.txt`).
- Original test post-fix loop: **30/30 passed**
  (`docs/reports/flaky-f1/fix/post-fix-loop-30.txt`).
- Mutation check: removed `self._wait_for_prior_supervisor(job_id)` from
  `resume`, reran the deterministic test →
  `docs/reports/flaky-f1/step-b/mutation-no-wait.txt`:

```
>       assert resumed.state != JobState.NEEDS_REVIEW, (
E       AssertionError: previous supervisor clobbered the resumed generation: state=JobState.NEEDS_REVIEW reason=invalid_commit_message run_generation=1
E       assert <JobState.NEEDS_REVIEW: 'needs_review'> != <JobState.NEEDS_REVIEW: 'needs_review'>
tests/test_f1.py:245: AssertionError
1 failed in 4.88s
```

The source was restored from the committed file; `git status --short` showed
only the untracked `docs/reports/flaky-f1/` evidence directory.

### Runner SIGKILL readiness

Commit `28476e1 test(runner): wait for SIGTERM-ignore readiness before signalling`.

- `tests/fake_agy/agy` writes `$TMPDIR/.fake_agy_sigterm_ready` immediately
  after installing `signal.SIG_IGN` for `hang_ignore_sigterm`.
- `tests/test_runner.py::test_runner_kill_escalates_to_sigkill` waits for that
  marker (5 s deadline) instead of `time.sleep(0.05)` before signalling.
- Loop: **30/30 passed**
  (`docs/reports/flaky-f1/fix/runner-loop-30.txt`).
- The original `-15 == -SIGKILL` failure was not reproduced in this session
  (it was reported once); there is no product-code change, so no mutation for
  this item.

## Table

| item | failing-test commit + raw failing line | fix commit | raw passing line (30/30) | mutation + raw failing line |
| --- | --- | --- | --- | --- |
| F1 invalid COMMIT_MSG resume race | `6c2b797`; 5/5 failures, raw: `E AssertionError: previous supervisor clobbered the resumed generation: state=JobState.NEEDS_REVIEW reason=invalid_commit_message run_generation=1` | `76a917b` | `run=30 status=PASS rc=0 seconds=5.75 last=1 passed in 5.55s` / `TOTAL_FAILURES=0/30` | removed `_wait_for_prior_supervisor`; `1 failed in 4.88s`; same `AssertionError ... state=JobState.NEEDS_REVIEW ... run_generation=1` |
| runner SIGKILL readiness | n/a (existing intermittent `assert -15 == -SIGKILL`, not reproduced; test-only hardening) | `28476e1` | `run=30 status=PASS rc=0 last=1 passed in 0.42s` / `TOTAL_FAILURES=0/30` | n/a (no product change) |

## Existing tests changed (justification)

- `tests/test_runner.py::test_runner_kill_escalates_to_sigkill`: replaced a
  50 ms sleep with a readiness-file wait. Stronger: it now proves the child has
  installed `SIG_IGN` before the group is signalled, which is the exact
  condition the test intends to exercise. No assertion was removed or relaxed.
- `tests/fake_agy/agy`: added the readiness marker write for the
  `hang_ignore_sigterm` path. No behavior change for other scenarios.
- No existing test was weakened, skipped, or deleted. The new F1 test was
  added; the original F1 test is unchanged.

## D65 text (docs/DECISIONS.md) and SPEC diff

`docs/DECISIONS.md` D65: resume serialization against the prior supervisor's
finalization, with the 5/30 measured flake, the instrumented ordering, the
join/fail-closed rule, the identity-guarded unregistration, and the residual
invalid-`COMMIT_MSG` note.

`SPEC.md` section 10:

```
*Resume finalization note:* `agy_resume` joins the previous run's supervisor
thread before taking the job lock and starting a new generation (D65). The join
is not a lock acquisition, so the order above is unchanged; a previous
supervisor that has not finished within 30 s fails the call closed with
`error.code="job_finalizing"` instead of letting its stale `job.json` write race
the new `running` state.
```

`docs/CODEMAP.md`: the resume path now says the previous supervisor thread is
joined before the state gate; the generated module index was regenerated.

## Residual / NOT RUN

- The latent stale-invalid-`COMMIT_MSG` behavior (a resumed long run retries the
  same stale file once more because the counter is per watcher generation) was
  documented, not changed. See the decision line at the end.
- The runner `-15 == -SIGKILL` flake was not reproduced before hardening; only
  the post-hardening 30/30 loop and the readiness mechanism are demonstrated.
- Full-suite tail and final lint/codemap/AST evidence are in the final response
  and `docs/reports/flaky-f1/final-pytest.txt`.
