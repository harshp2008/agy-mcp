# Architectural and security decisions: D1–D115

Status: current
Audience: maintainers, contributors

Append-only record of architectural, security, and design decisions across agy-mcp development.

---

## v2 Decisions (Initial Baseline)

### D1: Threat Model & Pluggable Runner Architecture
- **Host Execution:** `agy` executes directly on the host as the invoking user, allowing it to read any file the user has permission to read (verified in Probe 3 T1) and write within its designated workspace and `/tmp`.
- **Primary Risk:** Prompt injection and adversarial steering from content `agy` ingests (e.g. untrusted repository files, documentation, or search results).
- **Controls & Evolution:** Version 1 implements defense-in-depth mitigations (hardened git, worktrees, file auditing, secret staging blocks, environment sanitization) and explicitly discloses residual risks in the README.
- **Runner Interface:** Introduce an abstract `Runner` interface (`launch`, `poll`, `resume`, `cancel`, `cleanup`) now so an isolated runner (such as Docker or Linux container namespaces) can be cleanly implemented in v2 without changing MCP tool contracts.
  *Correction (2026-11-02):* the shipped `Runner` interface is `launch`, `poll`, `kill`, `cleanup`; there is no `resume` or `cancel` method (`src/agy_mcp/runner.py:175-231`, SPEC section 6).

### D2: Zero Custom Allow-Rules
- **Default Built-in Set:** The wrapper relies strictly on `agy`'s built-in safe command whitelist (`git status`, `git log`, `git diff`, `git branch` [listing only], `git show`, `ls`, `grep`, `head`, `echo`).
- **No Settings Modifications:** The wrapper never modifies `~/.gemini/antigravity-cli/settings.json` or project-level configuration files.
- **Documentation:** The README explains that adding custom allow-rules is entirely the user's prerogative, documenting specifically why `git branch` (prefix matching allows `-f` and `-D`) and bare interpreters or test runners (arbitrary code execution) carry high risk.

### D3: Wrapper-Managed Git Commits
- **Agent Role:** `agy` never executes modifying git commands (`git add`, `git commit`, `git branch`). Instead, `agy` writes structured commit messages to `.agy/COMMIT_MSG` after completing each coherent step (first line <= 72 characters, blank line, body).
- **Watcher Daemon:** The wrapper polls `.agy/COMMIT_MSG` every 5 seconds. When the file's hash changes, it runs security audits (D5), stages changes excluding sensitive patterns (D6), and executes `git commit -F .agy/COMMIT_MSG` using hardened git (D4). If no working files changed, the commit is skipped.
  *Correction (2026-11-02):* the watcher polls every 1 second with a 5 second settle window (`src/agy_mcp/manager.py:120-121`).
- **Safety-Net Commit:** On `done`, `timeout`, `failed`, and `cancelled` (not after an audit violation), if untracked/modified changes remain in the worktree, the wrapper creates a fallback commit (`agy: <title>`) using the agent's final response summary.
  *Correction (2026-11-02):* the live safety-net commit runs only when the final state is `done` (`src/agy_mcp/supervisor.py:1255`), not on `timeout`, `failed` or `cancelled`; SPEC section 11 agrees.
- **Exclusion:** The wrapper never writes to the user's `.git`. All wrapper `git add` calls pass an explicit pathspec that excludes the metadata tree, e.g. `git add -A -- . ':(exclude,top).agy'`, and the wrapper additionally filters the `git status --porcelain -z` list in Python before staging. `.agy/` never enters the index.

### D4: Hardened Git Execution
- **Wrapper Git Invocation:** Every `git` call executed by the wrapper runs with explicit hardening flags:
  `-c core.fsmonitor=false -c core.hooksPath=/dev/null -c core.pager=cat`, `GIT_CONFIG_NOSYSTEM=1`, and explicit user `user.name` and `user.email`.
- **Agent Git Environment:** The environment passed to `agy` injects the same security overrides via git environment variables:
  `GIT_CONFIG_COUNT=3`, `GIT_CONFIG_KEY_0=core.fsmonitor`, `GIT_CONFIG_VALUE_0=false`, `GIT_CONFIG_KEY_1=core.hooksPath`, `GIT_CONFIG_VALUE_1=/dev/null`, `GIT_CONFIG_KEY_2=core.pager`, `GIT_CONFIG_VALUE_2=cat`.
- **Verification Requirement:** The builder must test that these environment keys take effect in subshells and catalog any git configuration variables that cannot be forced.

### D5: Isolated Worktrees and Security Auditing
- **Worktree Isolation:** Every job operates in an isolated linked worktree:
  `git worktree add <jobdir>/work -b agy/<slug>-<shortid>` created from `HEAD` of the user's repository.
- **Untracked File Boundary:** The user's primary working tree is never modified. Uncommitted and untracked files are not copied to the worktree by default (an optional `copy_files=[...]` configuration will allow selective copying).
- **Security Audit:** The wrapper audits security-sensitive files before job start, at every 5-second checkpoint before committing, and after job completion:
  Hashes the worktree `.git` pointer file, `.gitattributes`, `.gitmodules`, and all files in `.agents/`.
- **Needs Review Trigger:** Any tampering with `.git*` or `.agents/`, or the introduction of `.agents/hooks.json`, `.agents/plugins/`, or `.agents/skills*`, immediately triggers state `needs_review`: the job process is halted, unreviewed changes are NOT committed, and the audit diff is reported.
- **Preflight Check:** If the user's source repository already tracks `.agents/hooks.json` or `.agents/plugins/`, the job is refused pre-launch unless configured with `trust_agents_dir=true`.

### D6: Sensitive File Staging Blacklist
- The wrapper never stages or commits files matching the following case-insensitive patterns:
  `.env*`, `*.pem`, `*.key`, `id_rsa*`, `*secret*`, `*credential*`, `*.p12`, `.npmrc`, `.pypirc`, and any file located under `.agents/` or `.git*`.
- Matches are left unstaged in the worktree and reported in tool outputs under `skipped_files`.

### D7: Sanitized Execution Environment
- The environment provided to `agy` is strictly isolated using an allowlist:
  `HOME`, `PATH`, `LANG=C.UTF-8`, `TMPDIR=<jobdir>/tmp` (created clean before launch and destroyed upon termination), and the D4 git hardening variables.
- All orchestration variables, API keys, and parent shell exports are excluded (`env -i`).

### D8: Verification and Automated Fix Loop
- **Wrapper Verification:** The wrapper executes the optional `verify` command in `<jobdir>/work` using the same restricted environment (shlex-split argument list, no shell, 15-minute timeout).
- **Fix Loop:** If verification fails, the wrapper resumes the `agy` session using `agy --conversation <id>` with a prompt containing the truncated failure output (up to 8 KB), repeated up to `auto_fix_attempts` (default 2) times.
- **No In-Agent Tests:** `agy` never runs test commands directly in v1.

### D9: Standing Prompt (Preamble)
- Prepend `src/agy_mcp/preamble.md` to every submitted prompt:
  1. Work strictly within the current working directory; never read or write outside it.
  2. Never create or edit `.git*`, `.agents/`, or system settings files.
  3. Never invoke the `ask_question` tool.
  4. Write a step-by-step checklist in `.agy/PLAN.md` and re-read it before each step.
  5. After completing each step, write a concise commit message to `.agy/COMMIT_MSG` and proceed.
  6. You cannot run test suites or modifying git commands.
  7. If an irreversible or costly decision is required, write `.agy/COMMIT_MSG` for completed work, then make the last line of your final response exactly: `NEEDS_DECISION: <question> | <options> | <recommendation>`.
  8. Document reversible choices under Assumptions in `.agy/PLAN.md`.
  9. The final response must list completed tasks, assumptions, and open issues.

### D10: Result Parsing and Review Tool
- **Exit Code Semantics:** Headless `agy` exits 0 for success, denials, and timeouts. Status is determined by JSON payload (`status`, `denied_actions`) and stderr markers (`jetski: no output produced`, `[agy] print timeout after`).
- **New State:** Introduce `needs_review` when audit violations occur.
- **New Tool:** Introduce `agy_review(job_id)` returning diff statistics, audit findings, skipped sensitive files, and commit logs.
- **No Upstream Changes:** The wrapper never merges or pushes branches.

### D11: Honest README Disclosure & Terms of Service
- The project `README.md` must feature a clear "What agy can do on your machine" table summarizing empirical probe findings (filesystem reads, workspace and `/tmp` writes, safe commands whitelist, web access, gated tools).
- Explicitly note that users are responsible for complying with Google's Antigravity Terms of Service.

### D12: Baseline Retention & Open Items
- v2 supersedes v1. The only retained requirements are the ones restated in `doc/design/spec.md`; anything not restated is out of scope. Changes from v1 that are explicitly new: `needs_review`, `needs_decision` as a parsed state, `agy_review`, the audit, the `Runner` interface, `trust_agents_dir`, and the resume-state restrictions.

---

## v3 Decisions (Applying Independent Review F01–F36)

### D13: Threat Model Honesty & Host Path Snapshot (F01)
- *Rationale:* Whitelisted commands with free-form arguments (`git diff --output=<path>`) can read/write host files; v1 is branch isolation and detector auditing, not a sandbox.
- *Rule:* The wrapper takes an mtime+sha256 snapshot of sensitive host paths (`~/.ssh/*`, `~/.gnupg/*`, `~/.bashrc`, `~/.profile`, `~/.zshrc`, `~/.gitconfig`, `~/.netrc`, `~/.config/agy-mcp/config.toml`, and the source repo's `.git/config`) before launch and after completion. Any mutation trips `needs_review(reason=host_path_modified)`.

### D14: Audit Checkpoint Hard-Gating & Pre-Launch Execution (F02)
- *Rationale:* Lifecycle hooks execute automatically on session start; audit must run before initial launch and before every relaunch.
- *Rule:* Audit is a mandatory pre-launch hard gate for `agy_start`, `agy_resume`, and fix turns, runs on an independent 5s timer during execution, and runs post-completion.

### D15: Pre-provisioning `.agy/` and `PLAN.md` (F03)
- *Rationale:* `agy` cannot execute `mkdir` under accept-edits mode; if `.agy/` is absent, write tools could fail or behave inconsistently.
- *Rule:* Wrapper creates `<jobdir>/work/.agy/` (mode 0700) and seeds `.agy/PLAN.md` before launching `agy`.

### D16: Pathspec Exclusion for `.agy/` (F04)
- *Rationale:* Mutating `.git/info/exclude` in the common dir alters the user's primary repo and is vulnerable to `.gitignore` precedence.
- *Rule:* Wrapper excludes `.agy/` using git pathspec `: (exclude,top).agy` on `git add` and Python status filtering; never touches `.git/info/exclude`.

### D17: Audit Precedence over Safety-Net Commit (F05)
- *Rationale:* Uncommitted changes must never be committed if an audit violation occurred.
- *Rule:* Safety-net commit only executes if the audit passed and state is not `needs_review`.

### D18: Crash Recovery & Persistent Process State (F06)
- *Rationale:* Long-running jobs must survive dsh/MCP server restarts without becoming untracked orphans.
- *Rule:* Wrapper logs stdout/stderr to files, writes `job.json` atomically with PID and process start time from `/proc/<pid>/stat`, and scans on startup to reattach or reconcile unmanaged jobs.

### D19: Timed Checkpoint Commits (F07)
- *Rationale:* If the agent neglects to write `.agy/COMMIT_MSG`, hours of autonomous work could be lost on interruption.
- *Rule:* The watcher executes a checkpoint commit every `checkpoint_commit_minutes` (default 10) if the worktree is dirty, and immediately before process cancellation or termination (unless audit failed).

### D20: Sensitive Path Blacklist Precision & Partial Commit Warning (F08)
- *Rationale:* Over-broad globs like `.git*` or `.env*` inadvertently block `.gitignore` or `.env.example`, while omitting real credentials.
- *Rule:* Explicit whitelist/blacklist in Python; if a modified tracked file is excluded, mark `partial_commit=true` and transition to `needs_review`.

### D21: Refined Audit Fatal vs Blocked Set (F09)
- *Rationale:* Non-executable files like `.gitignore` should not trigger fatal process termination, while resolved git configuration must be monitored.
- *Rule:* Fatal = worktree `.git` pointer, `.agents/**` manifests/plugins/skills/hooks, and resolved `git config --local --list` diff. Blocked-not-fatal = `.gitignore`, `.gitattributes`, `.gitmodules`.

### D22: Git Index Lock Retry Protocol (F10)
- *Rationale:* Concurrent execution of `git status` by `agy` and `git add` by the watcher can cause intermittent `index.lock` collisions.
- *Rule:* Wrapper wraps index-modifying git operations in an exponential backoff retry loop (10 attempts, up to 5s).

### D23: Process Group Supervisor & Wall-Clock Budget (F11)
- *Rationale:* CLI flags like `--print-timeout` do not protect against hung gRPC channels, and killing parent PID leaves orphaned subprocesses.
- *Rule:* Wrapper enforces a wall-clock deadline (`min(max_minutes, max_minutes_cap)*60 + 120`) and terminates the entire process group via `os.killpg`.

### D24: COMMIT_MSG Settle Window, Validation, and Consume Step (F12)
- *Rationale:* Polling raw files without settle windows risks committing partial writes or looping on identical content.
- *Rule:* Watcher waits for size/mtime stability across 2 ticks (>=5s), validates subject line <=72 chars, and renames file to `.agy/COMMIT_MSG.seen.<n>` upon committing.

### D25: Denied Actions Do Not Force Terminal Denied State (F13)
- *Rationale:* Common non-whitelisted commands (like `pwd`) trigger tool denials but should not abort an otherwise successful job.
- *Rule:* Denials record warnings; if progress was committed, state is `done`. If zero progress occurred, state is `needs_review(reason=blocked_by_permission)`.

### D26: Last-Line Parsing for NEEDS_DECISION (F14)
- *Rationale:* Preamble asks model to provide a summary first and place decision marker at the end; regex must match last non-empty line.
- *Rule:* Parse `^NEEDS_DECISION:\s*(.*)$` on the last non-empty line with ` | ` separator.

### D27: Absolute Paths for Arguments Without Shell (F15)
- *Rationale:* In `execve` without a shell, tildes (`~`) are not expanded and create literal directories.
- *Rule:* Wrapper resolves all CLI arguments (including `--log-file`) to absolute paths.

### D28: Resumption Guarding & Context Rigor (F16)
- *Rationale:* Resuming a conversation must preserve all budget, model, and isolation constraints.
- *Rule:* `agy_resume` enforces pre-launch audit, reattaches watcher, verifies worktree cwd, and blocks duplicate concurrent resumes.

### D29: State Machine Formalization & Recovery Matrix (F17)
- *Rationale:* Disjoint state transitions leave failure modes unclassifiable.
- *Rule:* Formal transition table; security violations in `needs_review` are terminal; non-security reviews (`verification_failed`, `blocked_by_permission`, `invalid_commit_message`) can be resumed via `agy_resume`.

### D30: Resilient Result Parsing Precedence (F18)
- *Rationale:* Signals, aborts, and malformed outputs must be mapped deterministically.
- *Rule:* Parse last JSON object line with strict precedence: cancellation -> audit flag -> negative return code -> non-zero code -> timeout marker -> NEEDS_DECISION -> denials -> SUCCESS.

### D31: Uniform Tool Contract Shapes (F19)
- *Rationale:* Orchestrator LLMs require consistent JSON envelopes across all tool responses.
- *Rule:* All tools return standardized base fields plus tool-specific payloads with explicit byte caps; `agy_start` reports `queued` during setup.

### D32: Realistic Liveness Feedback via cli.log & git log (F20)
- *Rationale:* Headless JSON mode produces no stdout until process exit.
- *Rule:* `agy_status` reports liveness via `cli.log` tail, worktree `git log`, and dirty state; does not pretend model stdout streams in JSON mode.

### D33: Resolved Path & Workdir Precedence (`repo_path`) (F21)
- *Rationale:* Lexical path checks allow symlink traversal and nested worktree bugs.
- *Rule:* Parameter renamed to `repo_path`; resolved via `Path.resolve(strict=True)`; verifies `.git` is a directory and not inside state dir.

### D34: Relaxed Precondition for Dirty Source Repos (F22)
- *Rationale:* Uncommitted files in user's repo are not copied to worktree; requiring clean tree blocks normal development for no benefit.
- *Rule:* `require_clean_source=false` by default; records `source_dirty=true` and warns rather than refusing.

### D35: Worktree Creation Sanitation & Collision Handling (F23)
- *Rationale:* Arbitrary title strings cause invalid git branch refnames.
- *Rule:* Sanitize slug (`[a-z0-9-]`), verify `HEAD` is resolvable, handle branch collisions, and enforce `min_free_gb=2`.

### D36: Explicit Worktree & Disk Lifecycle Management (`agy_cleanup`) (F24)
- *Rationale:* Worktrees and branches accumulate indefinitely in the user's repository metadata.
- *Rule:* Add `agy_cleanup(job_id, remove_worktree, delete_branch)` with safety checks, disk usage reporting, and retention policies.

### D37: Filesystem Permissions & Secret Persistence Warnings (F25)
- *Rationale:* Multi-user workstations can expose prompt secrets if state directories are world-readable.
- *Rule:* State directories 0700, job files 0600; README explicitly warns that prompts persist in `~/.gemini/antigravity-cli/`.

### D38: Inspection of Pre-existing Dangerous Rules (F26)
- *Rationale:* Existing user settings could grant dangerous permissions (`command(bash)`) unbeknownst to the wrapper.
- *Rule:* Preflight reads effective settings and flags `permission_drift` / warnings if dangerous rules exist.

### D39: Explicit Delimitation of Untrusted Job Text (F27)
- *Rationale:* Text generated by `agy` or repository files could execute prompt injection against the orchestrator.
- *Rule:* All job-derived text is tagged as untrusted data with strict byte limits (64 KB response, 8 KB verify output).

### D40: Strict Allowlist for `verify` Command (F28)
- *Rationale:* `verify` runs code on the host; an unrestricted command allows prompt-injected RCE.
- *Rule:* `verify` must match `verify_allowlist` (default `["pytest", "ruff", "npm test", "cargo test", "go test"]`); otherwise rejected with `verify_not_allowed`.

### D41: Complete `.agents/` Coverage & Instruction File Auditing (F29)
- *Rationale:* Untracked or added instruction files (`AGENTS.md`, `GEMINI.md`) persist prompt injections across resumes.
- *Rule:* Audit monitors instruction files and all manifests (`skills.json`, `plugins.json`); edits trigger `needs_review(reason=instruction_file_modified)`.

### D42: Empirical Confidence Column in Capability Table (F30)
- *Rationale:* Distinguish empirically tested boundaries from policy declarations.
- *Rule:* README table explicitly labels permissions as tested vs policy-gated untested.

### D43: Accurate Rationale for Banned Flags (F31)
- *Rationale:* Explanations should reflect actual mechanism: `--sandbox` falls back silently; `--effort` conflicts with model name; `-c` resumes wrong session.
- *Rule:* Document exact failure modes rather than generic claims.

### D44: Explicit Multi-Worktree Concurrency Rules (F32)
- *Rationale:* Same-workdir concurrency is prevented by worktrees, but shared object databases require coordination.
- *Rule:* Allow multiple jobs on one source repository via independent worktrees; index locks handled via backoff retry.

### D45: Documented User Merge Workflow ("Getting the result") (F33)
- *Rationale:* Users need clear instructions on how to inspect, diff, and merge completed branches.
- *Rule:* Specify exact git commands (`git log`, `git diff`, `git merge --no-ff`) and report `merge_base`, `ahead`, `behind` in `agy_review`.

### D46: Append-Only Security Event Log (`audit.jsonl`) (F35)
- *Rationale:* Security events must survive server crashes and provide an unalterable audit trail.
- *Rule:* Append all state transitions and audit events to `<jobdir>/audit.jsonl` with fsync.

### D47: Resource Budgeting & Cap Semantics (F36)
- *Rationale:* Resumed jobs could circumvent per-job duration caps without cumulative tracking.
- *Rule:* Track cumulative wall-clock budget across resumes; serialize daily job counts under file lock.

### D48: Automatic One-Shot Recovery on Shell Denial with Zero Commits
- *Rationale:* In headless print mode, agy halts immediately on unapproved shell commands (RunCommand). Complex coding tasks without progress can often succeed if nudged to use built-in file tools instead of shell tools.
- *Rule:* When a job halts in needs_review(blocked_by_permission) with zero commits and auto_resume_on_denial (0-3, default 1) attempts remain, the manager automatically resumes the same conversation with trusted wrapper guidance instructing agy to use file tools only or output NEEDS_DECISION. Audit event `denial_auto_resume` is recorded with attempt number. Wall-clock time counts against cumulative budget.

### D49: No False Success on Denied Turns Without Progress
- *Rationale:* Real agy runs in headless mode may spawn subagents or issue view/shell requests that get soft-denied, write a generic plan/commit message, and exit 0 with status SUCCESS while achieving zero repository changes. Reporting `done` under these conditions misleads the user and orchestrator into believing a task succeeded when no work was actually performed.
- *Rule:* When agy exits 0 with status SUCCESS but zero commits AND zero modified files AND at least one tool denial occurred (captured in `denied_actions` or `cli.log`), the wrapper assigns state `needs_review` with review reason `denied_without_progress`. It triggers automatic recovery via `auto_resume_on_denial` identically to `blocked_by_permission`. Zero commits without denials yields state `done` with warning `no_commits`. Denials with progress yield state `done` with warning `denied_actions`.

### D50: Transcript Reader Confinement and Scope Safety
- *Rationale:* While `cli.log` logs tool confirmation soft-denials with step numbers, it omits the requested shell commands and file paths. These details exist only in agy's internal session transcript (`<agy_home>/brain/<id>/.system_generated/logs/transcript.jsonl`). Accessing these transcripts requires strict safety boundaries to prevent host traversal or scope creep.
- *Rule:* The wrapper introduces `src/agy_mcp/transcript.py` with read-only access confined strictly to `<agy_home>/brain/<conversation_id>/.system_generated/logs/transcript.jsonl`. Path components must not contain `..` or symlinks, `conversation_id` must match a strict UUID regex, and file size is capped at 5 MB (tail-reading the last 2 MB). All extracted strings are treated as untrusted and wrapped with `wrap_untrusted_text`. No other module in `src/` may read `.gemini` paths.

### D51: Three Execution Modes Architecture (Protected, Sandbox, Unrestricted)
- *Rationale:* Different project environments require varying balances of security confinement and agent capability. Future evolution must support safe containment while remaining honest about wrapper capabilities.
- *Rule:* Plan three execution modes per project: `protected` (accept-edits with host worktree isolation and soft-denial blocking), `sandbox` (bwrap unprivileged container namespaces for filesystem and process isolation), and `unrestricted` (bypass permission checks for autonomous execution with post-hoc host snapshot detective monitoring and backup safeguards). The wrapper honestly discloses that it cannot intercept in-process agent commands in unrestricted mode.

### D52: Append-Tolerant Host Audit Rule for `known_hosts`
- *Rationale:* Legitimate developer SSH/Git activity on the host machine may modify `~/.ssh/known_hosts` during long-running background jobs, appending new host keys. Treating any mtime or size change to `known_hosts` as a fatal security violation kills legitimate jobs with `host_path_modified`.
- *Rule:* Host paths are classified into `strict` and `append_tolerant`. Strict paths (shell rc/profile files, `~/.ssh/authorized_keys`, `~/.ssh/config`, private keys, repository git configs) preserve strict fatal behavior on any modification. Append-tolerant paths (`~/.ssh/known_hosts` and `~/.ssh/known_hosts.old`) record `(size, sha256)` of content (capped at 1 MB) at snapshot time. On change, if the new file is a pure append of the old content (old_size <= new_size and initial old_size bytes match old sha256) or the file is newly created, the audit emits `host_path_changed_benign` ({`path`, `added_bytes`, `new_lines_count`}) and job execution continues with warning `known_hosts_changed`. If earlier bytes changed, the file shrank, was deleted, or exceeds 1 MB, the violation remains fatal (`host_path_modified`). Key material and hostnames are never logged.

### D53: Verification Loop Prompt Guidance and Interpreter Command Allowlist
- *Rationale:* Autonomous agents may attempt to run test commands directly, triggering unhandled permission blocks or bypassing wrapper-managed verification and git commit pipelines. Furthermore, pytest inside isolated venvs requires executing via absolute interpreter paths or sub-tokens (e.g. `["/abs/path/python", "-m", "pytest"]`) that standard single-word binary allowlists reject.
- *Rule:*
  1. Prompt Guidance: `agy_start` docstring and preamble rule 6 explicitly state: agy must NOT run tests; "the wrapper runs the verify command after your work and will send you the results; fix what fails".
  2. Failure Resume Guidance: On verify failure, `format_fix_prompt` attaches the first 2000 bytes of the failure tail wrapped with `<untrusted_text>`, lists failing test names if parseable, and includes exact instruction: "Change code, not tests, unless the test itself is wrong (and then say so in the commit message)."
  3. Interpreter Allowlist: `verify_allowlist` accepts token lists or strings matching prefix commands without invoking shell, allowing specific interpreter-based commands like `["/path/to/venv/bin/python", "-m", "pytest"]` safely.

### D54: Build Junk Exclusion, Verify Observability, and Non-Fatal `known_hosts.old`
- *Rationale:*
  1. Build artifacts (`__pycache__/`, `*.pyc`, `*.pyo`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.tox/`, `*.egg-info/`) generated by verification runners or language toolchains must never be staged into git commits or safety-net commits, nor should runs that generate only build junk produce vacuous commits.
  2. Verify command execution must be first-class observable in job telemetry: envelopes (`verify` field in `agy_status`/`agy_result`/`agy_review`), audit trails (`verify_started` and `verify_finished` events), and next_actions guidance without dumping verbose logs into audit trails.
  3. SSH clients routinely rotate or rewrite `~/.ssh/known_hosts.old` when updating `known_hosts`. Treating changes to `.old` backup files as fatal security violations causes false-positive job terminations.
- *Rule:*
  1. Junk Exclusion: A default junk exclusion filter (`__pycache__/`, `*.pyc`, `*.pyo`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.tox/`, `*.egg-info/`) is applied to all staging paths across both git pathspec excludes and python filtering. Filtered junk files are reported in `skipped_files` with reason `build_artifact` (capped at 50 entries). Pre-existing tracked junk is never deleted or rewritten from history. Safety-net commit skips committing if all remaining uncommitted files are build artifacts, logging audit event `safety_net_skipped_only_artifacts`. Verification runner environments inject `PYTHONDONTWRITEBYTECODE=1` and `PYTEST_ADDOPTS="-p no:cacheprovider"`.
  2. Verify Observability: The standard envelope for `agy_status`, `agy_result`, and `agy_review` includes a `verify` sub-envelope: `{requested: bool, ran: bool, passed: bool|null, rc: int|null, duration_seconds: float|null, attempts: int, last_tail: untrusted-wrapped, capped 2 KB}`. When requested but not run, `ran=false` with `reason` is included. Audit log records `verify_started` (`command` argv list, `cwd` basename only) and `verify_finished` (`rc`, `duration_seconds`, `timed_out`, `attempt`, `tail_bytes`, `passed`). When verification fails, `next_actions` lists verification failure first.
  3. Non-Fatal `known_hosts.old`: `~/.ssh/known_hosts.old` is classified as `warn_only`: any change (replacement, rewrite, appearance, deletion) emits `host_path_changed_benign` with reason `backup_file` and warning `known_hosts_old_changed`, never triggering a fatal `host_path_modified` violation.
  4. Warnings Sanitization: `dangerous_permission_rules_present` warning is emitted only when `dangerous_allow_rules` is non-empty, formatting rule names rather than raw dict representation. `source_dirty=true` is emitted only when the source repository has true uncommitted modifications.

### D55: Process-Control and State-Integrity Hardening (Batch 1 Findings F1, F3, F4, F11, F12, F13, F15, F20, F21, F23)
- *Rationale:* The independent review (`docs/DS_REVIEW_2.md`) identified critical gaps in process group supervision, concurrency cap atomicity, job cancellation guarantees, commit serialization, and recovery state integrity.
- *Rule:*
  1. Supervisor Finalization on Watcher Transition (F1): Whenever a job transitions out of `running` (such as watcher-initiated `needs_review` on `partial_commit` or `invalid_commit_message`), the supervisor finalizes the process group: attempts a pre-kill checkpoint of safe dirty work, persists `conversation_id`, sends SIGTERM then SIGKILL to the process group, cleans up handles, logs `process_terminated`, and preserves the watcher's assigned state. No path may leave an active agy process with a non-running job.
  2. Cancellation Guarantees Across Verify (F3): `agy_cancel` transitions the job to `cancelled`, terminates any running verify subprocess group via `os.killpg`, waits for finalization (bounded), and re-checks the cancellation flag post-verify. A cancelled job ends `cancelled`, executes at most one pre-kill checkpoint, and never starts auto-fix resume.
  3. Resume Serialization & Setup Cleanup (F4): Resumes and launches are serialized via per-job locks and guarded transitions (`starting`/`resuming`). Concurrent resume calls return `invalid_state_for_resume`. If an exception occurs after a process group is spawned, the process group is terminated prior to worktree/branch cleanup.
  4. Detached Grandchildren Limits & Unconditional PGID Kill (F11): `kill` unconditionally signals the process group (`os.killpg`) even if the group leader has exited, avoiding ESRCH early returns. Setsid/double-fork detached grandchildren are documented as an accepted limitation in SPEC section 5.
  5. Queued Cancellation & Force Cleanup (F12): `queued -> cancelled` is a legal transition in the state machine table. Provisioning threads abort without launching. Force cleanup on a running job cancels and finalizes the job prior to filesystem removal.
  6. Flock-Protected Concurrency Cap (F13): `count_active_jobs` counts `queued`, `starting`, and `running` jobs under a cross-process flock (`concurrency.lock`). Job slot checks and creation are atomic. Resumes also respect the cap without double-counting held slots.
  7. Commit SHA Accounting (F15): `commits_count` is incremented and `head_commit` updated only when `checkpoint_commit` returns a non-null commit SHA.
  8. Auto-Fix Prompt Single Build & Counter Persistence (F20): Auto-fix prompts are built once (trusted guidance outside, untrusted failure tail inside). Attempt counters (`auto_fix_count`, `denial_resume_count`) are persisted in `job.json`. A failed auto-resume exhausts attempts immediately so `wait()` does not stall.
  9. Worktree Commit Lock Serialization (F21): All git commit operations (watcher commits, periodic checkpoints, pre-kill checkpoints, safety-net commits) are serialized under a per-job `commit_lock`. Git errors during cancel do not convert `cancelled` to `failed`.
  10. Safe index.lock Deletion (F23): Deleting stale `index.lock` requires a verifiable liveness callback confirming no agent process is running. Unknown liveness is treated as alive (refusing deletion).

### D56: Fail-Closed Principle Across Audit, GitOps, Verify, and Server Configuration (Batch 2 Findings F2, F5, F6, F7, F9, F10, F17, F18, F25, Extra Item A)
- *Rationale:* The second independent review (`docs/DS_REVIEW_2.md`, Batch 2) identified critical fail-open vulnerabilities where unreadable baselines, unhardened git status calls, verify arguments, envelope fields, and invalid configuration silently defaulted to permissive states.
- *Rule:*
  1. F2 Audit Baseline Fail-Closed: Review and crash recovery audit against the persisted pre-launch `snapshot.json`. If `snapshot.json` is missing or corrupt, audit fails closed to `needs_review(reason=audit_violation)` and aborts recovery.
  2. F5 Envelope Untrusted Wrapping & Capping: Untrusted envelope fields (`diff_stat`, `commits[].subject`, CLI `denied_commands` lines, and audit event detail strings) are wrapped in `<untrusted_text>` tags with tag overhead deducted from byte caps.
  3. F6 Hardened Fail-Closed Git Status: All `git status` calls use `run_git` with sanitized environment and 30s timeout; non-zero exits abort staging, reset index changes to prevent secret leakage, and raise `GitError`.
  4. F7 Staging Deny-List Hardening: Staging deny-list includes `AGENTS.md`, `GEMINI.md`, and `.agents/**`, and removes blanket `.github/` exemptions.
  5. F9 Verification Command Argument Restrictions: Verify commands reject dangerous flags (`-c`, `-p`, `-o`, `--rootdir`, etc.) and paths outside the workspace, revalidating on resume.
  6. F10 Test & Build Junk Exclusions: Staging exclusions include test and coverage artifacts (`.coverage`, `htmlcov/**`, `junit.xml`, `coverage.xml`, `.hypothesis/**`, `.nox/**`, `target/**`, `node_modules/**`).
  7. F17 Redacted Audit Verify Tail: Verify failure tails in `audit.jsonl` are redacted with `redact_log_text` and capped at 2 KB; full output is preserved in `<jobdir>/verify_tail.txt`.
  8. F18 Populated Skipped Files: `job.skipped_files` is populated from `StagingResult` with path and reason across watcher, checkpoint, and safety-net commit paths, and returned in envelopes.
  9. F25 Fail-Closed Configuration: Server startup refuses to start on `ConfigError` (invalid TOML, unreadable file, or validation failure) with exit code 1, never silently continuing on defaults.
  10. Extra Item A Policy File Continuation: Edits to `.gitignore`, `.gitattributes`, and `.gitmodules` are blocked from staging and recorded in `skipped_files`, but do not terminate the running job; at completion, the job transitions to `needs_review(reason=policy_file_modified)` with `partial_commit=true`.

### D57: Hardened Follow-up Safeguards Across Supervision, Verify, Configuration, and Test Determinism (Batch 2b)
- *Rationale:* Follow-up audit and edge-case testing after Batch 2 identified gaps in tick-error audit exception handling, overly broad verify flag restrictions breaking standard cargo and go workflows, configuration startup guarantees, and test suite race conditions.
- *Rule:*
  1. Item 1 (Tick-Error Audit Fail-Closed): If `audit()` raises while a baseline snapshot exists during a supervisor tick error, log `audit_error_during_tick_error` (with exception class and redacted message), terminate the process group, and transition to terminal `needs_review(reason=audit_unavailable)` (never `done`, unresumable).
  2. Item 2 (Per-Tool Verify Flag Restrictions): Verify flag restrictions are applied per tool: `pytest` retains all existing rejections (`-c`, `-p`, `-o`, `--rootdir`, `--confcutdir`, etc.); `ruff` rejects `--config` and outside paths; `cargo` allows `-p`/`--package`, `--workspace`, `--release`, etc., rejecting `--config`, `-Z`, and outside `--manifest-path`/`--target-dir`; `go` allows `-p`, `-run`, `-v`, etc., rejecting `-exec`, `-toolexec`, `-overlay`, `-modfile`, `-C`; `npm` allows `npm test` only with no extra arguments.
  3. Item 3 (Missing Configuration Startup): A missing `config.toml` file on first run starts cleanly with default configuration settings; only an existing-but-invalid or corrupted config file fails closed with `ConfigError`.
  4. Item 4 (Test Determinism & Signaling): Fixed settle windows and arbitrary sleep intervals in test scenarios are replaced with deterministic polling predicates and watcher synchronization (`poll_until`, `settle_window=0.0`, `poll_interval=0.05`, and `.agy/COMMIT_MSG.done.<n>` signaling).

### D58: Operational Reliability, Envelope Transparency, Resource Enforcement, and Drift Alignment (Batch 3a Findings F8, F14, F16, F19, F22, F26, F27, F28)
- *Rationale:* The operational review (`docs/DS_REVIEW_2.md`, Batch 3a) identified failure information loss, crash recovery staleness, unbounded streaming memory risks, daily cap timing/timezone inaccuracies, missing resource and disk budget enforcement, path leakage in error envelopes, dead code accumulation, and specification drifts.
- *Rule:*
  1. F8 Structured Failure Envelope: Failed jobs preserve a structured error dictionary (`failure_reason`, `message`, `code`, and a redacted, untrusted-wrapped, 1 KB-capped `error_tail`) so the orchestrator can see why execution failed.
  2. F14 Crash Recovery Freshness: Startup recovery checks process launch wall-clock time and seeks to `stdout_offset`, failing closed to terminal `needs_review(reason=unconfirmed_recovery)` when stdout freshness cannot be established.
  3. F16 Bounded Streaming Reads: All dynamic file reads (`cli.log`, `audit.jsonl`, `job.json`, `snapshot.json`, `.agy/COMMIT_MSG`, and verify outputs) enforce explicit byte caps (10 MB stream cap, 64 KB message, 1 MB job, 2 MB snapshot) and truncate with an explicit marker instead of exhausting memory. The 10 MB stream tail cap in `supervisor.py` is deliberate so large result payloads fit.
  4. F19 Local Timezone Daily Cap: Daily quotas are tracked according to a configurable `timezone` (defaulting to local system timezone) and incremented only after prompt, model, verify, and repository path validation succeeds.
  5. F22 Disk Limit Enforcement & Trash Retention: `min_free_gb` and `max_state_disk_gb` are checked before job execution; terminal retention cleanup moves aged directories to `<state_dir>/trash/<job_id>_<ts>` while strictly protecting unmerged branch commits; `record_usage` is actively emitted on all job completions to `usage.jsonl`.
  6. F26 Absolute Path Sanitization: Envelopes, audit findings, and error messages returned to orchestrators redact host home directories (`~`) and worktree/state paths, logging raw paths locally.
  7. F27 Dead Code Removal: Unused helpers (`verify.VerifyError`, `worktree.sanitize_slug`, `watcher.CommitWatcher.check_once`) and duplicated `files_modified` recovery logic in `manager.status` are deleted.
  8. F28 Specification & Precedence Alignment: Parsing follows SPEC section 15: supervisor timeout (step 3) strictly precedes negative signals (step 4) and CLI errors (step 5), which in turn precede print-timeout detection (step 6); `agy_wait` timeout defaults to 30s (clamped to 300s); SPEC explicitly documents `verify_failed`/`verify_timeout` and local calendar date daily capping.

### D59: Usage Awareness, Quota/Auth Guards, Per-Job Cost, and Doc Alignment (Batch 3b Items 1-8)
- *Rationale:* Orchestrators could not decide whether a long job fits, and jobs could not stop cleanly when quota ran out or the agy login expired. Batch 3b adds a read-only usage surface, launch guards, per-job cost accounting, and interruption diagnosis, while correcting two documentation drifts.
- *Rule:*
  1. Item 1 Usage Parser: `usage.parse_usage_output`/`parse_usage_payload` parse `command.data.groups[].buckets[]` as primary and cross-check the tab-separated `response` lines (group, Weekly/Five Hour Limit Remaining, percent, reset UTC); output is `groups[{name, buckets[{window, remaining_fraction, reset_time_utc, resets_in_seconds}]}]`; any unexpected payload fails soft to `{available: false, reason: "unrecognized_format"}` and never raises.
  2. Item 2 Reading `/usage`: `usage.get_usage` runs `agy --log-file <tmp>/usage.log --output-format json -p "/usage"` in a fresh empty temporary directory with the sanitized allowlist environment and process-group kill handling (30s timeout), parses `applyAuthResult: email=..., authMethod=...` from the temporary log, masks the email as first 2 characters + `***@` + domain, deletes the temp dir, and caches the reading in memory for 60s (the email is never written to a persistent file).
  3. Item 3 `agy_usage` Tool: a read-only, parameterless MCP tool returns `available`/`reason`, `cached`, `account {masked_email, auth_method}`, and groups/buckets with per-bucket `low` flags; thresholds are `low_quota_five_hour_pct` (15) and `low_quota_weekly_pct` (10).
  4. Item 4 Start/Resume Guard: `usage_guard` = `off|warn|block` (default `warn`); `gemini*` maps to "Gemini Models" and `claude*`/`gpt*` to "Claude and GPT models"; below-threshold warn adds `low_quota` to the envelope while block raises `error.code=low_quota` with reset times; unavailable usage (including an expired login) only adds `usage_unavailable` and never blocks.
  5. Item 5 Per-Job Usage Cost: fresh readings at launch and finalization produce `usage_cost {five_hour_pct_points, weekly_pct_points, measured, concurrent_jobs, note}` in `job.json`, `usage.jsonl`, and the envelope; more than one concurrent job is explicitly marked approximate; unavailable readings set `measured=false` and are never an error.
  6. Item 6 Quota-Exhausted Detection: after a failed, timeout, or empty-response job, a fresh reading with a relevant 0% bucket sets `needs_review(reason=quota_exhausted)`, keeps the job resumable, sets `resume_after` to the latest exhausted bucket reset, and next_actions says to wait until then; `quota_error_patterns` may match stderr and cli.log tail, but the pattern list is UNVERIFIED against real agy and the `/usage` reading is the reliable signal.
  7. Item 7 Login-Expired Detection: `auth_error_patterns` matched against stderr, cli.log tail, and `/usage` output (including an `/usage` `auth_required` reason) set `needs_review(reason=auth_required)` and next_actions says to ask the human to log in to agy again, then resume; auth takes precedence over quota and the patterns are UNVERIFIED against real agy.
  8. Item 8 Doc Alignment: D58 item 8 now matches SPEC section 15 (supervisor timeout step 3 precedes negative signal step 4 and CLI error step 5, which precede print timeout step 6), and the stream tail cap is documented as the code's 10 MB (deliberate to fit large results) in both SPEC and D58.

### D60: Python Wrapper, Rust Reserved for the Host-Side Bridge/Tester
- *Rationale:* The wrapper's cost is dominated by waiting on agy, disk I/O and subprocess handling, not by CPU or memory work; Python's ecosystem makes the MCP, git and supervision logic cheaper to change and test. A future host-side bridge/tester has different constraints: it should ship as one static binary and needs memory safety.
- *Rule:* The wrapper stays in Python because it is I/O-bound on agy runtime; Rust is reserved for the future host-side bridge/tester where a single static binary and memory safety matter.


### D61: Hardening Batch — Checkpoint Config, Review Fail-Closed, Usage Follow-Ups, Dead Code, Cleanup Visibility, Doc Drift
- *Rationale:* The organization audit (A3, A5, A10, A12) and the usage review found a documented-but-dead checkpoint config, a security review that could look clean when the audit raised, five silent cleanup failures, verified dead code, and SPEC text that described behavior the code never had. Fixing them test-first keeps the contracts and the code aligned without widening scope.
- *Rule:*
  1. Item 1 A3: `Config.checkpoint_commit_minutes` is passed as seconds from `JobManager` through `JobSupervisor` into `CommitWatcher` on start, resume and reconcile; the default 10 still yields the historical 600 s interval, and config validation still rejects values below 1.
  2. Item 2 A5 review: if the audit or the git block in `JobManager.review` raises, the response contains a fatal `audit_unavailable` finding whose detail is only the short code, and `next_actions` starts with "a human must review"; a raising audit is never presented as a clean review.
  3. Item 3 usage follow-ups: `_kill_usage_process` always SIGKILLs the process group after SIGTERM and the wait; only `available=true` readings are cached; an available reading with no determinable model group adds `usage_unmeasurable` and never blocks (all modes except `off`); `/usage` stdout, stderr and the temp log read are capped at 1 MB each with reason `output_too_large`; a local part of two characters or fewer is fully masked as `***@domain` while longer local parts keep their first two characters.
  4. Item 4 A10 dead code: `gitops.run_hardened_git`, `runner.read_proc_pgid` and the unreachable tail after the `return` in `JobManager.resume` are deleted after grep verification; tests-only helpers stay.
  5. Item 5 A5 subset: the five agreed cleanup swallows in `manager._provision_and_launch`, `supervisor.run_loop`, `supervisor._try_pre_kill_checkpoint` and the supervisor safety-net commit now log and append a `cleanup_error` audit event with a short code only (no exception text, no host paths) while keeping control flow and state outcomes identical; the other A5 rows remain deferred.
  6. Item 6 docs: SPEC lock order now matches the real `resume` order (job lock before concurrency flock, with a documentation-only note that no path reverses it), the `use_cache=False` comment explains the short-job zero-cost bug, the `--print-timeout` 120 s grace comment is kept because the wrapper does pass that flag, and the A12 SPEC drift is corrected (`starting` state removed, `max_minutes` naming, no `audit_pass` event, first invalid `COMMIT_MSG` is discarded without logging, 1 s tick/5 s settle wording, envelope fields `denied_commands`/`denied_tools`/`verify`).

### D62: Job Scratchpad, Plan Accounting, Fact/Claim Separation, and A4 Provisioning (Batch 4)
- *Rationale:* Jobs forgot steps, skipped work and reported success the wrapper could not corroborate, and audit finding A4/F24 showed `.agy/` provisioning existed only in tests. Batch 4 gives every job a wrapper-seeded, untrusted-read scratchpad, makes the wrapper compute its own facts, and closes A4.
- *Rule:*
  1. Item 1 A4/F24: `JobManager._provision_and_launch` calls `worktree.provision_metadata_dir` after `create_worktree` and before `build_argv`; `<worktree>/.agy/` is mode `0700` and `.agy/` is added to the repository-local `info/exclude` (never a tracked `.gitignore`); any provisioning failure fails closed as `failed`/`setup_failed`, removes the worktree and branch, and never launches agy.
  2. Item 2 seeds: `provision_metadata_dir` (the only provisioning function) writes size-capped `.agy/PLAN.md` (numbered `- [ ]` list), `NOTES.md`, `LOG.md` and `REPORT.md` (sections `Done`, `Not done / skipped`, `Open issues`, `Files changed`, `How verified`); `agy_start` accepts an optional `checklist` of at most 30 single-line items of at most 200 characters, otherwise two default steps are seeded.
  3. Item 3 preamble: `preamble.md` tells the agent to keep notes in `.agy/NOTES.md`, tick a PLAN box only after the work is done, never tick undone work, always fill REPORT.md (including `Not done / skipped` and `Open issues`, even when `none`), and never claim tests passed because the wrapper runs verification.
  4. Item 4 plan accounting: at finalization the supervisor reads PLAN.md under the hardened reader and exposes envelope `plan {total, done, remaining}` with untrusted-wrapped, 200-character-capped titles; a would-be `done` job with unticked items gains `plan_incomplete` and, when `plan_nudge=1` (default), ends `needs_review(plan_incomplete)` and is auto-resumed once with trusted guidance naming the items; a second incomplete result stays resumable and is never nudged again; with `plan_nudge=0` the job stays `done` with only the warning.
  5. Item 5 facts vs claims: envelopes add wrapper-computed `facts` (commit count, files-modified count, verify summary, audit status, plan counts, denial counts) and `agent_report` (REPORT.md, untrusted-wrapped, 4 KB cap); `report_claims_unverified` is added when a `claim_phrases` match in REPORT.md body prose (default `tests pass`, `all tests passed`, `verified`) coexists with verify not run or not passed; Markdown heading lines are ignored so the required `How verified` heading is not a claim.
  6. Item 6 resume state block: every resume (`agy_resume`, verification auto-fix, plan nudge) prepends a wrapper-built `## Current State (wrapper-provided)` block with commits so far, the last verify result, remaining plan items and the last 2 KB of NOTES.md; agent-derived values are untrusted-wrapped, trusted headings/guidance stay outside, and the preamble is emitted exactly once.
  7. Item 7 retention and F26: finalization copies the four scratchpad files with the same hardened, size-capped reads into `<jobdir>/scratchpad/` so they survive worktree cleanup; job/envelope expose file names only, never absolute paths; symlinked, oversized or binary sources are skipped with the name-only warning `scratchpad_file_skipped:<NAME>`.
  8. Item 8 security rule: every read of an agent-written `.agy/*` file uses `O_NOFOLLOW` on the directory and file, requires a regular file whose descriptor resolves inside `<worktree>/.agy`, enforces a per-file byte cap, refuses NUL-containing content, and treats the text as untrusted data (wrapped and capped before entering any envelope or prompt).

### D63: Real-Smoke Fixes — Permission Config, Model Resolution, `python -m pytest`, Token Fields, Path Sanitization (Findings 1, 2, 3, 5, 9)
- *Rationale:* The first real-agy smoke run (`docs/reports/real-smoke-1/REPORT.md`) proved that the inspector read the wrong path, the tested model was outside the default allowlist and silently remapped, the canonical verify form was rejected, real token counters were dropped, and absolute worktree paths leaked into the response. Each fix is test-first and fail-soft where the input is host data.
- *Rule:*
  1. Finding 1 permission rules: `worktree.inspect_permission_rules(<home>)` probes `<home>/.gemini/config/config.json` first and keeps the older candidates after it; every read is `O_NOFOLLOW`, regular-file-only and capped at 1 MB; absent files are skipped and unreadable/non-regular/oversize/malformed input yields warning `permission_rules_unreadable` (plus `malformed_config_json` for bad JSON) without raising or blocking. The start envelope adds `dangerous_permission_rules_present: <count> dangerous allow rule(s)` and never echoes a rule body.
  2. Finding 2 model: the default `model_allowlist` gains `gemini-3.8-flash` (the low/medium/high variants stay); `cli_log.extract_model_resolved` parses `model alias "<X>" resolved to "<Y>"`, the job stores `model_resolved`, and every envelope plus `usage.jsonl` exposes it (`null` when absent). Usage group mapping accepts the resolved `gemini-3.8-flash-high` unchanged.
  3. Finding 3 verify: `verify.is_verify_allowed` accepts `python -m pytest ...` and `python3 -m pytest ...` (exact first/second/third tokens) for an allowlisted pytest tool under the same pytest flag/path policy; no other `-m` module, `-c`, absolute interpreter path, `.venv` path or glued `-mpytest` is implicitly accepted; rejection messages list the accepted forms.
  4. Finding 5 tokens: `models.TokenUsage` and `parse_result` carry optional `thinking_tokens`, `cache_read_tokens` and top-level `num_turns`; all three are `null` when absent and the envelope keeps the original three counters for old payloads.
  5. Finding 9 paths: `util.sanitize_absolute_paths` (applied in `models.build_result_response` before the untrusted wrapper) replaces the job worktree path with `<worktree>` and HOME with `~`, including inside `file://` URIs, without rewriting a sibling that merely shares the prefix or any other text.
  6. Findings 6-8 are deferred as low severity: benign `COMMIT_MSG` skips (`seen.*`/`done.*` files with no changes), the advisory checklist rewrite, and the unused `LOG.md`.
  7. Finding 4 auth patterns: a real healthy job logs `You are not logged into Antigravity.` before keyring auth succeeds, so `auth_error_patterns` MUST NOT contain `not logged in` or a broader match; the real expired-login signature is still unknown and needs a dedicated probe under an empty HOME; defaults stay empty and the `/usage` reading remains the reliable signal.

### D65: Resume Serialization Against the Prior Supervisor's Finalization (F1)
- *Rationale:* `tests/test_watcher_needs_review_resume.py::test_f1_invalid_commit_msg_kills_process_and_resumes` failed about 1 run in 3 with `assert NEEDS_REVIEW == DONE`, and the same failure exists on the older `93afaf2`, so it predates the recent changes. A 30-run stress loop reproduced it 5/30, and an instrumented replica proved the ordering: resume persisted `running` for generation 2, then the previous generation's `_run_supervisor` finally block (which had already read `job.json` as `needs_review`) wrote that stale record back from `_retain_scratchpad`, 4 ms later. The old thread then unconditionally removed the new supervisor from `_supervisors`/`_threads`, so `agy_wait` returned the transient `needs_review` instead of waiting for the resumed run's `done`. The stale invalid `COMMIT_MSG` is left in the worktree (the watcher only renames a valid, committed message), but it was not the cause: the resumed fake agy exits before a second settled validation, and no second invalid transition appears in the captured `audit.jsonl`.
- *Rule:*
  1. `JobManager.resume` joins the previous run's supervisor thread before taking the job lock and starting a new generation. The join is done without the job lock so an in-thread auto-resume (plan nudge, denial auto-resume, verification auto-fix) can still acquire that lock; the resumed generation therefore always reads the fully finalized job record. A previous supervisor that does not finish within 30 s fails the call closed with `ManagerError(code="job_finalizing")` rather than racing.
  2. `_run_supervisor` unregisters only when `_supervisors[job_id]` is still that exact supervisor object. A late finalizer must never drop a successor's registration; that would make `agy_wait` return a transient non-running state.
  3. Resume and auto-resume semantics are otherwise unchanged. The invalid `COMMIT_MSG` remains in place and the one-retry allowance stays scoped to one watcher generation; a resumed generation re-validates a stale file once more. The F1 fix is the generation hand-off, not a change to message validation.
  4. `tests/test_watcher_needs_review_resume.py::test_f1_resume_serializes_with_previous_supervisor_finalization` pins the hand-off deterministically by blocking the previous supervisor in its final `_retain_scratchpad` write; it failed 5/5 before the fix and passed 5/5 after it, and the original `test_f1_invalid_commit_msg_kills_process_and_resumes` then passed a 30/30 post-fix loop (evidence under `docs/reports/flaky-f1/`).

### D66: Discard the Invalid COMMIT_MSG on needs_review(invalid_commit_message)
- *Rationale:* D65 fixed the resume generation hand-off but deliberately left the invalid `.agy/COMMIT_MSG` in the worktree. A resumed generation constructs a fresh `CommitWatcher` with `validation_failure_count=0`, so the same stale file is validated again and can drive the resumed run back into `needs_review(invalid_commit_message)`. SPEC section 11 says an invalid message is discarded; the file should not survive the transition. This decision supersedes D65 rule 3's sentence that the invalid message remains in place; the hand-off rules 1-2 and 4 are unchanged.
- *Rule:*
  1. At the second failed validation, immediately before the `needs_review(invalid_commit_message)` transition, rename the offending `.agy/COMMIT_MSG` to `.agy/COMMIT_MSG.invalid.<n>`, where `n` is the first free integer from 1 through 20. When all 20 slots exist, `os.replace` renames onto `.invalid.20`, replacing that last slot in place; no slot is unlinked as a separate step, so the discard never grows the file count past the bounded 20.
  2. First-failure behavior is unchanged: the message is not renamed after the first invalid validation, so the existing one-retry allowance still lets agy finish a partial write or replace the file. The discard happens only when the job actually enters `needs_review(invalid_commit_message)`.
  3. Symlink safety: `.agy` and `COMMIT_MSG` are agent-controlled. The discard `lstat`s `COMMIT_MSG`, requires the parent to be the worktree's real `.agy` directory (`lstat` is a directory and `realpath` matches the worktree's own `.agy`), and calls `os.replace` on the directory entry itself. A symlinked `COMMIT_MSG` is renamed as a link and its target is never opened, modified, or renamed; a symlinked `.agy` parent is refused. Validation reads the message with `O_NOFOLLOW` and rejects non-regular entries.
  4. The discard appends audit event `commit_msg_discarded` with `detail={"code": <short code>, "file": <new basename or null>}` only. It never includes message text, a subject, or an absolute path. Codes are `invalid_message`, `refused_symlinked_agy_dir`, `refused_non_regular_entry`, `missing_entry`, `lstat_failed`, `rename_failed`, and `discard_failed`.
  5. The renamed files stay under excluded `.agy/**`: they are never staged or committed and create no audit finding. A resumed generation therefore starts with no pending message; a newly written valid message still commits normally.
  6. Tests: `tests/test_stale_commit_msg.py` pins the deterministic discard and no-second-failure resume, a valid message after resume, the symlinked file and symlinked `.agy` cases (target unchanged), the 20-file cap, and the audit event's contents. The `stale_commit_msg` and `stale_commit_msg_valid_resume` fake scenarios keep the resumed run alive long enough for a fresh watcher to settle.

### D67: Installer Chapter — Core/Client Split, Generic Default, Long Tool Timeout, Restart Guard, No Network Fetch
- *Rationale:* A stranger's machine must be installable with one command without silently editing their dsh configuration, and the same file must hold a chapter that can be pasted into the owner's `install-ai-stack.sh`. The dsh MCP client can block a tool call for the duration of `agy_wait`, and an installer launched from inside dsh must never restart the host that is executing it.
- *Rule:*
  1. Three layers in `installer/install.sh`: core (`agy_mcp_install_core`: prerequisites, venv, pip, config, state dir, run.sh/env), generic client (`agy_mcp_write_generic_files`: `mcpServers` JSON + `orchestrator-guidance.md`, printed), and the `ch_agymcp` function (core + generic + dsh marker blocks); `ch_agymcp` is sourceable and the paste-in notes name the `ALL_CHAPTERS`, chapter-description and doctor-hook edits.
  2. `generic` is the default client: with no `--client`, the installer writes nothing outside `$CONF_DIR/agymcp`, the agy-mcp config path and `~/.local/state/agy-mcp`, and never touches `~/.dsh`; dsh registration is opt-in with `--client dsh`.
  3. The dsh block sets `toolCallTimeoutMs: 360000` (360 s) because `agy_wait` may block for up to 300 s (`doc/design/spec.md` section 8) and a shorter client timeout can drop the result while the agy job keeps running; the installer prints the same 360000 ms requirement for generic clients.
  4. The installer refuses dsh registration/restart when `DSH_SESSION_ID`, `DSH_HOME` or `DSH_PROFILE` is set (`refuse_inside_dsh`), and skips the restart when `AGY_MCP_NO_RESTART=1`; otherwise it restarts dsh only when a systemd user session exists. Tests always set `AGY_MCP_NO_RESTART=1` and scrub `DSH_*`.
  5. Network fetching is deliberately not implemented: the hosting notes for a raw-URL `curl | bash`, a future `agy-mcp install` subcommand and a git-cache fetch-latest flow are comments only, because download/auth/proxy behaviour needs its own design and tests.

### D68: Usage Record Is Appended Before the Terminal State Becomes Observable (F22)
- *Rationale:* `tests/test_disk_limits_retention.py::test_record_usage_written_on_job_completion` failed intermittently in the full suite and alone (3/20 and 1/20 runs). It was not test interference: `--collect-only` puts `test_f22` at items 170-175 and `test_installer_chapter.py` at items 269+, no plugin reorders tests, and the test also fails when it is the only test collected. The real cause is a src race: `Supervisor._finalize_terminated_process` persisted the terminal state with `transition_job_state` and only then called `usage.record_usage`, and the supervisor runs on a background thread. An instrumented harness (`docs/reports/installer-fixes/f22_instrument.py`, 1 ms polling) showed the terminal state in `job.json` 4.274-7.057 ms before `usage.jsonl` existed in 10/10 runs. A reader polling with 50 ms sleeps lands in that window often enough to flake.
- *Rule:*
  1. `_finalize_terminated_process` accumulates execution time, appends the `usage.jsonl` record, and only then calls `transition_job_state`. Observing a terminal state in `job.json` therefore implies the usage record already exists.
  2. The record still names the terminal state: `usage.record_usage` gains an optional `state` keyword that overrides the value read from the job; the supervisor passes the state it is about to persist. Other callers (`recovery.py` and every test) keep the default and are unchanged.
  3. Record contents are unchanged and still carry the post-accumulation `cumulative_minutes` and `duration_seconds`, because `record_job_execution_time` still runs before the record is written.
  4. The fix is scoped to the terminated-process path. The state-persisted-then-usage-written order in `_finalize_non_running_process` (cancel, watcher- and audit-driven finalization) is unchanged; those transitions are owned by a different caller and keep their existing behavior. (Superseded for the watcher, tick-failure and recovery paths by D77.)
  5. `tests/test_disk_limits_retention.py::test_usage_record_is_written_before_done_is_observable` pins the ordering deterministically by delaying `usage.record_usage` by 500 ms; it failed on the old order and passes on the new one. The shipped F22 test is unchanged. Evidence is under `docs/reports/installer-fixes/`.

### D69: Installer Hardening — Upgrade on Rerun, Uninstall Safety, Flow-Style Guard, Standard Generic JSON, Shellcheck
- *Rationale:* A review of the installer chapter found five gaps between what the script does and what a stranger's machine needs: a rerun could never deliver repo changes to a non-editable install, an uninstall could leave a dsh registration pointing at a deleted `run.sh`, the flow-style guard refused harmless comment lines, the generic snippet carried two non-standard keys, and nobody had run shellcheck on the script.
- *Rule:*
  1. B1 upgrade on rerun: `--upgrade` (env `AGY_MCP_UPGRADE=1`) runs `pip install --upgrade --force-reinstall --no-deps` for the package itself from `AGY_MCP_SRC`, without re-resolving dependencies. When pip is skipped the installer prints `agy-mcp already installed; rerun with --upgrade to reinstall from <src>`. The flag is in `--help` and honoured by `--dry-run`. Tests: the skip note on a second run, a 0.0.0 → 0.0.1 wheel swap that the venv must see, and `--help`/`--dry-run` text.
  2. B2 uninstall safety: `--uninstall` with no explicit `--client` also removes the dsh patch and `AGENTS.md` marker blocks when present, printing each removal and `restart dsh from a normal terminal to unload the server` (nothing is restarted). Explicit `--uninstall --client generic` leaves dsh alone but warns that the block still registers the server and now points at a missing `run.sh`. `main()` passes an explicitness flag; the sourceable `uninstall_agymcp <client> [explicit]` keeps its old one-argument form.
  3. B3 flow-style guard: only lines that really use YAML flow style are refused. Full-line `#` comments (first non-space character) and everything between `# >>> ai-stack agymcp >>>` and `# <<< ai-stack agymcp <<<` are ignored, the offending `line: text` pairs are printed, and the file is still left untouched. `agy_mcp_flow_style_offenders` is shared by the install and uninstall paths.
  4. B4 generic JSON: `mcp-client-config.json` contains only `command`, `args` and `env`. `toolCallTimeoutMs` and `note` are removed because neither is part of the standard `mcpServers` entry schema; the 360000 ms requirement stays a printed note and is documented in `README.md`, `doc/design/spec.md` and `doc/project/orchestrator-workflow.md`. The dsh patch block keeps `toolCallTimeoutMs: 360000`. The existing test that asserted the two keys was updated (justified in the B4 commit).
  5. B5 shellcheck: `shellcheck-py 0.11.0` installed in a throwaway venv under `/tmp` and run on `installer/install.sh`. The only findings are four SC2016 info messages on the `printf` lines that emit the generated `run.sh` body, where literal `$` is intentional; no behavior change was made. Raw output is under `docs/reports/installer-fixes/b5-shellcheck-run.txt`.

### D70: agy_cleanup Deletes a Branch Only When No Commit Becomes Unreachable (A1)
- *Rationale:* A real dsh-orchestrated run merged a finished job branch into `integration`, fast-forwarded `main`, and then `agy_cleanup(delete_branch=true)` refused twice with `unmerged_commits` ("2 unmerged commits"), although `git merge-base --is-ancestor` and `git branch --contains` showed both commits reachable from `integration` and `main`. The gate counted `git rev-list --count <base_commit>..<branch>`, which counts every commit the job made whether or not it was merged; a branch with any commit at all could therefore never be deleted without `force=True`. Removing the worktree only (`delete_branch=false`) was the only working path.
- *Rule:*
  1. "Unmerged" means: some commit reachable from the job branch is not reachable from the repository's current HEAD or from any other local branch. `worktree.count_unmerged_commits()` implements it as `git for-each-ref --format=%(refname:short) refs/heads` followed by `git rev-list --count <branch> --not HEAD <every other local branch>`. The job branch is excluded from the negative list so it is not counted as merged with itself.
  2. Reachability is the correct test because it is ancestor-closed: if the branch tip is reachable from HEAD or another local branch, every commit on it is, so nothing is lost. `--not HEAD` covers a normal or detached HEAD; the remaining local refs cover a branch merged only into `integration`, including a `--no-ff` merge commit.
  3. Branch deletion then uses `git branch -D`, not `-d`: `-d` only compares against HEAD (and the upstream) and would still refuse a branch merged into another local branch even though the reachability gate proved it safe.
  4. Squash and cherry-pick merges create new commits and leave the originals unreachable, so such branches still count as unmerged and stay refused without `force=True`. `force=True` still deletes an unmerged branch; `remove_worktree` only (no branch deletion) is unchanged.
  5. An already-missing branch counts as 0 (nothing to lose). A failing git query raises `RuntimeError` so cleanup fails closed instead of deleting blind; `JobManager.cleanup` still maps the refusal to `error.code=unmerged_commits`.
  6. Tests: `tests/test_cleanup_branch_merged.py` (real git in temp repos): merged into HEAD, merged into another branch only, `--no-ff` merge, not merged anywhere (refused + error code), `force=True`, worktree-only, squash-merged (refused), detached HEAD. Evidence under `docs/reports/agymcp-fixes-2/a1-*.txt`.

### D71: One Shared Envelope Builder for status/result/review (A2)
- *Rationale:* SPEC section 8 defines a single standard envelope for all ten tools and lists `plan`, `facts`, `agent_report` and `scratchpad` in it, so the review contract does require them. In a real dsh-orchestrated run `agy_wait` returned populated `plan`/`facts`/`agent_report`/`scratchpad` while `agy_review` on the same finished job returned `plan {total 0}`, `facts {}`, `agent_report null`, `scratchpad []`. `build_review_response` and `build_result_response` copied base fields one by one, so every field they forgot silently became a dataclass default; `JobManager.status` had the same omission. SPEC says otherwise nowhere, so this was fixed in code, not by weakening the guidance.
- *Rule:*
  1. `models.standard_envelope_fields(envelope)` is the single mapping from a `StandardEnvelope` to extended-payload keyword arguments; it includes every base field (including `model_resolved`, `usage_cost`, `resume_after`, `denied_tools`).
  2. `build_result_response`, `build_review_response` and `JobManager.status` build their payload through it and only add/override their own fields (review keeps its audit-unavailable `next_actions` prepend).
  3. A future base-envelope field therefore reaches every extended response automatically; the failure mode that caused A2 cannot recur silently.
  4. Tests: `tests/test_envelope_agreement.py` runs a finished fake-agy job and asserts `plan`, `facts`, `agent_report`, `scratchpad`, `verify`, `tokens` and `usage_cost` are equal across `agy_wait`, `agy_review` and `agy_result` (and that the waited fields are populated, so the comparison is not empty-equals-empty). Evidence under `docs/reports/agymcp-fixes-2/a2-*.txt`.

### D72: One Branch Name, Allocated and Persisted Before agy_start Returns (A3)
- *Rationale:* `jobs.create_job` guessed `agy/<slug>-<job id prefix>`, `agy_start` returned that guess, and the background provisioning thread then had `create_worktree` allocate `agy/<slug>-<secrets.token_hex(4)>` and overwrite `job.branch`. In a real dsh-orchestrated run `agy_start` reported `...-8ece9385` while `agy_wait`, `agy_review`, `agy_cleanup` and `git branch` all reported `...-f1f7a6b3`; a branch copied from the start envelope did not exist.
- *Rule:*
  1. `worktree.allocate_branch_name(repo_path, title, id_generator=None)` performs the slug + 8-hex short-id allocation loop (`check-ref-format` + collision check) and is called once, by `JobManager.start`, inside the concurrency flock before `jobs.create_job`.
  2. The name is stored in `job.json` as part of job creation, i.e. persisted before the `agy_start` envelope is returned; it is the name every later tool reads.
  3. `create_worktree(..., branch=<name>)` uses a pre-allocated name verbatim (validates it and refuses if the ref already exists) instead of allocating a second one. A collision therefore fails the job with `setup_failed` rather than silently renaming the branch; the 8-hex suffix makes that window negligible. Callers that pass no branch (tests, direct users) keep the allocate-in-create_worktree behavior.
  4. Tests: `tests/test_branch_name_resolution.py` asserts the agy_start branch equals `job.json`, `agy_status`, `agy_wait`, `agy_review`, `agy_result`, the worktree HEAD and `git for-each-ref`; cleanup by that name works; two jobs with the same title get distinct persisted names. Evidence under `docs/reports/agymcp-fixes-2/a3-*.txt`.

### D73: Orchestrator Guidance — Integration Branch, No Force, Default Model (B)
- *Rationale:* The first real dsh-orchestrated run exposed two guidance gaps that turned real tool behavior into avoidable trouble: the block told the orchestrator to "merge the job branch into an integration branch ... then agy_cleanup" without forbidding a merge into `main` (the first run merged into main and then hit A1), and it gave no rule for a refused cleanup or for the `model` parameter. The review envelope fields were also described before A2 was fixed.
- *Rule:* (the text lives in `installer/agents_block.md` and is byte-identical inside `installer/install.sh`; the equality tests stay green, block 34 lines < 120)
  1. B1 "Running a job" step 4: accept means merging the job branch into a dedicated integration branch in the project (for example `integration/<topic>`) and running the project's tests there; never merge, fast-forward or push into `main`/`master` or the repository's default branch, because that is the human's decision; record `merge integration/<topic> into main when ready` in `docs/orchestrator/REPORT.md`; commit `docs/orchestrator/` on the integration branch, not on `main`.
  2. B2 step 5: never pass `force=true` to `agy_cleanup`; if cleanup refuses, remove the worktree only (`delete_branch false`), add the job branch to `NEEDS_HUMAN` and continue.
  3. B3 step 1: do not pass the `model` parameter to `agy_start` unless the human named one; use the server default.
  4. B4 step 3: A2 is fixed, so the text states that `agy_review` carries the same `plan`, `facts`, `agent_report` and `scratchpad` as `agy_wait` (wrapper-owned data) and can be read.
  5. Tests: `tests/test_installer_chapter.py` pins the integration-branch rule, the never-force rule, "no line offers `main` as a merge target", the default-model rule, and the existing byte-equality of the file with both generated blocks.

### D74: --upgrade Respects Editable Installs and Re-resolves Dependencies (C1)
- *Rationale:* `--upgrade` ran `pip install --upgrade --force-reinstall --no-deps` unconditionally. On an editable install that replaced the link with a normal copy, so the user's source edits silently stopped taking effect; `--no-deps` meant a new version's added or raised dependency was never installed.
- *Rule:*
  1. `agy_mcp_is_editable_install <venv>` detects an editable install via the PEP 610 `direct_url.json` (`"editable": true`) inside the `agy_mcp-*.dist-info`, or the legacy `agy_mcp.egg-link` / `__editable__*agy_mcp*.pth` shims.
  2. `--upgrade` on an editable install without `AGY_MCP_EDITABLE=1` prints `editable install is already live; nothing to reinstall` and runs no pip, so the install stays editable.
  3. `AGY_MCP_EDITABLE=1` forces an editable reinstall: `pip install --upgrade -e <src>` (no `--force-reinstall`).
  4. A normal (non-editable) upgrade runs `pip install --upgrade --force-reinstall <src>` without `--no-deps`, so pip re-resolves the package's dependency list.
  5. Tests: `tests/test_installer_chapter.py` (offline fake wheels) proves an editable install stays editable, the `AGY_MCP_EDITABLE=1` command shape, and that a 0.0.1 -> 0.0.2 upgrade installs a newly required `fakedep`. Evidence under `docs/reports/agymcp-fixes-2/c1-*.txt`.

### D75: Uninstall Claims a Restart Only When It Removed Something (C2)
- *Rationale:* `agy_mcp_remove_dsh_blocks` set `removed=1` whenever a marker block existed, even when `agy_mcp_remove_dsh_block` refused a flow-style file and warned "remove the agy-mcp block manually". The uninstall then printed `restart dsh from a normal terminal to unload the server` although nothing had been removed.
- *Rule:*
  1. `agy_mcp_remove_dsh_block` returns 0 only when it actually removed the block; it returns 1 when there is no block or the file is refused (flow style).
  2. `agy_mcp_remove_dsh_blocks` sets `removed=1` from that return value, so the restart hint appears only after a real removal.
  3. Tests: `tests/test_installer_chapter.py::test_uninstall_flow_style_patch_prints_no_restart_hint` (block present + flow-style line, nothing removed, no restart hint, no "removed the agy-mcp block" line). Evidence under `docs/reports/agymcp-fixes-2/c2-*.txt`.

### D76: The verify-not-found Flake Is the Auto-Fix Observation Window, Not a Persistence Race (C3)
- *Rationale:* `tests/test_manager.py::test_manager_verify_command_not_found_clean_error` failed about 1 in 30 runs (2 failures in 44 + 3 local runs). Instrumenting `save_job`, `transition_job_state` and `get_job` in a temporary copy of `jobs.py` (restored before the fix commit) showed that state and `review_reason` are always written together in one atomic `job.json` replace. The failure is the automatic verification auto-fix: after `needs_review(verify_failed)` the manager resumes the job, which persists `running` and clears `review_reason`. The test sampled `status()` and then `get_job()` in two separate reads and could land across that transition (raw trace: transition to `running` at .670797, `get_job` reads `needs_review/verify_failed` at .671192, the resume save lands at .672674, the next read sees `running/None`).
- *Rule:*
  1. No src change: `review_reason` is a property of the `needs_review` state and is cleared in the same atomic write that sets `running`; a client cannot observe a persisted `needs_review` without its reason. The transient is the state changing between two independent reads.
  2. `agy_wait` is the supported way to observe a settled outcome: it already keeps waiting while a verify auto-fix is pending (max 300 s per call).
  3. The test now uses `manager.wait(job_id, seconds=10)` and keeps every original assertion (`needs_review`, `review_reason == "verify_failed"`, `"Executable not found"` in the result). The change is test-only and listed in the report.
  4. Evidence: instrumentation trace, the raw failing lines, the mutation loop (old polling restored: failed on run 28 of 120 with `assert None == 'verify_failed'`) and 30 consecutive passes are under `docs/reports/agymcp-fixes-2/c3-*.txt`.

### D77: Usage Ordering on Every Terminal/needs_review Path and No Double Counting Across a Crash (C4)
- *Rationale:* D68 ordered the usage append before the state transition only on the process-terminated path. Three more paths persisted a terminal or `needs_review` state first: watcher-driven finalization (invalid `COMMIT_MSG`, fatal audit, sensitive partial commit) followed by `_finalize_non_running_process`; the supervisor's three-consecutive-tick-failures fail-closed transitions; and `recovery.reconcile_on_startup` finalizing a dead process. A reader that saw the new state could not yet find the usage row. D68 also moved the row earlier, which creates a new crash window: the finalizer can append the row and die before the state transition, leaving `job.json` `running` with the run's minutes already accumulated; recovery then appended a second row for the same run (experiment: 2 rows, both `duration_seconds 120.0`, `cumulative_minutes 2.0`).
- *Rule:*
  1. `JobSupervisor._record_usage_before_state(target_state)` takes the fresh usage reading, computes the cost and appends the row with the state about to be persisted; it is called by the supervisor tick-error fail-closed paths and, through the new `CommitWatcher(before_state_transition=...)` callback (default `None`, direct watcher users unchanged), immediately before each of the watcher's three `needs_review` transitions.
  2. `recovery.reconcile_on_startup` records the row before `transition_job_state` and passes `state=target_state`.
  3. `_usage_recorded_state` makes the append idempotent for a run, so `_finalize_non_running_process` does not append a second row after a watcher-driven transition. External transitions (for example `agy_cancel`) still append exactly one row there.
  4. `usage.record_usage` writes `run_generation` (additive; older rows are treated as generation 1) and `usage.has_usage_record_for_generation()` lets recovery skip its append when the crashed finalizer's row for the same run already exists. The crashed row survives, so the run is counted once; the recovered job may end in a different state than the pre-crash row names, which is documented rather than rewritten.
  5. Crash-window answer: if the process dies between the usage append and the state transition, the next startup sees `running` with a dead process and finalizes it as before; without rule 4 it counted the run twice, with rule 4 it keeps the single row. If it dies between the execution-time accumulation and the append, recovery writes the row (the job's cumulative minutes were already persisted) and nothing is lost or doubled. Missing rows for jobs that never ran (stale `queued` -> `failed`, a `needs_review` for a corrupted snapshot in `manager.reconcile`, cancel of a `running` job with no supervisor) are pre-existing gaps outside this ordering fix; they are listed in the report as open, not silently changed.
  6. Tests: `tests/test_usage_before_state.py` (deterministic: `usage.record_usage` delayed 500 ms while another thread polls `job.json`, for the watcher, tick-failure and recovery paths; plus the duplicate-row test and the crash-window experiment under `docs/reports/agymcp-fixes-2/c4-*.txt`). Supersedes D68 rule 4 for these paths.

### D78: agy-mcp fixes-2 evidence and reports
- *Rule:* Every raw command output referenced by D70-D77 is stored under `docs/reports/agymcp-fixes-2/` (`baseline-full.txt`, per-item `*-failing.txt`, `*-passing.txt`, `*-mutation.txt`, `c3-30passes.txt`, `c4-crash-window.txt`). The STEP 0 baseline recorded `632 passed in 200.11s` before any change.

### D79: Workflow Selection Is a Short Block Section; Examples Live in ORCHESTRATION.md
- *Rationale:* the orchestrator guidance in `installer/agents_block.md` is injected into every session and replayed on every step, so length is a recurring context cost. Worked examples and run numbers are reference material that is read once, not every step. Putting the examples in the block would make every step pay for them.
- *Rule:*
  1. The block gains a `## Choosing a workflow` section: write `docs/orchestrator/PLAN.md` first with the chosen workflow and why; then the four names GATED (default when tests can check the result), MICRO (risky core code or a task that cannot be specified up front), RESEARCH (no spec possible) and DIRECT (tiny). An ambiguous goal with nobody watching takes the most reasonable reading, says which in `PLAN.md`, and continues.
  2. GATED writes the contract and `tests/acceptance/` first, gives one job "make tests/acceptance pass, never modify tests/acceptance, add your own tests", waits without polling, checks that the acceptance tests are untouched (restoring them from the orchestrator's commit if needed), reviews the diff, merges into `integration/<topic>`, and runs the acceptance tests and the full suite itself. At most one fix job; an optional second job for extra tests or hardening is gated the same way.
  3. MICRO is 3-5 chunks, one job each, with `agy_review`, diff read, merge, full suite and `agy_cleanup` after every job. RESEARCH delegates only self-contained sub-topics and marks claims `VERIFIED` only with a source, otherwise `ASSUMED`. DIRECT does tiny work without agy.
  4. The always-on rules stay: self-contained prompt (goal, files, constraints, 3-10 step checklist, verify command, commit subject <= 72 characters), `agy_usage` before every start, handover to the orchestrator below the quota floor logged in `docs/orchestrator/DECISIONS.md`, odd states handled with `agy_status`/`agy_resume`/`agy_cancel`, and no digging into agy-mcp's source, config or state directories. The existing checklist range in "Running a job" was changed from 3-8 to 3-10 so the two rules agree.
  5. `installer/install.sh` embeds the same text inside `agy_mcp_write_agents_guidance`; the two copies stay byte-identical and the installer tests compare the generated block with `installer/agents_block.md`.
  6. Size budget: block 34 -> 43 lines (+9), under the +28 net-growth limit and the 62-line test cap. Worked examples (run 1 RESEARCH/prototype: 5 jobs, 82 steps, ~$0.12, 1.73 weekly points; run 2 GATED: 1 build + 1 fix job, 46 steps, ~$0.04, 0.57 points, gate caught a `connect()` deadlock; run 3 MICRO: 4 jobs, 49 steps, ~$0.05, 1.60 points, extra tests and hardening) are in `docs/ORCHESTRATION.md`, with a single-sample caveat.
  7. Tests: `tests/test_installer_chapter.py::test_agents_block_names_the_four_workflows`, `::test_agents_block_protects_acceptance_tests`, `::test_agents_block_states_the_commit_subject_limit` and `::test_agents_block_fits_the_workflow_budget` (<= 62 lines). Evidence under `docs/reports/orchestrator-workflows/`.

### D80: wrapper-owned commit subjects, repo hooks, and per-job verify timeout
- *Rationale:* H27 recorded two coupled failures. (1) A run ended `needs_review(reason="invalid_commit_message")` twice before verify; the docs blamed the wrapper checkpoint subject, but the checkpoint commits themselves were accepted by git (for example `61d01e0`, `dd981da` in the D48 job). The only code path that emits `invalid_commit_message` is `CommitWatcher._process_settled_commit_msg`, which validates the agent-written `.agy/COMMIT_MSG` with a 72-character subject rule. The checkpoint was the visible latest commit, not the rejected text. Independently, wrapper subjects were built by naive truncation (`recent_msg[:72]` / `f"agy: {title}"[:72]`) and the commit path bypassed repository hooks with `-c core.hooksPath=/dev/null`, so a hook could never be satisfied and an unlucky truncation could end in a trailing period or a split line. (2) `run_verify` had a hard-coded 900 s timeout, reported only `Verification command timed out after 900s`, and the result could not distinguish a wrapper kill from a command that failed by itself. On a real hang this cost a full 15 minutes and produced no command, timeout or last-lines evidence.
- *Rule:*
  1. `gitops.sanitize_commit_subject()` is the only subject builder for wrapper-generated commits: one line, printable ASCII (NFKD transliteration), no control characters, no trailing period/whitespace, at most 72 characters, non-empty fallback. `build_wrapper_commit_msg()` normalizes the body and `commit_message_problem()` mirrors the watcher rules before `git commit`; a fallback subject is written if a future edit ever produces an invalid one.
  2. Checkpoint subjects use `agy: checkpoint <UTC ISO seconds>Z`; safety-net subjects sanitize the most recent agy message, else `agy: <title>`.
  3. Wrapper commits run repository hooks: `commit_with_retry(run_hooks=True)` uses `GIT_CONFIG_COUNT=2` (fsmonitor/pager) and no `core.hooksPath` override, and never passes `--no-verify`. All other git calls keep the historical `core.hooksPath=/dev/null` default. A hook rejection transitions `needs_review(reason="invalid_commit_message")` (or `verify`-path rejection) with the hook's first stderr line, a `cause` and a `next_action`, and is never silently retried or bypassed. Residual risk (documented, not mitigated): a repository hook is owner-controlled code that runs as the user, so only operate on trusted repositories.
  4. `verify_timeout_seconds` is global config (default `900`, range 1..86400) and overridable per job through `agy_start(verify_timeout_seconds=...)`; the value is persisted on the job. `run_verify` kills the whole verify process group (SIGTERM then SIGKILL, `start_new_session=True`) and returns a `VerifyResult` tuple subclass carrying `command`, `timeout_seconds`, `timed_out`, `killed_by_timeout`, `group_alive_after_kill`, `duration_seconds` and `status`; plain `(rc, tail)` unpacking still works.
  5. The verify envelope keeps every old key and adds `status` (`passed`/`failed`/`timeout`/`not_run`), `timed_out`, `killed_by_timeout`, `command`, `timeout_seconds`, `cause`, `next_action`. Timeout is `needs_review(reason="verify_timeout")`, self-failure is `verify_failed`; timeout is not auto-fixed. `verify_tail.txt` and `last_tail` contain the command, timeout, result, known diagnosis (pytest import-file mismatch, missing executable, missing pytest), the last ~40 lines, and matching diagnostic lines even outside the tail.
  6. `needs_review` errors for invalid commit messages and verify are one line of cause plus one line of next action in `job.error`; a resumed run clears that actionable error on the RUNNING transition.
  7. Tests: `tests/test_wrapper_verify_commit_fix.py` (sanitizer/validator, checkpoint and safety-net subjects, a real commit-msg hook rejecting then accepting, hook-rejection error text, process-group kill with a live grandchild, timeout-vs-failure status, import-mismatch excerpt, config validation, per-job timeout reaching `run_verify`, old envelope keys). Evidence in `docs/reports/wrapper-verify-commit/`.

### D81: Strict conversation identity, early persistence, honest resume and explicit follow-up
- *Rationale:* A real job's cli.log contains `Conversation using project ID: default-cli-project`. The old fallback regex captured the word `using` as `job.conversation_id`; because `_extract_conversation_id` returned any stored value without re-parsing, the later real stdout JSON could not repair it, and `agy_resume` sent `--conversation using`. Separately, cancellation dropped the id (the `is_cancelled` early return in `parse_agy_output` returned no id/tokens), and a watcher-driven stop could persist before stdout JSON existed. A job with no usable id also resumed silently, pretending to continue a conversation it did not have.
- *Rule:*
  1. Only a strict full UUID is accepted. Precedence: stdout JSON `conversation_id`; cli.log `Created conversation <uuid>`; cli.log `Print mode: resuming conversation <uuid>`. The loose regex is removed; every path, including `--conversation`, `build_argv` and persistence, rejects a non-UUID.
  2. While running, the supervisor scans the bounded cli.log head for `Created conversation <uuid>` and writes `job.json` immediately. Cancellation keeps the payload's id/tokens, and `_finalize_terminated_process`/`_finalize_non_running_process` both merge through the strict resolver.
  3. A stored valid UUID is sticky (a different stdout UUID is ignored and `conversation_id_mismatch {stored, observed}` is audited once); a stored non-UUID is treated as absent and replaced by the first valid observation.
  4. `agy_resume` returns additive `conversation_continued` and, when false, the warning `conversation_not_continued: ...`; the prompt is self-contained (original task summary, current HEAD, files changed) even without a conversation.
  5. `agy_resume(..., follow_up=true)` is the only way a `done` or `cancelled` job can return to `running`; `TRANSITION_TABLE` marks those edges `explicit_follow_up_only` and `transition_job_state` filters them unless the flag is passed. Default behavior stays `invalid_state_for_resume`.
- *Tests:* `tests/test_resume_and_commitmsg.py` (strict parser, early persistence + cancel, loose-line/stdout race, resume honesty, follow-up gate for done/cancelled, sticky id and mismatch audit), updated `tests/test_parse_result.py`, `tests/test_runner.py`, `tests/test_watcher_needs_review_resume.py`, `tests/test_jobs.py`, `tests/server/test_smoke.py`, `tests/test_fake_agy.py`.

### D82: Repair an over-long commit subject; preserve evidence on rejection
- *Rationale:* H27 showed that a single over-long agent subject discarded otherwise-correct work into `needs_review(invalid_commit_message)`: the pre-kill checkpoint saved the code but the message was lost. Rejecting the whole message for a mechanically fixable length error is unnecessary; the remaining validation rules (empty, NUL/control, invalid UTF-8, oversized, symlink/non-regular) are real safety gates. When a message is still rejected, the worktree is eventually removed, so the invalid text must be copied to the job directory first.
- *Rule:*
  1. `watcher.repair_overlong_commit_subject` runs before validation. It repairs only when the sole violation is the >72-character first line: truncate at the last whitespace boundary, prepend the full original first line to the body, write atomically with mode 0600, and audit `commit_msg_repaired {original_len}` (no message text). `validate_commit_message` itself keeps rejecting >72 so direct callers and tests keep their strict semantics.
  2. On the second validation failure, copy the raw rejected regular file to `<jobdir>/rejected_COMMIT_MSG.txt` before the D66 discard rename, and add `first_line_repr` (`repr()` capped at 120 chars) and `evidence_file` to `job.error`. Symlinks/non-regular files are never followed. The audit event stays `{code, file}` only, so the privacy regression test still passes.
- *Tests:* `tests/git/test_h27_subject_repair.py` (repair end to end), `tests/test_stale_commit_msg.py` (repair, evidence file, privacy, NUL/symlink still rejected), `tests/supervisor/test_watcher_commit_pipeline.py`, `tests/test_wrapper_verify_commit_fix.py`.

### D83: Per-run tokens, wrapper wall-clock runtime and a configurable quota estimate
- *Rationale:* agy's JSON `duration_seconds` is the conversation's AGE (327.7 s reported for a 360.7 s wrapper run on a previously used conversation), `num_turns` is cumulative, and usage tokens describe only the last run. Treating any of those as this run's runtime would corrupt budgets, `usage.jsonl` and cost attribution. Measured `usage_cost` also disappears when `/usage` is unavailable, so a token-based estimate is useful alongside it.
- *Rule:*
  1. Every run appends `job.json.runs[] {run_generation, wall_clock_seconds, agy_duration_seconds, tokens}`. `wall_clock_seconds` is `now - started_at` from the wrapper; agy's value is diagnostic only. No budget, usage row or log uses agy's duration.
  2. `usage.jsonl.duration_seconds` and `wall_clock_seconds` carry the same wrapper-measured run time on every path (terminated, watcher-finalized, recovery); the old cumulative-minutes substitution is removed. **Meaning changed:** rows written before D83 stored *cumulative job minutes* in `duration_seconds`; rows written after it store *this run's wall clock*. Consumers must not mix old and new rows when aggregating; see `CHANGELOG.md`.
  3. `quota_estimate {tokens, weekly_pct_points, five_hour_pct_points, weekly_per_1k_tokens, five_hour_per_1k_tokens, estimated, note}` is stored next to `usage_cost` in `job.json`, `usage.jsonl` and the envelope, using `quota_estimate_weekly_per_1k_tokens` (default 0.00075) and `quota_estimate_five_hour_per_1k_tokens` (default 0.0027). It never replaces the measured delta. *The coefficient defaults in this rule are superseded by D85; the rest of the rule still holds.*
- *Tests:* `tests/git/test_h27_subject_repair.py` (agy reports 999999 s; run/usage rows stay wall-clock, tokens/quota estimate present), `tests/usage/test_usage.py`, `tests/test_usage_cost.py`, `tests/test_config.py`.

### D84: Additive agy_status run and conversation fields; node --test verify allowlist
- *Rationale:* Orchestrators polling `agy_status` need visibility into conversation identity and per-run lifecycle timing without parsing internal logs. Additionally, projects using Node.js need built-in test runner support (`node --test`) without granting general Node execution or dangerous flags (`-e`/`--eval`, `-r`/`--require`, etc.).
- *Rule:*
  1. `agy_status` additive fields: Status payload exposes `conversation_id` (str | null), `conversation_id_source` ("early" | "finalized" | null), `runs` (projection of `Job.runs[]` with `{index, started_at, ended_at, wall_clock_seconds, outcome}` where `index` is 0-based; internal fields `run_generation`, `agy_duration_seconds`, `tokens` and prompt text are omitted), and `wall_clock_seconds` (total sum of wrapper-measured run wall clocks, `null` when no run has finished).
  2. New run rows recorded by `supervisor._record_run` and `recovery.reconcile_on_startup` store `started_at` (ISO 8601 UTC), `ended_at` (ISO 8601 UTC), and `outcome` (state string). Legacy jobs missing these fields project `None`/`[]` without crashing.
  3. `node --test` verify allowlist: Added `"node --test"` to default `verify_allowlist` after `"go test"`.
  4. Grammar for `node --test`: Accepts `node --test` optionally followed by relative file, directory, or glob (`*`, `**`, `?`, `[]`) arguments that stay inside the job workdir after symlink resolution; before a fresh job's worktree exists, the repository root is the boundary. Rejects any token starting with `-` (including `--`, `--test-reporter`, `--test-name-pattern`, `-e`, `-r`, `--watch`, etc.), empty tokens, empty argv, bare `node` without `--test`, `/usr/bin/node ...`, `node --test=...`, absolute paths, paths containing `..` components, paths escaping the boundary, and arguments containing any of these shell metacharacters: `;`, `&`, `|`, a backtick, `$`, `(`, `)`, `<`, `>`, newline, CR, backslash, double quote, single quote.
- *Tests:* `tests/test_status_conversation_fields.py`, `tests/test_node_allowlist_grammar.py`, `tests/test_config.py`.

### D85: Calibrated conservative token quota estimate
- *Rationale:* The initial D83 token quota coefficients (`0.00075` weekly, `0.0027` 5-hour per 1k tokens) under-reported measured quota by ~4x on the reference job `6d9be49b`. An empirical calibration across all 7 jobs in the job store carrying both stored estimates and measured usage costs (`measured=true`) revealed an observed range of 0.00306–0.01071 (5-hour) and 0.00086–0.00306 (weekly) points per 1k tokens. Candidate least-squares fits were evaluated and rejected: least squares through the origin (`0.009305` / `0.002660`) under-estimated the reference job by 13%, while least squares with an intercept produced a negative constant (`-1.13` / `-0.325`) yielding negative estimates for small jobs. The conservative upper-envelope model (`0.011` 5-hour, `0.0031` weekly, base `0.0`) was chosen so the estimate never under-reports an observed job.
- *Rule:*
  1. Default coefficients are recalibrated to `quota_estimate_five_hour_per_1k_tokens = 0.011` and `quota_estimate_weekly_per_1k_tokens = 0.0031` in `Config`, `JobSupervisor`, and `usage.compute_quota_estimate`.
  2. Formula: `points = round(0.0 + tokens / 1000.0 * coefficient, 4)`. Negative coefficients passed by callers are clamped to `0.0` via `max(0.0, float(x))`.
  3. A usable `total_tokens` wins; a missing, zero, non-numeric or negative `total_tokens` falls back to `input_tokens + output_tokens` when both counters are present. The fallback is reachable in production because `TokenUsage.from_dict` materializes an absent total as `0`. When no total can be determined, the result is `None` (JSON `null`) rather than a fabricated `0`; a zero total with zero counters returns an estimate dict with `0.0` points and `estimated = False`.
  4. The returned dictionary includes module constant `QUOTA_ESTIMATE_BASIS = "calibrated-2026-10-31, n=7"`.
  5. Old stored estimates and measured usage costs in existing `job.json` records are never rewritten or recalibrated on read.
  6. Measured `usage_cost` remains authoritative; the estimate is explicitly labelled as an estimate in its note.
  7. Detailed calibration observations and candidate models are recorded in `docs/reports/quota-calibration-data.md`.
- *Tests:* `tests/test_quota_calibration.py`, `tests/git/test_h27_subject_repair.py`, `tests/usage/test_quota_estimate_fallback.py`.

### D86: Test-harness live state isolation guard and conftest bootstrap redirect
- *Rationale:* Tests that omitted an explicit `base_state_dir` could resolve the operator's real live state directory `~/.local/state/agy-mcp` if `HOME` or `XDG_STATE_HOME` were observed before session fixture initialization. The session fixture also previously re-captured `_REAL_LIVE_STATE_DIR` after redirection. Tests must be structurally prevented from resolving or mutating live operator state at any point in test collection or execution.
- *Rule:*
  1. `tests/conftest.py` captures the operator's real live state directory at module import time before any environment modification.
  2. At conftest import time, a bootstrap temporary directory is created, and `HOME`, `XDG_STATE_HOME`, and `AGY_MCP_STATE_DIR` are pointed to it so no test module import observes the live environment.
  3. An in-process resolution guard wraps `agy_mcp.jobs.get_base_state_dir` and patches all already-imported modules (such as `agy_mcp.manager`). Any resolution returning the real live state directory or any path under it raises `RuntimeError` matching `"live agy-mcp state"`.
  4. The `sys.addaudithook` write barrier remains installed and active, rejecting mutating file operations under the live state directory with `"BLOCKED by test isolation guard"`.
  5. The session-scoped autouse `isolated_state` fixture creates a per-session tree under `tmp_path_factory` and exposes `home`, `xdg_state`, `state_env`, and the import-time `real_live_state`.
  6. The write guard exempts the checkout under test (`_REPO_ROOT`), because the agy wrapper runs verification in a worktree that itself lives under the live store; pytest must be able to write its own cache/pyc there. The exemption is a no-op when the checkout is outside the store, and explicit real-state paths outside the repo still raise.
  7. `sys.audit` does not expose `dir_fd` for relative path arguments, so test-harness wrappers for `os.open`, `os.unlink`/`remove`, `os.rmdir`, `os.mkdir`, `os.chmod`, `os.chown`, `os.utime`, `os.symlink`, `os.link`, `os.rename` and `os.replace` resolve the fd through `/proc/self/fd` and reject writes that land under the real state dir. The real path is captured into `_AGY_MCP_CAPTURED_REAL_LIVE_STATE` so a module reload or an xdist worker cannot recapture the redirected HOME.
- *Tests:* `tests/test_live_state_isolation.py`, `tests/isolation/test_state_isolation_guard.py`.

### D87: Safe fixture-row lister/cleaner (`cleanup_fixtures`) candidate signature and safety contract
- *Rationale:* Accumulated pytest fixture rows in test or development stores must be purgeable without endangering live user jobs, parent job relationships, or symlink-targeted files. Deletion must be strictly opt-in, dry-run by default, and refused on any ambiguity or store anomaly.
- *Rule:*
  1. Invoked via `python -m agy_mcp.cleanup_fixtures`. `--store PATH` is mandatory; missing it exits 2 and mentions `store` on stderr.
  2. Default execution is a read-only dry run. Deletion requires explicit `--yes`. Refusal checks (exit 2) verify store exists, is a directory, is not a symlink, contains a non-symlinked `jobs/` directory, and resolves within `$HOME`.
  3. Candidate signature: direct child of `store/jobs/`, non-symlink directory, name is a canonical lowercase UUID (`fullmatch`, so no trailing newline), `job.json` is a regular file <= 1 MiB with matching `job_id`, state is terminal (`done`, `failed`, `cancelled`), `repo_path` is absolute and resolves under `tempfile.gettempdir()` with `pytest-of-*`, `pytest-<digits>` and a per-test `test_...<n>` component plus at least one component after it, store and repo do not share the same pytest basetemp, PID is null or a positive non-live integer (non-positive/boolean/non-integer kept), age exceeds `--min-age-days` using timezone-aware ISO 8601 `updated_at` (falling back to `created_at` only when `updated_at` is absent; unparseable values kept), no symlinks anywhere in the job directory and unreadable subtrees are treated as unsafe.
  4. Follow-up-edge protection: any candidate referenced by a raw string value inside any kept job's `job.json` is moved to `kept` with reason `"referenced by kept job"`. If any kept job record cannot be read or parsed, every candidate is moved to `kept` (fail closed). A symlinked job directory is not a readable record and does not block unrelated candidates.
  5. Manifest: immediately before deletion, write atomic ids-only manifest `.cleanup-manifest-<timestamp>.json` with mode 0600 inside `store/jobs/` containing only `{"removed_ids": [...]}`. Deletion removes regular files and rmdir directories bottom-up without following symlinks and verifies all removed paths resolve under `store/jobs/<job_id>`.
- *Tests:* `tests/test_cleanup_fixtures.py`.

### D88: Safe fixture cleaner fixes (fail-closed scan, key/substring protection, pre-delete revalidation, honest manifest)
- *Rationale:* Hardens `cleanup_fixtures.py` against edge cases identified during security audit: unreadable kept job directories or unsearchable store ancestor directories causing unhandled permission tracebacks, candidate job references appearing as dictionary keys or embedded within longer strings (branch names, follow-up titles), mutated candidate states between initial scan and deletion, and inaccurate manifests or lost candidate records in JSON output under `--yes`.
- *Rule:*
  1. *Fail-closed scan:* If any kept job directory is unreadable or its `job.json` cannot be read or parsed, all candidates are kept with a stated reason (exit 0 on dry run and delete nothing on `--yes`, no traceback).
  2. *Dict key and substring protection:* A candidate UUID is protected if it appears anywhere as a dict key (nested too) or as a substring inside any string value within a kept job's `job.json`. An unrelated UUID does not protect a candidate.
  3. *Pre-delete revalidation and `before_delete` seam:* `run_cleanup` accepts an optional `before_delete: Optional[Callable[[str], None]] = None`. Immediately before each deletion, after `before_delete(job_id)` is called (if provided), the target row is re-read and re-classified. If it is unreadable, non-terminal, alive, or otherwise no longer matches the fixture signature, only that row is skipped (remains on disk, reported in `skipped` with reasons, omitted from `removed_ids`), and remaining rows proceed.
  4. *Honest manifest:* Written before deletion with `status: "planned"` and `planned_ids: [...]`. Replaced atomically via tempfile + `fsync` + `os.replace` in `store/jobs/` after deletion with `status: "completed"` or `"partial"`, `removed_ids: [...]` (truly removed), and `failed_ids: [...]` (failed or skipped). Manifest contains IDs only. CLI exits non-zero (exit 1) on partial runs and prints failed/skipped IDs.
  5. *Ancestor permission refusal:* Path validation in `main()` catches `OSError` around store path and ancestor directory checks, refusing with exit code 2 and a clear message without traceback.
  6. *Candidate preservation in JSON:* Under `--yes`, `--json` preserves the complete `candidates` list and `candidate_count` as scanned before deletion; `deleted_ids` and `deleted_count` describe what was deleted.
- *Tests:* `tests/test_cleaner_fixes.py`.

### D89: Safe job-record lister and cleaner CLI (`cleanup_jobs`)
- *Rationale:* Historical job records accumulate in `~/.local/state/agy-mcp/jobs/` across multiple projects. Operators need a safe, dry-run-first CLI to purge stale job records either on a per-project basis or for explicit job IDs, without endangering live processes, git repositories, branches, or registered worktrees.
- *Rule:*
  1. *Module & CLI entrypoint:* `src/agy_mcp/cleanup_jobs.py`, runnable as `python -m agy_mcp.cleanup_jobs`.
  2. *Modes:* Exactly one of `--list`, `--project PATH`, or `--job ID` (repeatable) is required. `--store PATH` is required and must resolve inside `$HOME` without any symlinks in the path or its ancestors. Exit 2 on invalid arguments, conflicting modes, or missing store.
  3. *`--list` mode:* Read-only inspection; reports job counts by state and eligible cleanup count grouped by normalized `repo_path`. Leaves disk bit-for-bit unmodified even if `--yes` is passed. Exits 0.
  4. *`--project PATH` mode:* Normalizes target path (tilde expansion, dot/parent resolution, case preservation, trailing slash removal) and matches strictly against normalized `repo_path` in `job.json`. Sibling, prefix, parent, child, or case differences do not match. Terminal states (`done`, `failed`, `cancelled`), age older than `--older-than-days` (default 7.0), no live/ambiguous PID, no symlinks. Exits 0 if no jobs match.
  5. *`--job ID` mode:* Explicit IDs waive the age threshold. All requested IDs must be valid canonical UUIDs existing in the store; malformed or unknown UUIDs exit 2 before attempting any deletion. Exits 2 if all requested IDs are ineligible, exits 1 on partial deletion, and exits 0 when all requested IDs are deleted.
  6. *Worktree protection rule:* Any row whose directory contains a registered git worktree (`work/.git` exists or listed in the repo's `.git/worktrees`) is KEPT with reason `"worktree registered (run 'git worktree prune' in repository to unregister)"` and suggests `git worktree prune` in output.
  7. *Edge protection:* Any candidate referenced as a key or substring inside any kept job's `job.json` is protected and moved to `kept`.
  8. *Manifest & pre-delete seam:* An atomic manifest (`0600`) is written as `"planned"` before deletion and replaced after deletion with `"completed"` or `"partial"`, recording `mode`, `project`, `removed_ids`, and `failed_ids` (IDs only). `before_delete` hook allows re-reading and skipping mutated or corrupted rows right before deletion. One failing row never aborts remaining deletions (superseded for corrupt/unreadable rows by D91/D92).
- *Tests:* `tests/test_cleanup_jobs.py`.

### D90: Safe job-record cleaner hardening and edge-case fixes
- *Rationale:* Addresses review findings 1–6, 8, 9, 10 for `cleanup_jobs.py`: fail-closed retention on corrupt, non-object, or unreadable kept records across the store; project and edge re-checks immediately prior to each deletion; relative gitdir resolution against entry directories; linked worktree and submodule support (`.git` as a file with `commondir`) and fail-closed retention on unreadable worktrees directories; duplicate `--job` ID deduplication; partial run reporting and manifest tracking for ineligible `--job` IDs; `--list` consistency with `--project` mode via shared classification path; invalid `repo_path` rejection and grouping under `"(unknown)"`; and documenting known limitations (symlinked job folders in `--job` mode exit 2 by design; Unicode NFC/NFD paths are not normalized).
- *Rule:*
  1. *Project re-check before deletion:* In `--project` mode, immediately before deleting each candidate, re-read `job.json` and verify `repo_path` is a valid string matching `target_project`; if modified or invalid, skip row and record in `failed_ids` and `skipped`.
  2. *Dynamic edge protection re-check:* Immediately before deleting each candidate, recompute edge protection from the current on-disk records of all kept rows (including previously failed/skipped candidate rows); if referenced, skip deletion and retain with edge protection reason.
  3. *Relative gitdir resolution:* When inspecting `.git/worktrees/<entry>/gitdir`, relative paths are resolved relative to `<entry>`, not process CWD.
  4. *Linked worktree and submodule repo support:* When `repo_path/.git` is a file, follow `gitdir:` and `commondir` to detect registered worktrees. If any repository worktrees directory exists but is unreadable (`OSError`), fail closed and retain the row with reason `"worktree registrations directory unreadable"`.
  5. *Deduplication of `--job` IDs:* Duplicate `--job` arguments are deduplicated via order-preserving deduplication; a fully successful run exits 0 with manifest status `"completed"` and empty `failed_ids`.
  6. *Mixed `--job` run reporting:* In `--job` mode, any requested job ID that was not deleted (such as non-terminal or running rows) appears in `failed_ids` (in both JSON and manifest) and in `skipped` with its retention reasons; run is marked `"partial"` and exits 1.
  7. *`--list` consistency with `--project`:* `_list_projects()` reuses `run_cleanup()` for each project to calculate `eligible_count`, ensuring edge-protected candidates are excluded and counts strictly equal `--project` candidates without duplicating logic. Invalid or missing `repo_path` rows are grouped under `"(unknown)"` with `eligible_count = 0`.
  8. *Fail-closed kept records:* Any kept row whose `job.json` is corrupt JSON, non-object, or unreadable causes all candidates across the store to be retained with a clear reason; dry run exits 0, and `--yes` deletes nothing.
  9. *Invalid `repo_path` handling:* Non-string or missing `repo_path` never matches any project in `--project` mode, is displayed under `"(unknown)"` in `--list`, and is ignored in explicit `--job` mode so eligible jobs can still be deleted by ID.
  10. *Known limitations (documented only):*
      - Symlinked job directory in `--job` mode exits 2 by design (SPEC §23/D89 symlink refusal).
      - Unicode NFC/NFD paths are not normalized (known limitation: filesystem-dependent path normalization for non-ASCII repo paths).
- *Tests:* `tests/test_cleanup_jobs_fixes.py`.

### D91: Fail-closed pre-delete recheck, non-UTF-8 git metadata, job-mode exit code, and blocker explainability
- *Rationale:* Addresses review findings 1–4 (round 2) for `cleanup_jobs.py`: fail-closed pre-delete edge recheck stopping all deletions when any kept job record becomes corrupt or unreadable; non-UTF-8 git metadata (gitdir, commondir, .git files, worktrees directory) failing closed as "cannot determine" without allowing one bad entry to hide valid registrations; `--job` mode exit code 1 with partial manifest when an eligible candidate fails deletion (removing exit-2 shortcut when nothing deleted); and explainable over-blocking where corrupt/unreadable blocker records are reported by UUID and full path with advice to inspect or remove manually, including `--list` warnings.
- *Rule:*
  1. *Pre-delete edge recheck fail closed:* Immediately prior to deleting each candidate, re-verify edge protection against all kept rows on disk (`store_jobs` excluding planned candidates). If reading any kept row's `job.json` raises (`json.JSONDecodeError`, non-object root, `OSError`, `UnicodeDecodeError`), deletion fails closed: candidate is not deleted, reported in `failed_ids` and `skipped` with a reason naming the corrupt kept record and its path, and deletion stops for all remaining candidates (which are marked failed with the same blocker reason).
  2. *Non-UTF-8 git metadata fail closed:* When inspecting repository `.git` files, `commondir`, or `.git/worktrees/<entry>/gitdir`, treat `UnicodeDecodeError` and unreadable files as "cannot determine" and retain the row with a clear reason naming unreadable registrations. Process worktree entries individually so an unreadable entry does not abort the loop or hide a valid registration in another entry.
  3. *`--job` mode exit code and manifest preservation:* In explicit `--job` mode with `--yes`, exit 2 with no manifest written only when every requested job ID is ineligible (`candidate_count == 0`). If at least one requested job was eligible and its deletion fails (or only a subset is deleted), exit 1 with manifest status `"partial"`, recording `planned_ids`, `removed_ids`, and `failed_ids`.
  4. *Explainable over-blocking:* When corrupt or unreadable job records block cleanup, human and `--json` outputs state the offending record's UUID and full directory path, declare it was left untouched, and advise the operator to inspect or remove it manually. In `--list` mode, human output prints a warning line naming the UUID, and `--json` output includes a top-level `"warnings"` list containing the UUID; `eligible_count` remains 0; no files or manifests are written and directory mtimes remain unchanged.
- *Tests:* `tests/test_cleanup_jobs_fixes2.py`.

### D92: Skipped-candidate recheck, invalid .git files, dry-run reporting, NUL path safety, unreadable store, and no-swallow guard
- *Rationale:* Addresses review findings 1–8 (round 3) for `cleanup_jobs.py`: candidates skipped or failed earlier in a run must be re-validated fail-closed as kept rows before subsequent candidate deletions; repository `.git` files that are not valid gitdir pointers must fail closed; edge collection from rows transitioning to kept must reuse parsed data rather than swallowing read errors; `--job` dry run must not report eligible candidates as failed or skipped; NUL bytes in `repo_path` must never crash the CLI and must preserve job-local worktree protections while skipping repo-level lookups; unreadable `store/jobs` directory must exit 2 with a single-line error and no traceback; and broad exception handlers must not swallow errors with pass.
- *Rule:*
  1. *Skipped-candidate pre-delete recheck:* Immediately prior to deleting each candidate, `_collect_kept_strings` excludes only remaining candidates (`planned_ids[idx:]`). All rows not being deleted (including candidates skipped or failed earlier in the run) are re-validated on disk fail closed. Any unreadable, corrupt, non-object, or undecodable record stops deletion of all remaining candidates; they are appended to `failed_ids` and `skipped` with a reason naming the blocking row, manifest status is `"partial"`, and CLI exits 1. Supersedes D89 rule 8 ("one failing row never aborts remaining deletions") for corrupt or unreadable rows to guarantee fail-closed edge protection across the entire store.
  2. *Invalid `.git` file pointer fails closed:* When `repo_path/.git` is a regular file, it must start with `"gitdir:"` and provide a non-empty pointer path. Empty files, garbage text, `"gitdir"` without colon, or truncated pointers retain the row with a clear reason stating the `.git` file is invalid or registrations are unreadable. A valid pointer with no registered worktrees remains deletable.
  3. *Edge collection from transitioning rows without swallowing:* Edge collection for candidates transitioning to kept status reuses parsed job data from the classification phase (`cand_raw_data`). No second disk read occurs and no broad exception handler swallows exceptions.
  4. *`--job` dry run reporting:* In `--job` dry-run mode, eligible requested jobs are reported exclusively under `candidates` (not in `failed_ids` or `skipped`). Only requested IDs that are actually ineligible are reported in `failed_ids` and `skipped` with reasons. Live deletion (`--yes`) behavior is preserved.
  5. *NUL byte and invalid path safety:* `repo_path` containing NUL bytes or raising during path normalization never crashes the process. In `--list` mode, rows are displayed under `"(unknown)"` with `eligible_count = 0`. In `--project` mode, rows are retained in `kept` with reason `"repo_path invalid"`. In `--job` mode, `repo_path` is ignored for matching, but job-local protections (`work/.git` and explicit `worktree_path/.git`) are preserved; only repo-registration lookups requiring a valid repository path are skipped.
  6. *Unreadable `store/jobs` directory handling:* If `store/jobs` is unreadable (e.g. `chmod 0o000`), `--list`, `--project`, and `--job` modes exit 2 with a clean single-line error on stderr, no traceback, and no files written. Directory read access and `--job` entry existence checks are guarded against `OSError` at the CLI entry point.
  7. *Known limitation (documented only):* `--list` mode executes a full dry-run scan per distinct project (`run_cleanup`), resulting in $O(\text{projects} \times \text{jobs})$ scaling (approximately 8 seconds for 300 distinct projects).
  8. *Structural no-swallow guard:* AST verification prohibits broad exception handlers catching `Exception`, `BaseException`, or bare except whose bodies consist solely of `pass`, `continue`, `break`, ellipsis, bare constant returns, or logging-only calls without recording a reason.
- *Tests:* `tests/test_cleanup_jobs_fixes3.py`.

### D93: Single-line `.git` pointer validation, target directory existence fail-closed, candidate-scan exception recording, and accepted limitations
- *Rationale:* Addresses review findings (round 4) for `cleanup_jobs.py`: multi-line `.git` files containing comments, extra gitdir directives, or additional lines must fail closed as invalid; pointer targets must exist and be directories; candidate scanning in `run_cleanup` must record unreadable/corrupt job rows as kept with clear reasons rather than silently continuing with an exception-swallowing handler; and accepted limitations regarding `--job` invalid `repo_path` lookup skipping, `--list` $O(\text{projects} \times \text{jobs})$ scaling, and job-ID sorted planned deletion order are explicitly documented.
- *Rule:*
  1. *Single-line `.git` pointer validation:* When `repo_path/.git` is a regular file, it must consist of exactly one non-empty line starting with `"gitdir:"`. A single trailing LF (`\n`) or CRLF (`\r\n`) is permitted and ignored, and surrounding whitespace around the target path is stripped. Any additional line (including comments, second `gitdir:` lines, or trailing blank lines) makes the file invalid: retains the row fail-closed with a reason naming the `.git` file as invalid (`"worktree registrations unreadable (.git file is invalid: unexpected extra lines)"`). Single-line CRLF pointers with a registered worktree remain kept with `"worktree registered"`; valid single-line pointers without registered worktrees remain deletable.
  2. *Pointer target existence and directory check:* The resolved pointer target path (resolved relative to `repo_path` for relative paths) must exist and must be a directory. If the target does not exist or exists but is not a directory, the row is kept fail-closed with a reason naming the `.git` file target as missing or not a directory and including the resolved target path (`"worktree registrations unreadable (.git file target missing: <path>)"` or `"worktree registrations unreadable (.git file target is not a directory: <path>)"`).
  3. *Candidate scan exception recording:* Candidate scanning in `run_cleanup` never swallows unhandled exceptions with `except Exception: continue`. Unreadable or corrupt `job.json` records encountered during candidate scanning are recorded in `kept` with a clear reason (`"job.json unreadable or corrupt: <exc>"` or `"job.json root is not an object"`), and the store-wide fail-closed edge collector (`_collect_kept_strings`) continues to treat any corrupt/unreadable kept job as a store-wide blocker. Satisfies the AST structural guard checker (0 offending handlers).
  4. *Accepted limitations (documented only):*
     - *(a) `--job` mode invalid/NUL `repo_path`:* In `--job` mode, if `repo_path` is invalid or contains NUL bytes, repo-level worktree registration lookup is skipped, while job-local protections (`work/.git` and `worktree_path/.git`) strictly apply.
     - *(b) `--list` scaling:* `--list` executes a full dry run per distinct project ($O(\text{projects} \times \text{jobs})$) to ensure exact consistency with `--project` eligibility.
     - *(c) Deterministic planned order:* Planned deletion order is strictly sorted by job ID (UUID string).
- *Tests:* `tests/test_cleanup_jobs_fixes4.py`.

### D94: commondir and worktree entry gitdir validation, fail-closed retention on invalid metadata
- *Rationale:* Addresses review findings (round 5) for `cleanup_jobs.py`: when `repo_path/.git` is a regular file (linked worktree or submodule), `_check_worktree_status` follows a `commondir` file to find worktree registrations in the common git directory. An unvalidated commondir file (extra comments/lines, empty, nonexistent target, regular-file target, or a directory commondir) previously failed open by silently skipping registration checks and deleting jobs with active registrations in the main repository. Similarly, worktree entry gitdir files (`<common>/worktrees/<entry>/gitdir`) were parsed without line/emptiness validation, causing invalid entries to either fail open (deleting jobs when empty) or incorrectly match via parent path components instead of failing closed with an invalid entry reason.
- *Rule:*
  1. *commondir file validation:* Validate `commondir` exactly like the `.git` pointer: allow at most one trailing LF (`\n`) or CRLF (`\r\n`), require exactly one non-empty line; resolve relative to the gitdir containing it; the resolved target must exist and be a directory. Any violation (unexpected extra lines, empty path, target missing, target not a directory, or commondir being a directory/non-regular file) retains the row fail-closed with a reason naming the commondir file as invalid and including the path. Unreadable or undecodable commondir files retain the row fail-closed as well (as today). Valid relative and absolute commondir files continue to be followed to `<common>/worktrees`.
  2. *Worktree entry gitdir validation:* For each worktree entry's `gitdir` file in `<common>/worktrees/<entry>`, require exactly one non-empty line (after at most one trailing LF or CRLF). If `gitdir` is not a regular file, has unexpected extra lines, or is empty, treat the entry as unreadable/invalid and record the reason so that when no other entry matches the job, the row is kept fail-closed with a reason naming the entry's gitdir file as invalid or unreadable. Matching of valid registrations is strictly preserved and not weakened.
  3. *Audit of git metadata paths:* Every git metadata path read in `_check_worktree_status` (`.git` pointer file, `commondir`, and worktree entry `gitdir`) is strictly validated and fails closed.
- *Tests:* `tests/test_cleanup_jobs_fixes5.py`.

### D95: Git metadata validation (HEAD regular file presence, BOM/control character rejection, symlink safety), duplicate-key rejection, and differential test acceptance oracle
- *Rationale:* Addresses review findings (round 6) for `cleanup_jobs.py` identified by the differential review harness against real git (`tests/cleanup/test_git_oracle_differential.py`). Resolves 16 git layout fail-open cases and 1 job.json duplicate-key fail-open case across Layouts A–F by fixing root causes R1–R5: validating that git directories contain a regular file named `HEAD`, rejecting UTF-8 BOM and control characters in all git metadata text files, handling dangling symlinks and non-git directories at `repo_path/.git` fail-closed, enforcing regular file requirements on `commondir` and worktree entry files, and rejecting duplicate keys in `job.json`.
- *Rule:*
  1. *R1 (Git directory validation):* Any directory followed into git metadata (pointer target of `repo_path/.git`, commondir target, submodule modules dir, bare repo dir, or `repo_path/.git` itself) must look like a git directory: it must contain a regular file named `HEAD`. If not, keep the row fail-closed with a clear reason naming the path and stating `"not a git directory"` (e.g. `"worktree registrations unreadable (.git file target is not a git directory: <path>)"`, `"worktree registrations unreadable (commondir target is not a git directory: <path>)"`, or `"worktree registrations unreadable (.git is not a git directory: <path>)"`).
  2. *R2 (Git metadata text file validation):* Any git metadata text file read by the cleaner (`.git` pointer, `commondir`, worktree entry `gitdir`, worktree entry `locked`) containing a BOM (U+FEFF), NUL byte (`\x00`), or any control character (other than at most one trailing LF or CRLF) is invalid: keeps the row fail-closed with a reason naming the file and invalid character/content. BOM is never silently stripped (no `utf-8-sig`).
  3. *R3 (`repo_path/.git` validation and symlink safety):* Absent `repo_path/.git` means not a git repo (fine, checked for bare repo if `repo_path/HEAD` is a regular file); a regular file must be a valid pointer to a git directory containing `HEAD`; a directory must look like a git directory containing `HEAD`. Anything else that exists or is a symlink (including dangling symlinks, empty directories, directories without `HEAD`, fifos, sockets, or special files) keeps the row fail-closed with an invalid or unreadable reason. A symlink that resolves to a valid regular file or git directory is allowed.
  4. *R4 (Duplicate keys in `job.json` fail closed):* Duplicate keys in any `job.json` the cleaner reads across all modes (`_classify_job_for_cleanup`, `_collect_kept_strings`, `run_cleanup` candidate scan, pre-delete candidate recheck, `--list` scan, `--job` mode) are treated as corrupt: parsed using `json.loads` with an `object_pairs_hook` (`_raise_on_duplicate_keys`) that raises `ValueError`, failing closed through existing corrupt-record paths and reasons (`"job.json is not valid JSON: duplicate key..."` or store-wide blocker `"store contains unreadable or corrupt kept job record..."`).
  5. *R5 (Entry and commondir regular file requirement):* `commondir` and all worktree entry files (`gitdir`, `commondir`, `locked`) must be regular files or symlinks resolving to regular files; dangling symlinks or directories keep the row fail-closed with an invalid entry or commondir reason.
  6. *Differential acceptance oracle:* `tests/cleanup/test_git_oracle_differential.py` (390 layout cases + 18 job.json cases + 6 baselines) is the normative differential acceptance oracle comparing `cleanup_jobs.py` against real git ground truth; zero fail-open and zero crashes are required.
- *Tests:* `tests/cleanup/test_git_oracle_differential.py`.

### D96: Marker-based union worktree root discovery and bare-repo HEAD validation (R6)
- *Rationale:* Addresses adversarial review findings (round 7) for `cleanup_jobs.py` identified by Opus. Two fail-open bugs were confirmed:
  1. F1: when `repo_path` has no `.git` and is a bare repo whose `HEAD` is mutated (directory, dangling symlink, FIFO, or deleted), the cleaner gated bare-repo worktree scanning strictly on `(repo_path / "HEAD").is_file()`, falling through to "not registered" (deletable) even though git cannot read the repository (returncode 128) and valid worktree entries remain on disk.
  2. F2: when a bare repo contains a `.git` directory inside it, precedence logic took the `.git.is_dir()` branch and looked only in `.git/worktrees/`, shadowing `repo_path/worktrees/` where registered worktrees reside, while git (`git -C bare`) correctly lists the worktree.
- *Rule (R6):*
  1. *R6a (Marker-based union of worktree roots):* Compute candidate worktree roots to scan as a union: `repo_path/.git/worktrees` if `.git` is a valid git dir or pointer target, AND `repo_path/worktrees` if `repo_path` looks like a bare repo. Precedence branching never shadows one root with another.
  2. *R6b (Bare-repo markers & HEAD validation):* A directory looks like a git repository if it contains any of the standard git markers (`worktrees`, `objects`, `refs`, `config`, `packed-refs`, `commondir`). When `.git` is absent or not a pointer/dir: if any marker is present, `repo_path` looks like a git directory and MUST contain a regular file (or symlink to a regular file) named `HEAD`; otherwise the row is kept fail-closed with `"worktree registrations unreadable (repo_path is not a git directory: <path>)"`. When none of the markers are present, `repo_path` stays "not a git repo" (deletable as before).
  3. *R6c (Nested/shadowed bare repo handling):* When `.git` is a directory or pointer file, `repo_path` is also checked for bare repository markers. If `(repo_path / "HEAD").is_file()` and git markers are present, `repo_path/worktrees` is added to candidate worktree roots. If `repo_path` has bare repository internals (`worktrees`, `objects`, `refs`) directly under `repo_path` but `HEAD` is not a regular file, it fails closed (`"repo_path is not a git directory"`).
  4. *R6d (Structural guard compliance):* All exception handling adheres to AST structural guard rules (0 broad exception handlers swallowing exceptions).
- *Tests:* `tests/test_cleanup_jobs_fixes6.py`.

### D97: Comprehensive repository structure validation and worktree_path registration matching (R7–R9)
- *Rationale:* Addresses review findings (round 8) for `cleanup_jobs.py` identified by Gemini review:
  1. Bug A: `repo_path` is a linked worktree (`.git` is a pointer file to `<common>/.git/worktrees/<name>`). When that worktree's private dir is missing `commondir` (or `HEAD` or `gitdir`), real git fails with exit 128, but stock cleaner returned deletable (FAIL-OPEN).
  2. Bug B: `job.json` has a custom `worktree_path` outside the job folder, and real git lists that path as a registered worktree (even if prunable). Stock cleaner only matched registration entries against `<job_dir>` and `<job_dir>/work`, omitting `worktree_path`, returning deletable (FAIL-OPEN).
- *Rule:*
  1. *R7 (Full repository file and directory enumeration):* For each repository kind, every file and directory git needs to treat a path as a working repo is enumerated and validated fail-closed:
     - *Normal `.git` dir:* `HEAD` (regular file, non-empty, readable), `objects` (directory, readable), `refs` (directory, readable), `config` (regular file, readable).
     - *Linked worktree private dir:* `HEAD` (regular file, non-empty, readable), `commondir` (regular file, non-empty, readable, pointing to existing directory), `gitdir` (regular file, non-empty, readable). When `commondir` is missing, the private directory is checked as a standalone git dir (submodule); if it lacks standalone git structure (`objects`, `refs`), it fails closed (`"worktree registrations unreadable (.git file target is not a git directory: <path>)"`).
     - *Common dir (commondir target):* `HEAD` (regular file, non-empty, readable), `objects` (directory, readable), `refs` (directory, readable), `config` (regular file, readable).
     - *Bare repo:* `HEAD` (regular file, non-empty, readable), `objects` (directory, readable), `refs` (directory, readable), `config` (regular file, readable).
     - *Submodule modules dir:* `HEAD` (regular file, non-empty, readable), `objects` (directory, readable), `refs` (directory, readable), `config` (regular file, readable).
     - Any missing, wrong-type (dir vs file), dangling symlink, FIFO, empty, or unreadable required file or directory keeps the row fail-closed.
  2. *R8 (Comprehensive worktree registration matching):* Registration matching compares each worktree entry's `gitdir` target against ALL paths owned by the job: `<job_dir>`, `<job_dir>/work`, `<job_dir>/work/.git`, and `<worktree_path>`, `<worktree_path>/.git` (resolved deterministically with path normalization, handling both absolute and relative paths against cwd and `job_dir`), whether or not `worktree_path` exists on disk. Any match marks the worktree as registered and keeps the row.
  3. *R9 (Structural exception safety):* Zero broad exception handlers swallow exceptions without recording reasons; adheres strictly to the AST structural guard.
- *Tests:* `tests/test_cleanup_jobs_fixes7.py`.

### D98: Strict unified git metadata validation, hanging safety, and packed-refs validation (R10–R12)
- *Rationale:* Addresses round 9 findings discovered during adversarial fuzzing of `cleanup_jobs.py` (Finding F3-1):
  1. `_validate_full_git_dir` validated `HEAD` using `_validate_git_file` (which only checked `raw.strip()`) and `config` using a bare `read_bytes()` without validating encoding or characters. When `HEAD` or `config` contained NUL bytes or UTF-8 BOM, git failed completely (exit 128), but the cleaner classified unlinked jobs as candidates and deleted them under `--yes` (FAIL-OPEN).
  2. Git metadata files could block or cause hangs if replaced with FIFOs or character devices, or cause OOM on oversized files, causing subprocess hangs or worker termination (exit 137). Real git hangs indefinitely when attempting to open a FIFO metadata file for reading.
- *Rule:*
  1. *R10 (Unified Git Metadata Reader and Content Validator):* Every git metadata file read by the cleaner (`HEAD`, `config`, `packed-refs`, `commondir`, `gitdir`, `locked`, `.git` pointer file) must pass through `_read_git_metadata_file`:
     - Checks `stat()` before opening to guarantee non-regular files (FIFOs, sockets, character/block devices) never block.
     - Enforces regular file (or symlink to regular file) and a maximum size cap of 1 MiB (`MAX_GIT_METADATA_SIZE`) preventing unbounded memory usage and OOM.
     - Strict UTF-8 decoding; rejects any UTF-8 BOM (`\xef\xbb\xbf` or `\ufeff`) and NUL bytes (`\x00`).
     - Single-line metadata files (`HEAD`, `commondir`, `gitdir`, `locked`, `.git` pointer) reject unstripped whitespace, multiple lines, and control characters (category `Cc` or code < 32).
     - `HEAD` content must match `ref: refs/<name>` (symbolic ref) or a 40/64-character hex object id; all other contents fail closed.
     - `config` files are validated for git config syntax (sections, valid key names, numeric values for `repositoryformatversion`, boolean values, no bare keys outside sections).
     - `packed-refs` files (if present) must be terminated by newlines, start with `# pack-refs with:`, and contain only comments, refs (`<sha> refs/...`), or peeled refs (`^<sha>`).
     - Any syntax or encoding failure fails closed and retains the job row.
  2. *R11 (Hanging and Subprocess Safety):* The cleaner never opens non-regular files without `stat()` inspection first, never reads unbounded data, and all test/cleaner subprocesses enforce explicit execution timeouts.
  3. *R12 (AST Exception Structural Guard):* Zero broad exception handlers swallow errors; all exception paths record reasons or raise, maintaining 0 offenders in `test_no_swallowed_exceptions_structural_guard`.
- *Tests:* `tests/test_cleanup_jobs_fixes8.py`.

### D99: Non-git cleaner hardening (iterative JSON depth caps, duplicate-key rejection in fixtures, store/project argument validation, and process-group liveness)
- *Rationale:* Addresses round 10 review findings (R13–R16) outside the git-metadata area:
  1. *R13 (Depth bomb RecursionError):* A deeply nested `job.json` (within 1 MiB size limit) caused `_extract_strings` to hit Python's recursion limit, resulting in an unhandled traceback in `--list` and dry-run exit 1.
  2. *R14 (Fixtures duplicate keys and unreadable store):* `cleanup_fixtures.py` used bare `json.loads` allowing duplicate keys to silently overwrite kept-job references (causing referenced jobs to be deleted under `--yes`), and exited 1 with "Unexpected internal error" on unreadable `jobs/` directory.
  3. *R15 (Empty and whitespace CLI arguments):* `--project ""` or whitespace silently resolved to the current working directory, and `--store` or `--job` required explicit whitespace refusal.
  4. *R16 (Process group liveness):* Liveness checks only checked leader PID; if the leader process exited but child processes in its process group remained alive, the job was falsely treated as dead and eligible for deletion.
- *Rule:*
  1. *R13 (Iterative JSON Depth Caps and Traversal):* `_extract_strings` is iterative using an explicit stack with depth capped at 128 (`MAX_JSON_DEPTH`). `_safe_json_loads` verifies nesting depth iteratively and narrowly catches `RecursionError` from `json.loads`, re-raising `ValueError`. Deep records fail closed as corrupt/unreadable across candidate scanning, kept-neighbor collection, pre-delete recheck, and `--list` mode.
  2. *R14 (Fixtures Duplicate-Key Rejection & Store Refusal):* `cleanup_fixtures.py` uses the shared `_safe_json_loads` loader with `object_pairs_hook` duplicate-key rejection, failing closed on duplicate keys. Preflights `store/jobs` directory readability and refuses with exit status 2 and single-line stderr error on `OSError`.
  3. *R15 (Empty/Whitespace Argument Refusal):* Arguments for `--project`, `--store`, and `--job` that are empty or contain only whitespace are refused with exit status 2 and a clean single-line error on stderr. `normalize_repo_path` raises `ValueError` on empty or whitespace strings.
  4. *R16 (Process Group pgid Liveness):* If `job.json` specifies `pgid`, `_check_pgid_status` verifies group liveness via `os.killpg(pgid, 0)` mirroring `_check_pid_status` rules (permission denied = alive, ESRCH = dead, bool/string/float/<= 0/> pid_max = keep fail closed).
- *Tests:* `tests/test_cleanup_jobs_fixes9.py`.

### D100: Round 11 finishing pass (raw empty `--store` refusal and no global recursion limit in production)
- *Rationale:* Independent re-verification of rounds 6–10 against HEAD found one surviving hole and one production-code smell:
  1. *Empty `--store` was not actually refused.* `argparse` `type=Path` normalizes `""` to `Path(".")` before `main` sees it, so the guard `if not str(store_path).strip()` never fired. Run from inside a store directory, `--store ""` silently operated on the CWD store, and `--store "" --yes` deleted a row there (repro: `~/agy-review/ds/probe_empty_store.py`). This contradicts SPEC §22/§23 and D99 R15 ("empty or whitespace-only ... refused with exit status 2"). The frozen test `test_r15_empty_and_whitespace_store_refused` only passed incidentally because the repository checkout used as CWD has no `jobs/` directory.
  2. *Production code changed the process-wide recursion limit.* `cleanup_fixtures.py` called `sys.setrecursionlimit(max(sys.getrecursionlimit(), 5000))` at import time only so a frozen test could `json.dumps()` a 1100-level payload. Production must never alter interpreter-global limits; deep input must be rejected iteratively at the default limit.
- *Rule:*
  1. *R17 (Raw-argument empty `--store` refusal):* `--store` is parsed as `str` and validated before conversion to `Path`, in both `cleanup_jobs` and `cleanup_fixtures`. Empty or whitespace-only values are refused with exit 2 and `Error: --store path cannot be empty or whitespace`; an empty value is never interpreted as the current directory. Explicit relative stores such as `--store .` keep working.
  2. *R18 (No global recursion limit in production):* Only tests may raise the recursion limit, and only to *generate* deep input (`tests/conftest.py`, 1000 → 5000). The cleaners always run at the interpreter default and convert `RecursionError` into a fail-closed corruption reason via `_safe_json_loads`; 100000-level nested files are rejected with exit codes 0/1/2, no traceback, and no signal across candidate, kept-neighbor, pre-delete recheck, `--list`, `--job`, `--project`, and the fixtures CLI (proof: `~/agy-review/ds/depth_proof.py`).
- *Tests:* `tests/test_cleanup_jobs_fixes10.py`; frozen `tests/test_cleanup_jobs_fixes9.py` unchanged.

### D101: Refactor waves 0–1 (machine-checkable freeze, CI, dead code, dedupe)
- *Rationale:* Execute the Part 1 audit's Wave 0–1 items without changing behavior: F6 (freeze only lived in docstrings), A6/A11/G4/G7 (subpackage-blind codemap generator, no CI, stale AST baseline, gitignore gaps), C1/C1a (five dead symbols, one a security drift), C2–C5 (four duplicated helpers) and E2/E4/C7 (missing WHY/docstrings and an unexplained lint exception).
- *Rule:*
  1. `tests/frozen.sha256` + `tests/test_frozen_manifest.py` are the freeze contract: a hash mismatch and a manifest/glob disagreement both fail; frozen files are never edited for style.
  2. The AST baseline must compare clean at every commit: regenerate it in the same commit as any src-AST change, leave it untouched for a comment/docstring-only commit (the guard strips docstrings, so that proves itself). It was stale on `main` and was repaired first.
  3. Shared code goes where the dependency already points: `jobs.record_cleanup_error`; `load_job_json` + `MAX_JOB_JSON_BYTES` and `_apply_edge_protection` in `cleanup_fixtures` for both cleaners; `server._run_tool` with `get_manager()` still resolved inside the worker thread; `make_error_envelope` = `StandardEnvelope` + an explicit omission list, guarded by a test that a new field must be consciously classified.
  4. `is_dangerous_allow_rule()` now flags `uv`/`uvx`; the deleted constant listed `uv` while the live check did not. Detection is warning-only, so over-blocking is the safe direction (D7).
- *Tests:* `test_frozen_manifest.py`, `tests/cleanup/test_load_job_json.py`, `tests/cleanup/test_edge_protection.py`, `test_server_error_envelope.py`, `test_server_wrappers_snapshot.py`, `tests/verify/test_dangerous_allow_rules.py`; the frozen cleanup suite and differential harness are unchanged and green.

---

## Multi-account Decisions (D102–D114)

### D102: Multi-account registry and implicit default account
- *Rationale:* Accounts must be declarative, validated and testable, and adding the feature must not change a single-account install. A `[[accounts]]` list in the existing config file is the smallest source of truth; one implicit `default` account preserves today's behavior exactly.
- *Rule:*
  1. `[[accounts]]` entries carry `name`, `home`, optional `pool`, `enabled`, `max_concurrent_jobs`, `expires_at` and `expected_email`/`expected_email_sha256`; `max_accounts` (default 5) caps the list. Unknown/duplicate names and invalid homes fail closed with stable codes.
  2. The implicit `default` account always exists (today's real home, no isolation override) and is not declarable or disableable; `[[accounts]]` entries are additional, the total is `1 + len(accounts)` and is tested for 1, 2 and 5 total accounts, including accounts sharing a `pool`.
  3. Accounts sharing a `pool` label are one quota bucket; the CLI exposes no sharing identifier, so sharing is operator-declared only.
- *Tests:* registry validation matrix (1/2/5, duplicates, pools, bad homes, limit), single-account equivalence class.

### D103: Email masking and identity verification
- *Rationale:* An account email is identifying; logs, job records and MCP responses must never carry the full value, yet the wrapper must be able to prove the selected account is the expected one.
- *Rule:*
  1. One `mask_email(email, head=3, tail=2)` helper reveals at most `head` leading and `tail` trailing local-part characters in the shape `head***tail@domain` and never more than half the local part; 1–2 characters are fully masked.
  2. The full email parsed from `applyAuthResult` stays in memory only. Verification compares the full value with `expected_email` or its SHA-256 with `expected_email_sha256`; the masked string is never compared.
  3. `job.json`, audit logs, `usage.jsonl` and MCP envelopes carry the registry name and masked email only. A mismatch is `account_identity_mismatch`; a missing auth line is `account_identity_unknown` (blocked only when an expectation is configured).
- *Tests:* mask table (short/long local parts), planted-full-email grep over new outputs, verification mismatch/unknown.

### D104: Per-account isolation environment and real-home worktree preflight
- *Rationale:* Human testing 2026-11-03 proved that overriding `HOME` alone leaks the host keyring and that `HOME` plus a dead D-Bus isolates a second login. Worktrees, however, stay in the user's real filesystem.
- *Rule:*
  1. Every non-default account runs with its own `HOME` and `DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`; the default account keeps exactly today's env (no D-Bus key).
  2. Worktree preflight and host-path audit use the invoking user's real home; an account home is never added to `allowed_workdir_roots`. Repositories outside the real home still require an explicit `allowed_workdir_roots` entry.
  3. Account homes are mode 0700 and hold agy's file token; the wrapper never reads token, keyring or cookie material.
- *Tests:* env builder (D-Bus set only for non-default, no leak), preflight with a redirected account home, single-account equivalence.
- *Correction (2026-11-03):* The rule was not applied to every agy spawn. The scheduler's `/usage` probe passed only `home=`, so it reached the ambient desktop bus and authenticated as the primary login (`human test 2026-11-03`); the at-launch, preflight-guard and supervisor final readings had the same flaw. `usage.get_usage`/`read_usage_once` now take the `AccountConfig` and build the child env only through `accounts/env.py:build_account_env`, and an AST guard rejects a future spawn that bypasses it. Audit: `doc/audits/2026-11-03-multi-account/05-usage-env-bug.md`.

### D105: Shared state store with per-account caps and reservations
- *Rationale:* Job ids, recovery, cleanup and `agy_list` already assume one store; splitting it per account would split the concurrency lock and duplicate recovery. Per-account caps are counters, not stores.
- *Rule:*
  1. One state root; the account name is a job field. Per-account daily-cap files are `daily_cap.<name>.json`/`.lock`; the default account keeps the historical unsuffixed names.
  2. Global `max_concurrent_jobs`/`daily_jobs_cap` still apply; each account also has `max_concurrent_jobs` and the global daily cap. The count, increment and reservation are taken under one `concurrency_lock`.
  3. A running job reserves its estimated cost against its account until finalization; the scheduler subtracts reservations.
- *Tests:* per-account cap matrix, reservation release on terminal states, default-account file names unchanged.

### D106: Per-account permission policy parity
- *Rationale:* Human testing 2026-11-03 showed a fresh account auto-denying a command tool because headless mode cannot prompt; the wrapper's safety assumes the policy under `HOME`. A weaker account policy is a security regression.
- *Rule:*
  1. A new account receives a copy of the primary's `$HOME/.gemini/config/config.json` in the **manual recipe**, never by a silent runtime write.
  2. Before every start on an account, the wrapper verifies the file exists, parses, and its normalized `permissions.allow` set equals the primary's. Missing or drifted policy refuses the start (`account_permission_policy_missing`/`account_permission_policy_drift`); `--doctor` reports the same.
  3. The wrapper never mutates or weakens an account policy at runtime.
- *Tests:* parity pass/drift/missing, doctor reporting, no write into an account home.

### D107: Per-account usage pools, cache key and usage-log attribution
- *Rationale:* Two accounts can have wildly different remaining quota and resets; a cache keyed by binary returns account A's quota for B, and unattributed rows cannot be audited.
- *Rule:*
  1. `/usage` is parsed per account into both pools and both windows (`remaining_fraction`, `reset_time`); failures are soft and recorded with a reason.
  2. The cache key includes the account name/home and binary. The default account keeps the old key shape.
  3. `usage.jsonl` adds `account` (registry name) and `masked_email` only when a registry is configured; rows and the no-registry path stay byte-identical.
  4. Only scheduling candidates are refreshed, with a short max age (default 60 s); one failed account never blocks a start that has another usable account.
- *Tests:* per-account cache isolation, malformed/missing/not-signed-in, additive log fields, single-account log equality.

### D108: Quota-aware account scheduler
- *Rationale:* Selecting by remaining quota alone wastes quota that is about to expire and starts jobs that cannot finish; the decision must be pure, deterministic and tunable.
- *Rule:*
  1. Cost = `tokens_per_minute (default 35300) × min(max_minutes, expected_minutes (default 30)) × coefficient (0.011 5 h / 0.0031 weekly)`, optionally refined from history; it is a heuristic, never a fact.
  2. An account is eligible only if enabled, identity/policy/model verified, under its concurrency cap after reservations, with five-hour remaining ≥ floor (default 15%) and both windows ≥ cost × safety factor (default 1.5).
  3. Ranking score = `weekly_remaining / hours_to_weekly_reset` plus an `expires_at` boost within 168 h; ties by lower load, then oldest use, then name. The clock is injected.
  4. With no eligible account the result is `quota_exhausted` with the earliest plausible `resume_after`; the job keeps the existing state and there is no fallback to another tool.
- *Tests:* table-driven filter/ranking/reservations, the worked example, N=1/2/5, shared pool, deterministic ties.

### D109: Background priority and burn-before-expiry
- *Rationale:* Some accounts have quota that will expire unused; normal jobs still need a hard fit.
- *Rule:*
  1. `priority` is `normal` (default) or `background`, accepted by `agy_start` and stored on the job.
  2. Background jobs relax the cost filter when the longest wait until all relevant windows of the pool are usable again is ≤ `burn_horizon_minutes` (default 90); burn-eligible accounts rank before merely-strict ones, then by nearest binding reset, then the smallest remaining quota.
  3. A background job may not take an account's last free slot while a normal job is queued for it. Paused time does not count against `cumulative_minutes` (verified in code), so no budget change is made.
- *Tests:* burn eligibility boundary, worked example, anti-starvation, background tie-breaks.

### D110: Job pinning fields additive without a schema bump
- *Rationale:* `account`/`priority`/transfer links must persist, but old records and frozen cleaner fixtures must stay valid and single-account `job.json` must not change.
- *Rule:*
  1. Job gains optional `account`, `priority`, `transferred_from`, `transferred_to`; `from_dict` defaults them.
  2. `to_dict` emits `account`/`priority` only when they differ from the single-account default and emits transfer fields only when set. `CURRENT_SCHEMA_VERSION` stays 1 (the cleaner ignores unknown keys and old fixtures unchanged).
  3. Resume, auto-fix, plan nudge and follow-up always stay on the pinned account.
- *Tests:* old-record round-trip byte equality, new-field round-trip, cleaner fixtures unchanged, pinned resume/auto-fix.

### D111: Conversation transfer by db copy with a lock, and summary handoff
- *Rationale:* Phase 1 proved one `conversations/<id>.db` carries a multi-turn, tool-using conversation to another account, and that a missing id silently starts fresh. Copying must therefore be verified and locked, with a factual fallback.
- *Rule:*
  1. Transfer triggers when the pinned account's wait exceeds `handoff_min_wait_minutes` (default 120) or the caller sets `allow_handoff=True`; the target must be eligible.
  2. Copy only the conversation `db` while no process uses it, after a SQLite header check and a source-existence check; resume with `--conversation <id>` on the target. Hold a per-conversation lock so one id is never on two accounts at once; keep the source until the new run finishes.
  3. If the target is also exhausted, or the db is missing/corrupt/locked, continue the **same job** with a summary handoff run built from wrapper facts only (goal/constraints, plan done/remaining, branch, git log/diff stat, capped delimited report excerpt, verify command). Reuse the worktree/branch, WIP-commit dirty work first, and record `transferred_from`/`transferred_to` as account names.
  4. A shared or symlinked conversation store is explicitly unsupported (SQLite contention, desktop app, cleaner conflicts).
- *Tests:* copy success, corrupt/locked db, missing id, B also exhausted, id collision, repo changed, concurrent-use lock, handoff prompt bounds.

### D112: Cleaner compatibility with new job fields
- *Rationale:* The cleaner is frozen and must stay correct without being edited.
- *Rule:*
  1. New job fields are optional and are ignored by the cleaner's fail-closed `job.json` loader; the 13 frozen files and `tests/frozen.sha256` stay untouched.
  2. Serialization stays conditional, so old fixtures and default-account records are byte-identical; the cleaner needs no per-account logic.
- *Tests:* the frozen cleanup suite, `tests/test_cleanup_fixtures.py`, and a byte-equality test for a default-account job record.

### D113: Installer and doctor are read-only for accounts
- *Rationale:* The wrapper must never touch credentials; the installer already refuses risky actions and the doctor is a read-only health check.
- *Rule:*
  1. The manual recipe is documented and may be printed; the installer never logs in/out, never runs `agy auth`, and never writes an account home except a policy copy the human explicitly requests.
  2. `--doctor` parses the registry, checks home existence/mode 0700, policy parity, expected email (via a short isolated `/usage` with a timeout), and isolation env, printing masked emails only; any problem exits 1.
- *Tests:* installer-style hermetic doctor tests (healthy, missing home, mode, policy drift, email mismatch), no-login/no-write assertions.

### D114: Legitimacy and terms-of-service boundary
- *Rationale:* Quota-sharing across accounts can violate a provider's terms if abused; the feature must be a selector among the user's own logins, not an account factory.
- *Rule:*
  1. Accounts must be ones the user legitimately owns; the registry is a selection list, not a credential store.
  2. The wrapper never creates accounts, never automates sign-up, and never accepts credentials in prompts or config.
  3. Adding an account is a human login performed outside the wrapper; the wrapper only runs jobs on the resulting login and never reads or copies a token, keyring entry or cookie.
- *Tests:* documentation review; no credential-access code path.

### D115: Full windows have no urgency and account selection carries a decision trace
- *Rationale:* Two 2026-11-04 human-test findings. (1) A live `/usage` window at 100% remaining carries a rolling placeholder reset (for example, both pools' five-hour rows on an untouched account show the same reset five hours out). The urgency formula `remaining_weekly_pct / hours_to_reset` therefore scored a full window highest precisely when nothing was at risk, so a placeholder reset could outrank quota that was really about to expire. (2) `account_selected` recorded only the winner's name, so an operator could not tell why it won.
- *Rule:*
  1. A window whose remaining percentage is at or above `100 - account_full_window_margin_pct` (default `0.1`) is **full**. A full weekly window contributes zero urgency; the `expires_at` boost is unaffected. A full window's wait is zero, so it is never the binding window for a background burn.
  2. `schedule_account` returns a `DecisionTrace`: one bounded row per considered account with `eligible`, a `rejection` code when not, and for eligible accounts the pool, effective weekly/five-hour remaining, hours to the binding reset, urgency score (with the boost), and 1-based rank, plus the `winner` and the deciding `rule` (`urgency`, `expires_at`, `load`, `lru`, `only_candidate`, `explicit_account`, `force`). Rows are capped at `MAX_TRACE_ACCOUNTS` (32) and masked emails are attached only by `selection.py` from the usage reading.
  3. `create_job` appends `decision_trace` to `account_selected.detail` only when a registry selection produced one. With no registry the event is unchanged (and normally absent), and a supplied trace never changes the existing `account`/`account_masked_email` keys.
  4. The full-window margin is a validated config key, not a buried literal; the trace cap is a named constant with a docstring.
- *Tests:* table-driven urgency/full-window cases including the 96%-used/1-hour worked example and a full-window alternative, the tunable-margin boundary, burn-binding-window selection, N=1/2/5 accounts, a shared pool, background priority, trace content/rule/rank, the audit emission and no-registry byte-identical detail, the trace cap, and the planted-email hygiene test.

### D116: The implicit default account is optional and verifiable
- *Rationale:* A 2026-11-05 human observation: the implicit `default` account used the shared desktop keyring. The Antigravity chat app writes that keyring when the user switches accounts, so `default` silently became the same login as the configured `account-b`; plain `agy -p "/usage"` returned account-b's exact quota. `default` had no identity expectation, so nothing noticed, and the scheduler counted one quota pool as two accounts. The user wants every agy-mcp account to be an isolated HOME, including the main account, so agy-mcp never depends on the shared keyring.
- *Rule:*
  1. The new optional `[default_account]` table has `enabled` (bool, default true) and at most one of `expected_email` / `expected_email_sha256`, validated exactly like an `[[accounts]]` entry. With no table and no `[[accounts]]` the built registry, the scheduler path, `agy_usage` and `job.json` are byte-identical to before.
  2. With `enabled = false`, the scheduler never picks `default`, an explicit `account="default"` is refused with `default_account_disabled`, a job already pinned to default (including a legacy `account=None` record) fails resume with the same code and is never moved, and a registry with no enabled account fails with `account_none_enabled`.
  3. An expectation makes the default verifiable: the identity check runs on start, resume and `agy_usage` exactly like a declared account, and a mismatch is `account_identity_mismatch` / `account_identity_unknown` and never starts.
  4. `agy_usage` gains an optional `account` parameter. No argument keeps the historical default reading; with the default disabled and no argument, the first enabled account by name is used and the additive `account_name` key reports which one. A named disabled account is refused, never silently replaced.
  5. Shared-pool safety: every registry-scoped `/usage` probe carries a non-reversible digest of the observed login. Enabled accounts with the same digest are one quota pool when at least one member verified an expectation; the declared account's isolated reading is authoritative for the implicit default. A group where every name is unverified is reported by the doctor but not merged, because the legacy masked shape cannot prove identity.
  6. The doctor warns (never fails) when the default is enabled, has no expectation, and other accounts exist (`shared_keyring`), and reports two enabled accounts with the same observed login (`shared_identity`).
  7. The recommended setup documents isolating the main account into its own HOME and disabling the default, so agy-mcp never touches the shared keyring.
- *Tests:* config parsing and validation for the table; 0/1/2/5 accounts; default disabled (start, explicit name, resume of a pinned legacy job, no enabled account); default expectation match/mismatch; verified duplicate pool merge and unverified doctor report; `agy_usage` with/without an account and with the default disabled; no-table single-account equivalence; the updated non-frozen tool schema and wrapper snapshot.

### D117: A human-run account CLI with a login handoff
- *Rationale:* Editing `[[accounts]]` by hand and repeating the isolation/login/policy recipe made multi-account setup error-prone, and a mistyped block could silently drop unrelated comments or keys. The credential boundary must not move: if the wrapper automated the Google sign-in it would be handling credentials and crossing D114. The smallest safe command therefore edits config conservatively and hands the terminal to an isolated `agy` for the human.
- *Rule:*
  1. Entry point is `agy-mcp account list|add|remove|enable|disable` (also `python -m agy_mcp account ...`). `agy-mcp` or `python -m agy_mcp` with no subcommand still starts the stdio server byte-for-byte as before; only a literal first argument `account` dispatches to `accounts/cli.py`. No second console script is required.
  2. Writing commands are dry runs by default; `--yes` applies and `--dry-run` wins. A write holds `.<config>.lock`, keeps a timestamped `config.toml.<UTC>.bak`, and replaces the file with temp + fsync + rename (0600). The edited text is re-parsed with `tomllib` and the real schema before writing; a concurrent change is refused.
  3. Config edits are conservative line spans, never a TOML round-trip. CLI-written blocks carry `# agy-mcp:account <name> begin/end`; hand-written blocks are found by parsing their `name` and only the span from the `[[accounts]]` header to the next table header (or EOF) is edited. Ambiguous or corrupt blocks are refused instead of guessed. Comments and unrelated keys stay byte-identical.
  4. `add` never authenticates and never reads a token, keyring entry, cookie or credential file. It creates a 0700 home, proves isolation for a fresh/empty home with the isolated `/usage` (dead D-Bus) and aborts on any quota row (`account_cli_isolation_leak`). The proof ends as soon as the `Authentication required` marker appears: it reads the probe's merged stdout/stderr incrementally and terminates the probe's process group instead of waiting for agy's own login timeout, and it never logs or stores the auth URL that follows the marker. Then it launches interactive `agy` in that isolated environment attached to the human's terminal. The test seam is `CliSeams.login_runner`; no pty is used in tests.
  5. After login the CLI verifies the isolated `/usage`, asks for the expected email with hidden input (`getpass`), compares the typed value's SHA-256 with the observed login digest, and stores only `expected_email_sha256`. The full observed address never leaves `usage.read_usage_once` and the full typed address is never echoed, logged or stored.
  6. `add` copies the primary permission policy if present and writes a minimal `.gemini/antigravity-cli/settings.json` from `allowed_workdir_roots`, both 0600 and both never overwriting an existing file. It then appends the block, runs the per-account doctor checks and prints the restart hint. It is idempotent and resumable: a failure after the home exists keeps the home and prints what was done plus the exact resume command; an identical block is not rewritten.
  7. `remove` refuses the implicit default, refuses while any pinned job is active (exit 3, with the running-job message), and warns plus requires the typed phrase `remove account <name>` when paused/resumable jobs would become unresumable. It removes only the block. `--delete-home` additionally requires a real directory (not a symlink) directly under `--accounts-root`, no non-terminal pinned job, and the phrase `delete account home <name>`; it never follows symlinks and prints the server-side Google revocation reminder.
  8. `enable`/`disable` flip only the `enabled` line, preserve comments, and `disable` warns (never refuses) about active jobs and about leaving no enabled account. `disable default` writes `[default_account] enabled = false`.
  9. `list` is offline by default (names, flags, home/mode, active/paused counts, identity-hash presence); `--probe` adds one isolated `/usage` per account and shows only masked emails and quota. No CLI output prints a full email address.
  10. Stable exit statuses: 0 success/dry run, 2 usage/validation, 3 safety refusal, 4 conflict/missing target, 5 identity/isolation, 6 config/I/O, 1 unexpected. The codes and their mapping are documented in `doc/reference/cli-accounts.md` and `doc/reference/exit-codes.md`.
- *Tests:* `tests/test_accounts_cli.py` (happy add with injected login, non-TTY, isolation leak, wrong expected email/hygiene, idempotence, limit/duplicate, active/paused removal, hand-written byte preservation, ambiguity, delete-home safety, default removal, enable/disable, list offline/probe, untouched homes, `python -m agy_mcp` server start) and `tests/test_accounts_config_edit.py` (spans, markers, default table, backup/atomic/dry-run/concurrent change).

## Sources

- `src/agy_mcp/` — codebase architecture and implementations corresponding to D1–D117.
- `tests/` — automated test suites validating architectural and security decisions.






