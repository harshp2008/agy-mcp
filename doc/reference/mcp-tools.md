# MCP tools

Status: current
Audience: orchestrators, integrators, contributors

The server registers ten MCP tools. All of them are `async` and return a JSON
object. The six job tools (`start`, `status`, `wait`, `result`, `cancel`,
`resume`) return the standard envelope: `job_id`, `state`,
`elapsed_seconds`, `title`, `model_resolved`, `branch`, `base_commit`,
`head_commit`, `commits_count`, `files_modified`, `skipped_files`, `denials`,
`denied_commands`, `denied_tools`, `warnings`, `tokens`, `next_actions`,
`error`, `usage_cost`, `quota_estimate`, `conversation_continued`,
`resume_after`, `plan`, `facts`, `agent_report`, `scratchpad` and `verify`.
`agy_review`, `agy_cleanup`, `agy_list` and `agy_usage` return their own payload
(described below). A failed call returns an error envelope; see
[errors.md](errors.md) for the codes and recovery.

The parameter table is generated from `tests/fixtures/tools_schema.json`, which
is pinned to the registered tool signatures by the server tests. The per-tool
notes below add returns, errors and an example. The tool docstrings (the text a
client shows to a model) are the canonical "when to call / next action" wording.

## Parameters

<!-- BEGIN GENERATED: mcp-tools -->
| Tool | Parameters |
|---|---|
| `agy_start` | `repo_path`: string (required), `prompt`: string (required), `title`: string (required), `model`: string = "gemini-3.8-flash-medium", `max_minutes`: integer = 240, `verify`: string|null = null, `checklist`: array|null = null, `verify_timeout_seconds`: integer|null = null |
| `agy_status` | `job_id`: string (required), `tail_lines`: integer = 20 |
| `agy_wait` | `job_id`: string (required), `seconds`: integer = 30 |
| `agy_result` | `job_id`: string (required) |
| `agy_cancel` | `job_id`: string (required) |
| `agy_list` | none |
| `agy_resume` | `job_id`: string (required), `answer`: string (required), `follow_up`: boolean = false |
| `agy_review` | `job_id`: string (required) |
| `agy_cleanup` | `job_id`: string (required), `remove_worktree`: boolean = true, `delete_branch`: boolean = false, `force`: boolean = false |
| `agy_usage` | none |
<!-- END GENERATED: mcp-tools -->

## `agy_start`

Launches a job in a dedicated worktree. The call validates synchronously and
returns `state="queued"`; a background thread provisions the worktree and spawns
`agy`, so a launch failure appears later on the job (`failed`, with the error in
`job.json` and the logs), not in this response.

- **Errors:** `missing_prompt`, `invalid_checklist`, `checklist_too_long`,
  `checklist_item_too_long`, `invalid_verify_timeout`, `max_minutes_over_cap`,
  `model_not_allowed`, `verify_not_allowed`, `concurrency_limit_exceeded`,
  `cap_exceeded`, `low_quota`, `insufficient_disk_space`, `state_disk_full`,
  the preflight codes from `worktree.validate_repo_path`
  (`repo_not_found`, `not_a_directory`, `home_prohibited`,
  `state_dir_prohibited`, `path_outside_allowed_roots`, `path_outside_home`,
  `not_a_git_repo`, `linked_worktree_prohibited`, `invalid_git_dir`,
  `unborn_head`, `source_dirty`, `untrusted_agents_dir`) and `manager_error`.
- **Example:**

```json
{"repo_path": "~/code/widget", "title": "Add a retry helper",
 "prompt": "Add a bounded retry helper in src/widget/retry.py with tests.",
 "model": "gemini-3.8-flash-medium", "max_minutes": 240,
 "verify": "pytest -q", "checklist": ["Add helper", "Add tests"]}
```

## `agy_status`

Read-only progress view. Adds `last_cli_log_mtime` (float),
`last_commit_at` (ISO timestamp or null), `worktree_dirty` (bool),
`recent_log_tail` (bounded, redacted list), `conversation_id`,
`conversation_id_source`, `runs` (per-run records) and `wall_clock_seconds`
(null until a run has ended) to the standard envelope.

- **Errors:** `invalid_job_id`, `invalid_input` (job not found), `manager_error`.
- **Example:**

```json
{"job_id": "<job-id>", "tail_lines": 20}
```

## `agy_wait`

Blocks until the job leaves `running` (or `queued`) or the call times out. The
result is the standard envelope; a still-running job is returned with its
current state so the caller can call again. `seconds` defaults to 30, is
clamped to a minimum of 1 and a maximum of 300; a clamp is reported in
`warnings` (D58).

- **Errors:** `invalid_job_id`, `invalid_input` (job not found), `manager_error`.
- **Example:**

```json
{"job_id": "<job-id>", "seconds": 30}
```

## `agy_result`

Returns the final output and review material: `response` (capped at 64 KB),
`diff_stat` (8 KB), `commits` (up to 50) and `audit_events_tail`. For a failed
job with no stdout, the captured stderr tail is used as the response; for a
verification failure, `verify_tail.txt` supplies the tail.

- **Errors:** `invalid_job_id`, `invalid_input` (job not found), `manager_error`.
- **Example:**

```json
{"job_id": "<job-id>"}
```

## `agy_cancel`

Cancels a queued or running job. A queued job is moved to `cancelled` and its
worktree/branch are removed; a running job goes through the pre-kill checkpoint
and the SIGTERM/SIGKILL sequence, then is finalized as `cancelled`. The result is
the standard envelope; an already-finished job is returned unchanged.

- **Errors:** `invalid_job_id`, `invalid_input` (job not found), `manager_error`.
- **Example:**

```json
{"job_id": "<job-id>"}
```

## `agy_list`

Lists every job as a standard envelope plus global metrics:
`jobs`, `total_active_jobs` (`queued` + `running`), `disk_usage_bytes` and
`drifted_worktrees`. It also makes a best-effort retention sweep before
listing; a sweep failure is swallowed and does not fail the call.

- **Errors:** `manager_error` for an unexpected failure.
- **Example:**

```json
{}
```

## `agy_resume`

Resumes a job in `needs_decision`, `timeout`, or a non-security `needs_review`,
optionally with `follow_up=true` to continue a `done` or `cancelled` job in the
same conversation and worktree. It returns the standard envelope with
`state="running"` and `conversation_continued`: `true` when agy reported
resuming the same UUID (or a valid id was passed and no report exists yet),
`false` for a self-contained resume or a new conversation.

- **Errors:** `invalid_job_id`, `invalid_input` (job not found), `job_finalizing`
  (previous supervisor still finalizing), `invalid_state_for_resume`,
  `cannot_resume_security_review`, `cap_exceeded` (cumulative budget),
  `concurrency_limit_exceeded`, `low_quota`, `audit_violation`,
  `verify_not_allowed`, `manager_error`.
- **Example:**

```json
{"job_id": "<job-id>", "answer": "Use option B; keep the retry bounded.", "follow_up": false}
```

## `agy_review`

Read-only merge review: `merge_base`, `ahead`, `behind`, `base_moved`,
`audit_findings` and `untrusted_diff` (wrapped and capped) plus the standard
envelope. A missing/corrupted `snapshot.json` or a failed audit query produces a
**fatal finding** (`audit_violation` or `audit_unavailable`) and the envelope
says a human must look; it is not an error envelope.

- **Errors:** `invalid_job_id`, `invalid_input` (job not found), `manager_error`.
- **Example:**

```json
{"job_id": "<job-id>"}
```

## `agy_cleanup`

Prunes worktree metadata and optionally deletes the temporary branch. It returns
`job_id`, `worktree_removed`, `branch_deleted` and `freed_bytes`. It refuses a
dirty worktree or a branch with unmerged commits unless `force=true`, and
refuses a running or queued job unless `force=true` (which cancels first).

- **Errors:** `invalid_job_id`, `invalid_input` (job not found), `job_is_running`,
  `worktree_dirty`, `unmerged_commits`, `cleanup_failed`.
- **Example:**

```json
{"job_id": "<job-id>", "remove_worktree": true, "delete_branch": false, "force": false}
```

## `agy_usage`

Read-only quota report. It never starts, resumes, cancels or mutates a job. The
payload is `available`, `reason`, `cached` (a 60-second in-memory cache of
successful readings), `account` (`masked_email`, `auth_method`) and `groups`
(each with `buckets`: `window`, `remaining_fraction`, `reset_time_utc`,
`resets_in_seconds`, `low`). It fails soft: an unreadable `/usage` reply is
`available: false` with a `reason` (`unavailable`, `timeout`,
`output_too_large`, `auth_required`, `command_failed`, `unrecognized_format`),
not an error envelope. The email is always masked and is never persisted.

- **Errors:** none for a usage failure; an unexpected internal failure is
  `internal_error`.
- **Example:**

```json
{}
```

## Sources

- `src/agy_mcp/server.py` — the ten `@mcp.tool()` wrappers, their docstrings,
  `_run_tool` and `_format_error`.
- `src/agy_mcp/manager.py` — the `start`, `status`, `wait`, `result`, `cancel`,
  `list`, `resume`, `review`, `cleanup` and `usage` method contracts.
- `src/agy_mcp/models.py` — `StandardEnvelope`, `JobStatusResponse`,
  `JobResultResponse`, `JobReviewResponse`, `JobCleanupResponse`,
  `JobListResponse` and the byte caps.
- `tests/fixtures/tools_schema.json` — the pinned parameter schemas.
- `src/agy_mcp/usage.py` — the `agy_usage` payload and its fail-soft reasons.
- `src/agy_mcp/worktree.py` — the preflight codes named under `agy_start`.
- `doc/design/spec.md` — §8, the normative per-tool contract.
- `doc/audits/2026-11-02-part2c-coverage.md` — §3, the per-tool error/example
  gap (COV-07).
