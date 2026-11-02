# Project history: questions, SPEC review changelog, and scaffold notes

Status: historical
Audience: maintainers, contributors

Historical design discussions, questions, review logs, and initial scaffolding notes from earlier development phases.

---

## 1. Architectural Questions & Ambiguities (SPEC v2 / v3)

Status: historical (as of 2026-10-24)
Original file: `docs/QUESTIONS.md`

# Architectural Questions & Ambiguities (SPEC v2 / v3)

Date: 2026-10-24

This document tracks unresolved questions, potential conflicts, and design ambiguities identified across SPEC formulation and review iterations.

---

### Q1: Resuming from `needs_review` State (Settled in v3 via F17)
- **Status:** **SETTLED (v3 / F17)**
- **Resolution:** `needs_review` transitions depend on the reason:
  - Security violations (`reason="audit_violation"`, `reason="host_path_modified"`, `reason="instruction_file_modified"`) are strictly **terminal**. No resume allowed.
  - Non-security review conditions (`reason="verification_failed"`, `reason="blocked_by_permission"`, `reason="invalid_commit_message"`) may be resumed via `agy_resume`.

### Q2: `.agy/` Exclusion Mechanism in Linked Worktrees (Superseded by F04)
- **Status:** **SUPERSEDED (v3 / F04)**
- **Resolution:** Do NOT edit `.git/info/exclude` in the common repository directory, and do NOT override `core.excludesFile`. Instead, pass an explicit pathspec on all staging operations (`git add -A -- . ':(exclude,top).agy'`) and filter status output in Python before staging. `.agy/` never enters the git index.

### Q3: Commit Handling in the Verify / Auto-Fix Loop (Settled in v3 via F05/F07/F08)
- **Status:** **SETTLED (v3 / F05, F07, F08)**
- **Resolution:** The watcher remains active during fix turns. If `.agy/COMMIT_MSG` is not written by the end of the fix turn but modified files exist, the wrapper creates a fallback checkpoint commit `agy: fix verification failure (attempt N)`.

### Q4: Worktree Lifecycle on Job Cancellation and Timeout (Answered via F24 `agy_cleanup`)
- **Status:** **ANSWERED (v3 / F24)**
- **Resolution:** Worktrees and branches are retained across job completion for review. Pruning and disk reclamation are explicitly handled by the `agy_cleanup(job_id, remove_worktree, delete_branch)` tool, accompanied by automated startup pruning (`git worktree prune`) and retention policies (`job_retention_days`, `max_state_disk_gb`).

### Q5: Process Termination Semantics on Audit Failure (Settled in v3 via F11)
- **Status:** **SETTLED (v3 / F11)**
- **Resolution:** When an audit violation or supervisor deadline triggers termination, the wrapper signals the whole process group via `os.killpg(pgid, signal.SIGTERM)`. After a 3-second grace period, it escalates to `os.killpg(pgid, signal.SIGKILL)`.

### Q6: Host Filesystem Read Boundaries (Residual Risk) (Settled in v3 via F01)
- **Status:** **ACKNOWLEDGED & MITIGATED (v3 / F01)**
- **Resolution:** Whitelisted commands (`head`, `grep`, `git diff --output=<path>`) can read and potentially write host paths. v1 provides branch isolation and host path mutation detection (`~/.ssh`, `~/.bashrc`, `~/.gitconfig` mtime+sha256 snapshot), not a sandbox. Real isolation is deferred to v2 (container runner).


---

## 2. CHANGELOG: SPEC.md Review & Evolution (v2 -> v3)

Status: historical (as of 2026-10-24)
Original file: `docs/CHANGELOG_SPEC.md`

# CHANGELOG: SPEC.md Review & Evolution (v2 -> v3)

This document maps all 36 findings from `docs/DS_REVIEW_1.md` (F01–F36) to their resolution in `SPEC.md` v3, incorporating project owner directives.

---

| Finding | Severity | Disposition | Section in SPEC.md (v3) | Summary & Notes |
|---|---|---|---|---|
| **F01** | **BLOCKER** | **Modified** | Section 5 (Threat Model), Section 12 (Audit), Section 20 (README) | Accepted honesty on unconfined host reads/writes; modified per owner override to add host-sensitive path snapshot detector (`mtime`+`sha256` of `~/.ssh`, `~/.bashrc`, `~/.gitconfig`, etc.). |
| **F02** | **BLOCKER** | **Accepted** | Section 12 (Security Audit Specification) | Audit is a mandatory hard gate before initial launch, before every relaunch/resume, on an independent 5s timer, and post-completion. |
| **F03** | **BLOCKER** | **Accepted** | Section 9 (Preflight & Worktree Lifecycle) | Pre-creates `<jobdir>/work/.agy/` (0700) and seeds `.agy/PLAN.md` before launching `agy` because `mkdir` is denied in accept-edits mode. |
| **F04** | **HIGH** | **Accepted** | Section 11 (Watcher & Commit Pipeline) | Replaced `.git/info/exclude` in common dir with git pathspec `:(exclude,top).agy` and Python status filtering to avoid mutating user repo. |
| **F05** | **HIGH** | **Accepted** | Section 11 (Watcher & Commit Pipeline) | Safety-net commit is explicitly conditional on passing audit and absence of `needs_review`. |
| **F06** | **HIGH** | **Accepted** | Section 10 (Process Execution) & Section 16 (Crash Recovery) | Redirects streams to files, writes `job.json` atomically with PID/start_time, and adds startup reconciler to recover/reconcile running jobs. |
| **F07** | **HIGH** | **Accepted** | Section 11 (Watcher & Commit Pipeline) | Checkpoint commit runs every `checkpoint_commit_minutes` (default 10m) and before terminations/cancellations. |
| **F08** | **HIGH** | **Accepted** | Section 11 (Watcher & Commit Pipeline) | Replaced crude globs with precise blacklist; set `partial_commit=true` and `needs_review` if tracked files are skipped. |
| **F09** | **HIGH** | **Accepted** | Section 12 (Security Audit Specification) | Fatal set focused on `.git` pointer, `.agents/**` manifests, and resolved git config diff; `.git*` files demoted to blocked-not-fatal. |
| **F10** | **HIGH** | **Accepted** | Section 11 (Watcher & Commit Pipeline) | Added index-lock retry loop with exponential backoff (10 attempts up to 5s) and stale lock removal rules (>5 min). |
| **F11** | **HIGH** | **Accepted** | Section 10 (Process Execution & Supervisor) | Wall-clock supervisor deadline `min(max_minutes, max_minutes_cap)*60 + 120`s; terminates entire process group via `os.killpg`. |
| **F12** | **HIGH** | **Accepted** | Section 11 (Watcher & Commit Pipeline) | Settle window (>=5s stability across two ticks), <=72 char subject validation, and atomic rename to `.agy/COMMIT_MSG.seen.<n>`. |
| **F13** | **HIGH** | **Accepted** | Section 7 (State Machine) & Section 15 (Result Parsing) | Tool denials record warnings and do not force terminal state; only total blockage with zero commits triggers `needs_review(reason=blocked_by_permission)`. |
| **F14** | **HIGH** | **Accepted** | Section 14 (Standing Prompt) & Section 15 (Result Parsing) | Matched `NEEDS_DECISION` on last non-empty line using ` | ` separator across preamble and result parser. |
| **F15** | **MEDIUM** | **Accepted** | Section 10 (Process Execution & Supervisor) | Resolved `--log-file` and all arguments to absolute paths without unexpanded tildes. |
| **F16** | **MEDIUM** | **Accepted** | Section 8 (Tool Contracts) & Section 10 (Process Execution) | Resumption inherits identical flags/model/budget, enforces pre-audit, verifies worktree cwd, and rejects duplicate concurrent resumes. |
| **F17** | **MEDIUM** | **Modified** | Section 7 (State Machine) | Formatted small transition table; modified per owner override so security reviews are terminal while non-security reviews are resumable. |
| **F18** | **MEDIUM** | **Accepted** | Section 15 (Result Parsing & Precedence) | Deterministic precedence parser handles cancellations, audit flags, signals, timeouts, decisions, and parse errors without crashing server. |
| **F19** | **MEDIUM** | **Accepted** | Section 8 (Tool Contracts) | Standardized envelope for all tools with explicit byte caps; `agy_start` reports `queued` during background setup. |
| **F20** | **MEDIUM** | **Accepted** | Section 8 (Tool Contracts) & Section 16 (Crash Recovery) | `agy_status` surfaces `cli.log` tail, git log, and dirty state; does not claim live model stdout streams in JSON mode. |
| **F21** | **MEDIUM** | **Accepted** | Section 9 (Preflight & Worktree Lifecycle) | Renamed parameter to `repo_path`, resolved strictly with `Path.resolve(strict=True)`, disallows nested worktrees. |
| **F22** | **MEDIUM** | **Accepted** | Section 9 (Preflight & Worktree Lifecycle) | Default `require_clean_source=false`; records `source_dirty=true` and warns rather than refusing per owner override. |
| **F23** | **MEDIUM** | **Accepted** | Section 9 (Preflight & Worktree Lifecycle) | Sanitizes branch slug `[a-z0-9-]`, enforces valid `HEAD`, checks free disk space (`min_free_gb=2`), and handles branch collisions. |
| **F24** | **MEDIUM** | **Accepted** | Section 8 (Tool Contracts) & Section 9 (Preflight) | Added `agy_cleanup(job_id, remove_worktree, delete_branch)` with safety checks, disk reporting, and retention policies. |
| **F25** | **MEDIUM** | **Accepted** | Section 10 (Process Execution & Supervisor) | Enforced 0700 on job dirs, 0600 on files; documented Google persistence in `~/.gemini` in README. |
| **F26** | **MEDIUM** | **Accepted** | Section 9 (Preflight & Worktree Lifecycle) | Preflight inspects global and project-level settings and flags `permission_drift` / warnings if dangerous allow-rules exist. |
| **F27** | **MEDIUM** | **Accepted** | Section 5 (Threat Model) & Section 8 (Tool Contracts) | Tagged all job-derived outputs as untrusted data to protect orchestrator LLM; enforced byte caps. |
| **F28** | **MEDIUM** | **Modified** | Section 13 (Verification & Auto-Fix Loop) | Modified per owner override: `verify` command restricted to `verify_allowlist` (`pytest`, `ruff`, etc.); unlisted commands refused with `verify_not_allowed`. |
| **F29** | **MEDIUM** | **Accepted** | Section 12 (Security Audit Specification) | Complete coverage of `.agents/**` manifests (`plugins.json`, `skills.json`); instruction file edits set `needs_review(reason=instruction_file_modified)`. |
| **F30** | **LOW** | **Accepted** | Section 20 (Documentation & README Requirements) | Capability table includes explicit confidence column distinguishing tested from policy-gated untested tools. |
| **F31** | **LOW** | **Accepted** | Section 10 (Process Execution & Supervisor) | Fixed rationale for banning `--mode auto`/`--sandbox` (silent fallback), `--effort` (conflicting level), and `-c` (recent session ambiguity). |
| **F32** | **LOW** | **Accepted** | Section 9 (Preflight & Worktree Lifecycle) | Documented that multiple jobs can target the same repository concurrently using independent worktrees. |
| **F33** | **LOW** | **Accepted** | Section 19 (Getting the Result) | Documented exact git merge and inspection workflow for completed branches. |
| **F34** | **LOW** | **Accepted** | Section 1 (Purpose) & docs/DECISIONS.md | Clarified that v2/v3 supersedes v1 completely and only restated requirements apply. |
| **F35** | **LOW** | **Accepted** | Section 17 (Security Event Log & Caps) | Added append-only `audit.jsonl` with fsync; kept concise per owner override. |
| **F36** | **LOW** | **Accepted** | Section 17 (Security Event Log & Caps) | Cumulative wall-clock budget across resumes; lock-protected daily cap counter; kept concise per owner override. |


---

## 3. Scaffold Notes & SPEC v3 Interpretations

Status: historical (as of 2026-10-24)
Original file: `docs/SCAFFOLD_NOTES.md`

# Scaffold Notes & SPEC v3 Interpretations

Date: 2026-10-24

This document records the architectural readings and interface alignments applied during the construction of the initial project skeleton:

1. **`repo_path` vs `workdir`:**
   - In `SPEC.md` section 8, the `agy_start` parameter is named `repo_path` (the absolute path to the user's source repository).
   - In `SPEC.md` section 6, the `Runner.launch` method accepts `workdir: Path`.
   - *Resolution:* `repo_path` refers strictly to the user's source repository root where preflight checks occur and linked worktrees are created. `workdir` refers to the job-specific worktree directory (`<jobdir>/work`) where `agy` and verification commands execute.

2. **Tool Signatures and Return Types:**
   - Tool signatures in `src/agy_mcp/server.py` match Section 8 character-for-character:
     - `agy_start(prompt: str, repo_path: str, title: str, model: str = "gemini-3.8-flash-medium", max_minutes: int = 240, verify: Optional[str] = None)`
     - `agy_status(job_id: str, tail_lines: int = 20)`
     - `agy_wait(job_id: str, seconds: int = 60)`
     - `agy_result(job_id: str)`
     - `agy_cancel(job_id: str)`
     - `agy_list()`
     - `agy_resume(job_id: str, answer: str)`
     - `agy_review(job_id: str)`
     - `agy_cleanup(job_id: str, remove_worktree: bool = True, delete_branch: bool = False, force: bool = False)`
   - All tools return dictionaries conforming to the standard envelope and tool-specific extended payloads defined in `models.py`.

3. **State Machine Definition:**
   - `states.py` defines `JobState` with the 8 states: `queued`, `running`, `needs_decision`, `needs_review`, `done`, `failed`, `timeout`, `cancelled`.
   - The 13 state transitions from Section 7 are codified in `TRANSITION_TABLE`.

4. **License Exclusion:**
   - Per project owner constraints, no `LICENSE` file has been added. License selection is reserved for the owner.

## core-pure

1. **Config Loading & Validation (`config.py`):**
   - Signature: `load_config(path: Optional[Path] = None, base_config_dir: Optional[Path] = None) -> Config`.
   - Supports hermetic testing by accepting an explicit `path` or `base_config_dir`, falling back to `XDG_CONFIG_HOME` or `~/.config/agy-mcp/config.toml`.
   - Missing configuration files cleanly return default `Config()` with empty `unknown_keys`.
   - Strict type and range validation applied across all 14 SPEC section 18 keys.
   - Unknown keys are collected in `Config.unknown_keys` rather than causing an unrecoverable failure.

2. **Data Models & Tool Envelopes (`models.py`, `util.py`):**
   - `Job` dataclass encapsulates all lifecycle, budgeting, and process metadata needed across sections 8, 10, and 16.
   - `to_dict` and `from_dict` provide robust schema round-tripping with `schema_version` validation (v1).
   - `wrap_untrusted_text` enforces boundary tagging (`<untrusted_text>...</untrusted_text>`) on all agent responses and diffs to mitigate second-order prompt injection against orchestrator LLMs.
   - Byte caps enforced per SPEC section 8 & F27:
     - Model response: 64 KB (`MAX_RESPONSE_BYTES`)
     - Verify output tail: 8 KB (`MAX_VERIFY_TAIL_BYTES`)
     - Diff stat: 8 KB (`MAX_DIFF_STAT_BYTES`)
     - Untrusted diff: 32 KB (`MAX_UNTRUSTED_DIFF_BYTES`)
     - Commit subjects: 1 KB (`MAX_COMMIT_MSG_BYTES`)
     - Max commits in result: 50 (`MAX_COMMITS_COUNT`)
     - Audit events tail: 20 entries
   - `util.py` provides:
     - `derive_branch_slug`: regex `[^a-z0-9-]+` bounded to 40 characters with `'job'` fallback.
     - `env_allowlist`: strips parent secrets and passes only HOME, PATH, LANG=C.UTF-8, TMPDIR, and hardened `GIT_CONFIG_*` flags.
     - `truncate_tail` and `truncate_head`: guarantee safe alignment on UTF-8 code point boundaries.
     - `redact_log_text`: regex filtering for API keys, Bearer tokens, GitHub PATs, and private keys.
     - `sha256_file` and `sha256_bytes`: SHA-256 chunked hashing.

3. **Job Store & Lifecycle Management (`jobs.py`):**
   - State directory isolation: `0700` directory modes and `0600` file modes strictly applied.
   - Durability: `_atomic_write_file` writes to a temporary file in the target directory, calls `fsync`, replaces the target via `os.replace`, and fsyncs the parent directory descriptor on POSIX.
   - State Machine: `transition_job_state` verifies each transition against `TRANSITION_TABLE`. Terminal states (`done`, `failed`, `cancelled`) cannot transition. Transitions from `needs_review` with security reasons (`audit_violation`, `host_path_modified`, `instruction_file_modified`) to `running` are blocked as terminal.
   - Budgeting: `record_job_execution_time` tracks cumulative wall-clock minutes across resumes against `min(max_minutes, max_minutes_cap)`.
   - Concurrency & Daily Cap: `check_and_increment_daily_cap` uses `fcntl.flock(LOCK_EX)` on `daily_cap.lock`. `rebuild_daily_cap_from_audit` reconstructs the count from `audit.jsonl` job start events.

4. **Result Precedence Parser (`parse_result.py`):**
   - Implements exact 9-level precedence from SPEC section 15 (Cancel -> Audit -> Signal -> RC 1/2 -> Timeout -> NEEDS_DECISION -> Blocked -> SUCCESS -> Unparseable).
   - Employs backwards line scanning to extract the last valid JSON document, ensuring resilience against pre-JSON logs, banners, or trailing warnings.
   - Never raises an unhandled exception on malformed stdout/stderr; returns `JobState.FAILED` with `parse_error=True`.

5. **Usage Logger (`usage.py`):**
   - `record_usage` uses `fcntl.flock(LOCK_EX)` on `usage.jsonl`.
   - Excludes all prompt text, task descriptions, and code snippets to preserve privacy and prevent secret leakage into persistent metric logs.

6. **Untrusted Text Delimiter Hardening (`models.wrap_untrusted_text`):**
   - Implemented a two-tier boundary defense:
     1. Per-call cryptographic nonce tag: `<untrusted_text id="{nonce}">\n...\n</untrusted_text id="{nonce}">` using `secrets.token_hex(8)`. An attacker cannot predict or guess the matching boundary tag ID.
     2. In-payload tag escaping: Any occurrence of embedded opening or closing tag attempts and case variants (e.g. `</untrusted_text>`, `</UNTRUSTED_TEXT>`, `< / untrusted_text >`, `</untrusted_text id="fake">`, `</untrusted_`) has its leading `<` neutralized to `&lt;`.
   - `unwrap_untrusted_text` provides reciprocal extraction by verifying matching boundary nonce tags and unescaping inner tags.

7. **Core-Pure Ambiguities Resolved (from Git History Audit):**
   - *TOML boolean vs integer typing:* In Python, `isinstance(True, int)` evaluates to `True`. Naive integer checks allow `max_concurrent_jobs = true`. Resolved by explicitly rejecting boolean instances for integer fields before performing numeric range checks.
   - *Float field integer coercion:* SPEC section 18 specifies float types for `max_state_disk_gb` and `min_free_gb`. TOML users frequently supply round integers (e.g. `min_free_gb = 5`). Resolved by coercing integer values to float (`5.0`) rather than raising a type error.
   - *Job budget & token tracking:* Section 16 requires cumulative budgeting and token usage tracking across multiple resumes. Added `cumulative_minutes: float` and `tokens: TokenUsage` directly to the `Job` schema (version 1) to prevent state loss across process relaunches.
   - *Dynamic next_actions mapping:* Section 8 provides a static example of `next_actions` in the standard envelope. Resolved by deriving contextual next actions based on `JobState` and distinguishing non-security `needs_review` (resumable with `agy_resume`) from fatal security violations (`audit_violation`, etc., which permit only review and cleanup).
   - *Multibyte UTF-8 byte boundary truncation:* Truncating arbitrary byte slices could slice across 2-, 3-, or 4-byte UTF-8 code units. Resolved in `truncate_tail` and `truncate_head` by decoding byte slices with `errors="ignore"` after reserving space for notice strings, ensuring strictly valid UTF-8 and no cap overruns.
   - *Consecutive log redaction passes:* Redaction passes could match previously placed markers (e.g. `[REDACTED_API_KEY]`) when checking subsequent credential assignments. Resolved by incorporating `(?!\[REDACTED)` negative lookahead into `_RE_KEY_ASSIGNMENT`.
   - *Durability on POSIX filesystems:* Standard `os.replace` does not guarantee directory entry persistence across power failures on ext4/xfs. Resolved by explicitly opening the parent directory file descriptor and calling `os.fsync(parent_fd)` during `_atomic_write_file`.
   - *Resilient JSON line parsing:* Real `agy` CLI logs can emit pre-JSON banners or progress lines to stdout. Resolved in `parse_agy_output` by scanning backwards from the end of stdout to locate the final valid JSON object.
   - *Distinguishing tool denial with progress:* Probe findings show `agy` reports `denied_actions` for commands like `mkdir` while still making edits and succeeding. Resolved per SPEC section 7 note by setting `needs_review(blocked_by_permission)` only when `commits_count == 0` AND the response text is empty; turns with commits or responses proceed to `done` with denials recorded in `denials[]`.

## git-layer

1. **Git Runner & Staging Security Filter (`gitops.py`):**
   - `run_git` wraps `subprocess.run` with isolated environment variables, strict timeouts, and execution hardening (`-c core.fsmonitor=false`, `-c core.hooksPath=/dev/null`, `-c core.pager=cat`).
   - `stage_changes` uses `git status --porcelain=v1 -z --untracked-files=all` with null-byte separation to parse files reliably even with spaces, tabs, or non-ASCII characters.
   - Sensitive file staging exclusion (D6 blacklist) matches sensitive basenames, `.env*` globs, credentials, keyrings, and dangerous repository configurations.
   - *Prefix normalization fix:* `clean_path.lstrip("./")` in Python strips characters set-wise, inadvertently corrupting dotfiles like `.env` into `env`. Corrected by implementing explicit prefix stripping `while clean.startswith("./"): clean = clean[2:]`.
   - `execute_with_index_lock_retry` implements exponential backoff (0.1s to 5.0s, up to 10 attempts) when encountering Git index contention. Stale lock files (>5 minutes) are cleaned up only after verifying the agent process is dead via `is_process_alive_func`.

2. **Worktree Lifecycle & Preflight Validation (`worktree.py`):**
   - `preflight` validates repository path containment inside `$HOME` or `allowed_workdir_roots`, rejecting bare repos, linked worktrees, unborn HEADs, and dirty states (when `require_clean_source=True`).
   - Rejects repositories containing `.agents` directory unless `trust_agents_dir=True`.
   - `create_worktree` handles branch collision retries up to 5 attempts, creating a detached-safe worktree branch.
   - `provision` configures `.agy` with strict `0700` directory permissions, writes `PLAN.md` instructions, and seeds initial state.
   - `cleanup_worktree` safely refuses to delete worktrees with uncommitted dirty changes or unmerged branch commits unless `force=True`.
   - `inspect_permission_rules` scans `settings.json` / workspace rules to detect dangerous rule overrides (e.g. `allow_all`, wide shell rules).

3. **Security Auditor & Host Path Snapshot (`audit.py`):**
   - Implements pure filesystem snapshots with cryptographic SHA-256 hashes and modification timestamps.
   - Distinguishes fatal violations (`.git` pointer file tampering, `.agents/**` modifications, git config mutations) from blocked-not-fatal changes (`.gitignore`, `.gitattributes`, `.gitmodules`).
   - Detects instruction file tampering (`AGENTS.md`, `GEMINI.md`).
   - `take_host_path_snapshot` and `verify_host_path_snapshot` monitor sensitive host files (`~/.ssh`, `~/.gnupg`, `~/.bashrc`, source repo `.git/config`) before and after job runs.

4. **Commit Watcher Pipeline (`watcher.py`):**
   - Implements file stability settle window tracking `sha256`, `size`, and `mtime_ns`. Changes must remain completely stable for `>= settle_window_seconds` (default 5.0s, test configurable) before consumption.
   - Clock initialization: `last_checkpoint_time` initialized lazily on first tick to avoid clashing with simulated test clocks.
   - Atomic rename protocol: `.agy/COMMIT_MSG` is renamed to `.seen.<counter>` before processing to avoid race conditions with agent writes.
   - Format validation: Checks commit message formatting (first line <= 72 characters, non-empty UTF-8, no control characters except newline and tab).
   - Validation failure retry: On malformed commit messages, writes diagnostic feedback to `.agy/COMMIT_MSG.error`. After 3 failed attempts, transitions job to `needs_review(reason=malformed_commit_msg)`.
   - Audit gate: Invokes `audit.py` before executing commits. If a fatal violation is detected, stages are aborted and the job transitions immediately to `needs_review(reason=audit_violation)`.

## git-hardening

1. **Commit Message Validation Hardening (A1):**
   - Rules aligned strictly with SPEC Section 11 & F12: first line <= 72 characters (measured in characters so multibyte UTF-8 codepoints count as 1), non-empty, valid UTF-8.
   - CRLF line endings (`\r\n`) are permitted as valid line breaks, while standalone carriage returns (`\r`), NUL bytes (`\x00`), and illegal ASCII control characters (< 32 except 9, 10) are strictly rejected.

2. **Permission Rules Inspector (A2 / F26):**
   - Signature updated with injectable `gemini_dir: Optional[Path] = None` defaulting to `~/.gemini`.
   - Missing configuration directories return clean empty results without raising errors.
   - Malformed JSON files emit a `"malformed_config_json"` warning without crashing.
   - Inspects `permissions.allow`, `permissions.allowed_commands`, and top-level `allow` arrays across `antigravity-cli/settings.json`, `settings.json`, `config/config.json`, and `config/projects/*.json`.
   - Comprehensive dangerous rule categorization: shells (`bash`, `sh`, `zsh`, `fish`), interpreters (`python`, `node`, `ruby`, `perl`, `bun`, `deno`), test runners (`pytest`, `npm test`, `cargo test`, `go test`), git commands (`git push`, `git branch`), file deletion (`rm`), network tools (`curl`, `wget`, `ssh`), and any MCP tool grant.
   - Emits `"dangerous_allow_rules"` warning when dangerous rules are detected.

3. **Instruction File Modification Severity (A3):**
   - Verified against SPEC Section 7 line 138: tampering with `AGENTS.md` or `GEMINI.md` is classified as fatal audit violation (`severity=AuditSeverity.FATAL`, `reason="instruction_file_modified"`), process group is terminated, commits are suppressed, and the state transitions to terminal `needs_review`.

4. **Sensitive Path Blacklist & Policy Files Separation (A4):**
   - Normalized path evaluation with lowercase matching for case-insensitive exclusion of sensitive files (e.g. `ID_RSA`, `.ENV`, `cert.PEM`, `foo.KEY`).
   - Recursively matches sensitive directories (`.agy`, `.ssh`, `.gnupg`) across arbitrary path depths.
   - Explicitly preserves legitimate non-sensitive files: `.gitignore`, `.gitattributes`, `.gitmodules`, `.github/**`, `.env.example`, `.env.sample`, `secret_scan.py`, `docs/secrets.md`, `secretary.py`.
   - `StagingResult` separates blocked policy files into `policy_files` list, un-stages them from the Git index, and flags `partial_commit=True`.

5. **Commit Suppression on Security Review (A5 / F05):**
   - `run_safety_net_commit`, `run_checkpoint_commit`, and `final_commit` strictly suppress commits if `findings.has_fatal` is True or if `job.state == JobState.NEEDS_REVIEW` with a security review reason (`audit_violation`, `host_path_modified`, `instruction_file_modified`).

6. **Break-it-on-purpose Verification & Test Gap Closure (Part C):**
   - Mutation 1 (`remove :(exclude,top).agy pathspec`) revealed that while Python filters catch `.agy` paths downstream, Git add without pathspec exclusion is an unverified boundary. Added `test_stage_changes_uses_agy_exclude_pathspec` to enforce that `git add` always executes with `:(exclude,top).agy`.
   - Mutations 2 through 6 all reliably broke relevant tests, confirming rigorous regression prevention.

## runtime

1. **Fake `agy` Executable (`tests/fake_agy/agy`, `tests/fake_agy/README.md`):**
   - Strict CLI argument parsing adhering to real `agy` behavior: `--mode`, `--output-format`, `--print-timeout`, `--log-file`, `--model`, `--conversation`, and `-p <prompt>`.
   - Flags must precede `-p`; unexpected options or flags after `-p` immediately exit with code 1; `--effort` is explicitly rejected.
   - Deterministic offline simulation of 17+ scenarios: `success_simple`, `success_with_commits`, `needs_decision`, `denial_with_progress`, `denial_blocked`, `timeout`, `hang`, `crash_rc1`, `crash_signal`, `garbage_stdout`, `huge_stdout`, `malicious_hooks`, `malicious_gitptr`, `malicious_hostwrite`, `slow_progress`, `resume_echo`, `env_probe`, `stdin_probe`.
   - Scenario selection decoupled from environment via three mechanisms: env var `FAKE_AGY_SCENARIO`, file `.fake_agy_scenario` in cwd or `$TMPDIR`, or prompt marker `__SCENARIO:<name>__`.

2. **Runner Abstraction & LocalProcessRunner (`runner.py`):**
   - Implements `Runner` ABC with `launch`, `poll`, `kill`, and `cleanup`.
   - Process group isolation via `subprocess.Popen(..., start_new_session=True)` ensuring process group ID equals PID (`pgid == pid`).
   - Stdin bound strictly to `subprocess.DEVNULL` for headless security; file-backed stdout/stderr descriptors.
   - Linux single-argv prompt boundary protection: prompts exceeding 100,000 bytes rejected with `PromptTooLargeError` (`error.code="prompt_too_large"`).
   - PID reuse protection: parses `/proc/<pid>/stat` field 22 (clock ticks after system boot) to verify start time and prevent signaling recycled PIDs.
   - Process group termination sends `SIGTERM` via `os.killpg`, sleeps up to grace period, and escalates to `SIGKILL`.

3. **Job Supervisor (`supervisor.py`):**
   - Pure `tick(now: float)` method enables step-by-step deterministic unit testing without wall-clock sleeps, alongside multi-threaded `run_loop()`.
   - Wall-clock deadline enforcement: `min(max_minutes, max_minutes_cap) * 60 + 120s`. On expiration, executes pre-kill checkpoint and terminates process group.
   - Independent 5-second periodic audit timer: detects fatal mutations (`.git` pointer file, `.agents/**`, git configs, instruction files), immediately terminates process group, suppresses pending commits, and transitions to terminal `needs_review(reason=audit_violation)`.
   - Seamless watcher integration via `CommitWatcher.tick()`.
   - Automated safety-net commit executed on clean exit 0 if uncommitted modifications remain.
   - Persisted baseline snapshot (`snapshot.json`) ensures tamper detection persists across sessions and resumes.

4. **Verification & Auto-Fix Loop (`verify.py`):**
   - `is_verify_allowed`: exact prefix matching on tokenized commands against `verify_allowlist` (e.g. `pytest` permits `pytest -v tests/test_core.py`; unlisted binaries rejected).
   - `run_verify`: executes directly in worktree with no shell (`shlex.split`), 15m timeout, sanitized environment, dedicated scratch `TMPDIR`, and 8 KB tail truncation.
   - `format_fix_prompt`: formats error output tail into markdown code fence prepended with standing preamble.
   - Auto-fix loop in `JobManager`: resumes turn automatically upon test failure up to `auto_fix_attempts` (default 2), transitioning to `needs_review(verification_failed)` upon attempt exhaustion.

5. **Crash Recovery & Reconciliation (`recovery.py`):**
   - Startup reconciliation scans `state_dir/jobs/*/job.json`.
   - Active process liveness verified via `/proc/<pid>/stat` start time comparison; orphaned/dead jobs finalized from disk streams and audited; stale queued jobs cleaned up.
   - Automated `git worktree prune` executed across all registered repositories.
   - `get_liveness_summary`: inspects `cli.log` mtime, git log, dirty status, and redacts credentials from recent log tail.

6. **JobManager (`manager.py`):**
   - Unified orchestration of all nine MCP operations: `start`, `status`, `wait`, `result`, `cancel`, `list`, `resume`, `review`, `cleanup`.
   - Concurrency ceiling (`max_concurrent_jobs`) and daily start budget (`daily_jobs_cap`) strictly enforced under file lock.
   - Mandatory resume audit gate verifies baseline snapshot integrity before relaunching; terminal security reviews cannot be resumed.
   - Output text, diff stats, and untrusted diffs bounded by explicit byte caps and protected with `wrap_untrusted_text`.

## runtime-audit

1. **Verification Process Isolation & Group Termination (B1):**
   - Configured `verify.py::run_verify` with `start_new_session=True` so verification test commands run in an independent session / process group (`pgid == pid`).
   - On timeout (15m default) or cancellation, `run_verify` issues `os.killpg(proc.pid, signal.SIGTERM)` followed by `SIGKILL`, reliably killing both child processes and runaway grandchildren (e.g. background sleep tasks spawned by test commands). Exit code 124 returned on timeout.

2. **Pre- and Post-Verify Security Audit Gates (B2):**
   - In `supervisor.py`, integrated strict pre-verify and post-verify security audits around test command execution.
   - Pre-verify gate: if fatal modifications (`.git` pointer file, `.agents/**`, git configs, instruction files) are detected prior to verify execution, verification is aborted, pending commits suppressed, and job immediately transitions to `needs_review(audit_violation)`.
   - Post-verify gate: runs after test command completion to verify that the verification command itself did not tamper with security-critical files. Violations are flagged with fatal severity, suppressing any automated safety-net commits.

3. **Asynchronous Nonblocking `start()` Provisioning (B3):**
   - Refactored `JobManager.start` to record the initial job in `queued` state and return immediately without blocking the caller on disk provisioning.
   - A dedicated background thread (`_provision_and_launch`) provisions the worktree, baseline snapshot, and spawns the runner process group.
   - Setup failures during provisioning transition the job to `FAILED` with `review_reason="setup_failed"` and automatically invoke `remove_worktree_and_branch` to prevent disk/branch leaks per SPEC Section 9.

4. **Wait Call Clamping and Envelope Warning (B4):**
   - Updated `JobManager.wait` default timeout to 30s and clamped maximum timeout to 300s per SPEC Section 8 and Decision D7.
   - If a caller supplies `seconds > 300`, the manager clamps the duration to 300.0 and appends a clear warning entry to the result envelope.

5. **Supervisor Tick Loop Resilience (B5):**
   - Hardened `Supervisor.run_loop` against transient tick errors (e.g., intermittent I/O glitches).
   - Single tick exceptions are logged to `audit.jsonl` as `supervisor_error` without stopping the supervisory loop.
   - A consecutive error threshold (`MAX_CONSECUTIVE_TICK_ERRORS = 3`) transitions the job to `FAILED` with `review_reason="supervisor_failure"` if failures persist, while a successful tick resets the error counter.

6. **Process Liveness Verification & Recovery Reattachment (B6):**
   - Hardened `recovery.py::check_process_liveness` and `LocalProcessRunner.poll` to inspect `/proc/<pid>/stat` field 3 (state), explicitly rejecting zombie (`Z`) and dead (`X`) processes.
   - Implemented `JobManager.reconcile()` to recover running processes on server restart, re-instantiating `ProcessHandle`, `CommitWatcher`, and `Supervisor` background threads to resume supervisory control seamlessly.

7. **Break-it-on-purpose Mutations (Part C):**
   - All five mandatory mutations were verified to break relevant tests:
     1. Verify `start_new_session=False`: caught by `test_verify_process_group_kills_grandchild`.
     2. Synchronous `start()`: caught by `test_manager_start_asynchronous_nonblocking`.
     3. Supervisor exiting on first tick error: caught by `test_supervisor_tick_resilience_and_consecutive_failure_cap`.
     4. Recovery adopting process without start-time check: caught by `test_recovery_pid_reuse_rejected_end_to_end`.
     5. `wait()` ignoring 300s clamp: caught by `test_manager_wait_clamping_and_warning`.

## argv-fix

1. **Root Cause Analysis of Argv Discrepancies (Part A):**
   - The discrepancy where `--mode headless` and `--output-format stream-json` appeared in the previous audit response was an erroneous reporting artifact hand-written into the markdown report rather than captured from Python test output.
   - However, the underlying fake agy CLI allowed loose flags and default values without strict enforcement, leaving room for wrapper bugs to go unnoticed.
   - Fixed by making `build_argv` strictly validate against `VALID_MODES = {"accept-edits"}` and `VALID_OUTPUT_FORMATS = {"json"}`, rejecting any flags in `FORBIDDEN_FLAGS = {"--sandbox", "--effort", "-c", "--dangerously-skip-permissions"}`.
   - Ensured `<budget_seconds>s` suffix format and absolute path resolution for `<jobdir>/cli.log` and the `agy` executable.

2. **Strict Fake AGY Enforcement (Part B):**
   - Hardened `tests/fake_agy/agy` to exit with status 1 on 12 distinct invalid CLI conditions: unknown flags, unpermitted modes, non-json formats, missing/unformatted print timeouts, missing or relative log files, missing models, missing/empty prompts, and trailing arguments after `-p`.
   - Documented in `tests/fake_agy/README.md` that while the real `agy` binary merely issues a warning on unrecognized `--mode` values, the fake agy enforces `accept-edits` strictly to catch wrapper bugs early.
   - Added `test_argv_exact_fresh` and `test_argv_exact_resume` asserting complete character-for-character flag ordering matching SPEC section 10 recorded via `FAKE_AGY_RECORD`.

3. **Standing Prompt Integration (Part C):**
   - Implemented `load_preamble()`, `format_fresh_prompt()`, and `format_resume_prompt()` in `src/agy_mcp/util.py` using `importlib.resources`.
   - Structured fresh prompts to prepend the complete text of `src/agy_mcp/preamble.md` before the unmodified user task under a `## Task` header.
   - Structured resume prompts to place user answers under `## User Response`, wrapped safely inside untrusted text delimiters (`<untrusted_text id="...">`) per SPEC Section 5 and Section 14.
   - Configured `pyproject.toml` hatchling wheel force-include and sdist include rules to ensure `preamble.md` is packaged into distribution wheels.

4. **Behavioural Alignments & Hardening (Part D):**
   - D1: Aligned `denial_with_progress` behavior with SPEC section 15 lines 150 & 474-475; tool denials with committed progress or response complete cleanly with `JobState.DONE` and populate warnings and denials, whereas zero-progress denials enter `needs_review(blocked_by_permission)`.
   - D2: Distinct review reasons enforced across security exploit scenarios: `malicious_hooks` and `malicious_gitptr` enter `audit_violation`, while `malicious_hostwrite` enters `host_path_modified`.
   - D3: Comprehensive source code change log compiled across all 8 modified modules since git-hardening commit `49b4402`.
   - D4: Added test coverage for auto-fix loop success on second attempt (`test_manager_auto_fix_success_second_attempt`) and verified complete scenario coverage across all runtime requirements.

5. **Mutation Testing (Part E):**
   - Verified that mutations to `mode`, `print-timeout` units, `-p` flag ordering, preamble loading, and denial classification reliably trigger test failures and are caught by the test suite.

## server

1. **FastMCP Server Architecture (`src/agy_mcp/server.py`):**
   - Built with official MCP SDK `FastMCP("agy")` stdio transport.
   - Registers exactly nine tools corresponding to SPEC Section 8: `agy_start`, `agy_status`, `agy_wait`, `agy_result`, `agy_cancel`, `agy_list`, `agy_resume`, `agy_review`, `agy_cleanup`.
   - Tool docstrings crafted specifically for LLM orchestrator consumption (DeepSeek): bounded under 1,200 characters each, detailing what each tool does, when to call it, returned payloads, next action flow for all 8 states, plain-English state definitions in `agy_status`, and mandatory rules (no secrets in prompts, repo file specs, and `<untrusted_text>` boundary safety).

2. **Asynchronous Handler Nonblocking Execution:**
   - As discovered from `mcp` SDK v1.30.0 source analysis, FastMCP executes synchronous functions directly on the asyncio event loop (`return fn(**...)`).
   - All tool handlers are declared `async def` and delegate blocking operations via `await anyio.to_thread.run_sync()`. This guarantees long blocking wait operations like `agy_wait(300)` never block concurrent requests such as `agy_status`, verified via sub-second latency benchmarks.

3. **CLI Arguments & Startup/Shutdown Lifecycle:**
   - Command-line interface via `argparse`: `agy-mcp [--agy-binary PATH] [--state-dir PATH] [--config PATH] [--version]`.
   - Binary resolution: resolves to absolute path via `shutil.which("agy")` or `--agy-binary`; exits with status code 2 and explicit stderr diagnostic if missing or non-executable.
   - State directory created with secure `0700` permissions.
   - Configuration loaded with unknown key warnings surfaced exclusively to `sys.stderr`.
   - Logging strictly separated from the JSON-RPC stdio protocol channel: output sent to `sys.stderr` and `<state_dir>/server.log`. Print statements banned in source code via Ruff rule `T20`.
   - Startup runs `JobManager.reconcile()` to recover or finalize running jobs.
   - Clean shutdown: EOF on stdin or SIGTERM cleanly stops supervisor worker threads via `manager.shutdown()` without terminating background `agy` process groups, preserving jobs for subsequent startup recovery.

4. **Structured Error Envelopes:**
   - Every manager error, preflight failure, or validation issue converts into a standardized response envelope with an `error` object (`{code: str, message: str}`).
   - Unknown/internal exceptions are logged to stderr + `<state_dir>/server.log` and returned as `error.code="internal_error"` without leaking tracebacks.

## smoke-real

1. **Live Smoke Testing Harness (`scripts/smoke_real.py`, driver removed after the round):**
   - Driven exclusively over the official `mcp.client.stdio` stdio transport against a real spawned server instance.
   - Executed S1 through S7 test matrix against real `agy` v1.4.7 with `gemini-3.8-flash-medium`.
   - Complete protocol stream isolation: server stdout intercepted via `ProcessProxy` Tee ByteReceiveStream, verifying 100% JSON-RPC 2.0 purity (33/33 valid frames).

2. **Core Real-World Findings:**
   - **Parent Directories:** Real `agy`'s file editing tools create nested parent directories automatically without invoking shell commands (S2).
   - **Interactive Decisions:** `NEEDS_DECISION: <question> | <options> | <recommendation>` survives real output and triggers `needs_decision` FSM state cleanly; resuming via `--conversation <id>` retains complete conversational context and checklist state (S4).
   - **Denial Behavior:** Real `agy` print mode soft-denies unapproved tool confirmations (`RunCommand`) and terminates immediately upon denial with exit code 0 rather than executing subsequent instructions.
   - **Denial Schema:** Real `agy` JSON output provides coarse action metadata (`action: "command", display_name: "RunCommand"`) rather than the executed command argv string.

## smoke-fixes

1. **Denial Records & CLI Log Tail (`cli_log.py`, `models.py`, SPEC 15, D48):**
   - Headless print mode halts immediately on shell tool denial with exit 0 and empty response.
   - Analysis of `s1-cli.log` and `s3-cli.log` proved that command text is unrecoverable from `cli.log` (only `Print mode: soft-denying tool confirmation "<Tool>" at step <N>` is recorded).
   - `extract_denials_from_cli_log` scans the last 2 MB of `cli.log` for soft-denials, returning step, tool name, and raw line.
   - Result responses expose `denied_commands` capped at 20 entries, 500 bytes per command, wrapped in `<untrusted_text>`.
   - `derive_next_actions` guides the caller to resume with file tools only.

2. **Stop-and-Ask Guidance & Auto-Resume Recovery (`preamble.md`, `manager.py`, `config.py`):**
   - Added Rule 10 to `preamble.md` strictly prohibiting shell commands and prescribing `NEEDS_DECISION: need command <exact command> | allow it, or do it another way | <recommendation>`.
   - Added `auto_resume_on_denial` configuration (default 1, range 0–3).
   - When a job halts in `needs_review(blocked_by_permission)` with 0 commits and auto-resume attempts remain, `manager.py` records audit event `denial_auto_resume` and resumes the conversation with wrapper guidance.

3. **Watcher Commit-without-Changes Handling (`watcher.py`, `gitops.py`, `supervisor.py`):**
   - Fixed crash when `agy` emitted `COMMIT_MSG` without staged file changes: watcher checks `git diff --cached --quiet` before committing; if empty, consumes the file, emits audit event `commit_skipped_no_changes`, and resets without incrementing consecutive error counts.
   - Fixed `GitError` stringification so `str(exc)` returns the error message and stderr.
   - Extended `supervisor_error` audit events to log `exception_type`, `message`, and the last 500 bytes of `git_stderr`.

4. **Modified Files Telemetry (`gitops.py`, `supervisor.py`, `manager.py`, SPEC 8):**
   - `files_modified` populated by computing `git diff --name-only base..branch` on commit, finalization, and query, capped at 200 entries.

5. **Tool Descriptions & Test Suite Cleanup (`server.py`, `pyproject.toml`):**
   - Trimmed repetitive critical rules across tool descriptions to reduce token usage while preserving LLM orchestrator guidance.
   - Added prompt engineering guidance to `agy_start` emphasizing file tools and delegating test execution to the wrapper `verify` step.
   - Cleaned machine-specific ROS plugins from `pyproject.toml` `addopts`.








---

## Sources

- `src/agy_mcp/` — initial codebase implementation.
- `doc/design/spec.md` — architectural contract.
- Historical sources: `docs/QUESTIONS.md`, `docs/CHANGELOG_SPEC.md`, `docs/SCAFFOLD_NOTES.md` (consolidated into this file).
