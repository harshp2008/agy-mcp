# agy-mcp architecture

Status: current
Audience: contributors, maintainers

This is a short narrative of the layers and the invariants between them. The
normative contract is `doc/design/spec.md`; navigation is `doc/project/code-layout.md`; the honest
weakness list is `docs/AUDIT_ORGANIZATION.md`.

## Layers

The server is one Python process with a handful of worker threads per job. The
dependency direction is downward: each layer may import the layers below it,
and nothing lower imports the MCP server.

| Layer | Modules | Responsibility |
| --- | --- | --- |
| MCP server | `server.py` | FastMCP stdio transport, ten tool handlers, argument parsing, error envelopes, config/state/log bootstrap, startup recovery, shutdown. |
| Manager | `manager.py` | Validation, job creation, provisioning thread, supervisor threads, auto-fix/auto-resume, status/result/review/cancel/cleanup assembly, usage guard. |
| Supervisor | `supervisor.py` | Per-job deadline, periodic audit, watcher tick, process poll, finalization, verify gate, cost/usage, final transition. |
| Runner | `runner.py` | argv construction, `/proc` inspection, subprocess launch in a new session, SIGTERM→SIGKILL process-group kill. |
| Audit | `audit.py` | Pre-launch snapshot, host-path classification, pure comparison into `Findings`, fail-closed handling of missing/corrupt baselines. |
| Verify | `verify.py` | Allowlist and per-tool flag/path checks, shell-free execution with sanitized env, capped tail, auto-fix prompt. |
| GitOps | `gitops.py` | Hardened git calls, staging filters, commit retry, checkpoint/safety-net commits, review queries, git-config snapshots. |
| Usage | `usage.py` | `/usage` parsing/reading/cache, email masking, guard thresholds, per-job cost, `usage.jsonl`. |
| Support | `jobs.py`, `models.py`, `recovery.py`, `watcher.py`, `worktree.py`, `transcript.py`, `states.py`, `config.py`, `util.py`, `cli_log.py`, `parse_result.py` | Persistence/state machine, envelopes, crash recovery, commit watcher, worktree lifecycle, transcript confinement, state table, config, helpers, CLI-log denials, result parsing. |

The event flow is: MCP tool → `JobManager` → `runner` + `JobSupervisor` →
`watcher`/`gitops`/`audit`/`verify` → `jobs.transition_job_state` →
`models` envelope back to the MCP tool. `doc/project/code-layout.md` names every function
in order.

## Process and persistence model

- One server process; one `_provision_and_launch` thread and one supervisor
  thread per running job; supervisor threads are daemons and are stopped
  cleanly on shutdown without killing agy (the agy process group survives a
  server restart by design; recovery reattaches).
- agy runs with `start_new_session=True`, so its pid is normally its pgid.
  Cancellation/timeout signals the group, not just the leader (F11).
- All job state is on disk under `~/.local/state/agy-mcp`: `jobs/<id>/job.json`
  (atomic), `audit.jsonl` (fsync append), `stdout.txt`/`stderr.txt`,
  `cli.log`, `prompt.txt`, `snapshot.json`, `work/`, `tmp/`. Files are 0600 and
  directories 0700.
- `usage.jsonl` is an append-only record of completions, flock-protected and
  deliberately free of prompts, titles and repository content.

## Lock hierarchy

The wrapper coordinates execution with four locks. The real acquisition order in the code is:

1. **Job lock (Per-job lifecycle)** — `JobManager._get_job_lock(job_id)`. In-memory
   lock serializing lifecycle state transitions (`cancel`, `resume`) on a single job.
2. **Concurrency flock (Inter-process)** — `jobs.concurrency_lock()` on
   `<state_dir>/concurrency.lock`. Exclusive flock serializing active job count
   verification against `max_concurrent_jobs` during `start` and `resume`. Held
   while counting active jobs, checking the daily cap and creating a job
   (`manager.start`), or while checking the resume slot and launching
   (`manager.resume`). It is never held while waiting on an in-memory lock.
   `start` takes it with no job lock held (the job does not exist yet); `resume`
   takes it while already holding the job lock.
3. **Manager state lock** — `JobManager._lock` (`RLock`). In-memory `RLock`
   protecting manager collection mappings (`_supervisors`, `_threads`,
   `_resuming_jobs`, `_job_locks`, `_job_commit_locks`); held only for dictionary updates,
   never across long operations.
4. **Per-job commit lock** — `JobManager._get_job_commit_lock(job_id)`. Shared
   between `JobSupervisor` and `CommitWatcher` to strictly serialize git staging
   and commits (`watcher.tick`, `checkpoint_commit`, `safety_net_commit`,
   `_try_pre_kill_checkpoint`).

`resume` is the only path that holds both the job lock and the concurrency flock,
and it takes the job lock first (`src/agy_mcp/manager.py`). No path acquires them in the reverse
order, so the documented order matches the code and no deadlock is reachable
through these locks. Never hold the flock while waiting on an in-memory lock in new code.

## State machine

The **only** states are those in `states.py` (`JobState`):

```
queued
running
needs_decision
needs_review
done
failed
timeout
cancelled
```

Terminal: `done`, `failed`, `cancelled` (`TERMINAL_STATES`). `timeout` and
`needs_review` are deliberately resumable; `needs_review` is resumable only for
a non-security reason. `done` and `cancelled` leave their state only through an
explicit `agy_resume(..., follow_up=true)` edge; the default resume gate stays
terminal (`invalid_state_for_resume`).

Allowed edges, copied from `TRANSITION_TABLE` (16 rows = 14 lifecycle edges +
2 explicit follow-up edges):

- `queued → running`, `queued → cancelled`
- `running → done`, `running → needs_review` (audit/verify/permission/message),
  `running → timeout`, `running → needs_decision`, `running → failed`,
  `running → cancelled`
- `needs_decision → running`
- `timeout → running`
- `needs_review → running` (only when `review_reason` is not one of
  `audit_violation`, `host_path_modified`, `instruction_file_modified`,
  `audit_unavailable`)
- `done → running` and `cancelled → running` only with `follow_up=true`
  (`explicit_follow_up_only` rows)

There is no `starting` or `resuming` state; those words survive only as a
legacy string check (`jobs.count_active_jobs`) and an in-memory manager set
(`_resuming_jobs`). See `docs/AUDIT_ORGANIZATION.md` A12.

## Security model

**Threat model (honest):** agy executes directly on the host as the invoking
user. It can read any file the user can read and can write inside its worktree
and the dedicated `TMPDIR`. The wrapper is a *detector and gate*, not an
OS-level sandbox (D1, SPEC §5).

Controls, layer by layer:

- **Input/preflight:** repository must be inside HOME or
  `allowed_workdir_roots`, cannot be HOME or the state dir, must have a real
  `.git` directory and resolvable HEAD; `.agents/**` is refused unless
  `trust_agents_dir=true`.
- **Environment:** children get only `HOME`, `PATH`, `LANG=C.UTF-8`,
  `TMPDIR=<job>/tmp` and three hardened git overrides; no parent secrets
  (`util.env_allowlist`).
- **Git:** the wrapper owns every modifying git call with
  `core.fsmonitor=false`, `core.hooksPath=/dev/null`, `core.pager=cat`.
  Sensitive paths (`.env*`, keys, `.ssh`, `.gnupg`, `.agents`, `.agy`,
  `AGENTS.md`/`GEMINI.md`, policy files) are never staged; junk/build
  artifacts are excluded too. Renames are checked on both sides.
- **Audit:** a baseline snapshot is taken before launch and compared
  periodically, before commits, before resume and after completion. Fatal
  findings (`.git` pointer, resolved git config, `.agents/**`, instruction
  files) kill the group and suppress commits; missing/corrupt snapshots fail
  closed. Host paths are strict except append-tolerant `known_hosts` and
  warn-only `known_hosts.old`.
- **Prompt/agent:** the standing preamble is prepended to every fresh/resume
  prompt; agent/transcript/CLI text is wrapped in `<untrusted_text>` with a
  random nonce; a resume answer from an untrusted source is wrapped.
- **Process:** signals go to the process group; cancellation and deadline both
  checkpoint safe dirty work first; a fatal audit suppresses commits.
- **Verification:** exact-token allowlist, no shell, per-tool dangerous-flag
  and path-escape rejection, sanitized env, 15-minute timeout.
- **Output:** envelope fields are capped and untrusted-wrapped; absolute host
  paths are relativized in audit findings and error envelopes; account emails
  are masked; the transcript reader is confined to
  `<agy_home>/brain/<uuid>/...` with UUID, `..`, symlink and size checks.
- **Merge boundary:** the wrapper never merges, rebases, pushes, or rewrites
  history; the user integrates the branch.

**Residual risks:** in-process file reads outside the worktree are visible only
through the host-path detector; detached grandchildren can escape a pgid kill
(documented limitation); pattern lists for quota/auth are UNVERIFIED against
real agy (D59); the audit is detective, not preventive.

## Sources

- `src/agy_mcp/manager.py` — layer definitions, lock hierarchy (`_get_job_lock`, `concurrency_lock`, `_lock`, `_get_job_commit_lock`), thread and supervisor management.
- `src/agy_mcp/jobs.py` — concurrency flock (`concurrency_lock`), job state transitions, active job counts.
- `src/agy_mcp/states.py` — `JobState`, `TRANSITION_TABLE`, and allowed lifecycle/follow-up edges.
- `src/agy_mcp/supervisor.py` — supervisor thread loop, tick error handling, verify gate.
- `src/agy_mcp/runner.py` — process group execution, session handling.
- `src/agy_mcp/audit.py` — pre-launch snapshot and security auditing.
- `src/agy_mcp/verify.py` — command allowlists and test verification.
- `src/agy_mcp/gitops.py` — git commands, staging filters, commit retries.
- `src/agy_mcp/usage.py` — quota parsing, usage record logging.
