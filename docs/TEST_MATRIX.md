# agy-mcp Requirements to Test Matrix

This matrix maps requirements from doc/design/spec.md v3 (sections 7, 8, 15, 17, 18) to the automated unit and integration tests proving them.

---

## SPEC Section 7: State Machine & Job Lifecycle

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 7.1 | FSM contains exactly the 8 specified states (`queued`, `running`, `needs_decision`, `needs_review`, `done`, `failed`, `timeout`, `cancelled`) | `tests/server/test_smoke.py::test_state_enum_members` | Tested |
| 7.2 | `TRANSITION_TABLE` codifies exactly the 16 defined lifecycle transitions (14 base + 2 explicit follow_up) | `tests/server/test_smoke.py::test_transition_table_matches_spec` | Tested |
| 7.3 | Transition 1: `queued -> running` on process spawn observed | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.4 | Transition 2: `running -> done` on clean exit 0 | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.5 | Transition 3: `running -> needs_review` on fatal audit/security violation | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.6 | Transition 4: `running -> needs_review` on exhausted verification attempts | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.7 | Transition 5: `running -> needs_review` on tool denial with zero progress | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.8 | Transition 6: `running -> needs_review` on malformed COMMIT_MSG retries | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.9 | Transition 7: `running -> timeout` on wall-clock or print timeout | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.10 | Transition 8: `running -> needs_decision` on NEEDS_DECISION marker | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.11 | Transition 9: `running -> failed` on unhandled crash/signal | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.12 | Transition 10: `running -> cancelled` on agy_cancel | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.13 | Transition 11: `needs_decision -> running` on agy_resume | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.14 | Transition 12: `timeout -> running` on agy_resume | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.15 | Transition 13: `needs_review -> running` on agy_resume (operational reason) | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.15b | Transition 14: `queued -> cancelled` before spawn | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually` | Tested |
| 7.16 | Terminal states (`done`, `failed`, `cancelled`) cannot transition | `tests/test_jobs.py::test_disallowed_state_transitions` | Tested |
| 7.17 | Illegal transitions (skipping running from queued to done) raise ValueError | `tests/test_jobs.py::test_disallowed_state_transitions` | Tested |
| 7.18 | Resuming from `needs_review` with security violation is strictly refused | `tests/test_jobs.py::test_security_needs_review_is_terminal` | Tested |
| 7.19 | Non-fatal denials with progress do not abort job | `tests/test_parse_result.py::test_parse_agy_output_precedence[denial_with_commits_is_done]` | Tested |
| 7.20 | `done -> running` only with `agy_resume(..., follow_up=true)`; the default resume gate returns `invalid_state_for_resume` | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually`, `tests/test_resume_and_commitmsg.py::test_follow_up_is_required_for_done_jobs` | Tested |
| 7.21 | `cancelled -> running` only with `agy_resume(..., follow_up=true)`; the default resume gate returns `invalid_state_for_resume` | `tests/test_jobs.py::test_all_sixteen_state_transitions_individually`, `tests/test_resume_and_commitmsg.py::test_follow_up_is_required_for_cancelled_jobs` | Tested |

---

## SPEC Section 8: Tool Contracts & Envelope Schemas

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 8.1 | Standard response envelope contains all base fields and rounds elapsed_seconds | `tests/server/test_models.py::test_build_standard_envelope` | Tested |
| 8.2 | Job model serializes to dict and restores from dict preserving types | `tests/server/test_models.py::test_job_serialization_round_trip` | Tested |
| 8.3 | Unsupported schema versions (>1) rejected with ValueError | `tests/server/test_models.py::test_unsupported_schema_version_rejected` | Tested |
| 8.4 | Dynamic next_actions derived based on JobState and review reason | `tests/server/test_models.py::test_derive_next_actions` | Tested |
| 8.5 | `agy_result` caps response to 64 KB and wraps in untrusted_text | `tests/server/test_models.py::test_build_result_response_caps_and_wrapping` | Tested |
| 8.6 | `agy_result` caps diff_stat to 8 KB | `tests/server/test_models.py::test_build_result_response_caps_and_wrapping` | Tested |
| 8.7 | `agy_result` caps commits to 50 max and commit subject to 1 KB | `tests/server/test_models.py::test_build_result_response_caps_and_wrapping` | Tested |
| 8.8 | `agy_result` caps audit events tail to last 20 entries | `tests/server/test_models.py::test_build_result_response_caps_and_wrapping` | Tested |
| 8.9 | `agy_review` caps untrusted_diff to 32 KB and wraps in untrusted_text | `tests/server/test_models.py::test_build_review_response_caps_and_wrapping` | Tested |
| 8.10 | Extended response models (`JobStatusResponse`, `JobCleanupResponse`, `JobListResponse`) serialization | `tests/server/test_models.py::test_response_models_and_audit_event_to_dict` | Tested |
| 8.11 | `wrap_untrusted_text` neutralizes closing tag injection, case variants, and delimiter split attempts | `tests/server/test_models.py::test_wrap_untrusted_text_attack_vectors` | Tested |
| 8.12 | End-to-end FastMCP stdio RPC dispatch of all 10 tools | *Deferred to MCP server layer (`test_server.py`)* | Deferred |

---

## SPEC Section 15: Result Parsing & Precedence

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 15.1 | Precedence 1: Cancellation flag -> state="cancelled" | `tests/test_parse_result.py::test_parse_agy_output_precedence[cancel_on_rc_0]` | Tested |
| 15.2 | Cancellation overrides non-zero exit codes, signals, and audit violations | `tests/test_parse_result.py::test_parse_agy_output_precedence[cancel_overrides_rc_1]` | Tested |
| 15.3 | Precedence 2: Audit violation flag -> state="needs_review(reason=audit_violation)" | `tests/test_parse_result.py::test_parse_agy_output_precedence[audit_violation_rc_0]` | Tested |
| 15.4 | Audit violation overrides signals, rc 1/2, and decision markers | `tests/test_parse_result.py::test_parse_agy_output_precedence[audit_violation_overrides_signal]` | Tested |
| 15.5 | Precedence 3: Negative return codes -> state="failed" with signal=abs(rc) | `tests/test_parse_result.py::test_parse_agy_output_precedence[sigterm_neg_15]` | Tested |
| 15.6 | Precedence 4: Return code 1 or 2 -> state="failed" with stderr tail | `tests/test_parse_result.py::test_parse_agy_output_precedence[rc_1_effort_mismatch]` | Tested |
| 15.7 | Precedence 5: Stderr print timeout marker or supervisor flag -> state="timeout" | `tests/test_parse_result.py::test_parse_agy_output_precedence[stderr_print_timeout_marker]` | Tested |
| 15.8 | Precedence 6: Decision marker `^NEEDS_DECISION:` on last line -> state="needs_decision" | `tests/test_parse_result.py::test_parse_agy_output_precedence[decision_simple]` | Tested |
| 15.9 | Decision marker extracted with fields separated by pipe ` \| ` | `tests/test_parse_result.py::test_parse_agy_output_precedence[decision_fixture]` | Tested |
| 15.10 | Non-final `NEEDS_DECISION:` within response body does not trigger decision state | `tests/test_parse_result.py::test_parse_agy_output_precedence[decision_in_middle_is_not_decision]` | Tested |
| 15.11 | Precedence 7: Tool denial with 0 commits and empty response -> `needs_review(blocked_by_permission)` | `tests/test_parse_result.py::test_parse_agy_output_precedence[permission_blocked_zero_commits_empty_resp]` | Tested |
| 15.12 | Precedence 8: `status == "SUCCESS"` -> state="done" with token counts extracted | `tests/test_parse_result.py::test_parse_agy_output_precedence[success_clean_fixture]` | Tested |
| 15.13 | Precedence 9: Malformed JSON, truncated writes, or unhandled status -> state="failed" with parse_error=True | `tests/test_parse_result.py::test_parse_agy_output_precedence[truncated_json_mid_write]` | Tested |
| 15.14 | Resilient backward scan skips pre-JSON logs/banners | `tests/test_parse_result.py::test_parse_agy_output_precedence[success_preceded_by_log_lines]` | Tested |
| 15.15 | Server resilience: malformed output never raises an unhandled exception | `tests/test_parse_result.py::test_parse_agy_output_precedence[non_json_plain_text]` | Tested |

---

## SPEC Section 17: Security Event Log, Caps & Budgeting

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 17.1 | `audit.jsonl` atomic append with fsync and 0600 file mode | `tests/test_jobs.py::test_create_and_load_job_permissions`, `tests/usage/test_usage.py::test_append_audit_event` | Tested |
| 17.2 | Cumulative wall-clock budget accounting across resumes against `min(max_minutes, max_minutes_cap)` | `tests/test_jobs.py::test_cumulative_budget_accounting` | Tested |
| 17.3 | Multi-threaded daily cap increment under file lock | `tests/test_jobs.py::test_concurrent_daily_cap_increments` | Tested |
| 17.4 | Multi-process daily cap increment (4 spawned processes) never exceeds daily_jobs_cap | `tests/server/test_multiprocess_concurrency.py::test_multiprocess_daily_cap_concurrency` | Tested |
| 17.5 | Rebuild daily cap count by scanning `audit.jsonl` job start events | `tests/test_jobs.py::test_rebuild_daily_cap_from_audit` | Tested |
| 17.6 | Multi-process concurrent `usage.jsonl` append under file lock without torn lines | `tests/server/test_multiprocess_concurrency.py::test_multiprocess_usage_log_no_torn_lines` | Tested |
| 17.7 | `usage.jsonl` strictly excludes prompt text, titles, and repository code (F25) | `tests/usage/test_usage.py::test_record_usage_no_prompt_text_and_permissions` | Tested |
| 17.8 | Concurrency counter (`count_active_jobs`) tracks running jobs | `tests/test_jobs.py::test_list_jobs_and_concurrency` | Tested |
| 17.9 | Disk usage calculation and expired terminal job pruning (`prune_retained_jobs`) | `tests/test_jobs.py::test_retention_and_disk_usage` | Tested |

---

## SPEC Section 18: Configuration File & Defaults

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 18.1 | Missing `config.toml` returns defaults without error | `tests/test_config.py::test_missing_config_returns_defaults` | Tested |
| 18.2 | All 14 SPEC keys loaded correctly from TOML | `tests/test_config.py::test_valid_custom_config` | Tested |
| 18.3 | Int for float field coercion (`min_free_gb`, `max_state_disk_gb`) | `tests/test_config.py::test_int_for_float_is_coerced` | Tested |
| 18.4 | Unknown keys collected in `Config.unknown_keys` warning list | `tests/test_config.py::test_unknown_keys_collected` | Tested |
| 18.5 | Hermetic config resolution via injectable path and base_config_dir | `tests/test_config.py::test_injectable_base_config_dir` | Tested |
| 18.6 | Config resolution respects `XDG_CONFIG_HOME` | `tests/test_config.py::test_xdg_config_home_resolution` | Tested |
| 18.7 | Strict type and range validation rejections (int, bool, float, list, bounds) | `tests/test_config.py::test_validation_errors` (21 parametrized cases) | Tested |
| 18.8 | Malformed TOML syntax raises `ConfigError` | `tests/test_config.py::test_malformed_toml_syntax` | Tested |

---

## SPEC Section 9: Git Operations & Safety Filters (`gitops.py`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 9.1 | Sensitive file staging exclusion (D6 blacklist: .env*, credentials, keyrings, dangerous repo configs) | `tests/git/test_gitops.py::test_is_sensitive_path`, `tests/git/test_gitops.py::test_stage_changes_sensitive_and_exclusions` | Tested |
| 9.2 | Hardened Git execution: isolated env (`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`), `-c core.fsmonitor=false`, `-c core.hooksPath=/dev/null`, `-c core.pager=cat` | `tests/git/test_gitops.py::test_run_git_basic_and_check` | Tested |
| 9.3 | Safe path handling with unicode characters, spaces, and null-separated porcelain -z | `tests/git/test_gitops.py::test_stage_changes_unicode_and_spaces` | Tested |
| 9.4 | Index.lock exponential backoff retry loop (0.1s to 5.0s, up to 10 attempts) and stale lock removal verification | `tests/git/test_gitops.py::test_commit_with_retry_and_stale_lock` | Tested |
| 9.5 | Concurrent commits from separate worktrees to the same repository | `tests/git/test_gitops.py::test_concurrent_commits_to_two_worktrees` | Tested |
| 9.6 | Periodic checkpoint commits and safety-net commits gated on dirty worktree state | `tests/git/test_gitops.py::test_checkpoint_and_safety_net_commits` | Tested |
| 9.7 | Review diff and commit inspection helpers (`get_merge_base`, `get_ahead_behind`, `has_base_moved`, `get_diff_stat`, `get_commits_list`, `take_git_config_snapshot`, `diff_git_config_snapshots`) | `tests/git/test_gitops.py::test_review_helpers` | Tested |

---

## SPEC Section 10: Worktree Lifecycle & Preflight Validation (`worktree.py`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 10.1 | Preflight path containment inside `$HOME` or `allowed_workdir_roots` | `tests/git/test_worktree.py::test_preflight_valid_repo_inside_home`, `tests/git/test_worktree.py::test_preflight_allowed_workdir_roots` | Tested |
| 10.2 | Rejection of symlink escapes pointing outside allowed roots | `tests/git/test_worktree.py::test_preflight_symlink_escape_rejected` | Tested |
| 10.3 | Prohibited target rejection: root `/`, `/tmp`, `$HOME`, `.config`, `.local` | `tests/git/test_worktree.py::test_preflight_prohibited_targets` | Tested |
| 10.4 | Refusal on bare repositories and linked worktrees | `tests/git/test_worktree.py::test_preflight_bare_and_linked_worktree_rejections` | Tested |
| 10.5 | Refusal on unborn HEADs | `tests/git/test_worktree.py::test_preflight_unborn_head` | Tested |
| 10.6 | Dirty source repo rejection when `require_clean_source=True` | `tests/git/test_worktree.py::test_preflight_dirty_cleanliness_policy` | Tested |
| 10.7 | Refusal on `.agents` directory in source repo unless `trust_agents_dir=True` | `tests/git/test_worktree.py::test_preflight_agents_dir_check` | Tested |
| 10.8 | Atomic worktree creation with branch collision retry (up to 5 attempts) and disk free space verification | `tests/git/test_worktree.py::test_create_worktree_and_provision` | Tested |
| 10.9 | Metadata directory `.agy` provisioned with `0700` permissions and `PLAN.md` instructions | `tests/git/test_worktree.py::test_create_worktree_and_provision` | Tested |
| 10.10 | Safe worktree cleanup refusing uncommitted dirty changes or unmerged branch commits unless `force=True` | `tests/git/test_worktree.py::test_cleanup_worktree_safety_checks` | Tested |
| 10.11 | Inspection of `settings.json` / workspace rules to detect dangerous rule overrides | `tests/git/test_worktree.py::test_inspect_permission_rules_dangerous_detection` | Tested |

---

## SPEC Section 11: Commit Watcher & Settle Window Pipeline (`watcher.py`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 11.1 | Commit message format validation: subject <= 1000 bytes, optional body separated by blank line, no control chars | `tests/supervisor/test_watcher_commit_pipeline.py::test_validate_commit_message` | Tested |
| 11.2 | Settle window mechanism tracking file stability (`sha256`, `size`, `mtime_ns`) for `>= settle_window_seconds` | `tests/supervisor/test_watcher_commit_pipeline.py::test_watcher_settle_window_and_commit_pipeline` | Tested |
| 11.3 | Atomic rename protocol: `.agy/COMMIT_MSG` -> `.seen.<n>` before processing | `tests/supervisor/test_watcher_commit_pipeline.py::test_watcher_settle_window_and_commit_pipeline` | Tested |
| 11.4 | Malformed commit message retry loop with feedback in `.agy/COMMIT_MSG.error` and transition to `needs_review` | `tests/supervisor/test_watcher_commit_pipeline.py::test_watcher_invalid_commit_msg_retry_then_needs_review` | Tested |
| 11.5 | Pre-commit audit gate blocking commits and triggering `needs_review(audit_violation)` on security violations | `tests/supervisor/test_watcher_commit_pipeline.py::test_watcher_audit_gate_blocks_commit` | Tested |
| 11.6 | Watcher periodic checkpoint commits and final safety-net commit | `tests/supervisor/test_watcher_commit_pipeline.py::test_watcher_checkpoint_and_final_commits` | Tested |
| 11.7 | Build junk staging exclusion across pathspecs and Python filter layers per D54 | `tests/git/test_gitops.py::test_verify_build_junk_not_committed_fake_repo`, `tests/git/test_gitops.py::test_junk_layer_isolation_pathspec_and_python_filter` | Tested |
| 11.8 | Safety-net commit skips creating empty commit when only build artifacts remain, logging audit event | `tests/git/test_gitops.py::test_only_junk_no_commit_and_audit_event_written` | Tested |
| 11.9 | Pre-existing tracked junk untouched by staging filter and never rewritten | `tests/git/test_gitops.py::test_junk_tracked_from_start_is_untouched` | Tested |

---

## SPEC Section 12: Security Auditor & Host Path Snapshot (`audit.py`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 12.1 | Pure filesystem snapshot comparison with SHA-256 and modification timestamps | `tests/test_audit.py::test_audit_clean_pass` | Tested |
| 12.2 | Fatal security violation on worktree `.git` pointer file modification | `tests/test_audit.py::test_audit_fatal_worktree_dot_git_pointer_tampering` | Tested |
| 12.3 | Fatal security violation on `.agents/**` hook file introduction or mutation | `tests/test_audit.py::test_audit_fatal_agents_directory_mutation` | Tested |
| 12.4 | Fatal security violation on resolved git config modifications | `tests/test_audit.py::test_audit_fatal_git_config_tampering` | Tested |
| 12.5 | Fatal security violation on instruction file modifications (`AGENTS.md`, `GEMINI.md`) | `tests/test_audit.py::test_audit_fatal_instruction_file_tampering` | Tested |
| 12.6 | Blocked-not-fatal policy violation on `.gitignore`, `.gitattributes`, `.gitmodules` modifications | `tests/test_audit.py::test_audit_blocked_not_fatal_policy_files` | Tested |
| 12.7 | Host security path snapshot detector for sensitive paths (`~/.ssh`, `~/.gnupg`, `~/.bashrc`, source repo config) | `tests/test_audit.py::test_audit_host_path_detector`, `tests/test_audit.py::test_verify_host_path_snapshot_detector_standalone` | Tested |
| 12.8 | `known_hosts.old` classified `warn_only`: replacement or rewrite is benign and never fatal per D54 | `tests/test_audit.py::test_audit_known_hosts_old_replaced_by_older_known_hosts` | Tested |

---

## Hardening Scenarios & Boundary Proofs (Parts A & B)

| Req ID | Scenario / Security Check | Proving Test | Status |
|---|---|---|---|
| A1 | Commit message validation: 72 ok, 73 rejected, multibyte char counting, empty, whitespace-only, NUL byte, invalid UTF-8, CRLF ok, CR rejected | `tests/supervisor/test_watcher_commit_pipeline.py::test_validate_commit_message` | Tested |
| A2 | Permission rules inspection: missing dir, malformed JSON warning, `command(git branch)`, `command(python3 -m pytest)`, `command(ls)` harmless, project dangerous rule | `tests/git/test_worktree.py::test_inspect_permission_rules_dangerous_detection` | Tested |
| A3 | Instruction file tampering (`AGENTS.md`, `GEMINI.md`) fatal audit severity | `tests/test_audit.py::test_audit_fatal_instruction_file_tampering` | Tested |
| A4 | Sensitive path blacklist: exceptions permitted, case-insensitivity, nested paths, policy files separated | `tests/git/test_gitops.py::test_is_sensitive_path`, `tests/git/test_gitops.py::test_stage_changes_sensitive_and_exclusions` | Tested |
| A5 | Safety net and final commit suppression while in security needs_review or after audit violation | `tests/supervisor/test_watcher_commit_pipeline.py::test_safety_net_and_final_commit_suppressed_on_security_review` | Tested |
| B1 | Hostile root `.gitignore` with `!.agy` negations cannot leak `.agy/` into git index | `tests/git/test_git_scenarios.py::test_b1_hostile_root_gitignore` | Tested |
| B2 | Branch slug table (`a/b`, `..`, `~x`, `x^y`, `-rf`, `x.lock`, `.hidden`, emoji, empty, 500 chars, newline) produces valid `agy/` refs | `tests/git/test_git_scenarios.py::test_b2_branch_slug_title_table` | Tested |
| B3 | Branch collision retry allocates fresh id when candidate branch exists | `tests/git/test_git_scenarios.py::test_b3_branch_collision_retry` | Tested |
| B4 | Tracked `.env.production` modification skipped + partial commit; untracked `id_rsa` skipped without fatal error; deleted `secrets.pem` partial commit | `tests/git/test_git_scenarios.py::test_b4_sensitive_staging_variants` | Tested |
| B5 | Normal file rename committed; rename into sensitive name `.env.local` skipped and flagged | `tests/git/test_git_scenarios.py::test_b5_rename_normal_vs_sensitive` | Tested |
| B6 | Filenames with newline, tab, quote, unicode, and leading dash staged correctly via porcelain -z | `tests/git/test_git_scenarios.py::test_b6_special_filenames_staging` | Tested |
| B7 | Identical COMMIT_MSG content across two steps creates two distinct commits and persists across reload | `tests/git/test_git_scenarios.py::test_b7_same_commit_msg_across_restart` | Tested |
| B8 | Half-written COMMIT_MSG resets stability window and commits only final completed message | `tests/git/test_git_scenarios.py::test_b8_half_written_commit_msg` | Tested |
| B9 | Safety-net commit suppressed after audit violation; branch ref strictly unchanged | `tests/git/test_git_scenarios.py::test_b9_safety_net_after_audit_violation` | Tested |
| B10 | 8 worktrees of ONE repo committing concurrently from 8 threads without index.lock leaks or corruption | `tests/git/test_git_scenarios.py::test_b10_eight_worktrees_concurrent_commits` | Tested |
| B11 | Stale index.lock (>5 min, dead proc) removed; young lock (<5 min) not removed and raises clear error | `tests/git/test_git_scenarios.py::test_b11_stale_index_lock_reclamation` | Tested |
| B12 | Preflight rejects symlinks outside HOME, state dir paths, submodule files; tolerates dirty tree when `require_clean_source=False` | `tests/git/test_git_scenarios.py::test_b12_preflight_advanced_checks` | Tested |
| C1 | Staging pathspec defense: verifies `stage_changes` executes `git add` with `:(exclude,top).agy` | `tests/git/test_gitops.py::test_stage_changes_uses_agy_exclude_pathspec` | Tested |

---

## SPEC Section 6 & 10: Runner, Process Execution & Fake CLI (`runner.py`, `tests/fake_agy`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 6.1 | Deterministic mock agy executable CLI parsing, argument ordering, and rejection of `-p` before flags | `tests/test_fake_agy.py::test_fake_agy_rejects_flags_after_prompt` | Tested |
| 6.2 | Rejection of `--effort` flag (effort encoded directly in model identifier) | `tests/test_fake_agy.py::test_fake_agy_rejects_effort_flag` | Tested |
| 6.3 | Emulation of all 17 simulation scenarios (`success_simple`, `success_with_commits`, `needs_decision`, `denial_with_progress`, `denial_blocked`, `timeout`, `hang`, `crash_rc1`, `crash_signal`, etc.) | `tests/test_fake_agy.py` (all tests) | Tested |
| 6.4 | Strict argv construction with `-p <prompt>` placed last and `--log-file` absolute path | `tests/test_runner.py::test_build_argv_flag_ordering_and_rules` | Tested |
| 6.5 | Prompt size limit: prompts > 100,000 bytes rejected with `PromptTooLargeError` | `tests/test_runner.py::test_build_argv_prompt_size_limit` | Tested |
| 6.6 | Process group isolation: `start_new_session=True` creating session leader where `pgid == pid` | `tests/test_runner.py::test_runner_launch_process_group_and_streams` | Tested |
| 6.7 | Headless stdin isolation: child stdin bound to `/dev/null` (`subprocess.DEVNULL`) | `tests/test_runner.py::test_runner_stdin_is_devnull` | Tested |
| 6.8 | Environment allowlist scrubbing: child receives ONLY `HOME`, `PATH`, `LANG=C.UTF-8`, `TMPDIR`, and `GIT_CONFIG_*` | `tests/test_runner.py::test_runner_environment_scrubbing` | Tested |
| 6.9 | Process group termination: `SIGTERM` sent to group (`os.killpg`) | `tests/test_runner.py::test_runner_kill_graceful_sigterm` | Tested |
| 6.10 | Signal escalation: stubborn hangs ignoring `SIGTERM` escalated to `SIGKILL` after grace period | `tests/test_runner.py::test_runner_kill_escalates_to_sigkill` | Tested |
| 6.11 | PID reuse detection: `/proc/<pid>/stat` field 22 start time check prevents signal delivery to recycled PIDs | `tests/test_runner.py::test_runner_pid_reuse_detection_via_proc_stat` | Tested |
| 6.12 | Verbatim argv recording for fresh launches and resumptions with conversation reuse | `tests/test_runner.py::test_record_argv_for_fresh_and_resume_jobs` | Tested |
| 6.13 | Crash handling on nonzero exit code (rc=1) and process kill by signal (SIGKILL/SIGTERM) | `tests/test_runner.py::test_manager_scenario_crash_rc1`, `tests/test_runner.py::test_manager_scenario_crash_signal` | Tested |
| 6.14 | Robust parser handling of unparseable garbage stdout and 5MB huge stdout payloads | `tests/test_runner.py::test_manager_scenario_garbage_stdout`, `tests/test_runner.py::test_manager_scenario_huge_stdout` | Tested |
| 6.15 | Forbidden flags rejection in `build_argv` (`--sandbox`, `--effort`, `-c`, `--dangerously-skip-permissions`, `--mode auto`, invalid mode/format, timeout < 1) | `tests/test_runner.py::test_build_argv_forbidden_flags` | Tested |
| 6.16 | Complete character-for-character exact argv matching for fresh and resume jobs recorded via `FAKE_AGY_RECORD` | `tests/test_runner.py::test_argv_exact_fresh`, `tests/test_runner.py::test_argv_exact_resume` | Tested |
| 6.17 | Strict Fake AGY CLI rejection tests across all 12 invalid invocation conditions | `tests/test_fake_agy.py::test_fake_agy_rejects_*` | Tested |

---

## SPEC Section 10: Process Group Supervisor (`supervisor.py`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 10.1 | Supervisor execution loop transitions cleanly to `JobState.DONE` on exit 0 | `tests/test_supervisor.py::test_supervisor_clean_success` | Tested |
| 10.2 | Supervisor detects `NEEDS_DECISION:` response marker and transitions to `JobState.NEEDS_DECISION` | `tests/test_supervisor.py::test_supervisor_needs_decision` | Tested |
| 10.3 | Tool denial with zero progress transitions to `JobState.NEEDS_REVIEW(blocked_by_permission)` | `tests/test_supervisor.py::test_supervisor_denial_blocked` | Tested |
| 10.4 | Wall-clock deadline expiration terminates process group and transitions to `JobState.TIMEOUT` | `tests/test_supervisor.py::test_supervisor_deadline_kill` | Tested |
| 10.5 | Fatal security audit violation kills process group, suppresses pending commits, and transitions to `JobState.NEEDS_REVIEW` | `tests/test_supervisor.py::test_supervisor_audit_violation_suppresses_commits` | Tested |
| 10.6 | Explicit cancellation via `terminate_group` kills process group and transitions to `JobState.CANCELLED` | `tests/test_supervisor.py::test_supervisor_explicit_cancel` | Tested |
| 10.7 | Clean exit 0 with uncommitted modifications executes automated safety-net commit | `tests/test_supervisor.py::test_supervisor_safety_net_commit` | Tested |
| 10.8 | Pre-verify security audit gate suppresses verify execution on security findings | `tests/test_supervisor.py::test_supervisor_verify_blocked_by_pre_verify_security_finding` | Tested |
| 10.9 | Post-verify security audit gate detects file tampering caused by verification command | `tests/test_supervisor.py::test_supervisor_verify_post_audit_gate_catches_tampering` | Tested |
| 10.10 | Supervisor tick resilience tolerating isolated tick errors, failing only on 3 consecutive errors | `tests/test_supervisor.py::test_supervisor_tick_resilience_and_consecutive_failure_cap` | Tested |

---

## SPEC Section 13: Verification & Auto-Fix Loop (`verify.py`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 13.1 | Verification allowlist exact prefix matching accepts permitted test commands and subcommands | `tests/verify/test_verify.py::test_is_verify_allowed_accepts_allowlisted_commands` | Tested |
| 13.2 | Verification allowlist rejects unlisted binaries, shell invocations, and command chaining | `tests/verify/test_verify.py::test_is_verify_allowed_rejects_unlisted_and_malicious_commands` | Tested |
| 13.3 | Verification execution runs directly in worktree with no shell (`shlex.split`) | `tests/verify/test_verify.py::test_run_verify_clean_success` | Tested |
| 13.4 | Output capture combining stdout and stderr on test failure | `tests/verify/test_verify.py::test_run_verify_failure_output_capture` | Tested |
| 13.5 | Verification execution timeout enforcement (default 15m; kills child and returns 124) | `tests/verify/test_verify.py::test_run_verify_timeout_enforcement` | Tested |
| 13.6 | Output tail truncation: capped to maximum 8 KB (`MAX_VERIFY_TAIL_BYTES`) | `tests/verify/test_verify.py::test_run_verify_output_tail_truncation` | Tested |
| 13.7 | Auto-fix prompt generation prepends standing preamble and formats Markdown code fence | `tests/verify/test_verify.py::test_format_fix_prompt_structure` | Tested |
| 13.8 | Process group session isolation (`start_new_session=True`) and killing grandchild process groups on timeout/cancellation | `tests/verify/test_verify.py::test_verify_process_group_kills_grandchild` | Tested |
| 13.9 | Verification environment injects `PYTHONDONTWRITEBYTECODE=1` and `PYTEST_ADDOPTS="-p no:cacheprovider"` to prevent junk creation per D54 | `tests/verify/test_verify.py::test_verify_env_dontwritebytecode_and_pytest_addopts` | Tested |
| 13.10 | Verify execution observability in audit log (`verify_started`, `verify_finished`) and standard envelope (`verify` field) | `tests/test_manager.py::test_manager_verify_pass_observable`, `tests/test_manager.py::test_manager_verify_fail_once_then_fixed` | Tested |
| 13.11 | Verify failure handling: auto-fix loop, review reason `verify_failed`/`verify_timeout`, rc 127, and ANSI/binary tail sanitization | `tests/test_manager.py::test_manager_verify_fail_twice_needs_review`, `tests/test_manager.py::test_manager_verify_timeout_needs_review`, `tests/test_manager.py::test_manager_verify_command_not_found_127`, `tests/test_manager.py::test_manager_verify_ansi_binary_junk` | Tested |

---

## SPEC Section 16: Crash Recovery & Liveness Monitoring (`recovery.py`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 16.1 | Active process liveness verification via `/proc/<pid>/stat` clock ticks | `tests/test_recovery.py::test_check_process_liveness_real_and_fake` | Tested |
| 16.2 | Rejection of recycled PIDs when start time differs from persisted `job.json` | `tests/test_recovery.py::test_check_process_liveness_real_and_fake` | Tested |
| 16.3 | Orphaned/dead process recovery: parses captured disk streams, audits, and finalizes state with `recovered=True` | `tests/test_recovery.py::test_reconcile_startup_dead_process_finalization` | Tested |
| 16.4 | Stale queued job cleanup: transitions un-spawned queued jobs to `FAILED` with `recovered=True` | `tests/test_recovery.py::test_reconcile_startup_stale_queued_job` | Tested |
| 16.5 | Preserves legitimately running process for reattachment | `tests/test_recovery.py::test_reconcile_startup_preserves_alive_process` | Tested |
| 16.6 | Liveness summary: parses `cli.log` mtime, git log, dirty status, and redacts sensitive credentials | `tests/test_recovery.py::test_get_liveness_summary` | Tested |
| 16.7 | Recovery adoption of live running process reattaching supervisor and watcher end-to-end | `tests/test_recovery.py::test_recovery_adoption_slow_progress_end_to_end` | Tested |
| 16.8 | Startup reconciliation rejects dead/zombie processes and recycled PIDs | `tests/test_recovery.py::test_recovery_pid_reuse_rejected_end_to_end`, `tests/test_recovery.py::test_recovery_process_already_finished_while_server_down` | Tested |

---

## SPEC Section 8: JobManager & Tool Operations (`manager.py`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 8.13 | `agy_start`: asynchronous nonblocking provisioning returning `state="queued"`, spawns process group, enforces concurrency ceiling, daily cap, model allowlist, and prompt limit; setup failures clean worktree/branch | `tests/test_manager.py::test_manager_start_and_wait_success`, `tests/test_manager.py::test_manager_start_asynchronous_nonblocking`, `tests/test_manager.py::test_manager_start_setup_failure_cleanup`, `tests/test_manager.py::test_manager_prompt_size_limit`, `tests/test_manager.py::test_manager_concurrency_ceiling`, `tests/test_manager.py::test_manager_daily_cap_enforcement` | Tested |
| 8.14 | `agy_status`: returns extended status payload with liveness telemetry and redacted log tail | `tests/test_manager.py::test_manager_status_and_result` | Tested |
| 8.15 | `agy_wait`: waits for state departure from RUNNING or honors call timeout cap (default 30s, max 300s, envelope warning on clamp) | `tests/test_manager.py::test_manager_start_and_wait_success`, `tests/test_manager.py::test_manager_wait_clamping_and_warning` | Tested |
| 8.16 | `agy_result`: extracts response wrapped in `untrusted_text`, diff_stat, commit log, and audit tail | `tests/test_manager.py::test_manager_status_and_result` | Tested |
| 8.17 | `agy_cancel`: sends SIGTERM/SIGKILL, executes checkpoint commit, transitions to CANCELLED | `tests/test_manager.py::test_manager_cancel_operation` | Tested |
| 8.18 | `agy_list`: returns active jobs, disk metrics, and drifted worktrees | `tests/test_manager.py::test_manager_list_operation` | Tested |
| 8.19 | `agy_resume`: injects user answer with preamble, passes `--conversation`, verifies mandatory resume audit gate | `tests/test_manager.py::test_manager_resume_from_needs_decision`, `tests/test_manager.py::test_manager_resume_blocked_by_audit_gate` | Tested |
| 8.20 | `agy_resume`: strictly refuses resumption from terminal security needs_review | `tests/test_manager.py::test_manager_resume_refuses_terminal_security_review` | Tested |
| 8.21 | `agy_review`: returns merge_base, ahead/behind counts, untrusted_diff, and audit findings | `tests/test_manager.py::test_manager_review_operation` | Tested |
| 8.22 | `agy_cleanup`: removes worktree and branch with dirty/unmerged safety checks | `tests/test_manager.py::test_manager_cleanup_operation` | Tested |
| 8.23 | Automated verification auto-fix turn loop with attempt counter exhaustion entering `needs_review` | `tests/test_manager.py::test_manager_auto_fix_exhaustion` | Tested |
| 8.24 | Security scenarios: malicious hooks, gitptr tampering, hostwrite blocked with security needs_review | `tests/test_manager.py::test_manager_scenario_malicious_hooks`, `tests/test_manager.py::test_manager_scenario_malicious_gitptr`, `tests/test_manager.py::test_manager_scenario_malicious_hostwrite` | Tested |
| 8.25 | Lifecycle scenarios: tool denial with progress (committed), tool denial blocked, timeout resumable | `tests/test_manager.py::test_manager_scenario_denial_with_progress`, `tests/test_manager.py::test_manager_scenario_denial_blocked`, `tests/test_manager.py::test_manager_scenario_timeout_resumable` | Tested |
| 8.26 | Concurrent jobs on same source repository execute simultaneously without git corruption (`git fsck` clean) | `tests/test_manager.py::test_manager_concurrent_jobs_same_repo_git_fsck` | Tested |
| 8.27 | Invalid job ID validation across all 7 manager methods (empty, non-UUID, non-existent, uppercase) | `tests/test_manager.py::test_manager_invalid_job_id_all_methods` | Tested |
| 8.28 | Auto-fix loop succeeds on second attempt when verify fails on turn 1 and passes on turn 2 | `tests/test_manager.py::test_manager_auto_fix_success_second_attempt` | Tested |

---

## SPEC Section 14: Standing Prompt & Packaging (`preamble.md`, `util.py`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 14.1 | Preamble package resource loading via `importlib.resources` and inclusion in built wheel | `tests/test_util.py::test_load_preamble_via_importlib_resources` | Tested |
| 14.2 | Fresh prompt preamble prepending, `.agy/COMMIT_MSG` instruction, `NEEDS_DECISION` exact format, and unmodified task text under header | `tests/test_util.py::test_format_fresh_prompt_spec_compliance` | Tested |
| 14.3 | Resume prompt preamble prepending with user answer wrapped in untrusted data delimiters | `tests/test_util.py::test_format_resume_prompt_spec_compliance` | Tested |

---

---

## SPEC Section 8: FastMCP Stdio RPC Protocol Server (`server.py`)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| 8.29 | Server initialization and `list_tools` schema verification matching snapshot `tools_schema.json` | `tests/server/test_server_e2e.py::test_server_e2e_list_tools_schema_matches_snapshot`, `tests/server/test_server_unit.py::test_tools_schema_matches_snapshot` | Tested |
| 8.30 | Tool descriptions written for LLM orchestrator, bounded < 1,200 chars each with required prompt/untrusted safety rules | `tests/server/test_server_e2e.py::test_server_e2e_tool_descriptions_under_1200_chars`, `tests/server/test_server_unit.py::test_all_tool_descriptions_under_1200_chars` | Tested |
| 8.31 | Happy path execution over MCP protocol: `agy_start` -> `agy_status` -> `agy_wait` -> `agy_result` | `tests/server/test_server_e2e.py::test_server_e2e_happy_path` | Tested |
| 8.32 | Interactive decision loop: `needs_decision` -> `agy_resume` with answer -> `done` | `tests/server/test_server_e2e.py::test_server_e2e_needs_decision_and_resume` | Tested |
| 8.33 | Tool denial handling: `denial_blocked` enters `needs_review` | `tests/server/test_server_e2e.py::test_server_e2e_denial_blocked` | Tested |
| 8.34 | Security violation: `malicious_hooks` triggers terminal `needs_review` and refuses resumption | `tests/server/test_server_e2e.py::test_server_e2e_malicious_hooks` | Tested |
| 8.35 | Job cancellation: `agy_cancel` kills process group and sets `state="cancelled"` | `tests/server/test_server_e2e.py::test_server_e2e_cancel` | Tested |
| 8.36 | Job listing: `agy_list` surfaces active jobs, total disk usage, and drifted worktrees | `tests/server/test_server_e2e.py::test_server_e2e_list` | Tested |
| 8.37 | Job review: `agy_review` retrieves merge divergence and diff statistics | `tests/server/test_server_e2e.py::test_server_e2e_review` | Tested |
| 8.38 | Worktree cleanup: `agy_cleanup` enforces dirty check without force, prunes with force=True | `tests/server/test_server_e2e.py::test_server_e2e_cleanup` | Tested |
| 8.39 | Robust error handling: invalid inputs (bad job_id, traversal, missing prompt, huge prompt, bad model, max_minutes, verify) return error envelopes and keep server alive | `tests/server/test_server_e2e.py::test_server_e2e_invalid_inputs`, `tests/server/test_server_unit.py::test_format_error_manager_error` | Tested |
| 8.40 | Concurrency: asynchronous handler execution via anyio threadpool answers `agy_status` in < 1s during in-flight `agy_wait` | `tests/server/test_server_e2e.py::test_server_e2e_concurrency_wait_nonblocking` | Tested |
| 8.41 | Stdout protocol purity: server process emits strictly valid JSON-RPC lines on stdout during full job run | `tests/server/test_server_e2e.py::test_server_e2e_stdout_purity` | Tested |
| 8.42 | Crash recovery over protocol: SIGKILL during slow execution, new server reconciles and adopts running job to completion | `tests/server/test_server_e2e.py::test_server_e2e_crash_recovery_over_protocol` | Tested |
| 8.43 | Binary preflight: startup refuses missing or non-executable binary with exit code 2 and stderr message | `tests/server/test_server_e2e.py::test_server_e2e_startup_refuses_missing_binary`, `tests/server/test_server_unit.py::test_resolve_agy_binary_missing` | Tested |
| 8.44 | Supervisor decoupling: SIGTERM terminates server cleanly without killing child `agy` process group | `tests/server/test_server_e2e.py::test_server_e2e_sigterm_does_not_kill_running_agy` | Tested |
| 8.45 | Secret isolation: `FAKE_SECRET` in server environment never appears in tool results, job directories, or fake agy environment | `tests/server/test_server_e2e.py::test_server_e2e_no_secrets` | Tested |

---

## Live Smoke Tests with Real `agy` (historical; `smoke_real.py` removed)

The `scripts/smoke_real.py` driver was removed after the round; the committed
outputs below are the historical record.

| Req ID | Smoke Requirement Description | Proving Test / Run | Status |
|---|---|---|---|
| SMOKE.1 | S1 Happy path execution with multi-step prompt and git commit inspection | Run S1, `docs/reports/smoke/s1-result.json` | Verified (Surprise: Shell RunCommand denied) |
| SMOKE.2 | S2 Automatic parent directory creation by real `write_file` tool | Run S2, `docs/reports/smoke/s2-result.json`) | Verified (Pass: Parent dirs created) |
| SMOKE.3 | S3 Denial probe: verification of command sandboxing and isolation | Run S3, `docs/reports/smoke/s3-result.json`) | Verified (Pass: Isolation preserved) |
| SMOKE.4 | S4 Interactive decision and resumption cycle via `--conversation` | Run S4, `docs/reports/smoke/s4-resume-result.json`) | Verified (Pass: NEEDS_DECISION + resume) |
| SMOKE.5 | S5 Permission rules inspection against real `~/.gemini` configuration | Run S5, `docs/reports/smoke/s5-preflight.txt`) | Verified (Pass: Zero leaks, safe output) |
| SMOKE.6 | S6 Observability of running jobs via periodic `agy_status` polling | Run S6, `docs/reports/smoke/s6-observability.txt`) | Verified (Pass: Log mtime proves activity) |
| SMOKE.7 | S7 FastMCP protocol stdio purity and JSON-RPC stream health | Run S7, `docs/reports/smoke/server-stdout.log`) | Verified (Pass: 100% JSON-RPC purity) |

---

## Smoke Fixes & Hardening Tests (Parts B–E)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| FIX.1 | Denial extraction from real `cli.log` tail (capped at 2 MB), formatting with byte limits and untrusted wrapping | `tests/test_cli_log.py` (9 tests across real, empty, multiple, truncated, binary, huge logs) | Tested |
| FIX.2 | Preamble Rule 10 (shell restriction & `NEEDS_DECISION: need command`) format and 100k size assertion | `tests/test_denial_recovery.py::test_preamble_rule_10_content_and_prompt_size` | Tested |
| FIX.3 | Automatic one-shot recovery on denial with zero commits; audit event `denial_auto_resume` | `tests/test_denial_recovery.py::test_auto_resume_on_denial_success` | Tested |
| FIX.4 | Exhausted auto-resume attempts remain in `needs_review(blocked_by_permission)` with `denied_commands` | `tests/test_denial_recovery.py::test_auto_resume_on_denial_exhausted` | Tested |
| FIX.5 | `auto_resume_on_denial=0` disables automatic resumption | `tests/test_denial_recovery.py::test_auto_resume_disabled_by_config` | Tested |
| FIX.6 | Double `COMMIT_MSG` without staged diff consumed as `commit_skipped_no_changes`, 0 supervisor errors | `tests/test_watcher_bug.py::test_double_commit_msg_skipped_without_error` | Tested |
| FIX.7 | Supervisor error audit events record exception type, message, and last 500 bytes of git stderr | `tests/test_watcher_bug.py::test_supervisor_error_records_git_stderr_regression` | Tested |
| FIX.8 | `files_modified` tracks changed files between base and job branch capped at 200 | `tests/test_files_modified.py::test_files_modified_tracking`, `test_files_modified_cap_at_200` | Tested |

---

## Verify Loop & Known Hosts Tests (D52 & D53)

| Req ID | Requirement Description | Proving Test | Status |
|---|---|---|---|
| D52.1 | `known_hosts` pure append continues execution, emits audit event `host_path_changed_benign`, and adds warning `known_hosts_changed` | `tests/test_audit.py::test_audit_known_hosts_pure_append` | Tested |
| D52.2 | `known_hosts` newly created file continues with benign event and warning | `tests/test_audit.py::test_audit_known_hosts_new_file` | Tested |
| D52.3 | `known_hosts` rewritten earlier bytes (even if larger size) is fatal `host_path_modified` | `tests/test_audit.py::test_audit_known_hosts_rewritten_earlier_bytes_fatal` | Tested |
| D52.4 | `known_hosts` truncated file is fatal `host_path_modified` | `tests/test_audit.py::test_audit_known_hosts_truncated_fatal` | Tested |
| D52.5 | `known_hosts` deleted file is fatal `host_path_modified` | `tests/test_audit.py::test_audit_known_hosts_deleted_fatal` | Tested |
| D52.6 | Strict host path append (e.g. `.bashrc`, `authorized_keys`) remains fatal `host_path_modified` | `tests/test_audit.py::test_audit_strict_path_append_fatal` | Tested |
| D52.7 | `known_hosts` exceeding 1 MB limit cannot be verified and is fatal | `tests/test_audit.py::test_audit_known_hosts_over_1mb_fatal` | Tested |
| D52.8 | Permission error reading `known_hosts` is fatal | `tests/test_audit.py::test_audit_known_hosts_permission_error_fatal` | Tested |
| D52.9 | Regression test with exact F1 audit event shape | `tests/test_audit.py::test_audit_known_hosts_f1_regression_shape` | Tested |
| D52.10 | End-to-end fake agy scenario `known_hosts_append` under isolated fake HOME | `tests/test_audit.py::test_scenario_known_hosts_append` | Tested |
| D53.1 | Absolute interpreter-based verify commands allowed if matching prefix allowlist | `tests/verify/test_verify.py::test_is_verify_allowed_interpreter_commands` | Tested |
| D53.2 | Direct shell wrappers (`sh`, `bash`, `zsh`) strictly rejected from verify allowlist | `tests/verify/test_verify.py::test_is_verify_allowed_rejects_shells` | Tested |
| D53.3 | Verify command not found returns clear error message without server crash | `tests/verify/test_verify.py::test_run_verify_not_found_returns_clear_error` | Tested |
| D53.4 | Verify command exceeding timeout returns `verify_timeout` | `tests/verify/test_verify.py::test_run_verify_timeout` | Tested |
| D53.5 | Binary and ANSI escape sequences in verify output safely preserved and truncated | `tests/verify/test_verify.py::test_run_verify_ansi_binary_junk` | Tested |
| D53.6 | Failing test names extracted from pytest failure output | `tests/verify/test_verify.py::test_extract_failing_tests` | Tested |
| D53.7 | `format_fix_prompt` wraps first 2000 bytes in `<untrusted_text>` and attaches required prompt guidance | `tests/verify/test_verify.py::test_format_fix_prompt_failing_tests_and_guidance` | Tested |
| D53.8 | Exact prompt guidance text assertions for `agy_start` docstring and preamble rule 6 | `tests/verify/test_verify.py::test_prompt_guidance_exact_text` | Tested |
| D53.9 | Auto-fix verification loop: single failure fixed on resume terminates `done` | `tests/test_manager.py::test_manager_auto_fix_verify_loop_success` | Tested |
| D53.10 | Auto-fix verification loop: double failure terminates `needs_review(verify_failed)` with tail attached | `tests/test_manager.py::test_manager_auto_fix_fails_twice_verify_failed_with_tail` | Tested |
| D53.11 | Auto-fix verification loop: zero change on failure counted as attempt | `tests/test_manager.py::test_manager_auto_fix_no_changes_counted_attempt` | Tested |

---

## Requirements with NO Test (or Deferred to MCP Server Layer)

None. All SPEC requirements across core-pure, gitops, runtime supervision, and the FastMCP server protocol layer are fully covered by automated tests.
