# Log and record formats

Status: current
Audience: operators, contributors

Four formats are written by the wrapper: `audit.jsonl`, `usage.jsonl`,
`server.log` and `verify_tail.txt`. `cli.log` is agy's own file, read by the
wrapper but not written by it. All wrapper-written timestamps are UTC ISO 8601
(`datetime.now(timezone.utc).isoformat()`); the configured `timezone` affects
the daily-cap date only.

## `audit.jsonl`

One JSON object per line, appended with `O_APPEND`, `fsync` after every event,
mode `0600`, in each job directory. The line shape is exactly:

```json
{"ts": "2026-10-24T22:50:00.123456+00:00", "event": "state_transition", "detail": {"from": "queued", "to": "running", "reason": "spawned"}}
```

The `detail` object is event-specific. `cleanup_error` carries only
`{"code": "..."}` (no exception text or host paths) because audit lines are
returned in envelopes; several commit/setup events carry a capped stderr
excerpt or error string instead. Successful audit passes are not individually
logged; the generated table below is the complete set of event names.

The event names are generated from the `event="..."` literals in
`src/agy_mcp/`. A new event name changes the generated table and fails
`scripts/check_docs.py` until this page is refreshed.

<!-- BEGIN GENERATED: audit-events -->
| Event | Emitted in |
|---|---|
| `account_rejected` | `manager.py` |
| `account_selected` | `jobs.py` |
| `account_verified` | `manager.py` |
| `audit_error_during_tick_error` | `supervisor.py` |
| `audit_violation` | `supervisor.py` |
| `checkpoint_rejected` | `watcher.py` |
| `cleanup_error` | `jobs.py` |
| `commit` | `watcher.py` |
| `commit_msg_discarded` | `watcher.py` |
| `commit_msg_repaired` | `watcher.py` |
| `commit_rejected` | `watcher.py` |
| `commit_skipped_no_changes` | `watcher.py` |
| `conversation_id_mismatch` | `supervisor.py` |
| `crash_recovered` | `recovery.py` |
| `deadline_expired` | `supervisor.py` |
| `denial_auto_resume` | `manager.py` |
| `job_created` | `jobs.py` |
| `plan_nudge` | `manager.py` |
| `pre_kill_checkpoint_rejected` | `supervisor.py` |
| `process_terminated` | `supervisor.py` |
| `safety_net_commit_rejected` | `supervisor.py` |
| `safety_net_skipped_only_artifacts` | `gitops.py` |
| `setup_failed` | `manager.py` |
| `state_transition` | `jobs.py` |
| `supervisor_error` | `supervisor.py` |
| `transcript_unreadable` | `transcript.py` |
| `verify_finished` | `supervisor.py` |
| `verify_started` | `supervisor.py` |
<!-- END GENERATED: audit-events -->

`account_selected` and `account_verified` are written when a job is created on a
registry account; `account_rejected` is written once per account the scheduler
could not use (at most five). Their details carry the registry `name`, the
machine-readable reason and a **masked** email only, never the full address.
`conversation_transferred` records a successful db copy (conversation id, size,
source and target account); `handoff_summary` records the fallback, with
`fallback_reason` carrying one of the `account_transfer_*` codes. Those two event
names are passed through a variable, so the generated table above does not list
them.

| Event | Meaning |
|---|---|
| `audit_error_during_tick_error` | The audit step raised during a supervisor tick. The tick handles it and records the exception class; it does not silently pass. |
| `audit_violation` | The security audit found a violation. `detail.reason` names it; a fatal finding moves the job to `needs_review`. |
| `checkpoint_rejected` | A periodic checkpoint commit was rejected (a hook refused it). `detail` carries the capped stderr and cause. |
| `cleanup_error` | A swallowed cleanup failure was recorded with a short `detail.code` only. |
| `commit` | A commit was created. `detail` has `sha`, `subject` and `generation`. |
| `commit_msg_discarded` | An invalid `.agy/COMMIT_MSG` was moved to `COMMIT_MSG.invalid.<n>`. `detail` has the code and target file. |
| `commit_msg_repaired` | An overlong commit subject was repaired before use. `detail.original_len` records the original length. |
| `commit_rejected` | A commit failed (hook or git). `detail` carries the generation and capped stderr/cause. |
| `commit_skipped_no_changes` | A settled message was consumed but there were no uncommitted changes to commit. |
| `conversation_id_mismatch` | The conversation id observed in `cli.log` differs from the stored one. `detail` has both ids. |
| `crash_recovered` | Startup reconciliation recovered a job left by a previous server process. `detail.from` says the recovered state. |
| `deadline_expired` | The job's wall-clock deadline was reached. `detail` has elapsed and deadline seconds. |
| `denial_auto_resume` | The wrapper automatically resumed after a shell denial. `detail.attempt` is the attempt number. |
| `job_created` | The job record was created. `detail` has the title, model, `max_minutes` and `verify`. |
| `plan_nudge` | The one-shot plan nudge was sent. `detail.remaining` is the unticked count at that moment. |
| `pre_kill_checkpoint_rejected` | The pre-cancellation checkpoint commit was rejected. `detail` has capped stderr and cause. |
| `process_terminated` | The supervised process ended. `detail` records the resulting state and return information. |
| `safety_net_commit_rejected` | The final safety-net commit was rejected. `detail` has capped stderr and cause. |
| `safety_net_skipped_only_artifacts` | The safety-net commit was skipped because every remaining file was a build artifact/junk file. |
| `setup_failed` | Worktree provisioning or process launch failed. The job goes to `needs_review(reason=setup_failed)`. |
| `state_transition` | A job changed state. `detail` has `from`, `to` and the trigger/reason. |
| `supervisor_error` | A supervisor tick raised an unexpected error; the tick fails closed and records the error text. |
| `transcript_unreadable` | The confined denied-call transcript could not be read. `detail.reason` says why; the wrapper does not fall back to an untrusted path. |
| `verify_finished` | A verify attempt finished. `detail` has `rc`, duration and outcome. |
| `verify_started` | A verify attempt started. `detail.command` is the token list. |

## `usage.jsonl`

One JSON object per finished run, appended under an exclusive `flock`, mode
`0600`, in the state root. The row is written **before** the terminal state is
persisted, so a reader that sees the new state in `job.json` finds the row
(D68). Fields:

| Field | Meaning |
|---|---|
| `ts` | UTC ISO 8601 time the row was written. |
| `job_id` | The job UUID. |
| `state` | The state the supervisor is about to persist. |
| `model` | The requested model alias. |
| `model_resolved` | The alias agy reported resolving, or `null` when it logged none (D63). |
| `duration_seconds` | This run's wall clock, rounded to 2 decimals. |
| `cumulative_minutes` | The job's accumulated minutes, rounded to 2 decimals. |
| `commits_count` | Commits created up to this point. |
| `tokens` | The agy token object for this run (input/output/total and optional thinking/cache/num_turns). |
| `usage_cost` | The per-job quota cost object, or `null` when unmeasured. |
| `quota_estimate` | The token-based estimate object, or `null` when no total could be determined. |
| `run_generation` | The run number inside the job (resume increments it; older rows count as 1). |
| `wall_clock_seconds` | Alias of `duration_seconds`, kept for the run record. |
| `account` | Optional. The registry name of the account that ran the job, present **only** when a registry is configured (D107). |
| `masked_email` | Optional. The masked account email; present only with `account`, never the full address. |

The row never contains a prompt, title, branch, repository path, code or a full
email address. Recovery does not append a second row for a run whose finalizer
already wrote one (`has_usage_record_for_generation`). Without a registry the
`account`/`masked_email` keys are absent, so the row is byte-identical to the
single-account record.

## `server.log`

Python `logging` at `INFO`, with this format, to stderr and
`<state>/server.log` (never stdout):

```text
%(asctime)s [%(levelname)s] %(name)s: %(message)s
```

An unexpected tool exception logs the traceback here and returns
`internal_error` to the client. Startup logs the recovered-job count; shutdown
logs that supervisor threads stop without killing the agy process groups.

## `verify_tail.txt`

The captured verification failure excerpt for a job whose review reason is a
verify failure/timeout. It is written best-effort; when missing, `agy_result`
falls back to the `verify_tail` value stored in the audit event. It is a plain
text tail, not JSON.

## `cli.log`

agy's own log, passed by path on the command line. The wrapper reads at most a
2 MB tail for denial lines and a 1 MB head for the early conversation id, and
uses a 4 KB prefix fingerprint to tell an appended generation from a truncated
one. It recognizes two denial line shapes and the glog-prefixed "Created
conversation <uuid>"/"resuming conversation" lines; everything extracted is
treated as untrusted data. The file format itself belongs to agy.

## Sources

- `src/agy_mcp/jobs.py` — `append_audit_event`, the `AuditEvent` shape and the
  `state_transition` detail.
- `src/agy_mcp/models.py` — `AuditEvent`, `TokenUsage`.
- `src/agy_mcp/usage.py` — `record_usage` and the `usage.jsonl` fields.
- `src/agy_mcp/accounts/pools.py` — the typed per-account pool/window view behind `account`.
- `src/agy_mcp/server.py` — the `server.log` format and handlers.
- `src/agy_mcp/supervisor.py` — the audit event call sites and `verify_tail.txt`.
- `src/agy_mcp/watcher.py`, `src/agy_mcp/gitops.py`, `src/agy_mcp/recovery.py`,
  `src/agy_mcp/transcript.py` — the remaining event call sites.
- `src/agy_mcp/cli_log.py` — the bounded `cli.log` reads and the recognized line
  shapes.
- `doc/design/spec.md` — §17, the audit/usage log contract.
- `doc/audits/2026-11-02-part2c-coverage.md` — §10, the format inventory
  (COV-08).
