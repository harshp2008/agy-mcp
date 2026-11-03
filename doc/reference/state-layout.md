# State layout

Status: current
Audience: operators, contributors

All wrapper state lives under one root:

```text
$XDG_STATE_HOME/agy-mcp         # when XDG_STATE_HOME is set
~/.local/state/agy-mcp          # otherwise
```

`agy-mcp --state-dir PATH` overrides it; the installer's generated `run.sh`
passes `--state-dir` with the resolved path (the `AGY_MCP_STATE_DIR` override or
the XDG default). The root and every job directory are created `0700`. A job
directory is `<state>/jobs/<uuid>/`.

## Tree

```text
<state>/
├── jobs/
│   ├── <job-id>/
│   │   ├── job.json                 # the persisted Job record (0600)
│   │   ├── prompt.txt               # the submitted prompt (0600)
│   │   ├── stdout.txt               # agy stdout stream (append)
│   │   ├── stderr.txt               # agy stderr stream (append)
│   │   ├── cli.log                  # agy's own log (read bounded)
│   │   ├── audit.jsonl              # append-only events (0600)
│   │   ├── snapshot.json            # audit baseline taken at launch
│   │   ├── verify_tail.txt          # last verify diagnosis, if any
│   │   ├── rejected_COMMIT_MSG.txt  # latest rejected message (0600)
│   │   ├── scratchpad/              # retained PLAN/NOTES/LOG/REPORT copies
│   │   ├── tmp/                     # child TMPDIR (0700)
│   │   └── work/                    # the git worktree
│   │       └── .agy/                # agent metadata (never committed)
│   │           ├── PLAN.md
│   │           ├── NOTES.md
│   │           ├── LOG.md
│   │           ├── REPORT.md
│   │           ├── COMMIT_MSG
│   │           └── COMMIT_MSG.{seen,invalid,done}.<n>
│   └── .cleanup-manifest-<UTC>.json # cleaner manifest (0600)
├── usage.jsonl                      # one row per finished run (0600)
├── server.log                       # Python logging (stderr + file)
├── concurrency.lock                 # job-count flock
├── daily_cap.json                   # {date, count} (0600)
├── daily_cap.lock                   # daily-cap flock
└── trash/<job-id>_<epoch>/          # pruned job directories
```

## Per-job files

| Path | Contents | Written by |
|---|---|---|
| `job.json` | The 55-field `Job` record below; atomic write (temp + fsync + replace), mode 0600, 1 MiB read cap. | `jobs.save_job` |
| `prompt.txt` | The exact submitted prompt, mode 0600. | `jobs.create_job` |
| `stdout.txt`, `stderr.txt` | Raw streams, opened append by the runner. A failed job with no stdout uses the stderr tail as the `agy_result` response. | `runner.LocalProcessRunner.launch` |
| `cli.log` | agy's own log. The wrapper reads at most a 2 MB tail and a 1 MB head, extracts denials and conversation ids, and treats the text as data. | `agy` (path passed on the command line) |
| `audit.jsonl` | Append-only audit events: `{ts, event, detail}`, one JSON object per line, fsync per event, mode 0600. See [log-formats.md](log-formats.md). | `jobs.append_audit_event` |
| `snapshot.json` | The security baseline (`dot_git_content`, `git_config`, `host_paths`, instruction/policy/agents files) taken before launch; used by the resume audit and `agy_review`. Missing or corrupted is a fatal audit finding, not a pass. | `audit.save_snapshot` |
| `verify_tail.txt` | The captured verification failure excerpt used by the diagnosis and the fix prompt. | `supervisor` |
| `rejected_COMMIT_MSG.txt` | The raw last invalid commit message, kept as evidence, mode 0600. | `watcher` |
| `scratchpad/` | Copies of `.agy/PLAN.md`, `NOTES.md`, `LOG.md`, `REPORT.md` retained before worktree cleanup (D62). | `manager._retain_scratchpad` |
| `tmp/` | The child `TMPDIR`, mode 0700. | `manager` |
| `work/` | The git worktree. `.agy/` is excluded from every `git add` by pathspec and Python filtering, and is appended to the repo-local `info/exclude` (D62). | `worktree.create_worktree` |

The `.agy/` tree is the agent protocol: `PLAN.md` (checkbox plan),
`NOTES.md`/`LOG.md`/`REPORT.md` (scratchpad), and `COMMIT_MSG` for the commit
request. The watcher consumes a settled message by renaming it to
`COMMIT_MSG.seen.<generation>`, records completion with `COMMIT_MSG.done.<n>`,
and moves an invalid message to `COMMIT_MSG.invalid.<n>` (capped at 20 slots).

## State-root files

| Path | Contents |
|---|---|
| `usage.jsonl` | One JSON line per finished run. Fields: `ts`, `job_id`, `state`, `model`, `model_resolved`, `duration_seconds`, `cumulative_minutes`, `commits_count`, `tokens`, `usage_cost`, `quota_estimate`, `run_generation`, `wall_clock_seconds`, and — only when a registry is configured — `account` (registry name) and `masked_email`. Appended under an exclusive flock, mode 0600, before the terminal state is persisted (D68). No prompt, title, code or full email is included. |
| `server.log` | Python logging at INFO: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`. stderr and this file; never stdout (stdout is the MCP stream). |
| `concurrency.lock` | An empty file used for the `flock` that serializes active-job counting during start/resume. |
| `daily_cap.json` | `{"date": "YYYY-MM-DD", "count": n}`, mode 0600. The date comes from `timezone`; a new date resets the count. |
| `daily_cap.lock` | The `flock` for `daily_cap.json` and `rebuild_daily_cap_from_audit`. |
| `trash/<job-id>_<epoch>/` | A finished job directory moved here by retention, never deleted automatically. Only terminal states (`done`, `failed`, `cancelled`) are pruned, and a branch with unmerged commits is left alone. |

`rebuild_daily_cap_from_audit` can reconstruct the day's count by scanning
`jobs/*/audit.jsonl` under the same lock.

## `job.json` fields

The table is generated from the `Job` dataclass (55 fields). `schema_version`
is `1`; a record with a higher version is rejected rather than guessed at.
Fields marked internal by their names (`cli_log_offset`,
`cli_log_prefix_sha256`, `usage_at_launch`, `plan_nudge_count`, `audit_status`,
`audit_reason`, `scratchpad_files`) are still persisted because recovery and the
envelopes read them.

<!-- BEGIN GENERATED: job-fields -->
| Field | Type |
|---|---|
| `job_id` | `str` |
| `title` | `str` |
| `repo_path` | `Path` |
| `branch` | `str` |
| `base_commit` | `str` |
| `head_commit` | `str` |
| `state` | `JobState` |
| `model` | `str` |
| `model_resolved` | `str | null` |
| `max_minutes` | `int` |
| `verify` | `str | null` |
| `verify_timeout_seconds` | `int` |
| `conversation_id` | `str | null` |
| `pid` | `int | null` |
| `pgid` | `int | null` |
| `start_time` | `int | null` |
| `worktree_path` | `Path | null` |
| `commit_msg_generation` | `int` |
| `review_reason` | `str | null` |
| `source_dirty` | `bool` |
| `recovered` | `bool` |
| `created_at` | `str` |
| `updated_at` | `str` |
| `cumulative_minutes` | `float` |
| `commits_count` | `int` |
| `files_modified` | `list[str]` |
| `skipped_files` | `list[str]` |
| `denials` | `list[dict[str, Any]]` |
| `denied_commands` | `list[dict[str, Any]]` |
| `denied_tools` | `list[dict[str, Any]]` |
| `warnings` | `list[str]` |
| `tokens` | `TokenUsage` |
| `schema_version` | `int` |
| `verify_status` | `dict[str, Any] | null` |
| `auto_fix_count` | `int` |
| `denial_resume_count` | `int` |
| `error` | `dict[str, Any] | null` |
| `stdout_offset` | `int` |
| `cli_log_offset` | `int` |
| `cli_log_prefix_sha256` | `str | null` |
| `conversation_id_source` | `str | null` |
| `conversation_continued` | `bool | null` |
| `run_generation` | `int` |
| `launch_wall_clock` | `float | null` |
| `usage_at_launch` | `dict[str, Any] | null` |
| `usage_cost` | `dict[str, Any] | null` |
| `quota_estimate` | `dict[str, Any] | null` |
| `runs` | `list[dict[str, Any]]` |
| `resume_after` | `str | null` |
| `plan_summary` | `dict[str, Any] | null` |
| `plan_nudge_count` | `int` |
| `agent_report` | `str | null` |
| `audit_status` | `str | null` |
| `audit_reason` | `str | null` |
| `scratchpad_files` | `list[str]` |
<!-- END GENERATED: job-fields -->

## Safety rules

- Writes are atomic where a reader can observe the file (`job.json`,
  `daily_cap.json`, manifests): temp file in the same directory, `fsync`, then
  `os.replace`, with the parent directory fsynced.
- The state root, job directories and `tmp/` are `0700`; files that can contain
  a prompt, a diff, an audit record or a commit message are `0600`.
- The state directory is never a valid `repo_path` (`state_dir_prohibited`), and
  the cleaners refuse a store outside `HOME`.
- `trash/` is retained rather than deleted, so a mistaken retention move can be
  recovered by hand.

## Sources

- `src/agy_mcp/jobs.py` — `get_base_state_dir`, `get_job_dir`, `create_job`,
  `save_job`, `_atomic_write_file`, `append_audit_event`,
  `check_and_increment_daily_cap`, `rebuild_daily_cap_from_audit`,
  `prune_retained_jobs`.
- `src/agy_mcp/models.py` — the `Job` dataclass and `Job.to_dict`.
- `src/agy_mcp/manager.py` — job directory creation, `tmp/`, scratchpad
  retention, `snapshot.json` and `verify_tail.txt` use.
- `src/agy_mcp/audit.py` — `save_snapshot`/`load_snapshot` and the 2 MiB cap.
- `src/agy_mcp/runner.py` — stdout/stderr/cli.log file handling.
- `src/agy_mcp/watcher.py` — `.agy/COMMIT_MSG*` names and `rejected_COMMIT_MSG.txt`.
- `src/agy_mcp/usage.py` — the `usage.jsonl` row and the per-account cache key.
- `src/agy_mcp/accounts/pools.py` — the typed pool/window view.
- `src/agy_mcp/server.py` — `server.log` format and destination.
- `src/agy_mcp/cleanup/store_io.py` — `_write_manifest` (shared by both cleaners).
- `doc/audits/2026-11-02-part2c-coverage.md` — §7 and §10, the path/format
  inventory (COV-02).
