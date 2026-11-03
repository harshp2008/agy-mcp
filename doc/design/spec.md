# agy-mcp normative specification (v3)

Status: current
Audience: maintainers, contributors, orchestrators

The normative behavior contract, tool specifications, process model, security gates, and lifecycle invariants for agy-mcp.

## 1. Purpose and Architecture
`agy-mcp` is an MCP (Model Context Protocol) stdio server that orchestrates autonomous, long-running coding jobs executed by the Google Antigravity CLI (`agy`). Jobs execute in dedicated git worktrees, make atomic commits via a wrapper-supervised watcher, undergo security auditing and automated test verification, and report structured results back to the invoking orchestrator LLM (e.g., DeepSeek inside dsh).

The system enforces defense-in-depth on the host by:
1. Confining file-tool edits to isolated git worktrees.
2. Managing all git staging and commits strictly from the wrapper using hardened Git flags.
3. Pre-provisioning job metadata and stripping all parent environment secrets.
4. Auditing repository security surfaces before execution, on continuous timers, and post-execution.
5. Detecting unauthorized mutations to host configuration and user security files via cryptographic snapshots.

## 2. Non-goals
- No GUI or interactive terminal prompts.
- No pushing commits to remotes.
- No merging job branches into user branches (the user or orchestrator merges explicitly).
- No handling, storage, or transmission of Google credentials or OAuth tokens.
- No bypassing `agy`'s internal permission system or running with `--dangerously-skip-permissions`.
- No multi-machine distribution.
- No running unvetted test runners or bare interpreters inside `agy`.
- Full container virtualization (Docker runner) is deferred to v2.

## 3. Packaging & Dependencies
- **Runtime:** Python >= 3.12. Standard `src` layout: package `agy_mcp`, CLI console script entry point `agy-mcp`.
- **Dependencies:** `mcp>=1.2,<2` (FastMCP; strictly `<2` as MCP Python SDK 2.x introduces breaking changes), stdlib otherwise.
- **Development Tooling:** `uv`, `pytest`, `ruff`.
- **Installation:** `uv tool install git+https://github.com/<user>/agy-mcp`.

## 4. Working Rules for Builders
1. Work only inside this repository root.
2. Commit after each coherent milestone: stage specific files, subject <= 72 chars, imperative mood, detailed body.
3. Never force-push, reset HEAD, or delete branches outside the task's scope.
4. Stop and emit `NEEDS_DECISION: <question> | <options> | <recommendation>` for irreversible choices.
5. Record reversible architectural choices in `doc/design/decisions.md`.
6. Automated tests must pass before committing, unless prefixed with `WIP:`.
7. Maintain strict documentation integrity; never claim verification without empirical proof.

---

## 5. Threat Model (Honest Version)
`agy` runs directly on the host machine as the local user. Empirical probing (documented in `docs/AGY_FINDINGS_3.md`) demonstrated that built-in safe inspection commands (`head`, `grep`, `git diff`) lack workspace path confinement and read host files outside the repository. Furthermore, git inspection commands accept arbitrary command-line arguments, including output redirection (e.g., `git diff --output=<path>`), which functions as an unconfined host write primitive.

### Reality of v1 Containment
- **Branch & Workspace Isolation:** `git worktree add` creates an independent checkout. The user's active checkout is not touched.
- **File Tool Confinement:** Built-in agent editing tools (`write_file`, `edit_file`) are path-restricted by `agy` to the workspace and `/tmp`, but shell inspection commands bypass path restrictions.
- **Not a Sandbox:** v1 provides branch isolation and automated tamper detection, **not** an OS sandbox. `agy` must be treated as possessing read access to all user-readable files and write access to the host filesystem. Real container isolation is scheduled for v2.
- **Untrusted Ingestion:** Text ingested from repository files, git diffs, web searches, or test outputs can steer `agy` via prompt injection.
- **Reverse Injection Defense:** All outputs returned by `agy` (response text, commit messages, file paths, test failures) are treated by the wrapper as untrusted data and strictly bounded by byte caps to prevent second-order prompt injection against the orchestrator LLM.
- **Process Group Limits & Detached Processes:** The supervisor signals the entire process group (`killpg`) upon job termination or cancellation, ensuring children in the process group are reaped even if the leader exited. However, grandchildren or subprocesses that detach via `setsid` or double-fork daemonization allocate a new session/process group and can escape process-group termination (an accepted limitation of non-containerized process group supervision).

---

## 6. Runner Interface
To cleanly separate process execution from worktree management and allow an unprivileged Docker runner in v2, execution is abstracted behind a `Runner` base interface:

```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, List


class ProcessHandle:
    pid: int
    pgid: int
    start_time: int
    job_id: str
    stdout_path: Path
    stderr_path: Path


class Runner(ABC):
    @abstractmethod
    def launch(
        self,
        job_id: str,
        workdir: Path,
        args: List[str],
        env: Dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> ProcessHandle:
        """Spawn process in a new process group with file-backed streams."""
        ...

    @abstractmethod
    def poll(self, handle: ProcessHandle) -> Optional[int]:
        """Return exit code if terminated, None if still running."""
        ...

    @abstractmethod
    def kill(self, handle: ProcessHandle, grace_seconds: float = 3.0) -> None:
        """Send SIGTERM to process group, wait grace_seconds, then SIGKILL."""
        ...

    @abstractmethod
    def cleanup(self, handle: ProcessHandle) -> None:
        """Release OS process resources."""
        ...
```
Version 1 implements `LocalProcessRunner`. Version 2 implements `DockerRunner` without changing tool contracts.

---

## 7. State Machine
A job is governed by a deterministic, finite state machine:

```
                  ┌──────────┐
                  │  queued  │
                  └────┬─────┘
                       │ (process spawn observed)
                       ▼
                 ┌───────────┐
      ┌─────────►│  running  │◄─────────┐
      │          └─────┬─────┘          │
      │                │                │
(agy_resume)           │          (agy_resume)
      │                │                │
      │   ┌────────────┼────────────┐   │
      │   ▼            ▼            ▼   │
┌──────────────┐┌──────────────┐┌──────────────┐
│needs_decision││ needs_review ││   timeout    │
└──────────────┘└──────┬───────┘└──────────────┘
                       │
             (reason == security?)
             ├── YES ──► [TERMINAL]
             └── NO ───► (resumable via agy_resume)

Terminal States from running:
  ├── done         (clean completion, audit passed, verify passed/skipped)
  ├── failed       (crash, unparseable output, nonzero exit code)
  └── cancelled    (explicitly cancelled by agy_cancel)
```

### Transition Table

| Current State | Event / Trigger | Target State | Resumable? | Notes |
|---|---|---|---|---|
| `queued` | Worktree ready, process spawned & PID confirmed | `running` | — | Transition occurs immediately upon background spawn. |
| `queued` | Explicit call to agy_cancel | `cancelled` | No (Terminal) | Cancelled before process launch; worktree cleaned up. |
| `running` | Clean exit 0, audit passes, verify passes/none | `done` | No (Terminal) | Normal success. |
| `running` | Clean exit 0 but `.agy/PLAN.md` items remain and `plan_nudge=1` | `needs_review` | **Yes** | `reason="plan_incomplete"`; warning `plan_incomplete`; the manager performs one automatic nudge resume. |
| `running` | Fatal audit violation, host path modified, or instruction file altered | `needs_review` | **No (Terminal)** | Process group killed; no commit made; `audit_violation`, `host_path_modified`, or `instruction_file_modified`. |
| `running` | Verify attempts exhausted (`verify_allowlist`) | `needs_review` | **Yes** | `reason="verify_failed"` (or `"verification_failed"`). User may resume with advice. |
| `running` | Quota exhausted (relevant bucket at 0%) or `quota_error_patterns` match after failure/timeout/empty response | `needs_review` | **Yes** | `reason="quota_exhausted"`; `resume_after` holds the bucket reset time. |
| `running` | Login expired (`auth_error_patterns` match or `/usage` auth-required) | `needs_review` | **Yes** | `reason="auth_required"`; ask the human to log in, then resume. |
| `running` | Verification command timed out (rc 124) | `needs_review` | **Yes** | `reason="verify_timeout"`. User may resume with advice. |
| `running` | Zero progress made and tool permission denied | `needs_review` | **Yes** | `reason="blocked_by_permission"`. Non-fatal warning. |
| `running` | Malformed `.agy/COMMIT_MSG` after retries | `needs_review` | **Yes** | `reason="invalid_commit_message"`. |
| `running` | Wall-clock supervisor or print timeout expires | `timeout` | **Yes** | Process group killed; checkpoint committed; resumable. |
| `running` | Last non-empty line is `NEEDS_DECISION:` | `needs_decision` | **Yes** | Checkpoint committed; waiting for user decision. |
| `running` | Unhandled process crash, signal, or syntax error | `failed` | No (Terminal) | Captures stderr tail. |
| `running` | Explicit call to `agy_cancel` | `cancelled` | No (Terminal) | Process group killed; checkpoint committed. |
| `needs_decision` | `agy_resume(job_id, answer)` | `running` | — | Answer injected as prompt with preamble; audit gated. |
| `timeout` | `agy_resume(job_id, answer)` | `running` | — | Resumes conversation; audit gated. |
| `needs_review` | `agy_resume(job_id, answer)` | `running` | — | **Only allowed if reason is non-security.** Security reasons are terminal. |
| `done` | `agy_resume(job_id, answer, follow_up=true)` | `running` | — | **Explicit follow-up only (D81).** Continues the same conversation/worktree; a plain `agy_resume` still fails with `invalid_state_for_resume`. |
| `cancelled` | `agy_resume(job_id, answer, follow_up=true)` | `running` | — | **Explicit follow-up only (D81).** Same gate as `done`. |

*Note on Denials:* An unpermitted command denial during a turn does **not** terminate the job if progress was made. Denials are recorded in `warnings` and `denials[]`. Only complete blockage with zero progress enters `needs_review(reason=blocked_by_permission)`.

*Note on plan accounting (D62):* With `plan_nudge=1` (default), a job that would
otherwise be `done` while PLAN.md has unticked boxes becomes
`needs_review(reason="plan_incomplete")`, gains the `plan_incomplete` warning,
and is auto-resumed once with a trusted prompt naming the unticked items. If it
is still incomplete after that resume it ends in the same state and is
user-resumable; the nudge never fires twice. With `plan_nudge=0` the job stays
`done` and only carries the warning.

---

## 8. Tool Contracts
All tools communicate over standard FastMCP JSON RPC. All responses share a consistent base schema, with field sizes bounded by explicit caps.

### Standard Response Envelope
```json
{
  "job_id": "c8f2a1b9-3d4e-4f5a-b6c7-d8e9f0a1b2c3",
  "state": "running",
  "elapsed_seconds": 142.5,
  "title": "Add JWT authentication middleware",
  "model_resolved": null,
  "branch": "agy/add-jwt-auth-c8f2a1b9",
  "base_commit": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
  "head_commit": "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
  "commits_count": 2,
  "files_modified": ["src/auth/jwt.py", "tests/test_jwt.py"],
  "skipped_files": [],
  "denials": [],
  "denied_commands": [],
  "denied_tools": [],
  "warnings": [],
  "tokens": {
    "input_tokens": 14200,
    "output_tokens": 450,
    "total_tokens": 14650,
    "thinking_tokens": 1200,
    "cache_read_tokens": 42000,
    "num_turns": 1
  },
  "next_actions": ["Wait for completion with agy_wait", "Check status with agy_status"],
  "error": null,
  "usage_cost": null,
  "quota_estimate": null,
  "conversation_continued": null,
  "resume_after": null,
  "plan": {
    "total": 2,
    "done": 1,
    "remaining": ["<untrusted_text ...>"]
  },
  "facts": {
    "commits_count": 2,
    "files_modified_count": 1,
    "verify": {"requested": false, "ran": false, "passed": null, "rc": null, "attempts": 0},
    "audit": {"status": "passed"},
    "plan": {"total": 2, "done": 1, "remaining": 1},
    "denials": {"count": 0, "commands": 0, "tools": 0}
  },
  "agent_report": "<untrusted_text ...>",
  "scratchpad": ["PLAN.md", "NOTES.md", "LOG.md", "REPORT.md"],
  "verify": {
    "requested": false,
    "ran": false,
    "passed": null,
    "rc": null,
    "duration_seconds": null,
    "attempts": 0,
    "last_tail": null,
    "status": null,
    "timed_out": false,
    "killed_by_timeout": false,
    "command": null,
    "timeout_seconds": null,
    "cause": null,
    "next_action": null
  }
}
```
The `plan`, `facts`, `agent_report` and `scratchpad` fields are wrapper-owned (D62):
- `plan` counts the numbered checkboxes in `.agy/PLAN.md`; each unticked title in
  `remaining` is untrusted-wrapped and capped at 200 characters (at most 30).
- `facts` contains only values the wrapper observed: commit and file counts, the
  verify summary, audit status, plan counts and denial counts. Agent text is
  never copied into `facts`.
- `agent_report` is the `.agy/REPORT.md` text, untrusted-wrapped and capped at
  4 KB.
- `scratchpad` lists the retained file **names** only (never absolute paths),
  copied to `<jobdir>/scratchpad/` at finalization.
- `model_resolved` is the model the CLI reported resolving to (the cli.log line
  `model alias "<X>" resolved to "<Y>"`), or `null` when no such line appeared;
  the requested `model` is unchanged. `tokens` always carries
  `input_tokens`/`output_tokens`/`total_tokens` and also carries
  `thinking_tokens`/`cache_read_tokens` (from `usage`) and `num_turns` (from
  top-level stdout) when present, each `null` when absent (D63).
- The `agy_result` `response` text is path-sanitized before untrusted wrapping:
  the job's absolute worktree path becomes `<worktree>` and the user's home
  directory becomes `~` (also inside `file://` URIs); no other text changes, and
  a sibling that merely shares the prefix is left intact (D63).

### Specific Tool Signatures & Payloads

#### 1. `agy_start`
- **Parameters:**
  - `prompt`: `str` (Required, task description)
  - `repo_path`: `str` (Required, absolute path to repository root)
  - `title`: `str` (Required, short slug title)
  - `model`: `str` = `"gemini-3.8-flash-medium"`
  - `max_minutes`: `int` = `240` (Job wall-clock budget)
  - `verify`: `Optional[str]` = `None` (Verification command, must be in `verify_allowlist`)
  - `checklist`: `Optional[List[str]]` = `None` (Optional PLAN.md seed; at most 30 items of at most 200 characters each, sanitized to single lines. When omitted the wrapper seeds the two default steps described in section 9.)
  - `verify_timeout_seconds`: `Optional[int]` = `None` (Per-job verify timeout; `None` uses the config's `verify_timeout_seconds`, default `900`. Must be 1..86400.)
  - `account`: `Optional[str]` = `None` (Registry name to pin; `None` lets the scheduler choose. With no `[[accounts]]` registry only `default` is acceptable. An explicit account must pass identity/policy/model/cap checks; it must also pass the quota filter unless `force`.)
  - `priority`: `str` = `"normal"` (`normal` or `background`; background may burn quota that is about to reset and never takes an account's last free slot while a normal job waits for it, D109).
  - `force`: `bool` = `False` (Skip only the quota filter/reservations for an explicit `account`; never skip identity, permission policy, home or the model/cap checks.)
- **Returns:** Standard envelope, plus `account`/`account_email` only when a registry selected an account (`account_email` is masked). `state` is `"queued"` while worktree is provisioned and process spawned, transitioning to `"running"`. `branch` is the final `agy/<slug>-<shortid>` name: it is allocated once and persisted in `job.json` before this envelope is returned, so `agy_status`, `agy_wait`, `agy_review`, `agy_result`, `agy_cleanup` and git all report the same ref (D72).

#### 2. `agy_status`
- **Parameters:**
  - `job_id`: `str` (Required)
  - `tail_lines`: `int` = `20` (Number of log lines to inspect)
- **Returns:** Standard envelope, plus:
  - `last_cli_log_mtime`: `float`
  - `last_commit_at`: `Optional[str]`
  - `worktree_dirty`: `bool`
  - `recent_log_tail`: `List[str]` (Max 50 lines, max 4 KB, extracted from `<jobdir>/cli.log`, strictly redacted; `last_commit_at` extracted from worktree `git log`)
  - `conversation_id`: `Optional[str]` (from `Job.conversation_id`)
  - `conversation_id_source`: `Optional[str]` (`"early"`, `"finalized"`, or `null`)
  - `runs`: `List[Dict[str, Any]]` (projection of `Job.runs[]` to `[{index, started_at, ended_at, wall_clock_seconds, outcome}]`; `index` is 0-based array position; internal fields `run_generation`, `agy_duration_seconds`, `tokens` and prompt text are omitted)
  - `wall_clock_seconds`: `Optional[float]` (total sum of wrapper-measured run wall clocks; `null` when no run has finished)

#### 3. `agy_wait`
- **Parameters:**
  - `job_id`: `str` (Required)
  - `seconds`: `int` = `30` (Timeout for call; default 30s, maximum 300s to prevent MCP client timeouts)
- **Returns:** Standard envelope immediately upon state leaving `"running"` or when timeout expires. Orchestrators poll via successive calls.

#### 4. `agy_result`
- **Parameters:**
  - `job_id`: `str` (Required)
- **Returns:** Standard envelope, plus:
  - `response`: `str` (Max 64 KB; truncated with notice if larger; marked `untrusted_text`)
  - `diff_stat`: `str` (Max 8 KB, `git diff --stat base..HEAD`)
  - `commits`: `List[Dict[str, str]]` (Up to 50 commits: sha, subject, author, timestamp)
  - `audit_events_tail`: `List[Dict[str, Any]]` (Last 20 entries from `audit.jsonl`)

#### 5. `agy_cancel`
- **Parameters:**
  - `job_id`: `str` (Required)
- **Returns:** Standard envelope with `state="cancelled"`. Process group killed via `os.killpg`; checkpoint committed.

#### 6. `agy_list`
- **Parameters:** None
- **Returns:**
  - `jobs`: `List[StandardEnvelope]`
  - `total_active_jobs`: `int`
  - `disk_usage_bytes`: `int`
  - `drifted_worktrees`: `List[str]`

#### 7. `agy_resume`
- **Parameters:**
  - `job_id`: `str` (Required)
  - `answer`: `str` (Required, response to decision prompt, fix direction, or continue)
  - `follow_up`: `bool` = `False` (optional; D81). When `true`, a `done` or `cancelled` job may continue the same conversation on the same worktree. When `false` (default) those states remain `invalid_state_for_resume`.
- **Returns:** Standard envelope with `state="running"` and the additive field `conversation_continued`:
  - `true`: a strict-UUID conversation id was passed to agy as `--conversation`.
  - `false`: no valid id existed; the run started a fresh conversation with a self-contained prompt (original task summary, current HEAD, files changed) and `warnings` contains `conversation_not_continued: ...`. The wrapper never passes a non-UUID.
- The answer-prefix audit trigger (`Resumed with answer: <answer[:40]>`; `Follow-up resumed with answer: ...` for a follow-up) is unchanged.

#### 8. `agy_review`
- **Parameters:**
  - `job_id`: `str` (Required)
- **Returns:** Standard envelope, plus:
  - `merge_base`: `str`
  - `ahead`: `int`
  - `behind`: `int`
  - `base_moved`: `bool`
  - `audit_findings`: `List[Dict[str, Any]]`
  - `untrusted_diff`: `str` (Max 32 KB)
- The payload is the same standard envelope as every other tool: `plan`, `facts`, `agent_report`, `scratchpad`, `verify`, `tokens`, `usage_cost`, `model_resolved` and `resume_after` carry exactly the values `agy_wait` returns for the same job (one shared builder, D71). The review-specific fields are added on top.
- *Fail-closed review:* If the audit or the git queries used to build the review raise, `audit_findings` includes a fatal `audit_unavailable` finding (detail is the short code only, never exception text or host paths) and `next_actions` starts with a statement that a human must review the job before merging.

#### 9. `agy_cleanup`
- **Parameters:**
  - `job_id`: `str` (Required)
  - `remove_worktree`: `bool` = `True`
  - `delete_branch`: `bool` = `False`
  - `force`: `bool` = `False`
- **Returns:**
  - `job_id`: `str`
  - `worktree_removed`: `bool`
  - `branch_deleted`: `bool`
  - `freed_bytes`: `int`
- *Safety:* Refuses cleanup if the worktree is dirty or the branch holds unmerged commits unless `force=True`. "Unmerged" means some commit reachable from the job branch is not reachable from the repository's current HEAD or from any other local branch (`rev-list --count <branch> --not HEAD <other local branches>`); a branch merged into an `integration` branch, or fast-forwarded into the default branch, is therefore deletable, and deletion uses `git branch -D` after that reachability gate. Squash and cherry-pick merges leave the original commits unreachable and stay refused (D70).

#### 10. `agy_usage`
- **Parameters:** None (read-only; it never starts, resumes, cancels, or mutates a job).
- **Returns:**
  - `available`: `bool`; `reason`: `Optional[str]` (`unrecognized_format`, `timeout`, `auth_required`, `command_failed`, `unavailable`)
  - `cached`: `bool` (in-memory reading reused within 60s)
  - `account`: `{masked_email: str|null, auth_method: str|null, identity?: str}` (email always masked; `identity` appears only when an expectation is configured)
  - `groups`: `List[{name: str, buckets: List[{window: "weekly"|"5h", remaining_fraction: float, reset_time_utc: str|null, resets_in_seconds: int|null, low: bool}]}]`
- Per-bucket `low` uses `low_quota_five_hour_pct` (default 15) and `low_quota_weekly_pct` (default 10).

---

## 9. Preflight & Worktree Lifecycle

### Preflight Validation
When `agy_start` is invoked:
1. **Path Resolution:** `repo_path` is resolved with `Path(repo_path).resolve(strict=True)`.
2. **Containment:** `repo_path` must reside inside `$HOME` (or match an entry in `allowed_workdir_roots`).
3. **Prohibited Targets:** `repo_path` cannot be `$HOME`, cannot reside within `~/.local/state/agy-mcp/**`, and cannot be an existing linked worktree (`.git` must be a directory, not a pointer file).
4. **Git State:** Must possess a valid, resolvable `HEAD` commit. Unborn HEADs or bare repositories return `error.code=invalid_repo_state`.
5. **Cleanliness Check:** Dirty working trees produce a warning (`source_dirty=true`), not a refusal. If user sets `require_clean_source=true` in `config.toml`, dirty trees are refused.
6. **Disk Check:** The host filesystem hosting the state directory must have at least `min_free_gb` (default 2 GB) available.
7. **Dangerous Rules Check:** Reads `<home>/.gemini/config/config.json` first (the file the real CLI loads, masked smoke evidence), then `~/.gemini/antigravity-cli/settings.json`, `~/.gemini/settings.json`, `~/.gemini/config/config.json` and matching `~/.gemini/config/projects/*.json`. Every read is `O_NOFOLLOW`, regular-file-only and capped at 1 MB; an absent file is skipped, and an unreadable, non-regular, oversize or malformed file adds warning `permission_rules_unreadable` (malformed JSON keeps `malformed_config_json` too) without raising or blocking the start. If any dangerous allow-rule is present (`command(bash)`, `command(sh)`, `command(python3)`, `command(git push)`, `command(git branch)`), the start envelope adds `dangerous_permission_rules_present: <count> dangerous allow rule(s)`; rule bodies are never echoed into the envelope (D63).
8. **Agents Directory Preflight:** If `repo_path` tracks `.agents/**` in Git, execution is refused unless `trust_agents_dir=true` is explicitly set in configuration.

### Worktree Provisioning
1. **Branch Naming:**
   - Slug derived: `slug = re.sub(r'[^a-z0-9-]+', '-', title.lower())[:40].strip('-') or 'job'`
   - Short ID: 8 hex characters from UUID.
   - Branch: `agy/<slug>-<shortid>` validated via `git check-ref-format --branch`. Collisions are retried with a fresh ID during allocation, which happens once in `JobManager.start` and is persisted in `job.json` before `agy_start` returns (D72); `create_worktree` uses that exact name.
2. **Creation Command:**
   ```bash
   git -C <repo_path> worktree add <jobdir>/work -b agy/<slug>-<shortid> HEAD
   ```
   Executed with a timeout of `worktree_setup_seconds` (default 300s).
3. **Provisioning Metadata Directory:**
   Immediately following checkout, the wrapper creates `<jobdir>/work/.agy/` with directory mode `0700` and seeds four size-capped files (D62):
   - `.agy/PLAN.md` — a numbered checkbox list. With no `checklist` parameter:
     ```markdown
     # Plan: <title>

     - [ ] 1. Complete the task described in the prompt
     - [ ] 2. Fill in .agy/REPORT.md honestly
     ```
     With `checklist`, each sanitized item becomes `- [ ] N. <item>` (at most 30
     items, 200 characters each).
   - `.agy/NOTES.md` — header line `working memory; keep decisions and findings here`.
   - `.agy/LOG.md` — header only; the agent may append.
   - `.agy/REPORT.md` — template with the sections `Done`,
     `Not done / skipped`, `Open issues`, `Files changed`, `How verified`.
4. **Local Exclusion:**
   The wrapper appends `.agy/` to the repository-local exclude file resolved by
   `git rev-parse --git-path info/exclude` (untracked, never a committed
   `.gitignore`), so scratchpad files cannot enter a job commit.
5. **Failure Cleanup:** If worktree creation **or provisioning** fails, the wrapper executes `git -C <repo_path> worktree prune` and deletes the partially created branch if created during the failed call. The job ends `failed` with `review_reason="setup_failed"`; no `agy` process is launched and the worktree is removed.

### Scratchpad Security Rule (D62)
The agent owns `.agy/` after launch and can replace it or any file in it with a
symlink. Every wrapper read of an agent-written file therefore:
- opens the `.agy` directory itself with `O_NOFOLLOW|O_DIRECTORY` and each file by a single
  known basename with `O_NOFOLLOW`, never following a symlink;
- requires the opened descriptor to resolve inside `<worktree>/.agy` and to be a
  regular file;
- enforces a per-file byte cap (64 KB for PLAN/NOTES/LOG, 4 KB retained
  REPORT/all envelope text) and refuses NUL-containing binary content;
- treats the text as untrusted data: values copied into prompts or envelopes are
  wrapped with `wrap_untrusted_text` and capped before use.

A symlinked file or symlinked `.agy` directory is skipped, never dereferenced.
Skipped retained files are reported by name only as
`scratchpad_file_skipped:<NAME>`, because envelopes never contain absolute
paths (F26).

---

## 10. Process Execution & Supervisor

### Directory Layout & Permissions
All job artifacts are isolated in `~/.local/state/agy-mcp/jobs/<id>/` created with mode `0700`:
- `job.json`: Mode `0600`, written atomically (tempfile + `fsync` + replace).
- `audit.jsonl`: Mode `0600`, append-only log of security events and state changes.
- `prompt.txt`: Mode `0600`, full submitted prompt.
- `stdout.txt` / `stderr.txt`: Mode `0600`, file-backed process streams.
- `cli.log`: Mode `0600`, `agy` internal log file.
- `work/`: The git worktree root.
- `scratchpad/`: Mode `0700`, retained copies of `.agy/PLAN.md`, `NOTES.md`,
  `LOG.md` and `REPORT.md`, written at finalization so they survive worktree
  cleanup (D62).
- `tmp/`: Mode `0700`, dedicated scratch `TMPDIR` passed to `agy`, deleted upon termination.

### Execution Command
The wrapper launches `agy` via `subprocess.Popen` with `start_new_session=True` (creating a new process group):
```bash
agy \
  --mode accept-edits \
  --output-format json \
  --print-timeout <budget_seconds>s \
  --log-file <jobdir>/cli.log \
  --model <model> \
  -p <prompt>
```
*Constraints:*
- `<jobdir>/cli.log` must be an absolute path (no unexpanded `~`).
- `--effort` is never passed.
- `--mode auto` and `--sandbox` are never passed (they fall back silently when unprivileged user namespaces are restricted).
- Resumes use `--conversation <id>` with identical flags, but `<id>` is passed only when it is a strict 36-character UUID. A stored non-UUID degrades to a fresh, self-contained run and `conversation_continued=false` (D81).

### Environment Sanitization (Allowlist)
`agy` runs with a sanitized environment (`env -i`):
- `HOME`: Set to user's home directory.
- `PATH`: System binary path (inherits server `PATH`, defaulting to `/usr/local/bin:/usr/bin:/bin` if unset).
- `LANG`: Set to `C.UTF-8`.
- `TMPDIR`: Set to `<jobdir>/tmp`.
- Git configuration overrides:
  - `GIT_CONFIG_COUNT`: `3`
  - `GIT_CONFIG_KEY_0`: `core.fsmonitor` | `GIT_CONFIG_VALUE_0`: `false`
  - `GIT_CONFIG_KEY_1`: `core.hooksPath` | `GIT_CONFIG_VALUE_1`: `/dev/null`
  - `GIT_CONFIG_KEY_2`: `core.pager` | `GIT_CONFIG_VALUE_2`: `cat`
- **Exception — wrapper commits (D80):** the `git commit` invocation is the only
  git call that must run repository hooks. It uses `GIT_CONFIG_COUNT=2`
  (`core.fsmonitor`, `core.pager`, no `core.hooksPath` override) and never passes
  `--no-verify`, so a repo's `commit-msg`/`pre-commit` hooks execute. All other
  git calls keep the `core.hooksPath=/dev/null` override above.

### Process Group Supervisor
The wrapper runs an asynchronous supervisor loop:
1. **Wall-clock Deadline:** `deadline = min(max_minutes, max_minutes_cap) * 60 + 120` seconds.
2. **Termination:** If the deadline expires or an audit triggers cancellation, the wrapper sends `SIGTERM` to the process group (`os.killpg(pgid, signal.SIGTERM)`), waits up to 3.0 seconds, and escalates to `SIGKILL` (`os.killpg(pgid, signal.SIGKILL)`).
3. **Post-Termination:** Runs audit and executes a checkpoint commit before recording `timeout` or `cancelled`.
4. **Tick-Error Audit Fail-Closed (D57):** If an unhandled supervisor tick exception occurs while a baseline snapshot exists, an immediate audit check is performed. If `audit()` itself raises during this handler, the exception is not swallowed: the supervisor appends audit event `audit_error_during_tick_error` (with exception class and redacted message), sends SIGTERM/SIGKILL to terminate the process group, and transitions to terminal `needs_review(reason=audit_unavailable)`. It never ends in `done` and never leaves `agy` running.

### Lock Hierarchy & Concurrency Control
The wrapper coordinates execution with four locks. When more than one is held, the real acquisition order is:

1. **Job lock (Per-job lifecycle):** In-memory lock (`_job_locks[job_id]`) serializing lifecycle state transitions (`cancel`, `resume`) on a single job.
2. **Concurrency flock (Inter-process):** An exclusive `flock` on `<state_dir>/concurrency.lock` serializes active job count verification (`queued`, `running`) against `max_concurrent_jobs` during `start` and `resume`. It is never held while waiting for an in-memory lock. `start` takes it with no job lock held (the job does not exist yet); `resume` takes it while already holding the job lock.
3. **Manager lock (In-memory manager state):** In-memory `RLock` (`JobManager._lock`) protecting manager collection mappings (`_supervisors`, `_threads`, `_resuming_jobs`, `_job_locks`, `_job_commit_locks`). Implemented as `RLock` to eliminate internal re-entrancy deadlocks during crash recovery reconciliation (`reconcile`). Held only for immediate dictionary updates, never across long operations.
4. **Commit lock (Per-job worktree):** In-memory lock (`_job_commit_locks[job_id]`) shared between `JobSupervisor` and `CommitWatcher` to strictly serialize git staging and commits (`watcher.tick`, `checkpoint_commit`, `safety_net_commit`, `_try_pre_kill_checkpoint`).

*Lock-order note (documentation-only, no automated test):* `resume` is the only path that holds both the job lock and the concurrency flock, and it takes the job lock first. No path today acquires them in the reverse order, so the documented order matches the code and no deadlock is reachable through these two locks.

*Resume finalization note:* `agy_resume` joins the previous run's supervisor thread before taking the job lock and starting a new generation (D65). The join is not a lock acquisition, so the order above is unchanged; a previous supervisor that has not finished within 30 s fails the call closed with `error.code="job_finalizing"` instead of letting its stale `job.json` write race the new `running` state.

---

## 11. Watcher & Commit Pipeline

### Automated Commit Protocol
`agy` never runs modifying git commands (`git add`, `git commit`). All commits are created by the wrapper:
1. `agy` writes a commit message to `.agy/COMMIT_MSG` (first line <= 72 characters, blank line, body).
2. The wrapper watcher polls `<jobdir>/work/.agy/COMMIT_MSG` synchronously on every supervisor loop tick (`poll_interval_seconds`, default 1 second).
3. **Settle Window:** The watcher acts only when `(sha256, size, mtime_ns)` has remained unchanged for at least the 5 s settle window since it was first observed.
4. **Validation and subject repair (D82):** The message must be non-empty, valid UTF-8, and have a first line <= 72 chars. If the only failure is the 72-character subject limit, the wrapper repairs instead of rejecting: it truncates at the last whitespace boundary to <= 72 characters, moves the full original first line to the top of the body, rewrites the file atomically (mode 0600), and records `commit_msg_repaired` with `{original_len}` only (no message text). Empty, NUL/control characters, invalid UTF-8, oversized, symlink and non-regular files are never repaired and still fail validation. If invalid, the watcher discards it without logging and retries once; on second failure, it sets `needs_review(reason=invalid_commit_message)`.
4b. **Evidence on rejection (D82):** Before the discard rename, copy the rejected regular file's raw bytes to `<jobdir>/rejected_COMMIT_MSG.txt` (wrapper-owned, never committed) and add `first_line_repr` (`repr()` of the first line, capped at 120 chars) plus `evidence_file` to `job.error`. Symlinks and non-regular files are never followed, so they produce no copy. The audit event stays `{code, file}` only.
4c. **Discard on Review (D66):** Before that second-failure transition, rename the offending entry to `.agy/COMMIT_MSG.invalid.<n>` (first free 1-20; at the cap `.invalid.20`), without following a symlinked `COMMIT_MSG` or `.agy`, and record `commit_msg_discarded` with a short code and the new basename only.
5. **Consume Step:** The wrapper atomically renames `.agy/COMMIT_MSG` to `.agy/COMMIT_MSG.seen.<n>`.
6. **Pre-commit Audit:** Runs the security audit. If audit fails, the commit is aborted, the process group is killed, and state becomes `needs_review`.
7. **Staging:** The wrapper stages changes using git pathspec exclusions:
   ```bash
   git -C <jobdir>/work add -A -- . ':(exclude,top).agy' ':(exclude,glob)**/__pycache__/**' ':(exclude,glob)**/*.pyc' ...
   ```
   Both git pathspec excludes and Python filters apply the default junk-exclusion list (`__pycache__/`, `*.pyc`, `*.pyo`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.tox/`, `.nox/`, `.hypothesis/`, `*.egg-info/`, `.coverage`, `.coverage.*`, `htmlcov/`, `junit.xml`, `coverage.xml`, `target/`, `node_modules/`) and sensitive file blacklist (D6, D54, D56). Filtered junk files are listed in `skipped_files` with reason `build_artifact` (capped at 50 entries). Pre-existing tracked junk is never purged or rewritten from git history.
   All `git status` calls use `run_git` with sanitized environment and a 30-second timeout. If `git status` exits non-zero, staging aborts immediately, resets the index (`git reset`) to prevent leaking staged secrets, and raises `GitError` (fail-closed).
8. **Commit Execution:** Commits using hardened Git flags and repository hooks (D80):
   ```bash
   git -C <jobdir>/work \
     -c core.fsmonitor=false \
     -c core.pager=cat \
     commit -F <jobdir>/work/.agy/COMMIT_MSG.seen.<n>
   ```
   No `--no-verify` and no `core.hooksPath=/dev/null` override is used for this
   command: the repository's `commit-msg`/`pre-commit` hooks run exactly as they
   would for a manual commit. A hook rejection transitions the job to
   `needs_review(reason="invalid_commit_message")` with the hook output, a
   one-line cause and a next action; it is never silently retried or bypassed.
9. **Commit Signaling (D57):** Upon successful commit or skip completion, the watcher creates marker `.agy/COMMIT_MSG.done.<generation>` to provide deterministic synchronization for test harnesses and external watchers.
9. **Index.lock Retry Loop:** If `git commit` fails due to `index.lock`, the wrapper retries up to 10 times with exponential backoff (0.1s up to 5.0s). Stale locks (>5 minutes old) are removed only after verifying no `agy` process is alive.

### Checkpoint & Safety-Net Commits
- **Timed Checkpoints:** Every `checkpoint_commit_minutes` (default 10 minutes), if uncommitted changes exist, the wrapper automatically commits:
  `agy: checkpoint <ISO-timestamp>` (UTC, seconds precision, trailing `Z`).
- **Pre-Kill Checkpoint:** Before cancelling, terminating on timeout, or stopping on verify failure, a checkpoint commit captures all dirty work.
- **Wrapper message rule (D80):** every wrapper-generated subject (checkpoint and
  safety-net) is passed through one sanitizer: single line, printable ASCII, no
  control characters, no trailing period, at most 72 characters, non-empty
  fallback. The body may stay UTF-8. The sanitized message is checked against the
  same rules before `git commit`, so a wrapper commit can never be rejected for
  the shape of its own subject.
- **Safety-Net Commit (D54):** Upon normal job termination (`done`), if untracked/modified changes remain and the audit passed:
  - If the only changes since the last commit were created by verify (all remaining paths are junk/build artifacts), make no commit and record audit event `safety_net_skipped_only_artifacts`.
  - When real uncommitted changes exist, commit them using the sanitized first line of the most recent agy `COMMIT_MSG` if available, else the sanitized `"agy: <title>"`.
  - If a repository hook rejects the safety-net commit, the job does not report
    `done`: it ends `needs_review(reason="invalid_commit_message")` with the hook
    output, cause and next action, and the work stays uncommitted on disk.


### Sensitive File Exclusion (Blacklist)
The wrapper never stages or commits files matching:
- `.env`, `.env.*` (except `.env.example`, `.env.sample`)
- `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`
- `id_rsa*`, `id_ed25519*`, `*.ppk`
- `.netrc`, `.npmrc`, `.pypirc`
- `.ssh/**`, `.gnupg/**`, `.docker/config.json`
- `AGENTS.md`, `GEMINI.md`, `.agents/**`
- `.agy/**`

(No blanket exemption exists for files under `.github/`).

*Partial Commit Handling:* If an excluded file was already tracked in the repository and modified by `agy`, the commit proceeds without it, sets `partial_commit=true`, records the file in `skipped_files`, and transitions the job immediately to `needs_review(reason=partial_commit)`.

---

## 12. Security Audit Specification

### Checkpoints
The security audit runs as a mandatory gate:
1. Immediately before initial launch (`agy_start`), persisting `<jobdir>/snapshot.json`.
2. Immediately before every relaunch (`agy_resume` and fix turns).
3. Every 5 seconds on an independent timer during job execution.
4. Immediately after job completion.

All mid-run checkpoints, `agy_review`, and crash recovery compare strictly against the persisted `<jobdir>/snapshot.json`. If `snapshot.json` is missing or corrupted, the audit fails closed immediately to `needs_review(reason=audit_violation)`.

### Fatal Violations (Hard Kill, No Commit, Terminal `needs_review`)
Any of the following causes immediate process group termination (`SIGTERM` -> `SIGKILL`), suppression of pending commits, and terminal state `needs_review(reason=audit_violation)`:
1. Any modification to the worktree `.git` pointer file (tampering with worktree configuration).
2. The creation, addition, or modification of any file under `.agents/**` (lifecycle hooks, plugins, skills, manifests).
3. Any change to the resolved git configuration as seen from the worktree (`git -C <jobdir>/work config --local --list` diff against pre-launch snapshot).

### Blocked-Not-Fatal Set
Changes to `.gitignore`, `.gitattributes`, and `.gitmodules` are **not fatal**, but they are blocked from staging. They are recorded in `skipped_files`, the active run continues uninterrupted, and the job transitions to `needs_review(reason=policy_file_modified)` with `partial_commit=true` upon completion.

### Instruction File Tampering
Modifications to `AGENTS.md` or `GEMINI.md` within the workspace are blocked from staging and set terminal state `needs_review(reason=instruction_file_modified)` to prevent prompt-injection persistence across resumes.

### Host-Sensitive Path Snapshot (Detector)
Before launch, at supervisor checkpoints, and immediately following job termination, the wrapper calculates a snapshot of host security files:
- `~/.ssh/*`
- `~/.gnupg/*`
- `~/.bashrc`, `~/.profile`, `~/.zshrc`
- `~/.gitconfig`, `~/.netrc`
- `~/.config/agy-mcp/config.toml`
- `<repo_path>/.git/config` (the source repository's primary git config)

Host paths are classified into `strict`, `append_tolerant`, and `warn_only` (D52, D54):
- **Strict paths:** Shell rc/profile files, `~/.ssh/authorized_keys`, `~/.ssh/config`, private keys, and git configs. Any modification trips fatal terminal state `needs_review(reason=host_path_modified)`.
- **Append-tolerant paths:** `~/.ssh/known_hosts`. Stores size and SHA-256 of content (capped at 1 MB) at snapshot time. On change, if the new file is a pure append of the old content (old_size <= new_size and initial old_size bytes match old sha256) or the file did not exist before and now exists, records audit event `host_path_changed_benign` ({`path`, `added_bytes`, `new_lines_count`}) and continues the job with warning `known_hosts_changed`. If earlier bytes changed, shrank, or disappeared, the change is fatal (`host_path_modified`). Key material and hostnames are never logged.
- **Warn-only paths (D54):** `~/.ssh/known_hosts.old`. Any change (replaced by an older copy during SSH rotation, rewritten, deleted, or appeared) records audit event `host_path_changed_benign` with reason `backup_file` and warning `known_hosts_old_changed`. It is never fatal. *Note: This is a detector, not an OS-level prevention sandbox.*

---

## 13. Verification & Auto-Fix Loop
The orchestrator can specify an optional test/verification command via `verify`.
- **Command Whitelist & Argument Restrictions:** `verify` must match an entry in `verify_allowlist` (default `["pytest", "ruff", "npm test", "cargo test", "go test", "node --test"]`). The config may also specify absolute interpreter-based commands such as `["/abs/path/python", "-m", "pytest"]` matching exact prefix tokens (D53). When the allowlist contains the pytest tool, the bare forms `python -m pytest` and `python3 -m pytest` are also accepted (first token exactly `python`/`python3`, second exactly `-m`, third exactly `pytest`) and are validated by the same pytest flag/path policy; no other `-m` module, `-c`, absolute interpreter path, `.venv` path or glued `-mpytest` is accepted implicitly (D63). Direct shell wrappers are strictly rejected. Unlisted commands are rejected immediately with `error.code=verify_not_allowed`, and the message lists the accepted forms. Dangerous flag and path restrictions are strictly enforced per tool and re-validated upon resume (D56, D57, D84):
  - `pytest`: Rejects `-c`, `-p`, `-o`, `--rootdir`, `--confcutdir`, `--prefix`, `--config`, `--config-file`, `--override-ini`, `--import-mode`, and any paths escaping repository/worktree boundaries.
  - `ruff`: Rejects `--config`, `-c`, `--config-file`, and any paths escaping repository/worktree boundaries.
  - `cargo`: Allows `-p`/`--package`, `--workspace`, `--release`, `--lib`, `--bins`, `--features`, `--all-features`, `--no-default-features`, and `--test <name>`. Rejects `--config`, `-Z`, and any `--manifest-path` or `--target-dir` resolving outside the repository/worktree boundaries.
  - `go`: Allows `-p`, `-run`, `-v`, `-count`, `-race`, `-short`, `-timeout`, and `./...` package patterns inside repo. Rejects `-exec`, `-toolexec`, `-overlay`, `-modfile`, `-C`, and package patterns or paths resolving outside repository/worktree boundaries.
  - `npm`: Allows `npm test` only, with no extra arguments.
  - `node --test`: Accepts `node --test` optionally followed by relative file, directory, or glob (`*`, `**`, `?`, `[]`) arguments that resolve strictly inside the job workdir after symlink resolution. Before a fresh job's worktree exists, the repository root is used as the boundary. Rejects: any extra token starting with `-` (covers `-e`/`--eval`, `-p`/`--print`, `-r`/`--require`, `--import`, `--loader`, `--test-reporter`, `--test-name-pattern`, `--watch`, and flags after `--`); empty tokens; empty argv; bare `node` without `--test`; interpreter paths (`/usr/bin/node ...`); glued flags (`node --test=...`); absolute paths; any path with a `..` component; paths resolving outside the boundary; and arguments containing any of these shell metacharacters: `;`, `&`, `|`, a backtick, `$`, `(`, `)`, `<`, `>`, newline, CR, backslash, double quote, single quote.
- **Prompt Guidance (D53):** In the `agy_start` tool description and standing preamble rule 6: agy must NOT run tests; "the wrapper runs the verify command after your work and will send you the results; fix what fails".
- **Execution Environment:** Runs directly in `<jobdir>/work` with no shell (`shlex.split`), the per-job verify timeout (`verify_timeout_seconds`, default `900` s; config default `900`, override per `agy_start` call in 1..86400), the sanitized environment, and a dedicated scratch `TMPDIR`. The environment automatically injects `PYTHONDONTWRITEBYTECODE=1` and `PYTEST_ADDOPTS="-p no:cacheprovider"` (merged safely with existing allowlisted opts) to prevent build junk generation (D54).
- **Timeout Semantics (D80):** On timeout the wrapper sends `SIGTERM` then `SIGKILL` to the verify process group (the child is spawned with `start_new_session=True`, so `killpg` reaches pytest/agy descendants), reaps the leader, and records `rc=124` plus `timed_out=true` and `killed_by_timeout`. If the command exits by itself, its own exit code is used and `timed_out=false`; a timeout is therefore never confused with a failure.
- **Pre/Post Audit & Observability (D54, D56, D80):** Audit runs before and after verification. Verification start records audit event `verify_started` (`command` argv list, `command_str`, `timeout_seconds`, `cwd` basename only). Completion records audit event `verify_finished` (`rc`, `duration_seconds`, `status`, `timed_out`, `killed_by_timeout`, `attempt`, `tail_bytes`, `passed`, `command_str`, `timeout_seconds`). The full output tail is preserved in `<jobdir>/verify_tail.txt` as a diagnostic block: command, timeout, pass/fail/timeout result, known diagnosis (for example pytest's import-file mismatch), the last ~40 output lines, and matching diagnostic lines even when they are not in the tail. Failure tails recorded in `audit.jsonl` are redacted via `redact_log_text` and byte-capped at 2 KB (D56). The standard envelope includes a structured `verify` field: `{requested, ran, passed, rc, duration_seconds, attempts, last_tail, status, timed_out, killed_by_timeout, command, timeout_seconds, cause, next_action}`. `status` is `passed`, `failed`, `timeout` or `not_run`; the original keys keep their shape and meaning. A verify timeout is `needs_review(reason="verify_timeout")`, a self-failure is `needs_review(reason="verify_failed")`. If requested but skipped (job terminated before verify), `ran=false` with reason is reported.
- **Auto-Fix Loop:**
  1. If `verify` exits non-zero, capture the stdout/stderr tail (maximum 8 KB).
  2. If `auto_fix_attempts` (default 2) has not been exhausted, format a resume prompt:
     Include the first 2000 bytes of the failure tail wrapped as `<untrusted_text>`, the failing test names if parseable, and instruction: "Change code, not tests, unless the test itself is wrong (and then say so in the commit message)." (D53).
     Please analyze the error, fix the issues, and write a commit message to .agy/COMMIT_MSG.
  3. Prepend the standing prompt preamble and resume `agy` via `agy_resume`.
  4. If attempts are exhausted, set `needs_review(reason=verify_failed)` with the last failure tail attached. This state is **resumable** by the user via `agy_resume`.
  5. A verify timeout (`timed_out=true`, rc 124) is not an auto-fix candidate: it ends `needs_review(reason=verify_timeout)` immediately with the command, timeout, kill result and last 40 lines, because resuming an agent against a hang wastes the remaining budget.

---

## 14. Standing Prompt (Preamble)
The following text is prepended to every prompt submitted to `agy` (maintained in `src/agy_mcp/preamble.md`):

```markdown
You are an autonomous coding assistant executing a task in this repository worktree.
Rules you must strictly follow:
1. Work strictly within the current working directory; never read or write files outside it.
2. Never create or modify .agents/, AGENTS.md, GEMINI.md, or .git*; the wrapper detects it and destroys the job.
3. Never invoke the ask_question tool (headless mode auto-skips questions).
4. Write your plan as a checklist in .agy/PLAN.md and re-read it at the start of each step.
5. After completing each step, write a commit message to .agy/COMMIT_MSG (first line <= 72 chars, blank line, descriptive body) and continue with your work.
6. The wrapper runs verification tests and handles git commits; agy must NOT run tests; the wrapper runs the verify command after your work and will send you the results; fix what fails. Do not attempt git or test suite operations directly.
7. If an irreversible or costly decision is needed: commit your progress so far by writing .agy/COMMIT_MSG, update .agy/PLAN.md, and make the last line of your final response exactly:
NEEDS_DECISION: <question> | <options> | <recommendation>
8. Record reversible decisions under Assumptions in .agy/PLAN.md.
9. Your final response must summarize: what was done, assumptions made, and any open issues.
10. Use only your built-in file tools (view, create, edit, list, search) to read and change files. Do NOT run shell commands: they are not available here and will end your run. If you believe a shell command is truly required, do not try it. Instead stop and end your final response with the last line exactly: NEEDS_DECISION: need command `<the exact command>` | allow it, or do it another way | <your recommendation>
11. Do not start subagents, research agents, browsers or any other delegated tool. They are unavailable here and will end your run. Do the work yourself with your file tools, and only read files inside the current directory.
12. Keep your working notes, decisions and findings in .agy/NOTES.md, so they survive across steps and resumes.
13. Tick a box in .agy/PLAN.md only after the work it describes is actually done. Never tick a box for work you did not do.
14. At the end, fill in .agy/REPORT.md, including "Not done / skipped" and "Open issues", even if the answer is "none".
15. Never claim that tests passed: the wrapper runs verification; you do not. Report what you actually did.
```

### Resume State Block (D62)
Every resume prompt (`agy_resume`, verification auto-fix, plan nudge) inserts a
wrapper-built `## Current State (wrapper-provided)` block between the preamble
and the user answer/guidance section. It contains:
- commits so far (subjects untrusted-wrapped, at most 10),
- the last verification result,
- remaining plan items (untrusted-wrapped),
- the last 2 KB of `.agy/NOTES.md` (untrusted-wrapped).

Trusted headings and wrapper guidance stay outside the `<untrusted_text>`
wrappers, and the preamble is emitted exactly once.

---

## 15. Result Parsing & Precedence
When `agy` terminates, the wrapper parses the last JSON object line from `stdout.txt`. Precedence is strictly ordered:

1. **Cancellation:** Wrapper cancellation flag set -> `state="cancelled"`.
2. **Audit Violation:** Security audit flag set -> `state="needs_review"`.
3. **Supervisor Timeout:** Wall-clock supervisor deadline expired (where supervisor terminates process group) -> `state="timeout"`.
4. **Negative Signal:** Unexpected process return code < 0 without supervisor timeout -> `state="failed"` with `signal=abs(rc)`.
5. **CLI Flag/Invocation Error:** Return code 1 or 2 -> `state="failed"` with stderr tail.
6. **Print Timeout:** Stderr contains `[agy] print timeout after` -> `state="timeout"`.
7. **Decision Marker:** The last non-empty line of response matches `^NEEDS_DECISION:\s*(.*)$` -> `state="needs_decision"`. `decision.prompt` extracted.
8. **Permission Blockage:** Tool denial occurred with zero commits and empty response -> `state="needs_review(reason=blocked_by_permission)"`.
   - *Verified Fact:* In headless print mode (`-p`), a denied tool confirmation halts the turn immediately (exit 0, empty response, status SUCCESS).
   - *Verified Fact:* `denied_actions` carries only `{"action": "command", "display_name": "RunCommand"}`.
   - *Verified Fact:* Fine-grained denial tracking inspects session transcripts (`<agy_home>/brain/<conversation_id>/.system_generated/logs/transcript.jsonl`) to recover actual shell commands and paths into `denied_commands` and `denied_tools`.
8b. **Denied Without Progress (D49):** `status == "SUCCESS"` but zero commits AND zero modified files AND at least one denial occurred (in `denied_actions` or `cli.log`) -> `state="needs_review(reason=denied_without_progress)"` (NOT done). Triggers `auto_resume_on_denial` recovery identical to `blocked_by_permission`.
9. **Success:** `status == "SUCCESS"` -> `state="done"`. Zero commits without denials yields warning `no_commits`. Denials with progress yield warning `denied_actions`.
10. **Unparseable / Other:** Non-JSON stdout or unhandled status -> `state="failed"` with `parse_error=true` and raw output tail (<= 4 KB).

*Resolved Model (D63):* The wrapper parses the first `model alias "<X>" resolved to "<Y>"` line in the bounded cli.log tail, stores it as `Job.model_resolved`, exposes it in every envelope, and appends it to `usage.jsonl`; it stays `null` when no such line appeared.
*Token Accounting (D63, D83):* `tokens` always carries `input_tokens`/`output_tokens`/`total_tokens`; it also carries `thinking_tokens` and `cache_read_tokens` from `usage` and top-level `num_turns` when present, otherwise `null`, so old payloads stay backward compatible. agy reports these counters for the **last run only**, so each run appends its own `job.json.runs[]` entry (`run_generation`, wrapper `wall_clock_seconds`, agy's `agy_duration_seconds`, `tokens`, and the run lifecycle fields `started_at`, `ended_at`, `outcome`).
*Conversation Identity (D81):* Only a strict UUID is accepted, in this precedence: (a) `conversation_id` in agy's stdout JSON; (b) cli.log `Created conversation <uuid>`; (c) cli.log `Print mode: resuming conversation <uuid>`. The loose regex that could capture the word "using" from `Conversation using project ID: ...` is removed. While a job runs, the supervisor watches for `Created conversation <uuid>` and writes it to `job.json` immediately, so a kill/cancel never loses it. Cancellation still outranks the payload, but now retains the payload's id and tokens. A valid stored UUID is sticky: a different stdout UUID is not applied; `conversation_id_mismatch` records both ids. A stored non-UUID is treated as absent and replaced by the first valid observation.
*Duration Accounting (D83):* agy's JSON `duration_seconds` is the conversation's AGE, not this run's runtime; `num_turns` is cumulative. The wrapper never uses it for budgets, usage rows or logging. `job.json.runs[].wall_clock_seconds`, `usage.jsonl.duration_seconds`, and `cumulative_minutes` come from the wrapper's own wall clock; agy's value is stored only as `agy_duration_seconds` for diagnostics.
*Stream Tail Cap:* After process exit, the wrapper reads at most the trailing 10 MB of `stdout.txt` and `stderr.txt` (`supervisor.py`). The 10 MB cap is deliberate so large result payloads fit; truncation is marked explicitly.
*Next Actions Precedence (D54, D80):* When verification tests fail or time out, `next_actions` prioritizes verification guidance first and names the command, timeout and kill result (e.g. `"Verification tests timed out after 900s; pytest -q was killed by the wrapper (process group killed). Inspect the last 40 lines with agy_result, fix the hang or raise verify_timeout_seconds, then resume with agy_resume or cancel with agy_cancel"`).
*Server Resilience:* A JSON parse failure never crashes the MCP server process; it is recorded as a failed job outcome.

---

## 16. Crash Recovery & Liveness Monitoring

### Process State Persistence
- All job stdout/stderr are redirected to disk files (`stdout.txt`, `stderr.txt`), ensuring process survival across MCP server restarts.
- `job.json` records: `pid`, `pgid`, `start_time` (read from `/proc/<pid>/stat`), `state`, `branch`, `worktree_path`, `conversation_id` (strict UUID or `null`), `commit_msg_generation`, and the additive fields `runs` (one entry per run: `run_generation`, `started_at`, `ended_at`, `wall_clock_seconds`, `outcome`, `agy_duration_seconds`, `tokens`), `quota_estimate`, and `usage_cost`.

### Startup Reconciliation
Upon server initialization:
1. Scans `~/.local/state/agy-mcp/jobs/*/job.json`.
2. For each job in state `running`:
   - Inspects `/proc/<pid>/stat`. If the process exists and its start time matches `job.json`, the server reattaches the watcher, supervisor, and audit timers.
   - If the process is dead, the server reads captured log files, parses the final result per Section 15, runs the post-completion audit, executes a safety-net commit if applicable, and writes the terminal state with `recovered=true`.
3. Runs `git -C <repo_path> worktree prune` across all registered repositories.

### Observable Liveness
Because `agy` in `--output-format json` emits no stdout until completion:
- `agy_status` inspects `last_cli_log_mtime` from `cli.log`.
- Tracks `last_commit_at` and worktree dirty state.
- Tail 50 lines of `cli.log` and `git log --oneline` are surfaced. The wrapper never pretends live model tokens are streaming.

---

## 17. Security Event Log & Caps

### Append-Only Event Log (`audit.jsonl`)
Every state transition, violation, commit, kill and swallowed cleanup failure is appended atomically with `fsync` to `<jobdir>/audit.jsonl`:
```json
{"ts": "2026-10-24T22:50:00.123Z", "event": "job_created", "detail": {"title": "Add JWT auth", "model": "gemini-3.8-flash-medium", "max_minutes": 240, "verify": null}}
{"ts": "2026-10-24T22:50:05.456Z", "event": "commit", "detail": {"sha": "a1b2c3d", "subject": "Add jwt middleware"}}
{"ts": "2026-10-24T22:51:12.789Z", "event": "audit_violation", "detail": {"path": ".agents/hooks.json", "action": "created"}}
{"ts": "2026-10-24T22:51:13.000Z", "event": "cleanup_error", "detail": {"code": "safety_net_commit_failed"}}
```
Successful audit passes are not individually logged; only violations, state changes and the events above are recorded. `cleanup_error` details carry a short code only (no exception text or host paths).

### Resource Caps & Budgeting
- **Cumulative Job Budget:** The job's `max_minutes` (default 240m, stored as `Job.max_minutes`, hard-capped by `max_minutes_cap` 360m) bounds cumulative wall-clock execution across resumes; elapsed time accumulates in `cumulative_minutes`.
- **Daily Job Limit:** `daily_jobs_cap` (default 20) counts job starts per local calendar date (calculated in configured `timezone`, defaulting to local system time), tracked under a state-directory file lock. Quota is consumed only after prompt, model, verify, and repository validation succeeds. Exceeding limits returns `error.code=cap_exceeded`.
- **Disk Budget & Retention:** `min_free_gb` (minimum free space on state filesystem) and `max_state_disk_gb` (maximum total bytes in state directory) are checked prior to job launch. Inactive job directories older than `job_retention_days` in terminal states are moved to `<state_dir>/trash/<job_id>_<ts>` (never deleting running jobs or branches holding unmerged commits).
- **Usage Metrics:** `~/.local/state/agy-mcp/usage.jsonl` records job completion metrics (`job_id`, `state`, `model`, `model_resolved`, `duration_seconds`, `cumulative_minutes`, `tokens`, `usage_cost`, `run_generation`) strictly excluding prompts, titles, code and full email addresses. With a configured registry the row additionally carries `account` (registry name) and `masked_email`; without one those keys are absent, keeping the single-account row byte-identical (D107). `model_resolved` is `null` when the CLI logged no alias resolution (D63). `run_generation` identifies the run inside a job (resume increments it; rows written before the field existed count as generation 1). The record is appended **before** the terminal or `needs_review` state is persisted on every such path - process termination, watcher-driven finalization, the supervisor tick-failure fail-closed paths and crash recovery - so a caller that observes the new state in `job.json` always finds the corresponding record (D68, D77). Recovery does not append a second row for a run whose finalizer already wrote one (`has_usage_record_for_generation`), so a crash between the append and the transition cannot double count the run's minutes (D77).

### Usage & Quota Awareness (D59)
- **Parsing (`usage.py`):** `/usage` structured data at `command.data.groups[].buckets[]` is primary; the tab-separated `response` lines (group, "Weekly Limit Remaining"/"Five Hour Limit Remaining", percent, reset UTC) are a cross-check/fallback. Unexpected payloads fail soft to `{available: false, reason: "unrecognized_format"}` and never raise. Buckets include `window`, `remaining_fraction`, `reset_time_utc`, and `resets_in_seconds`.
- **Reading:** `get_usage` runs `agy --log-file <tmp>/usage.log --output-format json -p "/usage"` from a fresh empty temp directory with the sanitized environment and process-group kill handling (30s timeout; after SIGTERM the whole group is always SIGKILLed). stdout, stderr and the temp log read are capped at 1 MB each; an oversize stream yields `{available: false, reason: "output_too_large"}`. It parses `applyAuthResult: email=..., authMethod=...` from the temp log, masks the email (a local part longer than two characters becomes its first two characters + `***@` + domain; a one- or two-character local part becomes `***@` + domain), removes the temp directory, and caches only `available=true` readings in memory for 60s. The cache key includes the account name and home when the caller supplies one, so two accounts never share a reading (D107). An optional `expected_email`/`expected_email_sha256` makes the read fail soft to `reason=account_identity_mismatch` (or `account_identity_unknown`) when the full parsed address does not match; the reading exposes only the masked address (D103). The account email is never persisted.
- **Start/Resume Guard:** `usage_guard` = `off|warn|block` (default `warn`). Models map to groups by name (`gemini*` -> "Gemini Models"; `claude*`/`gpt*` -> "Claude and GPT models"). Below-threshold usage adds warning `low_quota` (warn) or refuses with `error.code=low_quota` including reset times (block). If usage is unavailable, the wrapper only adds warning `usage_unavailable` and **never blocks**; this also covers an expired login. If the reading is available but the model's group cannot be determined, the wrapper adds warning `usage_unmeasurable` and **never blocks** in every mode except `off`.
- **Typed Pools:** `accounts/pools.py` converts a reading into both pools (`Gemini Models`, `Claude and GPT models`) and both windows (`weekly`, `5h`) with `remaining_fraction`, `reset_time_utc` and `resets_in_seconds`; a malformed or unavailable reading becomes `AccountUsage(available=false, reason=...)` rather than an exception.
- **Per-Job Cost:** fresh readings at launch and finalization record `usage_cost {five_hour_pct_points, weekly_pct_points, measured, concurrent_jobs, note}` in `job.json`, `usage.jsonl`, and the envelope. With more than one concurrent job the numbers are approximate because quota is shared; `note` says so. Unavailable readings set `measured=false` and are never an error.
- **Token-Based Quota Estimate (D83, D85):** next to `usage_cost`, the wrapper stores `quota_estimate {tokens, weekly_pct_points, five_hour_pct_points, weekly_per_1k_tokens, five_hour_per_1k_tokens, estimated, quota_estimate_basis, note}` from the last run's agy JSON tokens (`tokens/1000 x coefficient`, base 0.0 at zero tokens). A usable `total_tokens` wins; a missing, zero, non-numeric or negative `total_tokens` falls back to `input_tokens + output_tokens` when both counters are present (zero total with zero counters is the base value). When no total can be determined, `quota_estimate` is `null` instead of a fabricated number. Coefficients are configurable via `quota_estimate_weekly_per_1k_tokens` (default `0.0031`) and `quota_estimate_five_hour_per_1k_tokens` (default `0.011`), calibrated conservative upper envelope across 7 measured jobs. It carries `quota_estimate_basis` ("calibrated-2026-10-31, n=7") and is an estimate only; the measured delta remains authoritative.
- **Interruption Diagnosis:** after a failed, timeout, or empty-response job, a fresh reading with a relevant bucket at 0% marks `needs_review(reason=quota_exhausted)` (resumable, `resume_after` = latest exhausted bucket reset, next_actions says to wait until then). `quota_error_patterns` are also matched against stderr and the cli.log tail. `auth_error_patterns` are matched against stderr, cli.log tail, and the `/usage` output; a match marks `needs_review(reason=auth_required)` and next_actions says to ask the human to log in to agy again, then resume.
- **Caveat:** The quota/auth pattern list is UNVERIFIED against real agy; the `/usage` reading is the reliable signal. A real, healthy job logs `You are not logged into Antigravity.` dozens of times during startup (79 matches for one smoke job, 55 for the other) *before* keyring auth succeeds (`keyringAuth: loaded token, expiry=... expired=false`, `ChainedAuth: authenticated via keyring`, `Print mode: silent auth succeeded`). Therefore `auth_error_patterns` MUST NOT contain `not logged in` or any broader match that would hit that healthy line; the real expired-login signature is still unknown and needs a dedicated probe under an empty HOME (D63). Defaults stay empty.

---

## 18. Configuration Keys (`~/.config/agy-mcp/config.toml`)

| Configuration Key | Type | Default | Description |
|---|---|---|---|
| `max_concurrent_jobs` | `int` | `2` | Maximum concurrent active jobs (queued or running) across all repositories. Checked and created under flock. |
| `max_minutes_cap` | `int` | `360` | Absolute hard ceiling on cumulative job minutes. |
| `daily_jobs_cap` | `int` | `20` | Maximum job starts allowed per calendar day. |
| `allowed_workdir_roots`| `List[str]` | `[]` | Explicit directory allowlist for repositories (defaults to `$HOME`). |
| `model_allowlist` | `List[str]` | `["gemini-3.8-flash", "gemini-3.8-flash-low", "gemini-3.8-flash-medium", "gemini-3.8-flash-high"]` | Permitted `agy` model identifiers. The unqualified alias is what real agy resolves to the `high` variant (D63). |
| `verify_allowlist` | `List[str | List[str]]` | `["pytest", "ruff", "npm test", "cargo test", "go test", "node --test"]` | Permitted verification commands (strings or token lists; supports absolute interpreter commands per D53). |
| `trust_agents_dir` | `bool` | `false` | If false, refuses execution on repos tracking `.agents/**`. |
| `require_clean_source`| `bool` | `false` | If true, refuses execution on dirty source repositories. |
| `checkpoint_commit_minutes` | `int` | `10` | Interval for automatic fallback checkpoint commits. |
| `verify_timeout_seconds` | `int` | `900` | Default verify timeout in seconds (1..86400); a job may override it per `agy_start` call (D80). |
| `auto_fix_attempts` | `int` | `2` | Maximum auto-fix verification resume cycles. |
| `auto_resume_on_denial` | `int` | `1` | Maximum automatic resumptions on shell tool denial with zero commits (0-3). |
| `plan_nudge` | `int` | `1` | `0`/`1`. When 1, a job that would be `done` with unticked PLAN.md boxes ends `needs_review(plan_incomplete)` and gets one automatic nudge resume; when 0 only the warning is added. |
| `claim_phrases` | `List[str]` | `["tests pass", "all tests passed", "verified"]` | Case-insensitive literal phrases in `.agy/REPORT.md` that count as a "tests passed" claim; a claim while verify did not run/pass adds `report_claims_unverified`. Markdown heading lines are ignored. |
| `worktree_setup_seconds` | `int` | `300` | Timeout for `git worktree add` operations. |
| `job_retention_days` | `int` | `14` | Days before inactive job directories are pruned. |
| `max_state_disk_gb` | `float` | `20.0` | Maximum total disk space consumed by state jobs. |
| `min_free_gb` | `float` | `2.0` | Minimum free disk space required to launch a job. |
| `agy_home` | `str` | `"~/.gemini/antigravity-cli"` | Base directory for agy storage and session transcripts. |
| `timezone` | `Optional[str]` | `null` | Calendar timezone for `daily_jobs_cap` and `daily_cap.json`: `null`/`local`/`system` use the system zone; otherwise an IANA name resolved by `zoneinfo.ZoneInfo`. Job, audit and usage timestamps stay UTC. |
| `usage_guard` | `str` | `"warn"` | `off`/`warn`/`block` gate on `agy_start` and `agy_resume`; unavailable usage never blocks. |
| `low_quota_five_hour_pct` | `float` | `15.0` | Low threshold for the 5-hour bucket (percent remaining, 0-100). |
| `low_quota_weekly_pct` | `float` | `10.0` | Low threshold for the weekly bucket (percent remaining, 0-100). |
| `quota_estimate_weekly_per_1k_tokens` | `float` | `0.0031` | Weekly quota points per 1k agy JSON tokens for `quota_estimate` (>= 0; D83, D85). |
| `quota_estimate_five_hour_per_1k_tokens` | `float` | `0.011` | 5-hour quota points per 1k agy JSON tokens for `quota_estimate` (>= 0; D83, D85). |
| `quota_error_patterns` | `List[str]` | `[]` | Regexes matched against stderr and cli.log tail to diagnose quota exhaustion; UNVERIFIED against real agy. |
| `auth_error_patterns` | `List[str]` | `[]` | Regexes matched against stderr, cli.log tail, and `/usage` output to diagnose an expired login; UNVERIFIED against real agy. MUST NOT contain `not logged in` or a broader match: a healthy job logs `You are not logged into Antigravity.` before keyring auth succeeds (D63). |
| `accounts` | `List[Table]` | `[]` | Additional accounts beyond the implicit `default` (D102–D104). Each entry: required `name` and absolute `home`; optional `pool`, `enabled`, `max_concurrent_jobs`, `expires_at`, and at most one of `expected_email`/`expected_email_sha256`. Invalid entries fail closed. |
| `max_accounts` | `int` | `5` | Maximum **total** accounts including the implicit `default` (1..32). `[[accounts]]` may declare at most `max_accounts - 1` entries. |


*Fail-Closed Configuration Handling (D56):* Configuration loading strictly enforces fail-closed validation. If `config.toml` contains invalid TOML syntax, unreadable permissions, or type/constraint violations, server initialization fails immediately with an informative message on `stderr` and process exit code 1. Permissive defaults are never silently substituted for broken configurations.

---

## 19. Getting the Result
The wrapper never merges or pushes job branches. Once a job reaches `state="done"`, the user integrates the result using standard Git commands:

```bash
# 1. Review commits created by agy
git log --oneline <base_commit>..agy/<slug>-<shortid>

# 2. Inspect the unified diff against the branch base
git diff <base_commit>...agy/<slug>-<shortid>

# 3. Merge the completed feature branch into the active branch
git merge --no-ff agy/<slug>-<shortid>

# 4. Clean up worktree and delete the temporary branch via agy-mcp
agy_cleanup(job_id="<id>", remove_worktree=true, delete_branch=true)
```

---

## 20. Documentation & README Requirements
The project `README.md` must include an honest capability table with confidence levels:

### Capabilities & Boundary Confidence

| Operation / Capability | Behavior | Confidence Level | Boundary Mechanism |
|---|---|---|---|
| **Host File Reads** | Unrestricted read access across filesystem via `head`, `grep`, `git diff` | **Verified (Tested)** | No path confinement in whitelisted tools. |
| **Workspace Writes** | Writable in worktree and `/tmp` via built-in tools | **Verified (Tested)** | Path checks in `write_file`/`edit_file`. |
| **Host File Writes** | Arbitrary host writes via `git diff --output=<path>` | **Assumed Exploitable** | Unrestricted arguments in whitelisted CLI commands. |
| **Safe Commands** | `git status`, `log`, `diff`, `branch` (list), `show`, `ls`, `grep`, `head`, `echo` | **Verified (Tested)** | Policy Guardian whitelist. |
| **Denied Commands** | `cat`, `find`, `pwd`, `which`, `env`, `mkdir`, `curl`, `rm` | **Verified (Tested)** | Auto-denied in headless mode without rules. |
| **Web Search** | `search_web` permitted out of the box | **Verified (Tested)** | Whitelisted tool. |
| **Network & MCP** | `read_url_content`, browser, MCP tools, subagents | **Gated by Policy** | Blocked without explicit allow-rules. |

*Terms of Service Notice:* Users are solely responsible for ensuring their usage complies with Google's Antigravity Terms of Service.

### Installer and client registration artifacts

`installer/install.sh` is the one-command, client-agnostic installer. It is a
distribution artifact, not a server module: nothing under `src/agy_mcp/` imports
it. It has three layers:

- **Core layer** (`agy_mcp_install_core`): prerequisite checks (`agy` on `PATH`
  or at `~/.local/bin/agy`, git, Python >= 3.12), a venv at
  `$CONF_DIR/agymcp/venv`, a non-editable pip install from `AGY_MCP_SRC`
  (default: the repository containing the script) unless
  `AGY_MCP_EDITABLE=1`, a mode-755 `run.sh`, a mode-600 `env`, the config at
  `~/.config/agy-mcp/config.toml` (or
  `$XDG_CONFIG_HOME/agy-mcp/config.toml`) only when it does not exist, and
  `~/.local/state/agy-mcp`.
  When the venv already imports `agy_mcp` the pip step is skipped and the
  installer prints `agy-mcp already installed; rerun with --upgrade to reinstall
  from <src>`. `--upgrade` (or `AGY_MCP_UPGRADE=1`) reinstalls the package from
  `AGY_MCP_SRC` and is honoured by `--dry-run` and listed in `--help` (D69 B1).
  On an editable install (PEP 610 `direct_url.json` with `"editable": true`, or
  a legacy egg-link/`__editable__*.pth`) it prints `editable install is already
  live; nothing to reinstall` and runs no pip, so the install stays editable;
  `AGY_MCP_EDITABLE=1` forces `pip install --upgrade -e <src>` instead. A normal
  upgrade runs `pip install --upgrade --force-reinstall <src>` without
  `--no-deps`, so the package's dependency list is re-resolved (D74).
- **Generic client layer**: a standard `mcpServers` JSON snippet and
  `orchestrator-guidance.md`, both printed; no dsh files, and the default
  client is `generic`. The JSON entry carries only `command`, `args` and `env`;
  the 360000 ms tool-call requirement is a printed note and documentation, never
  a non-standard key (D69 B4).
- **dsh client layer** (`ch_agymcp`): the `# >>> ai-stack agymcp >>>` block in
  `~/.dsh/profiles/web/cordis.patch.yml` with `toolCallTimeoutMs: 360000` and
  the `<!-- >>> ai-stack agymcp >>> -->` guidance block in `~/.dsh/AGENTS.md`.
  Both files are backed up to `.bak`; only this installer's block is replaced.
  A flow-style (`[...]`) patch file is refused with a printed manual block and
  a non-zero exit; full-line `#` comments and the lines between this
  installer's own markers are ignored by that guard, and the offending
  `line: text` pairs are printed (D69 B3).
- **Uninstall**: `--uninstall` removes the generated files and the dsh marker
  blocks. With no explicit `--client` (or with `--client dsh`) it removes the
  dsh blocks when present, prints each removal, and prints `restart dsh from a
  normal terminal to unload the server` without restarting anything. That hint
  appears only when a block was really removed: a flow-style file that the
  uninstall refuses to edit produces no removal and no restart hint (D75). An
  explicit `--client generic` leaves dsh untouched but warns that the remaining
  block registers a server whose `run.sh` was just deleted (D69 B2).

`installer/agents_block.md` is the canonical orchestrator guidance text.
`docs/ORCHESTRATION.md` documents the dsh approval caveats and the non-dsh
paste-in procedure. `tests/test_installer_chapter.py` pins the installer
behaviour under a temporary HOME with a fake `agy` and a stub `systemctl`; those
tests never touch the real `~/.dsh`.

---

## 21. Verified Findings & Open Items / Untested Assumptions

### Verified Findings Summary
- **Probe 1 (`docs/AGY_FINDINGS.md`):** Allow-rules prefix matching; accept-edits behavior; headless exit codes (0 on denial/timeout); detach capabilities; parallel multi-repo execution.
- **Probe 2 (`docs/AGY_FINDINGS_2.md`):** AST compound-command blocking (`&&`, `;`, `|`); `/tmp` scratch write allowance; parent environment variable inheritance; safe whitelist mapping; stream-json question skipping (`ask_question` -> "User Skipped"); timeout recovery.
- **Probe 3 (`docs/AGY_FINDINGS_3.md`):** Unconfined file reads via `head`, `grep`, `git diff`; `.git/config` and `.git/hooks` writable by file tools; `command(git branch)` enables destructive `-D` and `-f`; test runners equal arbitrary code execution; project permissions machine-local.
- **Smoke Probes (`docs/SMOKE_FINDINGS.md`):**
  - In headless print mode (`-p`), a denied tool confirmation halts the turn immediately (exit 0, empty response `""`, status `SUCCESS`).
  - `denied_actions` carries only `{"action": "command", "display_name": "RunCommand"}`; command text is NOT recoverable from `cli.log`.
  - Built-in file tools create parent directories automatically without shell `mkdir`.
  - `NEEDS_DECISION: <q> | <opts> | <rec>` marker output by agy as final response line is verified.
  - `--conversation <id>` resume preserves full context across turns.
  - `.agy/` is never committed into repository branches.
  - Token consumption verified per run: S1: 33,061 tokens; S2: 90,114 tokens; S3: 24,654 tokens; S4: 260,286 tokens.

### Open Items & Settlement Matrix

| Item / Assumption | Status | Impact | Settlement Procedure / Findings |
|---|---|---|---|
| **`git diff --output=<path>` write primitive** | Assumed Exploitable | Critical | Run `agy -p "Run: git diff --output=/tmp/test.txt"` and inspect target file. |
| **`write_file` parent directory creation** | **Settled (Verified Safe)** | Medium | Verified in S2 smoke test: built-in file tools automatically create all nested directories. |
| **`.agents/hooks.json` reload timing** | Assumed Exploitable | High | Write hook mid-session and test if subsequent tool calls fire `sh -c`. |
| **`--conversation` cwd preservation** | **Settled (Verified Safe)** | Medium | Verified in S4 smoke test: resume preserves context and operates in dedicated worktree. |
| **`GIT_CONFIG_COUNT` enforcement** | Assumed Safe | High | Set `GIT_CONFIG_*` for `core.hooksPath` and verify hooks are bypassed. |
| **`git status` index lock contention** | Observed Risk | Medium | Handled via `execute_with_index_lock_retry` with exponential backoff. |
| **FastMCP event loop concurrency** | **Settled (Verified Safe)** | Medium | Verified across S1-S7: requests handled cleanly via anyio worker threads. |
| **agy `/usage` output schema** | Verified against committed fixture; live field drift assumed | Medium | Parser is fail-soft; re-capture `tests/fixtures/real_usage.json` if fields change. |
| **Quota/auth error text patterns** | **UNVERIFIED** | Low-Medium | Defaults are empty; the `/usage` reading is the reliable signal. Confirm real wording before enabling patterns. |

---

## 22. Safe Fixture Cleanup Tool (`agy_mcp.cleanup_fixtures`)

The standalone CLI module `src/agy_mcp/cleanup_fixtures.py` (runnable via `python -m agy_mcp.cleanup_fixtures`) scans an agy-mcp state store, classifies historical jobs against a strict whitelist signature of throw-away pytest test runs, and only with explicit `--yes` removes confirmed fixture records.

### CLI Contract
- `--store PATH` (required): target agy-mcp state store. Missing `--store` exits with status 2 and reports `store` on stderr.
- `--yes`: opt-in flag to enable deletion (default is a read-only dry run).
- `--min-age-days FLOAT`: threshold age in days for eligible fixture rows (default: `1.0`).
- `--json`: emit machine-readable JSON output to stdout without human-readable logs or headers.
- **Exit codes:** `0` on success (completed dry run or deletion), `2` on refusal or invalid arguments, `1` on unexpected internal errors.

### Refusals (Exit 2)
Cleanup refuses execution when:
1. `--store` is missing, empty or whitespace-only (for example `--store ""`), does not exist, or is not a directory. The empty/whitespace check runs on the raw argument before path normalization, so an empty value is never interpreted as the current directory.
2. The store path itself is a symlink or contains any symlinked ancestor directory.
3. `store/jobs` is a symlink, missing, or not a directory.
4. The resolved store path is not strictly contained inside the resolved `$HOME` directory (`resolved_home in resolved_store.parents`).
5. `--min-age-days` is negative, NaN, or infinite.
6. An unsearchable or unreadable ancestor of `--store` is encountered (permission errors caught with exit 2, clear refusal message, and no traceback).

### Candidate Signature
A direct child of `store/jobs/` is a candidate if and only if all of the following hold; otherwise it is retained in `kept` with descriptive reasons:
1. Real directory (not a symlink) whose name is a canonical UUID.
2. `job.json` is a regular file <= 1 MiB whose `job_id` matches the directory name.
3. `state` is terminal: `done`, `failed`, or `cancelled`.
4. `repo_path` is an absolute path that resolves under `tempfile.gettempdir()`, with components matching `pytest-of-*`, then `pytest-<digits>`, then a pytest per-test directory `test_...<n>`, with at least one component after it (for example `.../repo`). The per-test component separates a suite-created row from a user repository that merely sits under a pytest basetemp. A row whose store and repo share the same `pytest-<n>` basetemp is the current test environment, not a historical fixture, and is kept.
5. `pid` is null/absent, or a positive integer within kernel `pid_max` that is not alive via `os.kill(pid, 0)` (treating `PermissionError` as alive). Non-positive, boolean, non-integer, or out-of-range pids are kept.
6. Job age exceeds `--min-age-days` based on timezone-aware ISO 8601 `updated_at`; `created_at` is used only when `updated_at` is absent. A present-but-unparseable `updated_at`, or a missing/unparseable fallback, is kept.
7. No symlink exists anywhere inside the job directory; a subtree the scanner cannot read (for example a mode-000 directory) makes the job ambiguous and therefore kept.
8. Follow-up protection:
   - A candidate is protected if its UUID appears as a dict key anywhere (nested too) inside a kept record's `job.json`.
   - A candidate is protected if its UUID appears as a substring inside any string value of a kept record's `job.json`. An unrelated UUID does not protect it.
   - If any kept job directory is unreadable or its `job.json` cannot be read or parsed, the scan fails closed: all candidates are moved to `kept` with a reason. On dry run, exits 0 with no traceback; with `--yes`, deletes nothing and exits 0. A symlinked job directory is not a readable job record and does not block unrelated candidates.

### Deletion and Manifest
- `run_cleanup` accepts an optional `before_delete` hook (`Callable[[str], None]`, default `None`).
- When candidates exist in delete mode (`--yes`):
  1. A planned manifest is written before deletion with permissions `0600` at `store/jobs/.cleanup-manifest-<timestamp>.json` containing `{"status": "planned", "planned_ids": [...]}` (IDs only).
  2. For each planned candidate, `before_delete(job_id)` is called (if provided), and the row is re-read and re-classified. If the row is now unreadable, non-terminal, alive, or otherwise no longer matches the fixture signature, only that row is skipped (remains on disk, reported as skipped with reasons, omitted from `removed_ids`), and deletion continues with the remaining rows.
  3. Job contents are deleted bottom-up without following symlinks. If deletion of a row fails, only that row is recorded as failed/skipped, and deletion continues with the remaining rows.
  4. The manifest is replaced atomically (`mkstemp` + `os.fsync` + `os.chmod(0600)` + `os.replace` in `store/jobs`) after deletion with `{"status": "completed" | "partial", "removed_ids": [...], "failed_ids": [...]}` (IDs only).
  5. A partial run (any row failed or skipped) causes the CLI to exit non-zero (exit 1) and report which IDs failed.
- In dry-run mode, no filesystem changes or manifests are written.

### JSON Output Schema
```json
{
  "store": "<resolved absolute path>",
  "dry_run": true,
  "min_age_days": 1.0,
  "candidates": [{"job_id": "...", "title": "...", "state": "...", "repo_path": "...", "reasons": ["..."]}],
  "kept": [{"job_id": "...", "reasons": ["..."]}],
  "skipped": [{"job_id": "...", "reasons": ["..."]}],
  "candidate_count": 0,
  "kept_count": 0,
  "skipped_count": 0,
  "deleted_ids": [],
  "deleted_count": 0,
  "failed_ids": [],
  "manifest": null
}
```
Under `--yes`, `candidates` preserves the complete candidate list found before deletion (`candidate_count == len(candidates)`), while `deleted_ids` and `deleted_count` record what was actually removed.

---

## 23. Safe Job Record Cleanup Tool (`agy_mcp.cleanup_jobs`)

The standalone CLI module `src/agy_mcp/cleanup_jobs.py` (runnable via `python -m agy_mcp.cleanup_jobs`) removes stale agy-mcp job records (directories inside `store/jobs/`) either for one project (matched by the job's normalized `repo_path`) or for explicit job IDs, plus a read-only `--list` mode. It never deletes git branches, repositories, worktrees, or agy conversation histories.

### CLI Modes & Arguments
- `--store PATH` (required): target agy-mcp state store. Missing `--store` exits with status 2 and reports `--store` on stderr.
- Exactly one mode is required (omitting or combining modes exits 2):
  - `--list`: read-only store inspection; reports job counts by state and eligible cleanup counts per project (reusing the exact `--project` classification path so edge-protected candidates are excluded). Leaves disk bit-for-bit unmodified even if `--yes` is supplied. Known limitation: `--list` executes a full dry-run scan per project ($O(\text{projects} \times \text{jobs})$, ~8s for 300 distinct projects).
  - `--project PATH`: cleans up stale job records matching the normalized project repository path. Exits 0 if no jobs match.
  - `--job ID` (repeatable): removes specific job records by UUID. Waives the age requirement; duplicate `--job` IDs are deduplicated; exits 2 if any UUID is malformed or not found in the store before any deletion is attempted; in explicit `--job` mode `repo_path` is ignored for matching, and if `repo_path` is invalid or contains NUL bytes, repo-level worktree registration lookup is skipped while job-local worktree protections (`work/.git` and `worktree_path/.git`) strictly apply. In dry-run mode, eligible requested IDs are reported only as candidates, while only ineligible requested IDs appear in `failed_ids` and `skipped`. Known limitation: symlinked job folders in `--job` mode exit 2 by design.
- `--older-than-days FLOAT`: threshold age in days for eligible jobs under `--project` mode (default: `7.0`). Negative, NaN, or infinite values exit 2.
- `--yes`: opt-in flag to enable deletion (default is a read-only dry run).
- `--json`: emit machine-readable JSON output to stdout.
- Path normalization: expands `~`, resolves dot and parent components without requiring paths to exist on disk (`strict=False`), strips trailing slashes, and preserves case. Known limitation: Unicode NFC/NFD paths are not normalized (filesystem-dependent path normalization for non-ASCII repo paths). Empty or whitespace-only paths for `--store`, `--project`, or `--job` are refused with exit status 2 and a single-line message on stderr. The `--store` check runs on the raw argument before path normalization, so `--store ""` is refused and is never interpreted as the current directory (D100).
- **Exit codes:**
  - `0`: success (completed dry run, read-only `--list`, successful deletion of all planned records, or zero matches in `--project`).
  - `1`: partial deletion (some records removed, but one or more records failed deletion or were skipped during pre-delete re-validation; in `--job` mode, if at least one requested ID was an eligible candidate and its deletion fails or is partial, exits 1 with manifest status `"partial"` and `failed_ids` listing it).
  - `2`: refusal (invalid/missing arguments, empty or whitespace-only arguments, conflicting modes, symlinked store/ancestor, store outside `$HOME`, unreadable `store/jobs` directory [single-line stderr message, no traceback], malformed/unknown `--job` UUIDs, or every requested `--job` ID ineligible under `--yes` with no manifest written).

### Eligibility & Retention Rules
A job record directory in `store/jobs/` is eligible for removal if all of the following hold; otherwise it is retained in `kept` with stated reasons:
1. Real directory (not a symlink) named with a canonical lowercase UUID.
2. `job.json` is a regular file <= 1 MiB whose `job_id` matches the directory name.
3. `state` is terminal: `done`, `failed`, or `cancelled`. Non-terminal states (`running`, `queued`, `needs_review`, etc.) are never eligible.
4. `pid` is null/absent or confirmed dead via `_check_pid_status` (within kernel `pid_max` and `os.kill(pid, 0)` raises `ProcessLookupError`/`ESRCH`). If `job.json` specifies a `pgid`, the process group is verified via `_check_pgid_status` (`os.killpg(pgid, 0)`), mirroring the pid rules exactly: live processes in the group or permission denied keep the row (`"process group <pgid> is still alive"`), dead groups (`ProcessLookupError`/`ESRCH`) permit deletion, and invalid/ambiguous values (bool, non-integer, <= 0, > pid_max, string) fail closed and keep the row.
5. Contains no internal symlinks; unreadable subtrees fail closed and are kept.
6. **Registered worktree retention rule:** Any job row whose directory contains a registered git worktree (a `work/` subfolder with a `.git` file, listed by the repository's `.git/worktrees`, or linked worktrees/submodules resolved via `gitdir` and `commondir`) is KEPT with the reason `"worktree registered (run 'git worktree prune' in repository to unregister)"` and a suggestion in human output to run `git worktree prune`.
   - **`repo_path/.git` validation and union root scanning (R1, R3, R6):** Worktree roots to scan are computed as a union: `repo_path/.git/worktrees` if `.git` is a valid git dir or pointer target, AND `repo_path/worktrees` if `repo_path` looks like a bare repo. One branch never shadows the other (R6).
     - When `repo_path/.git` is absent or not a pointer/dir: checked against git directory markers (`worktrees/`, `objects/`, `refs/`, `config`, `packed-refs`, `commondir`). If any marker is present, `repo_path` looks like a git directory and MUST contain a regular file (or symlink to a regular file) named `HEAD`; otherwise the row is kept fail-closed with `"worktree registrations unreadable (repo_path is not a git directory: <path>)"`. When none of the markers are present, `repo_path` stays "not a git repo" (deletable as before).
     - When `repo_path/.git` is a regular file, it must be a valid pointer consisting of exactly ONE non-empty line starting with `gitdir:`. The pointer target path (resolved relative to `repo_path` for relative paths) must exist, must be a directory, and must look like a git directory by containing a regular file named `HEAD` (R1); otherwise the row is kept fail-closed with a reason naming the target path and stating `"not a git directory"` (`"worktree registrations unreadable (.git file target is not a git directory: <path>)"`).
     - When `repo_path/.git` is a directory, it must look like a git directory by containing a regular file named `HEAD`; otherwise the row is kept fail-closed (`"worktree registrations unreadable (.git is not a git directory: <path>)"`). Anything else that exists or is a symlink (including dangling symlinks, empty directories, directories without `HEAD`, fifos, sockets, or other special files) keeps the row fail-closed with an invalid or unreadable reason; symlinks resolving to valid regular files or git directories are allowed.
     - When `repo_path/.git` is a directory or pointer: `repo_path` is also checked as a bare repo (e.g. bare repo containing a `.git` directory inside it). If `(repo_path / "HEAD").is_file()` and git markers are present, `repo_path/worktrees` is added to candidate worktree roots in union with `.git/worktrees`. If `repo_path` has bare repository internals directly under `repo_path` (`worktrees/`, `objects/`, `refs/`) but its `HEAD` is not a regular file, it fails closed (`"worktree registrations unreadable (repo_path is not a git directory: <path>)"`).
   - **Git metadata text file validation (R2, R5):** Any git metadata text file read by the cleaner (`.git` pointer file, `commondir`, worktree entry `gitdir`, worktree entry `locked`) must be a regular file or a symlink resolving to a regular file (R5); dangling symlinks or directories keep the row fail-closed with a clear reason. Text files are read with strict UTF-8 decoding (no silent BOM stripping via `utf-8-sig`). Any file containing a UTF-8 BOM (`\ufeff`), a NUL byte (`\x00`), or any control character (other than at most one trailing LF or CRLF) is invalid and keeps the row fail-closed with a reason naming the file and invalid character.
   - **`commondir` validation (R1, R2, R5):** When following a `commondir` file to find registrations in a common git directory, `commondir` must be a regular file, allows at most one trailing LF or CRLF, and requires exactly one non-empty line without BOM or control characters. The target directory must exist, must be a directory, and must look like a git directory by containing a regular file named `HEAD` (R1); otherwise the row is kept fail-closed with a reason naming the path (`"worktree registrations unreadable (commondir target is not a git directory: <path>)"`).
   - **Worktree entry validation (R2, R5):** For each worktree entry in `<common>/worktrees/<entry>`, the entry's `gitdir` file must contain exactly one non-empty line (after at most one trailing LF or CRLF) without BOM or control characters. The `gitdir` file, as well as `commondir` or `locked` inside the entry directory if present, must be regular files; dangling symlinks or corrupt metadata fail closed with an invalid entry reason so that when no other entry matches the job, the row is kept fail-closed, without weakening matching of valid registrations. Relative paths in `gitdir` files are resolved against their worktree entry directory. Undecodable or unreadable git metadata is treated as "cannot determine": keeps the row with a clear reason naming unreadable registrations, never "not registered". Errors are handled per entry so one bad entry cannot hide a valid registration in another.
   - **Comprehensive repository structure validation (R7):** For each repository kind (normal `.git` directory, linked worktree private directory, common directory, bare repository, and submodule modules directory), every file and directory git needs to treat a path as a working repo is enumerated and validated fail-closed:
     - Normal `.git` dir: `HEAD` (regular file, non-empty, readable), `objects` (directory, readable), `refs` (directory, readable), `config` (regular file, readable).
     - Linked worktree private dir: `HEAD` (regular file, non-empty, readable), `commondir` (regular file, non-empty, readable, pointing to valid common directory), `gitdir` (regular file, non-empty, readable). When `commondir` is missing or invalid, the private directory is checked as a standalone git directory (submodule); if it lacks standalone git structure (`objects`, `refs`), it fails closed (`"worktree registrations unreadable (.git file target is not a git directory: <path>)"`).
     - Common dir (commondir target): `HEAD` (regular file, non-empty, readable), `objects` (directory, readable), `refs` (directory, readable), `config` (regular file, readable).
     - Bare repo: `HEAD` (regular file, non-empty, readable), `objects` (directory, readable), `refs` (directory, readable), `config` (regular file, readable).
     - Submodule modules dir: `HEAD` (regular file, non-empty, readable), `objects` (directory, readable), `refs` (directory, readable), `config` (regular file, readable).
     - Any missing required file, wrong type (directory vs file), dangling symlink, FIFO, empty file, or unreadable file/directory fails closed and keeps the job row.
   - **Job-owned paths worktree registration matching (R8):** Registration matching compares each worktree entry's `gitdir` target against ALL paths owned by the job: `<job_dir>`, `<job_dir>/work`, `<job_dir>/work/.git`, and `<worktree_path>`, `<worktree_path>/.git` (resolved deterministically with path normalization, handling both absolute and relative paths against cwd and `job_dir`), whether or not `worktree_path` exists on disk. Any match marks the worktree as registered and keeps the row.
   - **Unified git metadata validation (R10):** Every git metadata text file read by the cleaner (`HEAD`, `config`, `packed-refs`, `commondir`, `gitdir`, `locked`, `.git` pointer file) goes through ONE shared strict reader/validator:
     - Checks `stat()` before opening so FIFOs, device nodes, and sockets never block (R11).
     - Strictly validates regular file (or symlink to regular file) and caps file size to 1 MiB (`MAX_GIT_METADATA_SIZE`).
     - Enforces strict UTF-8 decoding, rejecting UTF-8 BOM (`\xef\xbb\xbf` or `\ufeff`) and NUL bytes (`\x00`).
     - Single-line metadata files (`HEAD`, `commondir`, `gitdir`, `locked`, `.git` pointer) reject unstripped whitespace, multiple lines, and control characters (category `Cc` or code < 32).
     - `HEAD` content must match `ref: refs/<name>` (symbolic ref) or a 40/64-char hex object ID.
     - `config` files are validated for git config syntax (sections, valid key names, numeric values for `repositoryformatversion`, boolean values, no bare keys outside sections).
     - `packed-refs` files (if present) must be terminated by newlines, start with `# pack-refs with:`, and contain only comments, refs (`<sha> refs/...`), or peeled refs (`^<sha>`).
     - Any syntax or encoding failure fails closed and retains the job row.
   - **Hanging & subprocess safety (R11):** Cleaner never opens non-regular files without `stat()` inspection first, caps read sizes, and all test subprocesses enforce explicit execution timeouts.
   - **AST exception structural guard (R12):** Zero broad exception handlers swallow errors; all exception handling adheres to AST structural guard rules (0 broad exception handlers swallowing exceptions).
   - **Differential acceptance oracle:** The 390 layout differential mutations and 18 `job.json` mutations in `tests/cleanup/test_git_oracle_differential.py` serve as the normative differential acceptance oracle against real git ground truth.
7. **Age threshold:** In `--project` mode, job age (derived from timezone-aware ISO 8601 `updated_at`, falling back to `created_at`) must exceed `--older-than-days`. In explicit `--job` mode, the age requirement is waived.
8. **Edge protection from kept jobs, duplicate-key rejection & explainability:** Any candidate referenced as a key or substring anywhere within any kept job's `job.json` is moved to `kept` with reason `"referenced by kept job"`. When candidates transition to kept status, edge collection reuses parsed job data from classification without re-reading from disk or swallowing exceptions. Duplicate keys in any `job.json` read by the cleaner across all modes (candidate classification, kept-neighbor edge collection, `--list` inspection, `--job` mode, pre-delete candidate recheck) are rejected as corrupt (via `json.loads` with `object_pairs_hook` raising `ValueError`, R4) and fail closed through existing corrupt-record paths. All JSON parsing and string extractions enforce iterative traversal with a maximum depth cap of 128 (`_safe_json_loads`, `_extract_strings`), converting any `RecursionError` to `ValueError`; records exceeding the depth cap fail closed as corrupt/unreadable. In `cleanup_fixtures`, duplicate keys are rejected via the shared `_safe_json_loads` loader, and unreadable `jobs/` directories trigger exit 2 refusal. If any kept job record across the store is corrupt JSON, a non-object root, or unreadable, all candidates are kept fail-closed with a stated reason. When a corrupt or unreadable job record blocks cleanup, human and `--json` outputs name its UUID and full directory path, state that it was left untouched, and advise the operator to inspect or remove it manually. In `--list` mode, human output prints a warning line naming its UUID, and `--list --json` includes a top-level `"warnings"` list containing its UUID; `eligible_count` stays 0 for all projects; `--list` still writes nothing and directory mtimes remain unchanged.
9. **Repo path validation:** `repo_path` that is missing, not a string, contains NUL bytes, or causes path normalization to raise never matches any project in `--project` mode (kept with `"repo_path invalid"`), and is reported in `--list` under `"(unknown)"` with `eligible_count: 0`. In `--job` mode, `repo_path` is ignored for matching, but job-local worktree protections (`work/.git` and `worktree_path/.git`) are preserved; only repo-registration lookups requiring a valid repository path are skipped. If a job's `repo_path` no longer exists on disk, it is still matched against `--project` by path normalization and safely removed if eligible.

### Manifest & Deletion Lifecycle
- `run_cleanup` accepts an optional `before_delete: Optional[Callable[[str], None]] = None` callback.
- When candidates exist in delete mode (`--yes`):
  1. A planned manifest is written before deletion with permissions `0600` at `store/jobs/.cleanup-manifest-<timestamp>.json` recording `status: "planned"`, `mode: "project" | "job"`, `project` path (if project mode), and `planned_ids: [...]` (IDs only, sorted deterministically by job ID; the deterministic tests rely on this ordering).
  2. For each candidate, `before_delete(job_id)` is invoked (if provided). Immediately before each deletion:
     - The row is re-read and re-classified; if it mutated into a non-candidate state (e.g. entered `running`), it is skipped and remains on disk, but if its own record or any other non-deleted row is corrupt or unreadable, deletion fails closed and stops all remaining deletions (candidates go to `failed_ids` with a blocker reason, manifest status `"partial"`, exit 1).
     - In `--project` mode, `repo_path` is re-validated to ensure it still matches `target_project`; otherwise skipped with a project mismatch reason.
     - Dynamic edge protection is recomputed from the current on-disk records of all kept rows (all rows in `store_jobs` excluding remaining candidates `planned_ids[idx:]`, thus re-validating all kept rows and previously skipped/failed candidates): if reading ANY kept row's `job.json` raises (`json.JSONDecodeError`, non-object root, `OSError`, `UnicodeDecodeError`), deletion fails closed: candidate is NOT deleted, reported in `failed_ids` and `skipped` with a reason naming the corrupt/unreadable kept record and path, and deletion STOPS for all remaining candidates (reported as not deleted with the same blocker reason; manifest `"partial"`, exit 1). If any kept row references the candidate, only that row is skipped with edge protection reason. No bare `except Exception: pass`, error-swallowing handler, or unhandled exception swallowing (such as `except Exception: continue` in candidate scanning) remains on any classification or deletion decision path; broad exception handlers must record reasons or raise, satisfying the AST structural guard.
  3. Each candidate directory is safely deleted bottom-up without following symlinks. Deletion failure on one row (e.g. read-only permissions) never stops other rows.
  4. In `--job` mode, if at least one requested ID was an eligible candidate and its deletion fails (or only a subset is deleted), manifest status is `"partial"`, `failed_ids` lists it, and CLI exits 1. Exit 2 with no manifest written occurs only when every requested ID is ineligible.
  5. The manifest is atomically replaced with permissions `0600` recording `status: "completed" | "partial"`, `mode`, `project` (if project mode), `planned_ids`, `removed_ids` (only actually deleted IDs), and `failed_ids` (failed or skipped IDs).

## Sources

- `src/agy_mcp/server.py` — MCP tool implementations and envelope definitions.
- `src/agy_mcp/manager.py` — job management, supervisor orchestration, and locking.
- `src/agy_mcp/states.py` — state machine and transition table.
- `src/agy_mcp/runner.py` — execution runtime and process management.
- `src/agy_mcp/audit.py` — security audit engine.
- `src/agy_mcp/verify.py` — test verification and allowlist enforcement.
- `src/agy_mcp/gitops.py` — git commit pipeline and staging filters.
- `src/agy_mcp/cleanup_jobs.py` — job cleaner engine and differential acceptance.
- `tests/` — integration test suite.

