# Organization audit — agy-mcp

Date: 2026-10-26
Branch: `exp/docs-codemap`
Mode: **read-only**. Nothing in this report was fixed. Line numbers refer to the
tree at the audit commit (after the documentation pass), not to `main`.

Evidence was collected with `wc -l`, an `ast` walker for function lengths and
`except Exception: pass` handlers, `ruff check`, `grep -rn --include=*.py`, and a
static import-graph check. Counts are from those commands. Where a conclusion is
an inference rather than a measurement, it is marked **UNSURE**.

## Summary

| ID | Finding | Severity | Size | Behavior-changing |
| --- | --- | --- | --- | --- |
| A1 | Three modules carry most of the complexity: `manager.py` 1,341 lines, `supervisor.py` 943, `gitops.py` 925 | Medium | L | No |
| A2 | 12 functions are ≥ 130 lines; `_finalize_terminated_process` is 395 | High | L | No (refactor only) |
| A3 | `checkpoint_commit_minutes` is validated and documented but never read; the watcher hardcodes 600 s | High | S | Yes |
| A4 | `.agy/` metadata provisioning (`provision_metadata_dir`) is never called by the production launch path | Medium | S | Yes — **RESOLVED (D62, batch 4)** |
| A5 | 53 `except Exception: pass` handlers; a subset fail open (audit reporting, process cleanup) | Medium | M | Yes (observability) |
| A6 | Manager `status` and `result` duplicate the same `files_modified` refresh block | Low | S | No |
| A7 | Reason strings are inconsistent (`verify_failed`/`verification_failed`, `audit_violation` overloaded) | Low | S | Yes (string contracts) |
| A8 | `usage.py`, `manager.py`, `gitops.py`, `audit.py` mix several responsibilities | Medium | L | No |
| A9 | Awkward layering: models → gitops, util → models, supervisor → audit private helper, manager → parse_result private helper | Low | S | No |
| A10 | Dead/tests-only code: `run_hardened_git`, `read_proc_pgid`, unreachable `resume` tail, six production-unused helpers | Low | S | Yes (deletion) |
| A11 | Magic numbers outside config: 120 s deadline grace, 300 s wait clamp, 300 s lock age, 600 s checkpoint, 900 s verify, 5 s audit/settle | Low | S | No |
| A12 | SPEC/code drift: `starting` state, `job_budget_minutes`, `audit_pass`, watcher warning, lock-order wording | Medium | M | Partly |
| A13 | No import-time cycles, but the acyclic graph relies on lazy imports and private cross-module helpers | Low | S | No |
| A14 | Test gaps around the config-checkpoint path, production `.agy` provisioning, lock order, and swallowed exceptions | Medium | M | No |

## A1/A2 — Oversized files and functions

`wc -l src/agy_mcp/*.py` (largest): `manager.py` 1,341; `supervisor.py` 943;
`gitops.py` 925; `models.py` 786; `usage.py` 765; `audit.py` 653; `server.py`
601; `jobs.py` 566. Total production Python: 9,906 lines.

Ten longest functions (AST, inclusive line span):

| Lines | Location | Function |
| ---: | --- | --- |
| 395 | `supervisor.py:445-839` | `JobSupervisor._finalize_terminated_process` |
| 191 | `recovery.py:98-288` | `reconcile_on_startup` |
| 187 | `audit.py:439-625` | `audit` |
| 182 | `parse_result.py:91-272` | `parse_agy_output` |
| 171 | `manager.py:300-470` | `JobManager._provision_and_launch` |
| 168 | `manager.py:1060-1227` | `JobManager.resume` |
| 167 | `config.py:69-235` | `_validate_config_dict` |
| 157 | `transcript.py:87-243` | `read_denied_calls` |
| 148 | `manager.py:151-298` | `JobManager.start` |
| 135 | `gitops.py:434-568` | `stage_changes` |

`_finalize_terminated_process` alone contains parsing, two audit gates, verify,
three commit paths, denial enrichment, quota/auth diagnosis, cost accounting,
state transition, usage logging and cleanup. Any extraction is a behavior-risk
refactor (it must preserve ordering: audit → verify → post-verify audit →
commit). Severity High for maintainability, but **not** a docs-only fix.

Proposed fix: extract `_run_post_completion_audit`, `_run_verify_gate`,
`_commit_and_account`, and `_diagnose_interruption` from
`_finalize_terminated_process` in that order. Size L. Behavior-changing: no, but
high test coverage required first.

## A3 — `checkpoint_commit_minutes` is dead configuration

- `config.py:52` defines it, `config.py:84` allows it, `config.py:116` validates
  it.
- `gitops.py`/`watcher.py` never read `Config.checkpoint_commit_minutes`.
- `watcher.py:92` hardcodes `checkpoint_interval_seconds: int = 600`.
- `supervisor.py:145` constructs `CommitWatcher(...)` and does not pass a
  checkpoint interval; `manager.py` never does either.
- `grep -rn --include=*.py checkpoint_commit_minutes src/ tests/` matches only
  `config.py` and `tests/test_config.py`.
- SPEC `SPEC.md:400` and `SPEC.md:590` promise the key controls the interval.

Impact: a user who sets 2 minutes still gets the hardcoded 10 minutes. The
default (10) happens to match the hardcoded 600 s, so this is invisible until
someone changes the key. Severity High (documented contract broken). Fix: pass
`self.config.checkpoint_commit_minutes * 60` into `JobSupervisor`, then into
`CommitWatcher`; add a test that changes the key and observes the interval.
Size S. Behavior-changing: yes.

## A4 — `.agy/` provisioning is never called in production

- SPEC `SPEC.md:306-310` says provisioning creates `<worktree>/.agy/` (0700) and
  seeds `PLAN.md` immediately after checkout.
- `worktree.provision_metadata_dir()` / `provision()` implement it
  (`worktree.py:245`, `worktree.py:268`).
- `manager._provision_and_launch()` calls `create_worktree()` at
  `manager.py:311` and then goes straight to argv/launch; there is no
  `provision` call anywhere in `src/`.
- `grep -rn "provision(" src/` shows only the alias definition;
  `grep -rn "provision" src/agy_mcp/manager.py` shows only comments/docstrings.
- Tests call `provision()` directly (`tests/supervisor/test_watcher_commit_pipeline.py:116`,
  `tests/git/test_worktree.py:157`, `tests/git/test_git_scenarios.py:35`).

Impact: in production the `.agy` directory is created implicitly when agy or the
watcher first writes `COMMIT_MSG`; the 0700 mode and seeded `PLAN.md` from SPEC
are not applied. Severity Medium (security/permissions promise and agent
guidance). Fix: call `provision_metadata_dir(wt_path, job.title)` after
`create_worktree`, before `build_argv`; add a launch-path test. Size S.
Behavior-changing: yes.

**Status: RESOLVED in batch 4 (D62).** `JobManager._provision_and_launch` now
calls `worktree.provision_metadata_dir` after `create_worktree` and before
`build_argv`; provisioning seeds the four scratchpad files, adds `.agy/` to the
repository-local `info/exclude`, and any failure fails closed
(`failed`/`setup_failed`, worktree and branch removed, agy never launched).
Covered by `tests/test_agy_provisioning.py`.

## A5 — Swallowed exceptions

Exact pattern `except Exception: pass` (AST, handler body is a single `pass`):
**53 occurrences**. Each row gives the location, the enclosing function, and
whether the failure mode is open (loses safety/visibility) or closed
(conservative). "Open (telemetry)" means data is silently lost, not a security
hole.

| Location | Function | What is swallowed | Verdict |
| --- | --- | --- | --- |
| `audit.py:51` | `classify_host_path` | home/path resolution failure | Closed — falls back to basename, unknown paths stay STRICT |
| `gitops.py:700` | `find_most_recent_commit_msg` | reading the active `COMMIT_MSG` | Open (cosmetic) — falls back to older seen files |
| `jobs.py:555` | `cleanup_retained_jobs` | branch/worktree safety git checks | Closed for branch deletion; dir still moves to trash |
| `manager.py:334` | `_provision_and_launch` | cleanup after cancellation | Open (cleanup) — worktree/branch may leak |
| `manager.py:429` | `_provision_and_launch` | kill/cleanup cancelled launch | Open (process) — a spawned agy may survive |
| `manager.py:438` | `_provision_and_launch` | kill on setup failure | Open (process) |
| `manager.py:442` | `_provision_and_launch` | cleanup on setup failure | Open (resource) |
| `manager.py:455` | `_provision_and_launch` | worktree prune/branch delete on failure | Open (cleanup) |
| `manager.py:558` | `_handle_verification_auto_fix` | read `verify_tail.txt` | Open (auto-fix quality) — falls back to audit log |
| `manager.py:569` | `_handle_verification_auto_fix` | parse audit log for tail | Open (auto-fix quality) — generic prompt |
| `manager.py:830` | `reconcile` | retention cleanup | Open (disk) |
| `manager.py:939` | `result` | diff/commit gathering | Open (observability) — partial envelope |
| `manager.py:954` | `result` | one audit-event JSON line | Open (observability) |
| `manager.py:956` | `result` | audit tail read | Open (observability) |
| `manager.py:991` | `cancel` | queued-job worktree cleanup | Open (cleanup) |
| `manager.py:1017` | `cancel` | checkpoint commit during cancel | Open (data capture) |
| `manager.py:1035` | `list` | retention cleanup | Open (disk) |
| `manager.py:1216` | `resume` | kill on launch failure | Open (process) |
| `manager.py:1220` | `resume` | cleanup on launch failure | Open (resource) |
| `manager.py:1284` | `review` | entire git/audit block | **Open (security reporting)** — a raising audit returns empty findings |
| `parse_result.py:85` | `_extract_last_json_object` | full-content JSON fallback | Closed — returns None, caller emits `parse_error` |
| `recovery.py:87` | `get_liveness_summary` | git log read | Open (observability) |
| `recovery.py:228` | `reconcile_on_startup` | safety-net commit | Open (data capture) |
| `recovery.py:260` | `reconcile_on_startup` | usage record | Open (telemetry) |
| `recovery.py:285` | `reconcile_on_startup` | worktree prune | Open (cleanup) |
| `runner.py:342` | `kill` | wait after SIGKILL | Open (reap) |
| `runner.py:351` | `cleanup` | kill survivors | Open (process) |
| `runner.py:362` | `_close_process_files` | flush/close | Open (fd) |
| `server.py:142` | `_format_error` | `get_job` for a richer error envelope | Open (information) |
| `supervisor.py:201` | `_extract_conversation_id` | parse one stdout line | Open (information) |
| `supervisor.py:203` | `_extract_conversation_id` | read stdout | Open (information) |
| `supervisor.py:214` | `_extract_conversation_id` | regex cli.log | Open (information) |
| `supervisor.py:296` | `_finalize_non_running_process` | final usage cost | Open (telemetry) |
| `supervisor.py:319` | `_finalize_non_running_process` | usage record | Open (telemetry) |
| `supervisor.py:359` | `_try_pre_kill_checkpoint` | checkpoint commit | Open (data capture) |
| `supervisor.py:616` | `_finalize_terminated_process` | write `verify_tail.txt` | Open (observability) |
| `supervisor.py:670` | `_finalize_terminated_process` | safety-net commit | Open (data capture) |
| `supervisor.py:712` | `_finalize_terminated_process` | read denied calls | Open (denial detail) |
| `supervisor.py:782` | `_finalize_terminated_process` | final usage cost | Open (telemetry) |
| `supervisor.py:813` | `_finalize_terminated_process` | `on_state_change` callback | Open (callback) |
| `supervisor.py:835` | `_finalize_terminated_process` | usage record | Open (telemetry) |
| `supervisor.py:935` | `run_loop` | kill after 3 tick errors | Open (process) |
| `transcript.py:82` | `_unquote_once` | JSON unquote | Open (benign) — returns original |
| `transcript.py:262` | `_log_unreadable` | audit append | Open (audit trail) |
| `usage.py:567` | `_kill_usage_process` | wait after SIGTERM | Open (process) |
| `usage.py:629` | `read_usage_once` | communicate after kill | Open (process) |
| `verify.py:83` | `_is_path_outside` | resolve worktree root | Closed — root skipped, more restrictive |
| `verify.py:88` | `_is_path_outside` | resolve repo root | Closed — root skipped, more restrictive |
| `verify.py:104` | `_is_path_outside` | resolve candidate path | Closed — treated as outside |
| `verify.py:351` | `run_verify` | `on_proc_spawn` callback | Open (cancellation) — verify proc not tracked |
| `verify.py:374` | `run_verify` | wait after SIGKILL | Open (process) |
| `verify.py:398` | `run_verify` | scratch cleanup | Open (tmp) |
| `worktree.py:522` | `find_drifted_worktrees` | git HEAD read | Open (drift not reported) |

Highest-risk rows: `manager.py:1284` (a raised audit produces an empty
`audit_findings` list in `agy_review`), `manager.py:429/438` and
`supervisor.py:935` (failed kill can leave agy alive), and
`supervisor.py:359/670` (lost checkpoint/safety-net commits).

Proposed fix: keep the intentional cleanup swallows, but append an audit event
(`cleanup_error`, `telemetry_error`, ...) or narrow the exception type. For
`manager.review`, let audit errors surface as an `audit_unavailable` finding
instead of an empty list. Size M. Behavior-changing: observability only, except
review (yes).

## A6 — Duplicated logic

| Duplicate | Evidence |
| --- | --- |
| `files_modified` refresh block appears twice | `manager.py:650-658` and `manager.py:841-849` are identical |
| `usage.append_audit_event` is a pass-through to `jobs.append_audit_event` | `usage.py:710-717`; wrapper adds nothing |
| `worktree.provision` is an alias | `worktree.py:268-270` delegates to `provision_metadata_dir` |
| Last-JSON extraction implemented twice | `parse_result.py:62-88` (`_extract_last_json_object`) and `usage.py:256-278` (`_load_json_output`) |
| Tail-reading implemented three times | `util.read_file_tail` (`util.py:98`) and `cli_log.read_cli_log_tail` (`cli_log.py:47`) and `recovery`'s inline 64 KB tail use |
| Process-group kill logic implemented twice | `runner.LocalProcessRunner.kill` (`runner.py:308-344`) vs `usage._kill_usage_process` (`usage.py:548-568`) |
| Verify-tail recovery has two read paths | `manager.py:557-559` (file) and `manager.py:562-570` (audit JSON) |
| Safety-net commit invoked directly in three places | `recovery.py:226`, `supervisor.py:660`, `watcher.py:347` (the watcher wrapper is the only one that gates state) |

The `files_modified` duplicate was explicitly called out for removal in D58
item 7; it still exists. Proposed fix: extract one `refresh_files_modified(job)`
helper in `manager.py`; delete the `usage.append_audit_event` wrapper after
updating tests. Size S/M. Behavior-changing: no (pure refactor).

## A7 — Inconsistent naming

- `review_reason` accepts both `verify_failed` and `verification_failed`
  (`manager.py:486`, `manager.py:742`, `manager.py:879`, `models.py:375`), while
  SPEC §7 uses both spellings. This forces duplicated `in (...)` checks.
- `audit_violation` is overloaded: actual tampering, missing snapshot
  (`recovery.py:195-204`, `manager.py:1242-1249`, `supervisor.py:534`) and
  "audit unavailable" in some paths (`supervisor.py:891`). The distinct
  `audit_unavailable` is also a security reason (`jobs.py:287`).
- `failure_reason` (`parse_result.ParsedResult`) and `review_reason` (everywhere
  else) describe overlapping concepts.
- `JobManager._get_elapsed`, `record_job_execution_time`, `cumulative_minutes`,
  and `max_minutes`/`max_minutes_cap` use four names for two budget concepts.
- SPEC §17 calls the budget `job_budget_minutes`; the code calls it
  `Job.max_minutes`.
- `blocked_by_permission` and `denied_without_progress` are near-duplicates with
  different recovery guidance (`models.py:390`).

Severity Low individually, but these strings are the public contract. Any
normalization is behavior-changing for orchestrators; keep aliases and document
them. Size S.

## A8 — Mixed responsibilities

- `usage.py` (765 lines) parses `/usage`, spawns agy, masks email, caches,
  computes cost, detects auth/quota, appends `usage.jsonl`, **and** re-exports
  audit/daily-cap wrappers (`usage.py:710-717`, `usage.py:763-765`).
- `manager.py` (1,341) is a facade, but also owns provisioning, supervisor
  post-processing, auto-fix, denial auto-resume, usage guard, review assembly,
  disk accounting and cleanup.
- `gitops.py` (925) owns process execution of git, staging policy, sensitive
  classification, commit retry, review queries and config snapshots.
- `audit.py` (653) mixes the real audit, host-path classification, snapshot I/O
  and two legacy compatibility helpers (`audit.py:366`, `audit.py:628`).
- `models.py` mixes response envelopes, untrusted wrapping, `next_actions`
  policy and `job.json` schema migration.
- `util.py` mixes pure helpers with prompt assembly that imports models
  (`util.py:185`).

Proposed split (behavior-preserving, L): `usage/` package (`parser`, `reader`,
`cost`, `log`), `git/` package (`exec`, `staging`, `commits`, `queries`),
`manager` into `launch.py` + `responses.py`. Do this only after A2's function
extractions and with the full suite green.

## A9/A13 — Imports

A static top-level import graph (AST `Import`/`ImportFrom`, ignoring function-body
imports) found **no import-time cycles**. The graph is a DAG rooted at `server`
and `__main__`.

Awkward edges (all lazy, so they work):

- `models.py:282` imports `gitops.SkippedFile` inside `Job.from_dict`; the data
  model depends on the git layer. Better: move `SkippedFile` to `models.py` or
  serialize it as a plain dict.
- `util.py:185` imports `models.wrap_untrusted_text`; `models` imports `util` at
  module level, creating a cycle that only the lazy import hides.
- `supervisor.py:425` imports `audit._hash_or_missing` (private) to update the
  host baseline.
- `manager.py:859` imports `parse_result._extract_last_json_object` (private).
- `gitops.py:764` imports `jobs.append_audit_event` inside a function; the
  git↔jobs edge is otherwise avoided.
- `usage.py` is imported as `usage_utils` by manager/supervisor to avoid a name
  clash with the word "usage"; this is cosmetic but unusual.

Severity Low. Fix: promote `_hash_or_missing` and `_extract_last_json_object` to
public names, move `SkippedFile`, or accept the lazy edge and document it. Size
S. Behavior-changing: no.

## A10 — Dead / tests-only code

Verified with `grep -rn --include=*.py '<name>' src/ tests/` and excluding the
definition:

| Symbol | Production references | Tests | Verdict |
| --- | ---: | --- | --- |
| `gitops.run_hardened_git` (`gitops.py:323`) | 0 | 0 | Dead |
| `runner.read_proc_pgid` (`runner.py:94`) | 0 | 0 | Dead |
| `Config.checkpoint_commit_minutes` (`config.py:52`) | 0 reads | config tests only | Dead contract |
| `worktree.preflight` (`worktree.py:156`) | 0 | `tests/git/test_worktree.py`, `tests/git/test_git_scenarios.py` | Tests-only |
| `worktree.provision` / `provision_metadata_dir` (`worktree.py:245/268`) | 1 (`manager._provision_and_launch`) | several | Production launch path; A4 resolved (D62) |
| `audit.verify_host_path_snapshot` (`audit.py:366`) | 0 | `test_audit.py` | Tests-only |
| `audit.run_security_audit` (`audit.py:628`) | 0 | `test_audit.py` | Tests-only |
| `jobs.rebuild_daily_cap_from_audit` (`jobs.py:394`) | 0 | `test_jobs.py`, `tests/test_daily_cap_timezone.py` | Tests-only |
| `jobs.prune_retained_jobs` (`jobs.py:480`) | 0 | `test_jobs.py` | Tests-only; production uses `cleanup_retained_jobs` |
| `gitops.get_diff_stat` (`gitops.py:830`) | 0 | `tests/git/test_gitops.py` | Tests-only |
| `gitops.get_commits_list` (`gitops.py:841`) | 0 | `tests/git/test_gitops.py` | Tests-only |
| `gitops.has_base_moved` (`gitops.py:819`) | 0 | `tests/git/test_gitops.py` | Tests-only |
| `util.sha256_bytes` (`util.py:158`) | 0 | `test_util.py` | Tests-only |
| `usage.check_daily_cap` (`usage.py:763`) | 0 | `tests/usage/test_usage.py` | Tests-only wrapper |
| `manager.resume` tail after `return` (`manager.py:1224-1228`) | n/a | n/a | Unreachable |

Note: tests-only helpers may still be intentional compatibility surface. Before
deleting, check whether downstream users import them; this repo is the only
caller today. `manager.py:1224` returns, then `manager.py:1226-1227` can never
run.

Severity Low. Fix: remove the two dead symbols and the unreachable tail; decide
per tests-only helper (keep public ones with a deprecation note). Size S.
Behavior-changing: yes for deletion, but no runtime behavior today.

## A11 — Magic numbers not in config

| Value | Where | Why it matters |
| --- | --- | --- |
| `+ 120` seconds | `supervisor.py:114` | deadline grace before killing |
| `300` seconds / `300` clamp | `manager.py:705`, `manager.py:709` | `agy_wait` max |
| `300` seconds lock age | `gitops.py:386` | stale `index.lock` deletion threshold |
| `600` seconds | `watcher.py:92` | checkpoint interval (should be config, A3) |
| `900` seconds | `supervisor.py:570`, `verify.py:299` | verify timeout |
| `5.0` seconds | `supervisor.py:80` audit interval; `supervisor.py:82` settle; `manager.py:104` settle | scheduling behavior |
| `10 * 1024 * 1024` | `supervisor.py:452` | stream tail cap |
| `2048` / `4096` bytes | `supervisor.py:514/598/613`, `recovery.py:70` | tail caps |
| `64 * 1024` / `256 * 1024` / `4 * 1024 * 1024` | `manager.py:885/949/858`, `recovery.py:68` | read caps |
| `30.0` / `60.0` | `usage.py:34-35` | usage timeout/cache TTL |
| `60.0` seconds | `gitops.py:281` | git command timeout |
| `1024 * 1024` | `gitops.py:283` | git output cap |
| `0.1`/`5.0` backoff | `gitops.py:356-357` | lock retry |

Most are documented constants local to a module and are not user-facing; the
ones that a user would reasonably want to tune are the checkpoint interval
(A3), verify timeout and wait clamp. Severity Low. Fix: move the user-facing
ones into `Config` with defaults, leave protocol caps as module constants. Size
S.

## A12 — Code / SPEC disagreements

1. **Checkpoint interval** (A3): `SPEC.md:400,590` vs `watcher.py:92` +
   `supervisor.py:145`. High.
2. **`.agy` provisioning** (A4): RESOLVED (D62) — `manager._provision_and_launch`
   now calls `worktree.provision_metadata_dir` after `create_worktree`; the
   launch-path test is `tests/test_agy_provisioning.py`.
3. **Lock order**: `SPEC.md:364-368` says the concurrency flock is the outer
   tier and the job lock is inner; `manager.resume` acquires the job lock
   (`manager.py:1065`) before the concurrency flock (`manager.py:1153`). No path
   acquires them in the reverse order today, so no deadlock is reachable, but
   the documented hierarchy is not the real one. Medium (documentation or code).
4. **`starting` state**: `SPEC.md:365,582` and `jobs.py:242` mention a
   `starting` state; `states.py:11-19` has no such state. It survives only as a
   legacy string check. Low.
5. **`job_budget_minutes`**: `SPEC.md:563` names the budget
   `job_budget_minutes`; the code uses `Job.max_minutes`
   (`models.py:183`). Low (naming).
6. **`audit_pass` event**: `SPEC.md:557` shows an `audit_pass` audit event;
   `grep -rn '"audit_pass"' src/agy_mcp/*.py` returns 0 matches. Audit passes
   are not individually logged (only violations and state changes are). Low.
7. **Invalid-message warning**: `SPEC.md:379` says the watcher logs a warning on
   the first invalid `COMMIT_MSG`; `watcher.py:184-187` silently resets and
   retries once. Low.
8. **`starting`/`resuming` guarded transitions**: D55 item 3
   (`docs/DECISIONS.md:270`) describes guarded `starting`/`resuming`
   transitions; the implementation uses an in-memory `_resuming_jobs` set
   (`manager.py:115`) and only logs/waits on it. Low.
9. **Watcher cadence**: `SPEC.md:377` says the watcher polls every 5 seconds;
   the supervisor ticks every 1 second (`supervisor.py:80`) and applies a 5 s
   settle window. Behavior is equivalent, wording is not. Low.
10. **Envelope example**: `SPEC.md:165-188` omits `denied_commands`,
    `denied_tools` and `verify`, all of which the code returns
    (`models.py:128-129`, `models.py:162-170`). Low.

Severity is per item; the highest-value fixes are 1 and 2, then 3.

## A14 — Test coverage by module

Direct-import counts from
`grep -l "agy_mcp\.<module>" tests/*.py | wc -l`:

| Module | Direct test files | Dedicated tests | Weak spots |
| --- | ---: | --- | --- |
| `__init__.py` / `__main__.py` | 0 | e2e subprocess | no direct version/entrypoint test |
| `audit.py` | 12 | `test_audit.py` | review's swallowed-audit path not tested |
| `cli_log.py` | 2 | `test_cli_log.py` | format/limit edges thin |
| `config.py` | 29 | `test_config.py` | no test that checkpoint config reaches the watcher (A3) |
| `gitops.py` | 20 | `tests/git/test_gitops.py`, `tests/git/test_git_scenarios.py` | good |
| `jobs.py` | 33 | `test_jobs.py` | all 16 transitions covered, including the 2 explicit `follow_up` edges (`test_jobs.py:171`, `tests/server/test_smoke.py:62`) |
| `manager.py` | 29 | `test_manager.py`, `tests/test_agy_provisioning.py` | launch-path provisioning covered (A4 resolved, D62); no lock-order test |
| `models.py` | 35 | `tests/server/test_models.py` | good |
| `parse_result.py` | 3 | `test_parse_result.py` | good precedence coverage |
| `recovery.py` | 4 | `test_recovery.py` | good |
| `runner.py` | 32 | `test_runner.py` | good |
| `server.py` | 5 | `tests/server/test_server_unit.py`, `tests/server/test_server_e2e.py` | no direct SIGTERM-shutdown assertion observed |
| `states.py` | 13 | `tests/server/test_smoke.py`, `test_jobs.py` | good (table completeness checked) |
| `supervisor.py` | 3 | `test_supervisor.py` | huge finalization path only indirectly covered |
| `transcript.py` | 1 | `test_transcript.py` | good safety coverage (symlink, traversal, oversize) |
| `usage.py` | 6 | `test_usage_*.py` | good |
| `util.py` | 6 | `test_util.py` | good |
| `verify.py` | 7 | `tests/verify/test_verify.py` | good |
| `watcher.py` | 7 | `tests/supervisor/test_watcher_commit_pipeline.py` | good |
| `worktree.py` | 14 | `tests/git/test_worktree.py` | good |

Missing tests worth adding first (all behavior-preserving to add):

1. A manager/watcher test that sets `checkpoint_commit_minutes` and observes the
   interval (exposes A3).
2. ~~A launch-path test asserting `worktree/.agy` exists with mode 0700 and
   `PLAN.md` seeded (exposes A4).~~ Covered by `tests/test_agy_provisioning.py`
   (A4 resolved, D62).
3. A review test where `audit()` raises, asserting the response contains an
   `audit_unavailable` finding rather than an empty list (exposes A5).
4. A concurrency test that starts and resumes simultaneously and records lock
   order (documents A12 item 3).
5. A test that every swallowed-exception path at least appends an audit event,
   once A5 is addressed.

## Proposed fix batches, in order

**Batch 0 — tests only (no behavior change).** Add the tests above as
`xfail`/failing-first where they expose a bug; keep the suite green otherwise.
Size M.

**Batch 1 — config/SPEC correctness (behavior-changing).** Wire
`checkpoint_commit_minutes` through `JobSupervisor` → `CommitWatcher`; call
`provision_metadata_dir` in `_provision_and_launch`; log the first invalid
`COMMIT_MSG`. Size S. Add D61 (or extend an existing D-number) and update
SPEC/CODEMAP.

**Batch 2 — fail-open cleanup.** Fix `manager.review` to surface
`audit_unavailable`; add audit events to the process/commit swallows; do not
change the intentional best-effort ones. Size M.

**Batch 3 — dead code and unreachable tail.** Delete `run_hardened_git`,
`read_proc_pgid`, and the unreachable `resume` tail; decide on tests-only
helpers. Size S. Behavior-changing only in source shape.

**Batch 4 — naming normalization.** Accept both verify reason spellings with one
constant; rename `job_budget_minutes` mentions in SPEC or code; document
`audit_violation` overload. Size S/M.

**Batch 5 — structure.** Extract the 395-line finalizer, then the manager's
launch/response helpers, then split `usage.py`. Size L. Do not combine with
behavior fixes.

**Batch 6 — magic numbers.** Move the user-facing values (checkpoint, verify
timeout, wait clamp) to config after Batch 1; leave protocol caps as constants.
Size S.

Rationale for the order: Batch 0 makes the bugs visible; Batch 1 fixes the two
documented contracts with the smallest diffs; Batch 2 reduces silent failure;
Batches 3-4 are cheap cleanup; Batch 5 is the risky refactor and must come last
with a green suite.
