# Error codes returned by agy-mcp

Status: current
Audience: orchestrators, integrators, contributors

Every failed MCP tool call returns the standard envelope with an `error` object:

```json
{"error": {"code": "invalid_job_id", "message": "Invalid job_id format '...'"}}
```

The `code` is stable (tests and callers depend on it); the `message` is
human-readable and is sanitized before it leaves the server: `/home/<user>` and
`/Users/<user>` become `~`, `/root` becomes `~`, and an `[Errno ...]` path is cut
to its basename. When the failure belongs to an existing job, the envelope also
carries that job's normal fields; otherwise a minimal error envelope is built
from `StandardEnvelope` defaults. An unexpected exception is logged with its
traceback to `server.log` and returned as `internal_error` without details.

The table below is generated from the `code=` literals and exception defaults in
`src/agy_mcp/`. The 40 stable codes named by the Part 2 audit are here, plus the
account-registry/identity codes and the two server-generated fallbacks
(`invalid_input`, `internal_error`).

## Codes by source

<!-- BEGIN GENERATED: error-codes -->
| Code | Raised in |
|---|---|
| `account_concurrency_limit` | `manager.py` |
| `account_config_invalid` | `identity.py`, `registry.py` |
| `account_error` | `errors.py` |
| `account_home_invalid` | `registry.py` |
| `account_home_permissions` | `registry.py` |
| `account_identity_mismatch` | `identity.py` |
| `account_identity_unknown` | `identity.py` |
| `account_limit_exceeded` | `registry.py` |
| `account_name_duplicate` | `registry.py` |
| `account_name_invalid` | `registry.py` |
| `account_not_found` | `manager.py`, `registry.py` |
| `audit_violation` | `manager.py` |
| `cannot_resume_security_review` | `manager.py` |
| `cap_exceeded` | `manager.py` |
| `checklist_item_too_long` | `manager.py` |
| `checklist_too_long` | `manager.py` |
| `cleanup_failed` | `manager.py` |
| `concurrency_limit_exceeded` | `manager.py` |
| `forbidden_flag` | `runner.py` |
| `home_prohibited` | `worktree.py` |
| `insufficient_disk_space` | `manager.py`, `worktree.py` |
| `internal_error` | `server.py` |
| `invalid_argv` | `runner.py` |
| `invalid_checklist` | `manager.py` |
| `invalid_git_dir` | `worktree.py` |
| `invalid_input` | `server.py` |
| `invalid_job_id` | `manager.py` |
| `invalid_priority` | `manager.py` |
| `invalid_repo_state` | `worktree.py` |
| `invalid_state_for_resume` | `manager.py` |
| `invalid_verify_timeout` | `manager.py` |
| `job_finalizing` | `manager.py` |
| `job_is_running` | `manager.py` |
| `linked_worktree_prohibited` | `worktree.py` |
| `low_quota` | `manager.py` |
| `manager_error` | `manager.py` |
| `max_minutes_over_cap` | `manager.py` |
| `missing_prompt` | `manager.py` |
| `model_not_allowed` | `manager.py` |
| `no_account_eligible` | `manager.py` |
| `not_a_directory` | `worktree.py` |
| `not_a_git_repo` | `worktree.py` |
| `path_outside_allowed_roots` | `worktree.py` |
| `path_outside_home` | `worktree.py` |
| `prompt_too_large` | `runner.py` |
| `repo_not_found` | `worktree.py` |
| `runner_error` | `runner.py` |
| `source_dirty` | `worktree.py` |
| `state_dir_prohibited` | `worktree.py` |
| `state_disk_full` | `manager.py` |
| `unborn_head` | `worktree.py` |
| `unmerged_commits` | `manager.py` |
| `untrusted_agents_dir` | `worktree.py` |
| `verify_not_allowed` | `manager.py` |
| `worktree_dirty` | `manager.py` |
<!-- END GENERATED: error-codes -->

## Meaning and recovery

| Code | Meaning | How to recover |
|---|---|---|
| `audit_violation` | `agy_resume` was blocked: the audit baseline `snapshot.json` is missing/corrupted, or the resume audit found a fatal violation. | Do not resume. Run `agy_review` and read `audit_findings`; if the violation is security-related, discard the job or start a new one after fixing the cause. |
| `cannot_resume_security_review` | The job is in `needs_review` with a security reason; resuming a security-blocked job is refused. | Inspect `agy_review.audit_findings`; fix the repository/host state, then start a new job. Do not force a resume. |
| `cap_exceeded` | A start hit `daily_jobs_cap`, or a resume hit the job's cumulative minute budget. | For the daily cap, wait for the next local calendar day (or raise `daily_jobs_cap`). The cumulative budget (`max_minutes` bounded by `max_minutes_cap`) is not extendable; start a new job. |
| `checklist_item_too_long` | A checklist item is longer than 200 characters. | Shorten the item. The limit is `scratchpad.MAX_CHECKLIST_ITEM_CHARS`. |
| `checklist_too_long` | More than 30 checklist items were supplied. | Reduce the list to at most 30 entries (`scratchpad.MAX_CHECKLIST_ITEMS`). |
| `cleanup_failed` | `agy_cleanup` failed for a reason other than a dirty worktree or unmerged commits. | Retry once; inspect the worktree and branch manually. Use `force=true` only after reviewing what it would delete. |
| `concurrency_limit_exceeded` | The number of active (queued or running) jobs reached `max_concurrent_jobs`. | Wait for a job to leave `queued`/`running`; `agy_list` shows the active count. |
| `forbidden_flag` | `build_argv` rejected a flag that weakens the security model. | Never pass `--sandbox`, `--effort`, `-c` or `--dangerously-skip-permissions`; this is an internal contract violation, not a user option. |
| `home_prohibited` | `repo_path` resolved to `$HOME` itself. | Choose a repository directory below `$HOME`, not `$HOME`. |
| `insufficient_disk_space` | Free space on the state filesystem is below `min_free_gb` (preflight or start). | Free disk space, or lower `min_free_gb` in `config.toml`. |
| `invalid_argv` | The runner rejected an invalid mode, output format, empty model or a non-positive print timeout. | Use mode `accept-edits`, output format `json`, a non-empty model and a timeout of at least 1 second. |
| `invalid_checklist` | `checklist` is present but is not a list of strings. | Pass a JSON array of strings, or omit the argument. |
| `invalid_git_dir` | The repository has a `.git` entry that is neither a directory nor a linked-worktree file. | Repair the repository (the `.git` entry is corrupt); then retry. |
| `invalid_job_id` | `job_id` is not a canonical 36-character UUID. | Use the exact id returned by `agy_start` or found in `agy_list`. |
| `invalid_repo_state` | The `PreflightError` class default. Every current preflight call passes a more specific code, so this is a compatibility default rather than a live refusal. | Treat a message carrying it as a generic preflight failure; read the message for the check. |
| `invalid_state_for_resume` | `agy_resume` was called in a state that does not allow it, and `follow_up` was not set for `done`/`cancelled`. | Check `agy_status`. Pass `follow_up=true` only to continue a `done` or `cancelled` job; `needs_decision`, `timeout` and non-security `needs_review` resume without it. |
| `invalid_verify_timeout` | `verify_timeout_seconds` is not an integer between 1 and 86400. | Omit the argument (uses `verify_timeout_seconds` from config, default 900) or pass a valid integer. |
| `job_finalizing` | A previous supervisor for this job is still finalizing, so a new resume cannot start yet. | Wait a moment and retry `agy_resume`. |
| `job_is_running` | `agy_cleanup` was called on a running or queued job without `force=True`. | Cancel the job first, then clean up. `force=true` cancels and then cleans up; use it only when that is intended. |
| `linked_worktree_prohibited` | `repo_path` is a linked worktree or a submodule (its `.git` is a file). | Pass the main repository path instead. |
| `low_quota` | `usage_guard=block` and the selected model's quota is below `low_quota_five_hour_pct` or `low_quota_weekly_pct`. | Wait for the reset (`resume_after` names it), or change `usage_guard` to `warn`/`off`. Unavailable usage never blocks. |
| `manager_error` | Default `ManagerError` code when no more specific code applies. | Read the message; it is the manager's own validation text. |
| `max_minutes_over_cap` | Requested `max_minutes` exceeds `max_minutes_cap`. | Lower `max_minutes`, or raise `max_minutes_cap` (the hard ceiling is configured, default 360). |
| `missing_prompt` | `prompt` is empty or only whitespace. | Send a non-empty prompt. |
| `model_not_allowed` | The model is not in `model_allowlist`. | Use an allowlisted alias (for example `gemini-3.8-flash-medium`), or add the model to `model_allowlist` after deciding it is safe. |
| `not_a_directory` | `repo_path` exists but is not a directory. | Pass a directory path. |
| `not_a_git_repo` | The directory has no `.git` entry. | Point at a git repository, or initialise one. |
| `path_outside_allowed_roots` | `repo_path` is outside every `allowed_workdir_roots` entry. | Choose a path under a configured root, or add the root to the allowlist. |
| `path_outside_home` | With no `allowed_workdir_roots`, `repo_path` is outside `$HOME`. | Move the repository under `$HOME`, or configure `allowed_workdir_roots`. |
| `prompt_too_large` | The prompt exceeds 100,000 bytes (the Linux argv boundary). | Put the long specification in a file inside the repository and reference it from a short prompt. |
| `repo_not_found` | `repo_path` does not resolve. | Check the path; it must exist before `agy_start`. |
| `runner_error` | Default `RunnerError` code when no more specific runner code applies. | Read the message and `agy_status` output; a launch failure is reported through the job state and logs. |
| `source_dirty` | `require_clean_source=true` and the source repository has uncommitted changes. | Commit or stash the changes, or set `require_clean_source=false`. |
| `state_dir_prohibited` | `repo_path` is the state directory or inside it. | Choose a repository outside the state directory. |
| `state_disk_full` | The state directory's disk usage exceeds `max_state_disk_gb`. | Prune/clean up finished jobs, or raise `max_state_disk_gb`. |
| `unborn_head` | The repository has no commits (`HEAD` cannot be resolved). | Create an initial commit before starting a job. |
| `unmerged_commits` | `agy_cleanup` refused to delete a branch whose commits are not merged into the default branch. | Merge the branch first, or pass `force=true`/`delete_branch=true` only when discarding the work is intended. |
| `untrusted_agents_dir` | The repository tracks `.agents/**` and `trust_agents_dir=false`. | Remove that content from git, or set `trust_agents_dir=true` after reviewing it. |
| `verify_not_allowed` | The verify command is not in `verify_allowlist`. | Use an allowlisted command, or extend `verify_allowlist` after review. |
| `worktree_dirty` | `agy_cleanup` refused because the worktree has uncommitted modifications. | Commit or discard the changes, then clean up; `force=true` removes them. |
| `invalid_input` | Server fallback for a `ValueError`, `FileNotFoundError` or `PermissionError` that has no `.code` (for example a missing job, an oversized `job.json`, or a missing file). | Fix the input named in the message. A missing `job_id` is returned as this code, not a dedicated one. |
| `internal_error` | Unexpected server exception; the full traceback is in `server.log`, never in the envelope. | Check `<state>/server.log`, then report the failure with the tool name and job id. Do not retry blindly if the error repeats. |

## Account codes

Stable codes introduced with multi-account support (D102–D114). All are
fail-closed: a start or resume does not proceed.

| Code | Meaning | How to recover |
|---|---|---|
| `account_config_invalid` | An `[[accounts]]` entry has a bad shape, unknown key, bad pool/`expires_at`/`max_concurrent_jobs`, or conflicting expectations. | Fix the entry; see [config keys](config-keys.md) and the [multi-account design](../design/multi-account.md). |
| `account_limit_exceeded` | `1 + len(accounts)` exceeds `max_accounts`. | Remove an entry or raise `max_accounts` (maximum 32). |
| `account_name_invalid` | A name is empty, uses `default`, or does not match `[a-z0-9][a-z0-9-]{0,31}`. | Rename the account. |
| `account_name_duplicate` | Two entries share a name. | Use unique names. |
| `account_home_invalid` | The home is not absolute after expansion, does not exist, is not a directory, or is unreadable. | Create the directory and use an absolute path. |
| `account_home_permissions` | The account home is accessible by group or others. | `chmod 700` the home. |
| `account_identity_mismatch` | The full email parsed from agy does not match `expected_email` or its hash. | Confirm the login, or update the expectation. The message shows only the masked value. |
| `account_identity_unknown` | An expectation is configured but agy did not report an email. | Re-login the account; check that the CLI still logs `applyAuthResult`. |
| `account_not_found` | An `account` name was requested that the registry does not contain, or a pinned job's account was removed. | Use a name from the registry or omit the override. |
| `account_disabled` | The account is declared with `enabled = false`. | Enable it or use another account. |
| `account_not_eligible` | An explicit `account` cannot take the job under the strict quota filter (`force` skips only this check). | Wait for the reset, use another account, or pass `force=true` for a quota-only override. |
| `account_usage_unavailable` | That account's `/usage` failed, timed out, was unparsable, or failed identity verification. | Check the account login and the `reason`; the account is skipped for this start. |
| `account_model_unavailable` | The account rejected the requested model. | Use a model that account accepts; model sets are per account. |
| `account_permission_policy_missing` | The account home has no permission policy while the primary has one. | Copy the primary `$HOME/.gemini/config/config.json` into the account home. |
| `account_permission_policy_drift` | The account policy's allow-rule set differs from the primary's. | Re-copy the primary policy; never weaken it. |
| `account_concurrency_limit` | The account's own `max_concurrent_jobs` is reached. | Wait for a job to leave `queued`/`running`, or raise the account cap. |
| `invalid_priority` | `priority` is not `normal` or `background`. | Pass one of those two values. |
| `no_account_eligible` | The scheduler found no account that can take the job; the message names the earliest reset. | Wait for the reset, add/enable an account, or use `force=true` with a specific `account`. |

## Notes

- Preflight codes (`repo_not_found` through `untrusted_agents_dir`,
  `insufficient_disk_space`) are raised by `worktree.validate_repo_path`. They
  are returned by `agy_start` synchronously because validation happens before
  the job is created.
- `prompt_too_large`, `invalid_argv`, `forbidden_flag` and `runner_error` are
  raised by `runner.build_argv`/launch. `agy_start` returns `queued` before the
  background provisioning thread launches; a launch failure later moves the job
  to `failed` and records the error on the job rather than changing the start
  response.
- `agy_usage` never raises: an unreadable or malformed `/usage` reply is a
  successful call with `available: false` and a `reason` (`unavailable`,
  `timeout`, `output_too_large`, `auth_required`, `command_failed`,
  `unrecognized_format`). Those reasons are not error codes.
- `audit_unavailable` is a fatal audit *finding*, not an envelope code: it
  appears in `agy_review.audit_findings` and makes the review say a human must
  look at it.

## Sources

- `src/agy_mcp/manager.py` — `ManagerError`, the start/resume/cleanup codes and
  their conditions.
- `src/agy_mcp/runner.py` — `RunnerError`, `PromptTooLargeError`, the forbidden
  flags and argv validation.
- `src/agy_mcp/worktree.py` — `PreflightError` and every preflight code.
- `src/agy_mcp/server.py` — `_format_error`, `_sanitize_path_leak` and the
  `invalid_input`/`internal_error` fallbacks.
- `src/agy_mcp/jobs.py` — `get_job` (missing job -> `FileNotFoundError` ->
  `invalid_input`) and the audit/cleanup error paths.
- `src/agy_mcp/usage.py` — the fail-soft `available: false` reasons.
- `src/agy_mcp/accounts/errors.py` — `AccountError` and its stable codes.
- `src/agy_mcp/accounts/registry.py` — registry validation codes.
- `src/agy_mcp/accounts/identity.py` — identity mismatch/unknown codes.
- `doc/design/multi-account.md` — the account rules behind the codes.
- `doc/audits/2026-11-02-part2c-coverage.md` — the 40-code inventory this page
  closes (COV-01).
