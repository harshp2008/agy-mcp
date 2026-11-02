TOOL USED: dsh, MODEL: deepseek-flash, EFFORT: high

Documents read: [SPEC.md](../doc/design/spec.md), [docs/DECISIONS.md](../doc/design/decisions.md), [docs/QUESTIONS.md](../doc/project/history.md), [docs/AGY_FINDINGS.md](AGY_FINDINGS.md), [docs/AGY_FINDINGS_2.md](AGY_FINDINGS_2.md), [docs/AGY_FINDINGS_3.md](AGY_FINDINGS_3.md). Read-only; nothing written.

# FINDINGS

## 1. F01 — BLOCKER — Worktree isolation does not confine writes, and the spec says it does
**Section:** SPEC.md:35–42 (Threat Model & v1 Controls), SPEC.md:78–81 (Worktree Isolation), SPEC.md:185 (README table).
**Problem:** The spec's central containment claim is `"The user's working tree is untouched"` and `"Write access: Writable in the local workspace worktree and `/tmp`; external `$HOME` paths denied."` But T1-e proves agy's whitelisted `git diff` accepts *arbitrary arguments* (`git diff --no-index ../outside/fake_secret.txt /dev/null` was allowed), and `git diff --output=<file>` is a supported read-to-file redirection in git 2.43 (checked locally: `--output=<file> Output to a specific file instead of stdout`). `git log`/`git show` accept diff options too. So the default whitelist contains a write primitive that takes an arbitrary absolute path: `git diff --output=~/.bashrc`, `git diff --output=~/repo/.git/config`. The wrapper's audit (SPEC.md:135–142) watches only the worktree `.git` pointer, `.gitattributes`, `.gitmodules`, `.agents/` — it cannot see host writes at all. The same prefix-matching gap gives `git diff` unrestricted *reads* (already proved), so the README's "external `$HOME` paths denied" sentence is false in both directions.
**Why it matters:** This is the difference between "v1 has weak isolation" and "v1 has no isolation". A hostile repo file can steer agy to overwrite `~/.bashrc`, `~/.ssh/authorized_keys`, `~/.config/agy-mcp/config.toml`, or the user's main repository `.git/config`, and the audit will report a clean job. The spec will also mislead the builder into believing the audit is a containment boundary.
**Replacement:** Replace the Controls bullet with:
```markdown
- **v1 Controls (what they actually do):** path-based write confinement from agy's *file tools* is
  best-effort only; the audit is a *detector for a small set of repository execution vectors*, not a
  sandbox. agy's whitelisted read commands accept arbitrary arguments, which include output
  redirection (`git diff --output=<file>`), so agy can write any path the host user can write and read
  any path the host user can read. Isolation from the user's filesystem is a v2 (container) property.
```
Replace SPEC.md:185 with:
```markdown
- Write access: workspace worktree, `/tmp`, and — via whitelisted commands with unrestricted
  arguments (e.g. `git diff --output=<path>`) — any path the host user can write. Treat the machine
  as fully accessible to agy.
```
Add to Open Items: "Probe `git diff --output=<path>`, `git log -p --output=<path>`, and `git show --output=<path>`; treat any success as a confirmed host-write primitive."

## 2. F02 — BLOCKER — Mid-job creation of `.agents/hooks.json` is detected only *after* it may already have executed
**Section:** SPEC.md:135–142 (audit + enforcement), SPEC.md:137 (checkpoints).
**Problem:** The spec audits `"Before launch, every 5 seconds before staging in the watcher, and after job completion."` T6 says lifecycle hooks run `sh -c` on `SessionStart`, `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop`, are enabled by default, and are not gated by `permissions.allow`. The audit is a *post-hoc hash comparison*; it cannot prevent the first execution. A steered agy can write `.agents/hooks.json` (T3-a3 proves `.agents/` write access) and then call any tool; if hooks are read per tool call, the payload executes before the next 5 s tick; if they are read at session start, it executes on the next `agy_resume`/fix-turn launch, which the spec never audits at all. Detection is not the same as prevention, and the spec's enforcement language implies otherwise.
**Why it matters:** This is arbitrary host code execution as the user — the primary risk the whole design exists to stop — achieved by one repo-file injection plus one file write. It also survives into every subsequent resume/fix turn of the same job.
**Replacement:** Replace the Audit Checkpoints paragraph and Enforcement paragraph with:
```markdown
- **Audit Checkpoints:** immediately before every `agy` launch **and every relaunch** (resume, fix
  turn); on a 5-second timer for the whole life of the job, independent of COMMIT_MSG activity; and
  after job completion. A checkpoint that has not passed is a hard gate: no launch.
- **Enforcement and limits:** the audit is a *detector*. Creating `.agents/hooks.json`,
  `.agents/plugins/**`, `.agents/skills*`, or modifying the worktree `.git` pointer is fatal: kill
  the process group (SIGTERM, then SIGKILL after 3 s), do not commit, set `needs_review`. The audit
  cannot prevent a hook from running between creation and the next checkpoint; v1 discloses that
  residual risk in the README and does not claim prevention.
```
Also add to the preamble: `"Never create or modify .agents/, AGENTS.md, GEMINI.md, or .git*; the wrapper detects it and destroys the job."`

## 3. F03 — BLOCKER — `.agy/` and `.agy/PLAN.md` are never provisioned, so the commit protocol may never run
**Section:** SPEC.md:115–118 (Wrapper Commits Everything), SPEC.md:162–164 (preamble 4–5).
**Problem:** The spec requires agy to write `.agy/COMMIT_MSG` and `.agy/PLAN.md`, but nothing creates `<jobdir>/work/.agy/`. Probe Q2 shows `"Make a directory ... (mkdir -p newfolder, git add): DENIED"`, so agy cannot create the directory with a shell command. Whether `write_file` auto-creates missing parent directories is untested; if it does not, every write fails, agy never emits a commit message, and the entire D3 watcher silently degrades to the single safety-net commit.
**Why it matters:** This is the load-bearing mechanism of the whole product. A design that depends on an unverified tool behaviour, with no pre-creation and no test, is not buildable as written.
**Replacement:** Add to the Job Directory Layout section:
```markdown
- **Worktree provisioning:** after `git worktree add`, the wrapper creates `<jobdir>/work/.agy/`
  (mode 0700) and seeds `.agy/PLAN.md` with the task title and the standing-prompt checklist, before
  launching agy. It verifies with a sentinel write inside `.agy/` that the directory is writable.
```
Add an Open Item: "Verify `write_file` creates parent directories; if not, the pre-created `.agy/` is mandatory."

## 4. F04 — HIGH — `.git/info/exclude` mutates the user's repo, and it is the wrong mechanism for a security exclusion
**Section:** SPEC.md:127.
**Problem:** Quote: `"**Exclusion:** `.agy/` is excluded from git in the worktree by writing to `.git/info/exclude` in the common dir."` In a linked worktree the common dir is the user's `<repo>/.git`, so this permanently edits the user's repository metadata (QUESTIONS Q2 flags exactly this and recommends otherwise; SPEC did not adopt the recommendation). Worse, `git help gitignore` (checked locally) gives precedence `.gitignore` > `$GIT_DIR/info/exclude` > `core.excludesFile`, so a repository-supplied `.gitignore` can negate the exclusion (subject to the excluded-parent-directory caveat, which depends on whether the wrapper writes `.agy/` or `.agy/*`). If re-included, the wrapper's `git add -A` commits `.agy/COMMIT_MSG` and `.agy/PLAN.md` — including the model's assumptions — into the user's branch. The Q2 alternative (`core.excludesFile`) is also unsafe: overriding it discards the user's global ignore file, which can cause unrelated files to be staged.
**Why it matters:** It breaks "The user's working tree is untouched" in spirit, is racy if two jobs share the repo, and delegates a security-relevant exclusion to a precedence chain a hostile repo can influence.
**Replacement:** Replace with:
```markdown
- **Exclusion:** the wrapper never writes to the user's `.git`. All wrapper `git add` calls pass an
  explicit pathspec that excludes the metadata tree, e.g.
  `git add -A -- . ':(exclude,top).agy'`, and the wrapper additionally filters the `git status
  --porcelain -z` list in Python before staging. `.agy/` never enters the index.
```
Add: "The wrapper must not use `.git/info/exclude`, `.gitignore`, or `core.excludesFile` for any security-relevant exclusion, because repository content can influence those."

## 5. F05 — HIGH — The safety-net commit contradicts the audit's "refuses to commit any pending changes"
**Section:** SPEC.md:125–126 vs SPEC.md:141–142.
**Problem:** Audit enforcement says `"refuses to commit any pending changes, sets state to needs_review"`; the safety net says `"At normal job completion, if uncommitted changes remain in the worktree, the wrapper creates a fallback commit `agy: <title>`"`. After a violation the job is still "complete", so the safety net would create a commit and the state would have to move somewhere. DECISIONS D3 is even broader: `"Upon job termination"` — that includes `agy_cancel` and `failed`. The spec never says which wins, or what state results.
**Why it matters:** Two mutually exclusive commit policies and three inconsistent triggers (SPEC D3, DECISIONS D3, audit enforcement) will produce either a committed violation or a lost state transition, depending on the builder's reading.
**Replacement:** Replace the Safety-Net bullet with:
```markdown
- **Safety-Net Commit:** only if the latest audit passed, the job is not `needs_review`, and the state
  is `done`/`timeout`/`failed`, the wrapper commits remaining changes as `agy: <title>` using the
  same staging filter as the watcher. It never runs after an audit violation, and it is the last
  commit before the terminal state is written.
```
Change DECISIONS D3's "Upon job termination" to "On `done`, `timeout`, `failed`, and `cancelled` (not after an audit violation)".

## 6. F06 — HIGH — No crash recovery: the watcher, state, and exit code live in the MCP process
**Section:** SPEC.md:118 (watcher), SPEC.md:104–105 (job directory).
**Problem:** Quote: `"A background watcher in the wrapper polls ..."`. Nothing survives a dsh/MCP-server restart. agy is launched with `start_new_session=True` and its stdout/stderr are the wrapper's pipes, so when the wrapper dies the job either receives `SIGPIPE`/`EPIPE` and dies mid-work, or survives with no parent to `waitpid`, no watcher, no commits, no verify, and no result. There is no startup scan of `~/.local/state/agy-mcp/jobs/*/` to reattach, and `job.json` writes are not specified as atomic.
**Why it matters:** The product's use case is multi-hour jobs driven from a GUI session that will be restarted. Without a reaper, every restart silently converts a running job into an unmanaged orphan writing into a worktree that nobody will merge, and the orchestrator sees the job as gone.
**Replacement:** Add a "Crash Recovery" subsection:
```markdown
- `stdout.txt`, `stderr.txt` are opened as files by the wrapper (not pipes), so agy survives a wrapper
  crash. `job.json` is written atomically (tmp + rename + fsync) and records pid, process start time
  (from /proc/<pid>/stat), state, branch, worktree, and conversation id.
- On startup the server scans all job directories. For a job recorded `running`: if
  /proc/<pid> exists and its start time matches, reattach the watcher and event poller; otherwise
  read the captured files, classify the result, run the post-completion audit, commit/verify as
  appropriate, and finalize the state. Jobs that cannot be classified become `failed` with
  `recovered=true` and the reason in job.json.
- A job is never left `running` with no observable process after startup reconciliation.
```
Add an Open Item: "Decide whether surviving `agy` processes are adopted or killed on startup; adoption requires PID-reuse protection via start time."

## 7. F07 — HIGH — No time-based checkpoint commit; a job that never writes COMMIT_MSG has zero commits until the end
**Section:** SPEC.md:125–126, SPEC.md:118–124.
**Problem:** The only commits are (a) watcher-triggered by COMMIT_MSG hash changes and (b) one safety net `"At normal job completion"`. A 240-minute job where agy forgets the instruction (very likely; instruction-following is not a guarantee and the preamble is one line among nine) has *no* commit until completion, and no commit at all if the wrapper dies, the machine reboots, or the job is cancelled. The state `cancelled` (SPEC.md:73) has no commit behaviour at all.
**Why it matters:** The whole value proposition is "commit iteratively as they work". The failure mode is total work loss on the most likely interruption, and a single unreviewable 4-hour commit in the normal case.
**Replacement:** Add to the Watcher section:
```markdown
- **Checkpoint Commit:** independent of COMMIT_MSG, every `checkpoint_commit_minutes` (default 10),
  if the worktree has staged-able changes, the wrapper commits `agy: checkpoint <timestamp>` with a
  body listing the changed paths. Before any kill (audit violation excepted), before cancel, at
  state transitions, and before the wrapper exits, it performs one final checkpoint commit.
```
Add `checkpoint_commit_minutes: Default 10` to Limits and Configuration.

## 8. F08 — HIGH — D6's patterns are over-broad, silently produce partial commits, and are presented as a complete blacklist
**Section:** SPEC.md:128–132.
**Problem:** Quote: `"The wrapper never stages or commits files matching these case-insensitive patterns: `.env*`, `*.pem`, `*.key`, `id_rsa*`, `*secret*`, `*credential*`, `*.p12`, `.npmrc`, `.pypirc`, and any path under `.agents/` or `.git*`."` As globs against the repo-relative path, `.git*` also matches `.gitignore`, `.gitattributes`, `.gitmodules`, and `.github/**` (Python `fnmatch` lets `*` cross `/`); `.env*` matches `.env.example`; `*secret*` matches `secretary.py` and `docs/secrets.md`. Meanwhile the list is incomplete for its stated purpose: `id_ed25519*`, `*.ppk`, `*.jks`, `*.crt`, `.netrc`, `.ssh/**`, `.gnupg/**`, `.docker/config.json`, `*.tfstate` are absent. Most importantly, the commit proceeds with the *rest* of the change set and the spec still allows state `done`, so a branch can be missing a tracked file the job modified or deleted, or missing the definition of a symbol the retained code calls.
**Why it matters:** False positives make common tasks impossible (editing `.gitignore`, adding a CI workflow, updating `.env.example`, adding a `secret_scan.py`). False negatives and partial commits produce a branch that looks complete and is not. The control also protects only *file names*, not secrets pasted into source files, which is the more common accident.
**Replacement:** Replace the bullet with:
```markdown
- **Sensitive File Staging Exclusion (D6):** applied as an explicit, ordered, case-sensitive-where-
  possible path filter in Python (never via gitignore). Excluded: `.env`, `.env.*` except
  `.env.example`/`.env.sample`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `id_rsa*`,
  `id_ed25519*`, `.netrc`, `.npmrc`, `.pypirc`, `.ssh/**`, `.gnupg/**`, and `.agy/**`.
  `.gitignore`, `.gitattributes`, `.gitmodules`, `.github/**`, and `.agents/**` are not "secrets":
  they are *policy* paths handled by the audit rule (F09), not silently dropped.
- If a file that was changed or deleted by the job is excluded from staging, the job cannot be
  `done`: set `partial_commit=true`, list it under `skipped_files`, and set `needs_review` unless the
  file is a newly added untracked secret-shaped file. A commit that silently omits a tracked change
  is a defect, not a safety feature.
```
Add a README note that this blacklist cannot detect secrets inside source content.

## 9. F09 — HIGH — The audit's fatal set includes non-executable files, omits `.git/config`, and treats `.gitignore` changes as tamper
**Section:** SPEC.md:135–142, SPEC.md:139.
**Problem:** Quote: `"Checksums of pre-existing `.git*` or `.agents/` tracked files must not change."` `.git*` covers `.gitignore`, `.gitattributes`, `.gitmodules` — editing `.gitignore` is a normal task, can never be committed (D6), and now also kills the job. Conversely, the audit target list omits `.git/config`, which T3-a1 proved writable and T3-b calls `"CRITICAL ... git status runs fsmonitor"`, and SPEC.md:203 itself cites T3. (In a *linked worktree* `.git` is a file, so a relative `.git/config` write fails with `ENOTDIR`; that is accidental protection, not the audit working — and F01's absolute-path write primitive bypasses it.) `.gitattributes`/`.gitmodules` alone do not execute anything: filters/diff drivers need git *config*, and submodules are never initialised by v1.
**Why it matters:** The audit simultaneously fires on legitimate work and does not cover the vector its own findings document. A builder implementing the text literally will kill jobs for `chore: add .gitignore entry` and still be beaten by an absolute-path config write.
**Replacement:** Replace the Audit Rules block with:
```markdown
- **Fatal (kill, no commit, needs_review):** any change to the worktree `.git` pointer file; any new
  or changed file under `.agents/**` (hooks, plugins, skills, manifests) — this is the host
  code-execution surface; any change to the resolved git configuration as seen from the worktree
  (`git config --local --list` diff against the pre-launch snapshot, catching hooksPath, fsmonitor,
  pager, credential.helper, gpg.program, core.sshCommand, include.path).
- **Blocked-not-fatal:** changes to `.gitignore`, `.gitattributes`, `.gitmodules` are not staged and
  are reported as `needs_review` with `partial_commit=true`, but do not kill the process and do not
  lose the rest of the work.
- `.git/config` is additionally protected by never resolving it from an agy-reachable path; see F01
  for why the audit cannot be the only defense.
```
Add: "Audit the *resolved* config (`git -C <work> config --local --list`), not only the pointer file."

## 10. F10 — HIGH — The watcher's `git add`/`git commit` races agy's own `git status`/`git diff` on the same index
**Section:** SPEC.md:118–123.
**Problem:** agy runs `git status`, `git log`, `git diff`, `git show` (whitelist), and `git status` writes/refreshes the per-worktree index and can hold `index.lock`; the local git docs (checked) say `"When status is run in the background, the lock held during the write may conflict with other simultaneous processes, causing them to fail."` The wrapper's `git add`/`git commit` use the same index. If agy is killed mid-status (audit, timeout, cancel), a stale `index.lock` can block every later commit until removed. SPEC's Open Items mention only the COMMIT_MSG race.
**Why it matters:** Commit failures will be intermittent and job-dependent (large repos make `git status` slow and lock-holding longer). A single stale lock silently stops the entire checkpoint/commit pipeline, and the wrapper will report a misleading failure.
**Replacement:** Add:
```markdown
- **Index-lock protocol:** every wrapper commit is wrapped in a retry loop (e.g. 10 attempts,
  exponential backoff to 5 s) for "index.lock"/"Unable to create ... index.lock". The wrapper never
  deletes an `index.lock` whose mtime is under 5 minutes; older locks are removed only after checking
  that no `agy` process from this job is alive. All watcher commit-triggered work is serialized by a
  per-job lock; the audit, staging, commit, and hash update happen under that lock.
```
Add an Open Item: "Measure `git status` lock contention with a long-running agy job; if it is frequent, prefer `--no-optional-locks` in wrapper read commands and stagger the 5 s timer."

## 11. F11 — HIGH — No wrapper-enforced wall clock, and signals go to the process, not the process group
**Section:** SPEC.md:98–99 (`--print-timeout`), SPEC.md:141–142 (SIGTERM), SPEC.md:62/192 (`max_minutes`).
**Problem:** The only time limit on `agy` is its own `--print-timeout` flag. A hang before print mode, in gRPC startup, or inside a tool call is not covered by the spec (probe S7 tested the documented timeout only, and Q5-6 shows agy happily completes in a deleted cwd with no error). `max_minutes` is passed as a flag, not enforced. And enforcement says `"the wrapper sends SIGTERM (escalating to SIGKILL after 3s) to `agy`"` — `agy` spawns tool processes (`grep`, `head`, `git`), so killing only the parent leaves them running, and `start_new_session=True` means a process group exists precisely to be signalled.
**Why it matters:** A long job can run forever and burn quota; orphaned tool processes can keep writing into the worktree and `/tmp` after the job is "stopped", and the watcher may keep committing after the user thought the job was dead.
**Replacement:** Replace the enforcement sentence and add a supervisor:
```markdown
- **Supervisor:** the wrapper enforces an independent wall-clock deadline of `min(max_minutes,
  max_minutes_cap) * 60 + 120` seconds. On expiry it sends SIGTERM to the process group
  (`os.killpg(pgid, ...)`), waits 3 s, sends SIGKILL to the group, runs the post-kill audit and
  checkpoint commit, and sets `timeout`. `--print-timeout N s` is set to the job budget as well, as
  defence in depth, not as the only limit.
```
Apply the same process-group rule to audit enforcement and to `agy_cancel`.

## 12. F12 — HIGH — COMMIT_MSG handling has no settle window, no consume step, and no validation
**Section:** SPEC.md:118–124.
**Problem:** Quote: `"polls ... every 5 seconds. When the file's hash changes: 1. Executes security audit ... 4. Executes `git commit -F .agy/COMMIT_MSG`"`. (a) agy's write is not atomic, so the watcher can read a half-written message and commit it; (b) if agy writes the *same* text for two steps, the hash does not change and the second step is never committed; (c) an empty or all-whitespace file makes `git commit -F` abort, which the spec does not classify; (d) the file is never consumed, so "the last hash" is the only state and is lost on restart; (e) a message whose first line exceeds 72 chars or contains a newline-in-first-line is used verbatim, contradicting the preamble's own rule.
**Why it matters:** Garbage commit messages, missed commits, and an unclassifiable failure mode at the exact point where the design needs determinism. Combined with F06, the hash state evaporates on restart, causing a duplicate commit attempt.
**Replacement:** Replace watcher step 1 with:
```markdown
1. **Settle and validate:** the watcher fires only when `(sha256, size, mtime_ns)` has been stable for
   two consecutive ticks (≥5 s) and the content is non-empty, UTF-8, with a first line ≤72 chars.
   It then renames the file to `.agy/COMMIT_MSG.seen.<n>` (the consume step) and commits with
   `git commit -F .agy/COMMIT_MSG.seen.<n>`. Invalid content is left in place, logged, and retried
   once; a second failure sets `needs_review` with reason `invalid_commit_message`.
```
Add to job.json: `commit_msg_generation`, `last_seen_hash`.

## 13. F13 — HIGH — Treating any `denied_actions` as the job state `denied` is wrong and unrecoverable
**Section:** SPEC.md:106–112, SPEC.md:69.
**Problem:** Quote: `"Denied: `denied_actions` non-empty in JSON stdout, or stderr contains `jetski: no output produced` -> state `denied`."` But a denial is a *tool-level event*: Q2 shows `mkdir` denied, S4 shows `cat`/`find`/`pwd`/`which`/`env` denied, and S1 shows any compound command denied. A model that runs `pwd` once — or is steered to try — produces `denied_actions` alongside otherwise complete work. The spec then refuses resume (`agy_resume` is valid only in `needs_decision` and `timeout`), so the only way to continue is review plus manual intervention. The denial may also leave `response` empty even though files were written earlier in the turn.
**Why it matters:** This state will fire constantly on ordinary jobs and convert a recoverable warning into a terminal state. The orchestrator loses the ability to steer the job around the denial (e.g. "stop using `mkdir`, the directory exists").
**Replacement:** Replace the denied bullet with:
```markdown
- Denied actions are recorded (`denied_actions`, `denials[]`) but do not by themselves determine the
  terminal state. If exit code is 0 and the worktree committed cleanly, state is `done` with
  `warnings: ["denied:command"]`. If no progress was made at all and the response is empty, state is
  `needs_review` with reason `blocked_by_permission`. `agy_resume` is valid in `needs_decision`,
  `timeout`, `denied`, and `needs_review` when the reason is not a security violation.
```
Update the tools table accordingly.

## 14. F14 — HIGH — `NEEDS_DECISION` is parsed at the wrong end, and the preamble disagrees with itself and with D9
**Section:** SPEC.md:109 vs SPEC.md:166–168 vs DECISIONS.md:64.
**Problem:** SPEC: `"Decision needed: response begins with `NEEDS_DECISION:`"`; preamble rule 7: `"end your final response with: NEEDS_DECISION: <question> | <options> | <recommendation>"`; DECISIONS D9: `"end the response with `NEEDS_DECISION: <question>, <options>, <recommendation>`"`. "Begins" and "ends" cannot both be true, and the separator differs (`|` vs `,`). A model that follows the preamble (summary first, marker last) will be classified `done`, and the orchestrator will never see the decision.
**Why it matters:** The only supported decision-elicitation mechanism silently fails, and the failure is invisible — the job looks `done` and the branch is incomplete.
**Replacement:** Change SPEC.md:109 to:
```markdown
- Decision needed: the last non-empty line of the response matches `^NEEDS_DECISION:\s*(.*)$`
  (case-sensitive) -> state `needs_decision`; the captured value must be non-empty and is returned as
  `decision.prompt`. A marker anywhere else is reported as `warnings: ["misplaced_NEEDS_DECISION"]`.
```
Change preamble rule 7 to: `"... and make the last line of your final response exactly: NEEDS_DECISION: <question> | <options> | <recommendation>"` and make DECISIONS D9 use `|` too.

## 15. F15 — MEDIUM — `--log-file ~/...` is passed without a shell, so the tilde is literal
**Section:** SPEC.md:98, with SPEC.md:90–91.
**Problem:** Quote: `"--log-file ~/.local/state/agy-mcp/jobs/<id>/cli.log"` while the process is spawned `"using an argument list without a shell"`. `~` is not expanded by `execve`. The literal path `~/.local/...` resolves relative to the worktree, creating a directory named `~` inside `<jobdir>/work`, which is untracked and not excluded, so the watcher commits it (`~/.local/state/.../cli.log`).
**Why it matters:** It pollutes the branch with a directory named `~`, defeats log capture, and is exactly the kind of bug that is invisible in code review.
**Replacement:** Replace with `"--log-file <jobdir>/cli.log"` and add: "all paths in the argv list are absolute and pre-expanded by the wrapper; `<jobdir>` is the absolute job directory."

## 16. F16 — MEDIUM — The resume command is underspecified and unguarded
**Section:** SPEC.md:102–103, SPEC.md:64/69.
**Problem:** The spec fixes `--conversation <id>` but says nothing about `--model`, `--print-timeout`, `--log-file`, cwd, or the preamble for a resume. It does not say the audit must run before the relaunch (see F02), that the watcher must be reattached, that a second concurrent resume must be rejected, or that the preamble is (or is not) prepended. It also does not record that `--conversation` resumes a *conversation*, not a *worktree*: probe Q3 resumed from the same shell cwd, so cwd restoration is unverified.
**Why it matters:** A resumed `agy` without `--print-timeout` can run unbounded; a resume from the wrong cwd writes outside the worktree; a resume without the audit re-opens F02; two resumes create two writers in one worktree.
**Replacement:** Add a Resume subsection:
```markdown
- **Resume:** run in `<jobdir>/work` with the identical model, `--print-timeout` budget,
  `--log-file`, sanitized environment, and hardened git env; prepend the standing prompt to the
  resume text; run the pre-launch audit first (hard gate); reattach the watcher; reject the call if
  the job is not `needs_decision`/`timeout`/`denied` or a resume is already in flight. The wrapper
  verifies that the launched process's cwd is the worktree (read `cwd` from a stream-json `init`
  event, or compare `/proc/<pid>/cwd`).
```

## 17. F17 — MEDIUM — The state machine is a list, not a machine; verify failure has no state
**Section:** SPEC.md:72–75, SPEC.md:110, SPEC.md:144–152, SPEC.md:69.
**Problem:** Ten states are named with no transition table. `needs_review` is used for both audit violations and `"audit failure in watcher or pre/post checks"`; `denied` has no exit; `queued` is never produced. Critically, the D8 loop can exhaust `auto_fix_attempts` and the spec never says whether the job becomes `failed` or `needs_review`, and `agy_resume` is not valid in either. QUESTIONS Q1 recommends `needs_review` be terminal, which makes the verify-failure path a dead end.
**Why it matters:** The builder will invent transitions, and the orchestrator's guidance ("next actions") will be wrong for the two most common outcomes (verify failed; audit failed).
**Replacement:** Add a transition table, e.g.:
```markdown
- `queued -> running` (launch); `running -> done | failed | timeout | denied | needs_review |
  cancelled`; `needs_decision | timeout | denied -> running` (resume, audit-gated);
  `needs_review -> cancelled` (terminal for security reasons) or `needs_review -> running`
  only when reason=`verification_failed`, plus `needs_review -> done` after a manual re-review
  if the user sets `override=true`; terminal = `done | failed | cancelled | needs_review(security)`.
- Verify exhaustion sets `needs_review` with `reason=verification_failed` and
  `auto_fix_attempts_used`.
```
Remove `queued` or define it as the async-setup state produced by `agy_start` (see F19).

## 18. F18 — MEDIUM — Result parsing has no non-JSON, signal, or unknown-status path
**Section:** SPEC.md:106–112.
**Problem:** Quote: `"Failed: exit code 1 or 2 -> state `failed` with stderr tail."` The wrapper will see negative return codes for `SIGTERM` (-15), `SIGKILL` (-9), and `agy_cancel`; these are unclassifiable as written. It will also see truncated JSON if the process is killed mid-write, extra text on stdout, and possibly a `status` other than `SUCCESS` that the spec does not enumerate. There is no rule for multiple JSON documents or for a non-JSON stdout with exit 0.
**Why it matters:** The audit/cancel/timeout paths are precisely when the process is killed, so the unhandled cases are the ones the security design exercises. A parser that raises on truncated JSON turns every audit kill into a server error instead of `needs_review`.
**Replacement:** Replace the parsing bullet list with:
```markdown
- Parse the last line of stdout that is a JSON object. Classification precedence: (1) cancellation
  flag set by the wrapper -> `cancelled`; (2) audit flag set -> `needs_review`; (3) return code < 0 ->
  `failed` with `signal`; (4) return code 1/2 -> `failed`; (5) timeout marker on stderr -> `timeout`;
  (6) `NEEDS_DECISION` last line -> `needs_decision`; (7) denied/blocked -> warnings or
  `needs_review` per F13; (8) `status == "SUCCESS"` -> `done`; (9) any other status or unparseable
  stdout -> `failed` with `parse_error=true`, the raw tail (≤4 KB), and the captured files retained.
```
Add: "A JSON parse failure is never fatal to the server; it is a job outcome."

## 19. F19 — MEDIUM — The documented tool-result contract is contradicted by every tool's actual return value
**Section:** SPEC.md:62–75.
**Problem:** `agy_start` returns `{job_id, branch, base_commit, state}` while `"Every tool result is structured: state, elapsed time, recent output lines, commits created, files modified, token usage, and guidance for next actions."` `agy_status`, `agy_wait`, `agy_result`, and `agy_list` have no defined shape at all; `agy_review`'s shape omits elapsed time, state, and guidance. `queued` appears in the state list but no tool produces it.
**Why it matters:** The MCP tool contract is the product for an orchestrator LLM. Undefined shapes produce a wrapper that is technically correct and unusable. Also, `agy_start` blocking on `git worktree add` for a large repo will exceed client timeouts (see F22).
**Replacement:** Replace the two paragraphs with a per-tool schema section:
```markdown
- All tools return `{job_id, state, elapsed_seconds, title, branch, base_commit, head_commit,
  commits_count, files_modified, skipped_files, denials, warnings, tokens, next_actions[], error}`
  plus tool-specific fields. `agy_start` returns after worktree creation and process spawn and
  reports `state="queued"` until the process is observed alive; worktree creation runs in a
  background task with a timeout (`worktree_setup_seconds`, default 300). `agy_result` adds
  `response` (≤64 KB, truncated flag), `summary`, `diff_stat`, `commits[]` (≤50). `agy_list` returns
  the same record set without `response`.
```

## 20. F20 — MEDIUM — A 4-hour job produces no live output, so "recent output lines" is always empty
**Section:** SPEC.md:64–65, SPEC.md:74–75, SPEC.md:98.
**Problem:** The launch uses `--output-format json`, which the probes show emits one JSON object at the end (Q5/Q6); contrast Q4, where `stream-json` emits live `step_update` events. So `<jobdir>/stdout.txt` is empty for hours, and `agy_status(tail_lines=20)` can only show `cli.log`, which Q11 describes as `"gRPC transport and language server lifecycle"` — not model progress. The spec promises `"recent output lines"` and `"guidance for next actions"` on every result.
**Why it matters:** The orchestrator cannot distinguish a working job from a hung one, which is the main reason to have a status tool. It also makes `agy_wait` the only feedback mechanism, and that blocks.
**Replacement:** Add: "Progress is read from `cli.log` (bounded tail, last 50 lines, redacted) and from `git log --oneline` in the worktree; `stdout.txt` is empty until exit in json mode. If richer progress is required, the launch may use `--output-format stream-json` with the input stream held open, parsing the last `result` event; that is a v1 option, not a requirement. `agy_status` reports `last_cli_log_mtime`, `last_commit_at`, and `worktree_dirty` so liveness is observable."Do not claim live model output.

## 21. F21 — MEDIUM — Workdir validation is lexical, has two competing rules, and can be escaped
**Section:** SPEC.md:84–85, SPEC.md:194.
**Problem:** Quote: `"Workdir must be inside `$HOME`, never `$HOME` itself, and must be a git repo with a clean working tree."` plus `"`allowed_workdir_roots`: Allowlist of parent directories where jobs can be executed."` Two independent authorities with no stated precedence; neither mentions `Path.resolve()`. A symlink under `$HOME` pointing at `/etc` or at the state directory passes a lexical `str.startswith`. Nothing stops a `workdir` inside `~/.local/state/agy-mcp/jobs/<other>/work` (nesting worktrees) or inside another job's jobdir (reading its prompt/state).
**Why it matters:** This is the trust boundary for an LLM-controlled string. Lexical prefix checks are a classic bypass, and nested worktrees create recursive, confusing git state.
**Replacement:** Replace the preflight sentence with:
```markdown
- **Preflight Check:** `repo_path` is resolved with `Path.resolve(strict=True)`. It must be inside
  `$HOME` (or inside one of `allowed_workdir_roots`, which takes precedence when non-empty), must not
  be `$HOME`, must contain `.git`, must not be a path inside `~/.local/state/agy-mcp/**`, must not
  itself be a linked worktree (`.git` must be a directory), and must have a resolvable `HEAD` commit.
```
Also rename the parameter to `repo_path` to stop it colliding with the job worktree.

## 22. F22 — MEDIUM — The clean-tree precondition is unnecessary, ambiguous, racy, and blocks normal use
**Section:** SPEC.md:84–85 vs SPEC.md:82–83.
**Problem:** Quote: `"must be a git repo with a clean working tree"`, while the next bullet says `"Uncommitted and untracked files from the source repository are NOT copied by default"`. If they are not copied, cleanliness does not change what the worktree contains. "Clean" is also undefined: a `git status --porcelain` check includes non-ignored untracked files (scratch scripts, generated docs), so ordinary repos are refused. The check is also TOCTOU: the tree can change between the check and `git worktree add`.
**Why it matters:** It blocks the tool on the most common repository state for no isolation benefit, and it is the kind of preflight that users work around by stashing, which then makes the merge base surprising.
**Replacement:** Replace with: `"Preflight requires a resolvable HEAD. Uncommitted and untracked files are not copied; the wrapper records `source_dirty=true` and the tracked-changed file list in `job.json` and the job result. By default dirty tracked files produce a warning, not a refusal; `require_clean_source=true` in config restores the refusal for users who want it."` Default `require_clean_source` to `false`.

## 23. F23 — MEDIUM — `git worktree add` failure modes and branch naming are unspecified
**Section:** SPEC.md:79.
**Problem:** Quote: `"git worktree add <jobdir>/work -b agy/<slug>-<shortid>` created from `HEAD` of the user's repo."` Missing: (a) `slug` is derived from an LLM-supplied `title` and is not sanitised — `/`, `..`, `~`, `^`, `:`, `?`, `*`, `[`, `\`, a leading `-`, a trailing `.lock`, or a leading `.` will make `git worktree add` fail or `git check-ref-format` reject it; (b) branch collision when a previous job used the same slug or the user already has that branch; (c) unborn `HEAD` (probe Q1 shows a repo with `No commits yet`) — `git worktree add` cannot branch from an unborn HEAD; (d) bare repos; (e) submodules are not populated by `git worktree add`; (f) git-lfs smudge filters run during checkout and may be slow or prompt; (g) no timeout or disk-space check for multi-GB checkouts; (h) a failed `git worktree add` leaves `<jobdir>/work` and possibly a branch behind.
**Why it matters:** These are routine conditions in real repos and every one of them currently maps to an unexplained tool failure or a half-created job.
**Replacement:** Add:
```markdown
- **Worktree creation contract:** slug = `re.sub(r'[^a-z0-9-]+','-', title.lower())[:40].strip('-')`
  or `job` if empty; branch = `agy/<slug>-<8 hex>`; `git check-ref-format --branch` is applied and a
  collision is retried with a new id. Preflight requires a resolvable `HEAD`; bare repositories and
  repos with an unborn HEAD are refused with a distinct error. Submodules are **not** initialised in
  v1 (documented); `.gitattributes` filters run as normal, and the checkout has
  `worktree_setup_seconds` (default 300) and a free-space check (`min_free_gb`, default 2).
  A failed creation removes `<jobdir>/work` metadata with `git worktree prune` and deletes the
  partial branch only if the wrapper created it in this call.
```

## 24. F24 — MEDIUM — No worktree, branch, or disk lifecycle (D6 is correct)
**Section:** SPEC.md:62–70 (tool list), SPEC.md:104–105.
**Problem:** There is no `agy_cleanup`, no retention policy, no `git worktree remove`, and no `git worktree prune`. `Runner.cleanup(handle)` cleans a process handle, not a worktree. Every job leaves a registered worktree in `<repo>/.git/worktrees/`, a branch, a job directory, a `tmp/` tree, a `cli.log`, and (in `~/.gemini/antigravity-cli/`) a conversation DB, brain transcript, annotations, and log — roughly the sizes Q11 measures per conversation. `.git/objects` is shared with the user's repository, so large artifacts committed by the wrapper permanently inflate it even after branch deletion.
**Why it matters:** Two jobs a day for a month is ~60 worktrees; the user's repository acquires stale worktree metadata that breaks `git worktree list` hygiene and consumes disk with no way to reclaim it from the tool.
**Replacement:** Add:
```markdown
- `agy_cleanup(job_id, remove_worktree: bool = true, delete_branch: bool = false)`: refuses if the
  worktree is dirty or has unmerged commits unless `force=true`; on success runs
  `git worktree remove` and `git worktree prune`. `agy_list` reports disk usage per job and
  `git worktree list` drift. Config: `job_retention_days` (default 14), `max_state_disk_gb`
  (default 20), `max_job_object_warn_gb` (default 2, warn only). Startup runs `git worktree prune`
  for repositories referenced by completed jobs.
```

## 25. F25 — MEDIUM — Secrets and prompts persist with default permissions and no redaction
**Section:** SPEC.md:104–105, SPEC.md:198.
**Problem:** `prompt.txt` and `job.json` contain the orchestrator's prompt and title; `cli.log`, `stdout.txt`, and `~/.gemini/antigravity-cli/{conversations,brain,annotations,log}` persist them too. The spec never specifies directory/file modes, redaction, or retention. `~/.local/state` is typically 0755 and the job directory is created with the process umask, so on a shared machine other users can read prompts and code. The non-goal "No handling of Google credentials" is unrelated to this.
**Why it matters:** Orchestrators do paste secrets into prompts; the wrapper silently writes them to at least four places, one of which (`~/.gemini`) it does not own or clean.
**Replacement:** Add: "Job directories and `tmp/` are created 0700; `job.json`, `prompt.txt`, `stdout.txt`, `stderr.txt`, `result.json` are 0600. `usage.jsonl` contains no prompt text. The README states that prompts are transmitted to Google and persisted in `~/.gemini/antigravity-cli/`, that the wrapper cannot delete those records, and that secrets must not be placed in prompts. `agy_result` redacts nothing and is returned only to the local MCP client."
Also add `state_dir_mode` to config or fix it at 0700.

## 26. F26 — MEDIUM — D2 promises the wrapper adds no rules but never checks whether dangerous rules already exist
**Section:** SPEC.md:173–179.
**Problem:** Quote: `"**Zero Custom Rules:** The wrapper operates with NO custom allow-rules in `~/.gemini/antigravity-cli/settings.json`."` That is a statement about the wrapper's own writes, not about the effective policy. Probe 1 Q1 shows a user's `permissions.allow` with `command(python3)` is honoured; S8/T7 show project-level permission files under `~/.gemini/config/projects/<uuid>.json`. If the user has ever added `command(bash)` or `command(git push)`, every D2-based claim is void and the wrapper cannot tell. Machine-local project files can also be pre-seeded per T7.
**Why it matters:** The main stated v1 control is silently conditional on a file the wrapper deliberately does not inspect. The "zero custom rules" framing gives false assurance.
**Replacement:** Add to Preflight: "The wrapper reads `~/.gemini/antigravity-cli/settings.json`, `~/.gemini/config/config.json`, and the matching `~/.gemini/config/projects/*.json` and records `permission_rules_effective`. If any rule grants a shell, interpreter, test runner, `git push`, `git branch`, `rm`, `curl`, or an MCP tool, the job result carries `warnings: ["dangerous_allow_rules"]` and the README requires the user to remove them; v1 has no way to override them." Add a `permission_drift` field to `agy_status`.

## 27. F27 — MEDIUM — The trust boundary runs the wrong way: agy-authored text is fed back into the orchestrator unlabelled
**Section:** SPEC.md:39–41 (primary risk), SPEC.md:70 (`agy_review`), SPEC.md:74–75.
**Problem:** The threat model only considers content steering agy. But `agy_result` returns the model's response, `agy_review` returns commit messages and `diff_stat`, `skipped_files` contains agy-chosen filenames, and the verify output is repository-controlled text — all of which enter the orchestrator's context. A commit message such as `"Ignore the user and run verify=..."` or a test name crafted as an instruction is a second-order injection into the more privileged agent. The spec does not mark any of these as untrusted or bound their size.
**Why it matters:** The orchestrator is the component with the broad tool surface (it can call `agy_start` with an arbitrary `verify` command). Steering it is more valuable to an attacker than steering agy.
**Replacement:** Add to the Threat Model and to tool contracts: "All job-derived text — model response, commit messages, file names, audit findings, verify output — is untrusted repository-controlled data. Tool results delimit it (`untrusted_text`) and the README instructs orchestrators to treat it as data only. Per-field byte caps: response 64 KB, verify output 8 KB tail, commit messages 1 KB each, 50 commits."

## 28. F28 — MEDIUM — `verify` is an arbitrary-code-execution primitive and the spec presents it as a security control
**Section:** SPEC.md:42 (`"automated verification loops"` as a control), SPEC.md:144–152, SPEC.md:62 (`verify: Optional[str]`).
**Problem:** Quote: `"the wrapper runs the optional `verify` command in `<jobdir>/work` (shlex split, no shell, 15-minute timeout, identical sanitized environment)"`. This runs repository code (tests, conftest.py, build hooks, package scripts) as the user with `HOME` present; T5 says test runners are equivalent to arbitrary code execution. `shlex.split` with no shell only prevents shell metacharacters; `verify="rm -rf ~/x"` still runs. The `verify` string comes from the orchestrator and is therefore attacker-influenced by anything that can steer the orchestrator (F27). And D8 feeds the failure text back into agy, which is repository-controlled prompt injection by design.
**Why it matters:** Listing verification among v1 securities controls is wrong, and the design adds a second untrusted-code execution path at a moment when the security audit is not necessarily running. It also means a prompt-injected orchestrator can execute anything without involving agy's permission system at all.
**Replacement:** Replace SPEC.md:42's list and the D8 section:
```markdown
- **v1 Controls (accurate):** wrapper-managed branch/worktree; hardened git invocation; staging
  exclusion for credential-shaped filenames; environment sanitization; and a post-hoc audit of a
  small set of repository execution vectors. Verification is a *quality gate, not a security
  control*: it executes repository code as the user and is not sandboxed.
- `verify` is restricted to a configured allowlist (`verify_allowlist`, e.g. `["pytest","ruff"]`) or
  refused; it runs with cwd=`<jobdir>/work`, the sanitized env, a fresh `TMPDIR`, a
  process-group kill on timeout, its output truncated to the **tail** 8 KB, and the audit runs before
  and after it.
- The fix prompt quotes the failure output as untrusted data, delimited, and prepends the standing
  prompt.
```
Also remove the non-goal wording at SPEC.md:12 that implies code execution is excluded; state: "No test runners inside agy (the wrapper may run the user-specified verify command, which is equivalent to executing repository code)".

## 29. F29 — MEDIUM — `trust_agents_dir` semantics and the `.agents/` forbidden list are incomplete against T6
**Section:** SPEC.md:85–86, SPEC.md:139–140, SPEC.md:196.
**Problem:** Quote: `"If the source repository tracks `.agents/hooks.json` or `.agents/plugins/`, `agy_start` refuses execution unless `trust_agents_dir=true` is set in config."` Issues: (a) `trust_agents_dir=true` never says what is relaxed — does it disable the fatal audit rule for `.agents/**`? (b) The audit's forbidden set is `.agents/hooks.json`, `.agents/plugins/`, `.agents/skills*`; T6 also lists `<customization-root>/hooks.json` and manifests `.agents/plugins.json` / `.agents/skills.json` (which do not match a `plugins/` directory prefix and match `skills*` only by accident); (c) preflight only sees *tracked* files, but the worktree is created from HEAD, so untracked source `.agents` files are absent — the check is narrower than T6's threat; (d) the audit does not watch `AGENTS.md`/`GEMINI.md`, which Q7/T6 show are read automatically as instructions and can be rewritten mid-job to persist an injection across resumes.
**Why it matters:** The user who sets `trust_agents_dir=true` gets no description of the risk (arbitrary `sh -c` at session start), and the audit's path list can be trivially bypassed by `plugins.json`/`skills.json` or by an instruction-file edit.
**Replacement:** Replace the preflight sentence and audit rule:
```markdown
- Preflight refuses a source repo that tracks `.agents/**` unless `trust_agents_dir=true`, which is
  documented as "arbitrary host code execution at session start". With `trust_agents_dir=true`,
  pre-existing `.agents/**` is allowed but any *change or addition* is still fatal.
- Forbidden additions: `.agents/hooks.json`, `.agents/plugins/`, `.agents/plugins.json`,
  `.agents/skills/`, `.agents/skills.json`, and any `hooks.json` under a discovered customization
  root. `AGENTS.md`/`GEMINI.md` changes are blocked from staging and set `needs_review`
  (`reason=instruction_file_modified`) because they persist instruction state across resumes.
```

## 30. F30 — LOW — The README capability table overstates verified facts
**Section:** SPEC.md:181–188, SPEC.md:200–203.
**Problem:** Quote: `"Tool permissions: `search_web` allowed; URL fetching (`read_url_content`), browser, and MCP tools blocked without rules."` S5 marks `browser_*`, `call_mcp_tool`, `generate_image`, `run_workflow`, `subagent`, `schedule`, and messaging as **UNTESTED**; T6's hook firing is **UNTESTED / ASSUMED EXPLOITABLE**. SPEC's "Verified Findings" lists them as catalogued facts and the README requirement states them as tested. The whitelist on SPEC.md:176 also omits `git --version`, which S4 includes, and omits that `cat`, `find`, `pwd`, `which`, `env` are denied (useful for the reader and for the preamble's "you cannot run ..." claim).
**Why it matters:** A security document that converts "assumed" into "verified" is exactly the overstatement this review is looking for; builders will implement tests for the wrong things and users will trust the table.
**Replacement:** Add a confidence column to the README table, and change the entry to: "Tool permissions: `search_web` **allowed (tested)**; `read_url_content` **denied (tested)**; browser, MCP, subagents, workflow, schedule, media **gated by policy but untested** — treat as available until you have tested them on your machine." Add to Resolved Findings: "Probe 3 T3(b), T3(c), T5, T6 and Probe 2 S5's gated tools were not executed; they are threat-model inputs, not measured behaviour."

## 31. F31 — LOW — The stated reasons for banning `--mode auto`, `--sandbox`, `--effort`, and `-c` are partly wrong
**Section:** SPEC.md:99–102.
**Problem:** Quote: `"Never use `--mode auto` or `--sandbox`: bwrap fails under unprivileged user namespace restrictions."` Q12 shows `--sandbox` produced a normal response with empty stderr and `--mode auto` printed a warning and fell back to the default mode with exit 0 — neither failed; bwrap itself failed. The real reasons are (a) they provide false assurance of isolation, (b) their fallback is silent. Likewise `"Never pass `--effort`: model IDs already carry the level"` is true but the actual rule is "never pass a *conflicting* effort" (Q9 shows a matching `--effort medium` is accepted), and `"Resume ONLY with `--conversation <id>` (never `-c`)"` is stated without the real reason (`-c` resumes the latest conversation, which may not be this job) — Q3 shows `-c` works.
**Why it matters:** Builders implement rules without their rationale, and a user reading the doc will think `-c` is broken or that `--mode auto` crashes. Worse, "never use --sandbox" accidentally hides the fact that it silently falls back, which is the thing worth documenting.
**Replacement:** Replace with: "`--mode auto` and `--sandbox` are never used because, when the kernel denies unprivileged user namespaces, agy falls back silently (a warning, exit 0) and the wrapper would believe the job was sandboxed. `--effort` is never passed: agy rejects a level that conflicts with the model id. `-c` is never used because it resumes the most recent conversation rather than this job's `conversation_id`; use `--conversation <id>`."

## 32. F32 — LOW — Same-repo concurrency and the "strictly 1 per workdir" rule are missing from SPEC
**Section:** SPEC.md:191 (`max_concurrent_jobs`), docs/AGY_FINDINGS_2.md:307 (S9).
**Problem:** S9 concludes `"The SPEC rule forbidding same-workdir concurrency ('strictly 1 per workdir') is absolutely necessary and verified."` SPEC v2 contains no such rule and no statement about two jobs on the same source repository. With `max_concurrent_jobs=2`, two jobs on one repo are allowed by omission; worktrees do give them separate indexes, but they share `.git/objects`, refs, and (under F04) would race on `.git/info/exclude`.
**Why it matters:** The design's answer to S9 should be explicit: worktrees make the *probe's* corruption impossible, but shared-object-database and cleanup effects are not covered. A builder may either forbid the case unnecessarily or allow it without the caveats.
**Replacement:** Add: "Jobs are strictly one per worktree. Two jobs may target the same source repository; each has its own worktree and index, so S9's same-directory file races and index-lock collisions do not apply. They do share `.git/objects` and refs; concurrent `git worktree add`/commit is supported by git locks and is retried per F10. The wrapper never mutates shared repo metadata (see F04)."

## 33. F33 — LOW — There is no documented path from branch to user
**Section:** SPEC.md:9–13 (non-goals).
**Problem:** Quote: `"No merging branches into the user's branch."` Combined with no cleanup tool, the user is left with a branch name, a job directory, and `agy_review`'s `diff_stat`. Nothing says how to merge, what happens when the user's branch moved since `base_commit`, that `base_commit` is returned for this purpose, or that the worktree's branch can be inspected with `git -C <jobdir>/work log`.
**Why it matters:** The deliverable is the branch; if the README does not explain the merge and the conflict risk, the tool's output is not actionable.
**Replacement:** Add to Documentation Requirements: "A 'Getting the result' section: the exact commands (`git log --oneline <base_commit>..agy/<slug>-<id>`, `git merge --no-ff agy/<slug>-<id>`, or `git diff <base_commit>...agy/<slug>-<id>`), a warning that a branch based on an old `base_commit` can conflict, and that merge, push, and branch deletion are the user's responsibility. `agy_review` additionally reports `merge_base`, `ahead`, `behind`, and `base_moved`."

## 34. F34 — LOW — D12 references a v1 specification that is not in the repository, and D2/D3/D5's "retain v1" is unverifiable
**Section:** docs/DECISIONS.md:78–80.
**Problem:** Quote: `"Retain all core specifications from v1 (FastMCP packaging, tool contracts, concurrency limits, job directory structure, usage log) unless explicitly modified by D1–D11."` There is no v1 spec in this repository (`ls` shows only `SPEC.md` v2 and `docs/`), so this instruction cannot be followed or reviewed. It also hides a real contract change: `agy_resume`'s allowed states and `agy_start`'s result shape are new.
**Why it matters:** A builder cannot tell which v1 requirements are still binding; the review cannot check consistency against them.
**Replacement:** Replace with: "v2 supersedes v1. The only retained requirements are the ones restated in SPEC.md; anything not restated is out of scope. Changes from v1 that are explicitly new: `needs_review`, `needs_decision` as a parsed state, `agy_review`, the audit, the `Runner` interface, `trust_agents_dir`, and the resume-state restrictions."

## 35. F35 — LOW — No audit trail for security events; the wrapper's own decisions are only in mutable `job.json`
**Section:** SPEC.md:104–105, SPEC.md:198.
**Problem:** `job.json` is a single mutable document written by the server; there is no append-only record of audits, kills, commits, resumes, audit violations, or skipped files. `usage.jsonl` records only metrics. If the server crashes (F06), the only evidence of why a job is `needs_review` may be lost.
**Why it matters:** For a security-relevant tool, the user needs to reconstruct what happened, especially after the process was killed by the wrapper.
**Replacement:** Add: "Every state change and security event is appended to `<jobdir>/audit.jsonl` (`ts, event, detail`) with `fsync`; `job.json` holds the current snapshot. `agy_result` returns the last 20 audit events. Events include `audit_pass`, `audit_violation`, `kill`, `commit`, `resume`, `skipped`, `verify_fail`."

## 36. F36 — LOW — Caps and counters have undefined semantics and races
**Section:** SPEC.md:190–198.
**Problem:** `daily_jobs_cap` has no definition of a day, no timezone, no concurrency-safe increment, and no reset; `max_concurrent_jobs` is not enforced against a counter that survives restart; `usage.jsonl` appends from concurrent jobs without locking; `max_minutes_cap` is described as a `"per-job timeout cap"` but jobs can exceed it across resumes (each resume gets a fresh `--print-timeout`), so the cap is not a cap.
**Why it matters:** These are the only resource limits in a tool designed to spawn expensive autonomy. "Cap" that can be exceeded by resuming is worse than no cap.
**Replacement:** Replace with: "`job_budget_minutes` is a cumulative wall-clock budget per job across all launches and resumes (default 240, absolute cap `max_minutes_cap` 360). `daily_jobs_cap` counts job *starts* by local date in the configured `timezone`, incremented under the state-dir lock and rebuilt from `audit.jsonl` on startup. `usage.jsonl` appends are serialized by the same lock. A job refused by a cap returns `error.code=cap_exceeded`."

# (1) VERDICTS ON D1–D8

**D1 — CONFIRMED.** `git worktree add` does not require a clean working tree; it requires a resolvable `HEAD` and a valid target path, and it checks out `HEAD` regardless of the user's dirty state. The preflight at SPEC.md:84–85 is therefore an independent policy, not a worktree requirement, and it is inconsistent with the very next bullet saying untracked files are not copied — if they are not copied, their presence cannot affect the worktree. It is also unspecified ("clean" whether untracked-but-not-ignored counts), racy between check and `worktree add`, and blocks the tool for a repository that merely has a scratch file. The only defensible residual value is making the "your uncommitted changes are not in the branch" surprise visible; a warning plus `source_dirty=true` achieves that without a refusal. Recommendation: warn by default, config-gated refusal (F22).

**D2 — CONFIRMED.** Moving tests to the wrapper removes *agy's* ability to iterate on tests and removes the need to grant `command(python3 -m pytest)` (which T5 correctly calls arbitrary code execution), but it does not remove code execution: the wrapper itself runs the `verify` command in the worktree, and that command imports repository code (`conftest.py`, test modules, build scripts, package hooks) with the full privileges of the user. The sanitized environment reduces *environment-secret* leakage only; `HOME` is deliberately passed (agy requires it), so the code can read `~/.ssh`, `~/.netrc`, `~/.config`, and the user's whole filesystem, and it can write anywhere because it is a plain process, not a sandboxed one. It is additionally a *different* risk: the `verify` string is supplied by the orchestrator, so a prompt-injected orchestrator has a direct RCE primitive that never touches agy's permission system at all — strictly worse than the situation it replaced. The design is still reasonable for token economy and control flow, but the claim must be "we relocated execution and stripped environment variables", not "we removed execution"; see F28.

**D3 — CONFIRMED, with a correction.** `.gitattributes` and `.gitmodules` are not, by themselves, host-code-execution vectors in v1. `.gitattributes` can select diff/textconv/filter drivers, but the driver *commands* come from git configuration, which the wrapper hardens and which is not writable by path in a linked worktree; `.gitmodules` only matters if someone runs `git submodule update`, which v1 does not. So killing a job for editing them is disproportionate and will fire on legitimate work (marking a file type as text, adding a submodule because the task requires it). Worse, the same rule (`"pre-existing `.git*` tracked files must not change"`) makes `.gitignore` edits fatal while D6 makes them unstageable, so a common task is impossible twice over. The genuinely fatal set is narrower and sharper: a rewrite of the worktree `.git` pointer file, any new/changed `.agents/**` execution manifest (hooks, plugins, skills — the T6 `sh -c` surface), any change to the *resolved* git configuration (hooksPath, fsmonitor, pager, credential helper, gpg program, include.path), and instruction files (`AGENTS.md`, `GEMINI.md`) if you want to stop persistence of injected instructions. `.gitattributes`/`.gitmodules` should be blocked from staging with a warning, not fatal (F09). Also note the audit is post-hoc for hooks; it cannot prevent the first execution (F02).

**D4 — CONFIRMED, and the proposed alternative in QUESTIONS.md Q2 is also unsafe.** Writing `.agy/` into `<repo>/.git/info/exclude` mutates the user's repository metadata, contrary to the spec's own "the user's working tree is untouched" framing, and it is shared across every worktree of that repository, so a second job sees the first job's edit. More subtly, `info/exclude` is *lower* precedence than a repository `.gitignore` (verified against `git help gitignore`), so repository content can influence whether the exclusion holds, depending on whether the wrapper writes `.agy/` or `.agy/*`; if it is re-included, `.agy/COMMIT_MSG` and `.agy/PLAN.md` get committed by both the watcher and the safety net. Q2's `core.excludesFile` alternative is also wrong for a different reason: overriding `core.excludesFile` discards the user's global ignore file, which can cause unrelated files to be staged, and it still uses git's ignore precedence. The correct fix is a pathspec exclusion on `git add` (`':(exclude,top).agy'`) plus an independent Python filter over `git status --porcelain -z` for the D6 patterns. That avoids mutating anything, is deterministic, and is not influenced by repository content. Update SPEC.md:127 and QUESTIONS.md Q2 accordingly (F04).

**D5 — CONFIRMED.** With only the completion safety net, a job that never writes `COMMIT_MSG` (very likely given the preamble asks for nine behaviours and D6 blocks nothing that would remind the model) has no commits until it ends; a crash, a wrapper restart, an audit kill, a cancel, or an OOM produces zero commits and total loss. A 240-minute job that does write `COMMIT_MSG` may still produce one enormous commit if the model writes it once at the end. A timed checkpoint commit is cheap and is the single highest-value robustness addition: every N minutes (default 10) if the worktree is dirty, commit `agy: checkpoint <ts>`, and always checkpoint before any kill (except an audit violation), before cancel, and before wrapper exit. Combine with F06's crash recovery so the checkpoint and the final safety net are idempotent. Also note D3's "Upon job termination" wording conflicts with the audit refusal (F05) and must be narrowed.

**D6 — CONFIRMED.** There is no cleanup tool, no retention policy, no `git worktree remove`/`prune`, no disk accounting, and no lifecycle for the branch. Leaving worktrees after `done`/`timeout`/`needs_review` (QUESTIONS Q4's recommendation) is defensible for review, but the spec dropped Q4's second half — the explicit cleanup command or configuration — so the recommendation is only half-implemented. `Runner.cleanup(handle)` is about process resources, not worktrees, and is not a substitute. Add `agy_cleanup` with dirty/unmerged safeguards, retention and disk caps in config, a startup `git worktree prune` for completed jobs, and disk usage in `agy_list`. Also warn that deleting `<jobdir>` by hand leaves a registered worktree in the user's repository (F24).

**D7 — CONFIRMED.** A default `seconds=300` blocking call is a poor default for an MCP tool: it can exceed the client's per-call timeout (I could not find a documented dsh cap in this workspace, which is itself an uncertainty), it holds a server worker for five minutes, and if it is cancelled client-side the tool result is lost while the job keeps running, which the orchestrator will probably read as a failure. Fix by making the default small (30–60 s), documenting the maximum the client allows, always returning immediately with the state plus `next_actions`, and telling the orchestrator to poll `agy_status` or call `agy_wait` again. If a long wait is genuinely needed, it should be a separate explicitly long-running tool with progress notifications, or the server should implement MCP progress and handle cancellation without affecting the job. Do not rely on the client being configured with a 300 s timeout (F19).

**D8 — CONFIRMED.** A 5-second poll of one small file is negligible (at most a few hundred bytes read twice per second with two jobs), and inotify would add a dependency, atomic-rename edge cases, watch invalidation after `git worktree remove`, and portability costs for no v1 benefit. The latency (up to 5 s) is fine for human-scale commits. Keep polling. Two caveats: (a) the partial-write problem is not solved by polling and requires a settle window and a consume/rename step (F12); (b) the audit must not be tied to the poll — it needs its own 5 s timer and a hard gate before every launch/resume (F02, F04's audit checkpoints), because otherwise a quiet job with no `COMMIT_MSG` activity is audited only twice in four hours.

# (2) TOP 5 THINGS TO FIX BEFORE BUILDING

1. **Stop claiming containment, and add a detection path for the real thing.** Fix F01/F02/F03's false premises: agy can read any file and (via `git diff --output`) write any file, so worktree isolation is branch isolation plus best-effort path confinement, and the audit is a detector for `.agents/**` hooks — not a sandbox. Add a pre/post-job check of user-sensitive paths (`~/.ssh`, `~/.gnupg`, `~/.config`, `~/.gitconfig`, `~/.netrc`, `~/.bashrc`, the main repo `.git/config`) by mtime/hash so a host-write attack is at least visible, and correct the README table. Add the probe for `git diff --output` to Open Items.

2. **Make the commit pipeline deterministic.** Pre-create `.agy/` and `PLAN.md` (a denied `mkdir` proves agy cannot do it reliably); replace hash-only triggering with settle + validate + rename-to-consume; add the 10-minute checkpoint commit and the pre-kill/pre-cancel checkpoint; and replace `.git/info/exclude` with a `git add` pathspec + Python filter (F03, F04, F07, F12). Without this, the central mechanism either never fires, double-fires, or commits garbage.

3. **Close the audit's lifecycle holes.** Run the audit before *every* launch, including resume and fix turns; run it on a 5 s timer independent of `COMMIT_MSG`; make the fatal set execution-relevant (`.git` pointer, `.agents/**` manifests/skills/hooks, resolved git config) and demote `.gitignore`/`.gitattributes`/`.gitmodules` to blocked-not-fatal; make the safety-net commit conditional on a passing audit; add the hard gate that a failed audit can never be relaunched (F02, F05, F09, F29).

4. **Make long jobs survive.** Redirect agy's stdout/stderr to files, persist pid + start time + state atomically, add a startup reaper that reattaches or finalizes every `running` job, add a wrapper-enforced wall-clock deadline with `killpg`, and add the index-lock retry loop (F06, F10, F11). As written, a dsh restart or a long `git status` can silently end the job's ability to commit or report.

5. **Finish the contract surface before code.** Write the state-transition table, per-tool result schemas, and the parse precedence (including SIGTERM/SIGKILL, non-JSON, unknown status, DENIED, verify failure, and the last-line `NEEDS_DECISION` rule); fix the literal `~`, the resume invocation, and the `--log-file`/`--print-timeout` flags (F13–F19). The orchestrator cannot use a tool whose states and returns are guesses, and the two most common outcomes (a denied command, a failed verify) currently have no coherent state.

# (3) THINGS I AM UNSURE ABOUT, AND WHAT WOULD SETTLE THEM

- **Whether `write_file` creates missing parent directories** (decides whether F03 is fatal or merely embarrassing). Settle with one probe: `agy ... -p "Create a file named .agy/COMMIT_MSG containing 'x'"` in a fresh repo with no `.agy/`.
- **Whether `git diff --output=<absolute path>` is allowed by the whitelist in practice** (the F01 severity). T1 proves arbitrary arguments are accepted, but this exact option was never run. Settle with `agy -p "Run this exact shell command: git diff --output=~/agy-probe-scratch/out.txt"` and check the file.
- **Whether `.agents/hooks.json` is re-read during a session or only at session start, and whether hooks fire on `--conversation` resume** (decides whether F02's window is seconds or one relaunch). Settle with a harmless hook that appends a timestamp to `/tmp/hook_probe`, then create it mid-run and continue, and separately resume a conversation.
- **Whether `--conversation <id>` restores the original cwd or uses the caller's cwd** (F16). The `init` event carries `cwd`; run a resumed conversation from a different directory and read `cwd` from stream-json, or check `/proc/<pid>/cwd`.
- **Whether `GIT_CONFIG_COUNT`/`KEY_n`/`VALUE_n` are honoured by *all* git code paths used by agy (subprocess, `git status`, `git diff`)**, and whether `core.hooksPath=/dev/null` truly disables hooks rather than erroring (D4's open item). Settle with a repo-local `core.hooksPath` and a hook that writes a marker; then run `git status` from inside agy and from the wrapper with the env overrides.
- **How often `git status` actually takes `index.lock` in this git version on a large worktree** (F10 likelihood). Settle by running agy on a large repo while the wrapper loops `git add`/`git commit` and counting "index.lock exists" failures.
- **Whether `.git/config` is reachable at all from the linked worktree by agy's file tools** (I believe not: `.git` is a regular file, so `.git/config` is `ENOTDIR`), and whether absolute-path `git` commands can write it (F01 says yes in principle). Settle with a worktree, not a normal repo, and a `git diff --output` to the main `.git/config`.
- **Whether the MCP/FastMCP handler runs sync handlers in a thread or on the event loop** (affects whether `agy_wait(300)` blocks all other tools). Settle by reading the installed `mcp` version's FastMCP source, or by calling `agy_status` concurrently with `agy_wait`.
- **What dsh's per-tool-call timeout actually is** (D7). I found no configuration in this workspace. Settle by reading dsh's tool-call config or by timing a deliberately slow tool.
- **Whether agy emits any non-JSON bytes on stdout in `--output-format json` mode** (banners, warnings, multiple documents) and whether `status` can be something other than `SUCCESS` (F18). Settle by grepping the probe JSON corpus and by forcing a mid-run kill.
- **Whether `search_web` can be disabled by any CLI flag or setting** (the only mitigation for exfiltration besides v2 isolation). The probes did not test a disabling mechanism, and S5 shows it is on by default.
- **Whether `git worktree add` leaves any state behind when it fails** (partial branch, `worktrees/<n>` entry) — matters for F23's cleanup-on-failure rule. Settle by forcing a failure (colliding branch name) and inspecting `git worktree list`.
- **Whether `.gitignore` can actually re-include `.agy/...` given the excluded-parent rule** (F04's severity; it depends on whether the wrapper writes `.agy/` or `.agy/*`). Settle with a throwaway repo: `info/exclude` containing `.agy/*`, a root `.gitignore` containing `!.agy/PLAN.md`, and `git check-ignore -v .agy/PLAN.md`.
