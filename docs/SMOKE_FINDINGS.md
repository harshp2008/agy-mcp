# Real `agy` Smoke Test Findings Report

**Date**: 2026-10-25  
**Tool**: `agy` v1.4.7  
**Model**: `gemini-3.8-flash-medium`  
**Execution Environment**: Linux x86_64, agy-mcp stdio protocol over `mcp.client.stdio`  
**Working Directory**: `~/Documents/GITHUB PROJECTS/agy-mcp`  
**Scratch Directory**: `~/agy-smoke`  

---

## Executive Summary

This report documents the first end-to-end execution of the real `agy` CLI driven through the `agy-mcp` FastMCP server over the official stdio MCP protocol. A throwaway fixture repository (`~/agy-smoke/repo`) was used with isolated state storage (`~/agy-smoke/state`). All safety rails were strictly respected: no configuration files outside the scratch area were modified, exactly 5 real `agy` CLI invocations were performed (within the 6-run cap), and every job worktree was cleanly pruned upon completion.

---

## Run-by-Run Findings (S1 – S7)

### S1. Happy Path with Commits

* **Prompt Run**:
  ```text
  In this repo add src/mathutil.py with add(a,b) and mul(a,b), then tests/test_mathutil.py with 3 tests, then update README.md with usage. Do these as three separate steps.
  ```
* **Raw Result Files**:
  * Tool Result: [`docs/reports/smoke/s1-result.json`](reports/smoke/s1-result.json)
  * Status Polls: [`docs/reports/smoke/s1-status-polls.txt`](reports/smoke/s1-status-polls.txt)
  * Git Log: [`docs/reports/smoke/s1-git-log.txt`](reports/smoke/s1-git-log.txt)
  * CLI Log: [`docs/reports/smoke/s1-cli.log`](reports/smoke/s1-cli.log)

* **Observed vs Expected**:

| Metric / Aspect | Expected | Observed | Match? |
|---|---|---|---|
| Final State | `done` | `needs_review` | No (Surprise) |
| Review Reason | `None` | `blocked_by_permission` | Explains state |
| Wrapper Commits | >= 1 | 0 | No |
| Tool Denials | `[]` | `[{"action": "command", "display_name": "RunCommand"}]` | Yes (Captured) |
| agy Duration | < 600s | 16.07s (elapsed 28.1s) | Yes |
| Token Usage | tracked | 32,134 in, 927 out (33,061 total) | Yes |
| Num Turns | >= 1 | 1 | Yes |
| `.agy/` Committed | Never | False | Yes |
| Checkpoint Timer | Fired if >2m | Did not fire (job took 28.1s < 120s) | Yes |

* **Verdict**: **SURPRISE**  
  *Analysis*: In headless print mode (`-p`), when given a complex coding prompt, real `agy` attempted to run shell commands (`RunCommand`). Because the host environment requires confirmation for shell commands (`toolPermission=request-review`), `agy` logged: `Print mode: soft-denying tool confirmation "RunCommand" at step 8` and immediately stopped with exit code 0 and empty response. Because zero commits were made before the denial, `agy-mcp`'s precedence rules correctly categorized the job as `needs_review(blocked_by_permission)`.

---

### S2. Parent Directories

* **Prompt Run**:
  ```text
  Create the file docs/guide/deep/nested/notes.txt containing one sentence, using your file tools.
  ```
* **Raw Result Files**:
  * Tool Result: [`docs/reports/smoke/s2-result.json`](reports/smoke/s2-result.json)
  * Git Log: [`docs/reports/smoke/s2-git-log.txt`](reports/smoke/s2-git-log.txt)
  * CLI Log: [`docs/reports/smoke/s2-cli.log`](reports/smoke/s2-cli.log)

* **Observed vs Expected**:

| Metric / Aspect | Expected | Observed | Match? |
|---|---|---|---|
| Final State | `done` | `done` | Yes |
| Parent Dirs Created | `docs/guide/deep/nested/` created | Created automatically by file tool | Yes |
| Commit Created | 1 commit | 1 commit (`4ac2ded46b23d9cec17a09652bd54926db824e37`) | Yes |
| Commit Subject | Written by agy | `"Add nested notes documentation file"` | Yes |
| Diff Stat | 1 file changed, 1 insertion(+) | `docs/guide/deep/nested/notes.txt \| 1 +` | Yes |
| Denials | `[]` | `[]` | Yes |
| Duration | < 600s | 42.71s (elapsed 53.17s) | Yes |
| Token Usage | tracked | 87,358 in, 2,756 out (90,114 total) | Yes |

* **Verdict**: **PASS**  
  *Settles Open Question*: Real `agy`'s file tools automatically create all parent directories without needing shell `mkdir` commands. When instructed to use file tools, real `agy` successfully writes `.agy/PLAN.md` and `.agy/COMMIT_MSG`, which the supervisor watcher reliably commits.

---

### S3. Denial Probe

* **Prompt Run**:
  ```text
  Using the shell, run these commands one at a time and report the output of each: `cat README.md`, `pwd`, `pgrep -f python`, `mkdir tmpdir`, `python3 -c 'print(1)'`, `rm -f README.md`, `ls`, `grep -n add src/mathutil.py`. If a command is refused, say so and continue with the next.
  ```
* **Raw Result Files**:
  * Tool Result: [`docs/reports/smoke/s3-result.json`](reports/smoke/s3-result.json)
  * Git Log: [`docs/reports/smoke/s3-git-log.txt`](reports/smoke/s3-git-log.txt)
  * CLI Log: [`docs/reports/smoke/s3-cli.log`](reports/smoke/s3-cli.log)

* **Observed vs Expected**:

| Metric / Aspect | Expected | Observed | Match? |
|---|---|---|---|
| Final State | `needs_review` or `done` | `needs_review` (`blocked_by_permission`) | Yes |
| `denied_actions` | captured | `[{"action": "command", "display_name": "RunCommand"}]` | Yes |
| Exact command string | Expected in denied_actions | **Not present** (only `"action": "command"`) | Surprise |
| agy Behavior on Denial | Continue to next | **Stops immediately** upon first soft-denial | Surprise |
| README.md Preserved | Exists intact | `True` (never deleted) | Yes |
| Duration | < 600s | 13.50s (elapsed 24.11s) | Yes |
| Tokens | tracked | 23,715 in, 939 out (24,654 total) | Yes |

* **Verdict**: **PASS (Isolation) / SURPRISE (Output Schema & Halt Behavior)**  
  *Analysis*: The shell commands were completely prevented from modifying the repository (`README.md` was untouched). However, `agy`'s CLI JSON envelope outputs only coarse categorization (`action: "command", display_name: "RunCommand"`), omitting the exact command argv string. Furthermore, print mode does not attempt remaining commands once a confirmation is soft-denied; it immediately terminates the conversation stream.

---

### S4. Decision and Resume

* **Prompt Run**:
  ```text
  Add a config file to this repo. You must pick between TOML and YAML, which is a choice the owner must make; do not guess.
  ```
* **Resume Answer**: `"TOML"`
* **Raw Result Files**:
  * Decision Result: [`docs/reports/smoke/s4-decision-result.json`](reports/smoke/s4-decision-result.json)
  * Resume Result: [`docs/reports/smoke/s4-resume-result.json`](reports/smoke/s4-resume-result.json)
  * Resume Argv: [`docs/reports/smoke/s4-argv-resume.json`](reports/smoke/s4-argv-resume.json)
  * Git Log: [`docs/reports/smoke/s4-git-log.txt`](reports/smoke/s4-git-log.txt)
  * CLI Log: [`docs/reports/smoke/s4-cli.log`](reports/smoke/s4-cli.log)

* **Observed vs Expected**:

| Metric / Aspect | Expected | Observed | Match? |
|---|---|---|---|
| Step 1 Last Line Marker | `NEEDS_DECISION: <q> \| <opts> \| <rec>` | `NEEDS_DECISION: Which configuration format should be used for the repository config file? \| TOML, YAML \| TOML` | Yes (Exact) |
| Step 1 FSM State | `needs_decision` | `needs_decision` | Yes |
| Resume Invocation | `--conversation <id>` | Used `--conversation 8b202219-915f-40fe-98f0-0a41e06c82b3` | Yes |
| Context Preserved | Mentions earlier decision | Explicitly records owner's decision in `.agy/PLAN.md` | Yes |
| Final FSM State | `done` | `done` | Yes |
| Commit Created | 1 commit | 1 commit (`367b468fad1197b91381ec39bb1d9a15008ac337`) | Yes |
| Commit Subject | Descriptive | `"feat: add config.toml configuration file"` | Yes |
| Total Duration | < 600s | 145.51s (elapsed 155.42s cumulative) | Yes |
| Cumulative Tokens | tracked | 251,412 in, 8,874 out (260,286 total) | Yes |

* **Verdict**: **PASS**  
  *Analysis*: Flawless interactive lifecycle. Real `agy` adhered to the `NEEDS_DECISION` prompt preamble instruction, halted cleanly with the required marker, resumed via `--conversation`, and completed the work with proper git commit and plan tracking.

---

### S5. Permission Preflight on the Real Config

* **Function Run**: `inspect_permission_rules(Path.home() / ".gemini")`
* **Raw Result File**: [`docs/reports/smoke/s5-preflight.txt`](reports/smoke/s5-preflight.txt)
* **Observed**:
  ```text
  permission_rules_effective: []
  dangerous_allow_rules: []
  warnings: []
  ```
* **Verdict**: **PASS**  
  *Analysis*: Preflight safely inspected configuration files without disclosing any secret tokens, correctly identified zero dangerous allow rules, and completed without warnings.

---

### S6. Observability

* **Raw Result Files**:
  * Status Polls: [`docs/reports/smoke/s1-status-polls.txt`](reports/smoke/s1-status-polls.txt)
  * Summary: [`docs/reports/smoke/s6-observability.txt`](reports/smoke/s6-observability.txt)

* **Observed**:
  * `agy_status` polled at T+0s and T+15s during active execution.
  * `last_cli_log_mtime` updated from `0.0` at initial queueing to `1792909960.38` during execution.
  * `recent_log_tail` returned redacted log lines directly from `cli.log`.
  * `commits_count` and `worktree_dirty` tracked repo state accurately.
* **Verdict**: **PASS**  
  *Can an orchestrator tell a working job from a hung one?* **YES**. While `agy` runs, `last_cli_log_mtime` updates steadily every few seconds. An orchestrator can safely declare a job hung if `last_cli_log_mtime` remains unchanged for an extended interval while state is `running`.

---

### S7. Protocol Health

* **Raw Result Files**:
  * Server Stdout: [`docs/reports/smoke/server-stdout.log`](reports/smoke/server-stdout.log)
  * Server Stderr: [`docs/reports/smoke/server-stderr.log`](reports/smoke/server-stderr.log)

* **Observed**:
  * Exact number of stdout lines captured across all runs: **33**.
  * Validation: Every single line (100%) parsed cleanly as a valid JSON-RPC 2.0 message with `"jsonrpc": "2.0"`.
  * Zero non-protocol characters, zero logging output, and zero tracebacks leaked onto stdout.
  * Server stderr contained 33 log messages with 0 unexpected warnings or fatal errors.
* **Verdict**: **PASS**

---

## Assumptions Confirmed and Assumptions Broken

### Assumptions Confirmed

1. **Preamble instruction adherence (SPEC Section 14)**: Real `agy` accurately reads and follows preamble rules 4, 5, 7, and 8 when file tools are used, maintaining `.agy/PLAN.md` checklists and writing imperative commit messages with blank lines and descriptive bodies to `.agy/COMMIT_MSG` (confirmed in S2 and S4).
2. **Parent directory creation (SPEC Section 10)**: Real `agy`'s file writing tools automatically create nested parent directory paths without requiring shell commands or explicit directory creation steps (confirmed in S2).
3. **NEEDS_DECISION marker format (SPEC Section 14)**: Real `agy` outputs the exact specified format `NEEDS_DECISION: <question> | <options> | <recommendation>` as the final line of its response when instructed to pause for an owner choice (confirmed in S4).
4. **Resumption context preservation (SPEC Section 10 & 11)**: Calling `agy` with `--conversation <id>` preserves full prior trajectory and context, allowing `agy` to resume work seamlessly from where it left off (confirmed in S4).
5. **Git metadata cleanliness (SPEC Section 9)**: Neither `.agy/` nor any wrapper metadata files were ever staged or committed into repository branches across any run (confirmed in S1, S2, S4).
6. **Stdio protocol purity (SPEC Section 8)**: FastMCP stdio server emits exclusively valid JSON-RPC 2.0 frames on stdout; all logging is strictly redirected to stderr and `server.log` (confirmed in S7).
7. **Host settings integrity (SPEC Section 5)**: `agy` print mode does not modify user settings or configuration files under `~/.gemini/config/` or `~/.gemini/antigravity-cli/` (confirmed via SHA-256 comparison before and after all runs).

### Assumptions Broken

1. **`denied_actions` does NOT contain exact command strings (SPEC Section 15)**:
   * *Observation*: SPEC section 15 assumed `denied_actions` in the agy JSON output contains the full executed command string (e.g. `command: "rm -f README.md"`). In reality, real `agy` outputs only `[{"action": "command", "display_name": "RunCommand"}]`.
   * *Affected Section*: SPEC Section 15 (Denial Classification and Action Extraction).
   * *Proposed Fix*: Update SPEC Section 15 to reflect that `denied_actions` provides only coarse tool action types (`"RunCommand"`), and document that fine-grained command auditing requires extracting command arguments from `cli.log` or language server RPC logs.
2. **Headless `agy` does not continue after a tool denial (SPEC Section 15)**:
   * *Observation*: SPEC section 15 assumed `agy` might work around or continue with remaining commands after a permission denial. In reality, in headless print mode (`-p`), any unapproved tool confirmation triggers a soft-denial that immediately terminates the session turn with status `SUCCESS` and an empty response.
   * *Affected Section*: SPEC Section 15 (Tool Denial Lifecycle).
   * *Proposed Fix*: Clarify in SPEC Section 15 that when running non-interactively, an unapproved tool call halts the turn immediately, leaving subsequent commands unexecuted.
3. **Multi-step tasks without tool hints may default to shell commands (SPEC Section 10)**:
   * *Observation*: When tasks do not explicitly restrict tools to file operations, `agy` may default to invoking shell commands (`RunCommand`), which are soft-denied by default in headless mode.
   * *Affected Section*: SPEC Section 10 & 14 (Preamble and Execution Modes).
   * *Proposed Fix*: In `preamble.md`, add an explicit instruction directing `agy` to prefer built-in file tools over shell commands for creating, editing, and reading repository files.

---

## Answers to Core Evaluation Questions

### (1) Does real agy follow the COMMIT_MSG instruction after every step?
**Yes**, when file tools are utilized. In S2, `agy` wrote `.agy/COMMIT_MSG` with `"Add nested notes documentation file"`, and in S4 it wrote `"feat: add config.toml configuration file"`, both of which were successfully committed by the wrapper watcher. In S1, it was halted before completing any step due to a shell command denial.

### (2) Does `denied_actions` contain exact command strings?
**No**. As captured in [`docs/reports/smoke/s1-result.json`](reports/smoke/s1-result.json) and [`docs/reports/smoke/s3-result.json`](reports/smoke/s3-result.json), the raw JSON output is strictly:
```json
"denied_actions": [
  {
    "action": "command",
    "display_name": "RunCommand"
  }
]
```
The exact command string (e.g., `cat README.md`, `pwd`) is not included in `denied_actions`.

### (3) Does agy stop on a denial or work around it?
**It stops immediately**. As evidenced by `docs/reports/smoke/s3-cli.log`:
```text
I1025 12:03:57.940562  tool_confirmation_manager.go:216] Print mode: soft-denying tool confirmation "RunCommand" at step 8
I1025 12:03:57.940645  server.go:2646] Tool confirmation for conversation ... approved=false
I1025 12:03:57.951509  conversation_manager.go:1034] Stopping conversation stream
```
The conversation stream stops at step 8 upon the first denial; subsequent commands in the prompt are not executed, and `README.md` was never touched.

### (4) Does the NEEDS_DECISION last-line marker survive real output?
**Yes, perfectly**. In S4, the final response line produced by `agy` was:
```text
NEEDS_DECISION: Which configuration format should be used for the repository config file? | TOML, YAML | TOML
```
The wrapper FSM successfully matched the marker and transitioned the job to `needs_decision`.

### (5) Does `--conversation` resume keep context?
**Yes**. When resumed with `--conversation 8b202219-915f-40fe-98f0-0a41e06c82b3` and user answer `"TOML"`, `agy` referenced the owner's decision in `.agy/PLAN.md` and successfully created the requested `config.toml` file without restarting from scratch.

### (6) Did anything outside `~/agy-smoke` change?
**No configuration or settings changed**.
* `~/.gemini/antigravity-cli/settings.json` SHA-256 was identical before and after: `fc58bd6133bc37c97d2674100c811e9fc04bf895fb54189aa4e763c0315d9798`.
* All regular configuration files under `~/.gemini/config/` had identical hashes before and after.
* The only files under `~/.gemini` whose hashes changed were `conversation_summaries.db` and `jetbox_summaries_proto.pb`, which are `agy`'s internal SQLite and protobuf databases tracking conversation metadata (expected behavior).
* `pgrep -af "agy-smoke"` showed zero leftover processes.

### (7) Total tokens and wall time of all runs
* **Total Tokens**: **408,115** tokens across all runs:
  * S1: 33,061 tokens
  * S2: 90,114 tokens
  * S3: 24,654 tokens
  * S4: 260,286 tokens (cumulative across 2 turns, including 175,165 cached read tokens)
* **Total Wall Time**: **260.81s** (~4.35 minutes) of real execution:
  * S1: 28.10s
  * S2: 53.17s
  * S3: 24.11s
  * S4: 155.42s

### (8) Was `.agy/` ever committed?
**No**. In every git commit verified (`4ac2ded46b23d9cec17a09652bd54926db824e37` in S2 and `367b468fad1197b91381ec39bb1d9a15008ac337` in S4), git show confirmed that only repository files (`docs/guide/deep/nested/notes.txt` and `config.toml`) were committed.

### (9) Any content-filter block or warning from Google?
**No**. Zero content filter blocks, zero safety rating violations, and zero rate limit warnings occurred during any run.
