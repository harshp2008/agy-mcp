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

### `account_selected` decision trace (D115)

When a registry account is chosen, `account_selected.detail` also carries an
additive `decision_trace` object. With no `[[accounts]]` registry the event is
not written at all, so single-account audit output is unchanged; when no trace
is present the detail is exactly the pre-existing
`{account, account_masked_email?}` shape. The trace is bounded to
`MAX_TRACE_ACCOUNTS` (32) rows, which is the maximum `max_accounts`, so the cap
never truncates a valid registry; if it ever does, `truncated: true` is added.

```json
"decision_trace": {
  "accounts": [
    {"account": "default", "account_masked_email": "jo***@example.com", "eligible": true,
     "pool": "default", "weekly_remaining_pct": 58.0, "five_hour_remaining_pct": 91.0,
     "hours_to_binding_reset": 0.6667, "binding_window": "weekly",
     "urgency_score": 87.0, "expiry_boost": 0.0, "rank": 1},
    {"account": "account-b", "account_masked_email": "av***@example.com", "eligible": true,
     "pool": "account-b", "weekly_remaining_pct": 88.0, "five_hour_remaining_pct": 90.0,
     "hours_to_binding_reset": 96.0, "binding_window": "weekly",
     "urgency_score": 0.9167, "expiry_boost": 0.0, "rank": 2},
    {"account": "account-c", "eligible": false, "rejection": "disabled"}
  ],
  "winner": "default",
  "rule": "urgency"
}
```

| Field | Meaning |
|---|---|
| `accounts` | One row per registered account the scheduler considered, in name order (capped). |
| `account` | Registry name. |
| `account_masked_email` | The masked form already produced by the `/usage` reading; omitted when the account was not probed. Never a full address. |
| `eligible` | Whether the account could take this job (strict fit, or burn-eligible for a background job). |
| `rejection` | Present only when `eligible` is false: the scheduler's machine-readable reason (`disabled`, `identity_unverified`, `policy_unverified`, `model_unavailable`, `usage_unavailable`, `account_concurrency`, `reserved_for_normal`, `quota_exhausted`). |
| `pool` | The quota-bucket key; accounts sharing it show the group minimum. |
| `weekly_remaining_pct` / `five_hour_remaining_pct` | Effective remaining percentage after the group's reservations, rounded to 4 decimals. |
| `hours_to_binding_reset` | Hours to the reset of the window that bound the decision. For a background burn this is the later of the two window resets; for a strict fit it is the weekly reset used by the urgency score. |
| `binding_window` | `weekly` or `5h`: which window produced `hours_to_binding_reset`. A full window is never chosen. |
| `urgency_score` | `urgency + expiry_boost`; a full (100%, within the configured margin) weekly window contributes zero urgency. |
| `expiry_boost` | The optional `expires_at` boost component of the score. |
| `rank` | 1-based position among eligible accounts; `1` is the winner. |
| `winner` | The selected registry name (omitted when no account won). |
| `rule` | The first ranking criterion that separated the winner from the runner-up: `urgency`, `expires_at`, `load`, `lru` (also the final name tie-break), `only_candidate`, `explicit_account` or `force`. |
| `truncated` | Present and `true` only when the row list hit the bound. |


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
| `masked_email` | Optional. The masked account email in `head***tail@domain` shape; present only with `account`, never the full address. |

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
