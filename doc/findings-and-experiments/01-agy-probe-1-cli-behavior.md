# 01 — AGY CLI Behavior and Headless Operation (Probe 1)

Status: current
Audience: maintainers, contributors

Empirical findings from the first exploratory probe of the `agy` CLI in headless
non-interactive mode (`-p`), establishing baseline tool execution, allow-rules,
resumption, failure shapes, timeouts, instruction files, process detachment, and concurrency.

## Question

How does the `agy` CLI behave under non-interactive headless operation (`-p`), what
permissions are required for basic file and git operations, and how does it handle
resumption, timeouts, failures, and concurrency?

## Method

A systematic series of 60 test turns was executed against `agy` on Linux x86_64
(model `gemini-3.8-flash-low`, Antigravity IDE assistant). Tests evaluated 12 specific
operational questions (Q1–Q12) using exact CLI invocations in throwaway scratch
directories (`~/agy-probe-scratch/`):
- Q1: `permissions.allow` prefix matching behavior in `~/.gemini/antigravity-cli/settings.json`.
- Q2: Inherent tool permissions under `--mode accept-edits` with zero custom rules.
- Q3: Conversation context preservation across `--conversation <id>` and `-c` resumption.
- Q4: Stdio NDJSON streaming via `--input-format stream-json --output-format stream-json`.
- Q5: Exit codes and JSON error schemas under denials, invalid models, invalid modes, and empty prompts.
- Q6: Duration parsing and turn truncation behavior under `--print-timeout`.
- Q7: Automatic discovery and loading of hierarchical instruction files (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`).
- Q8: Process group detachment without a controlling TTY (`setsid nohup`, stdin from `/dev/null`).
- Q9: Compatibility between explicit `--model` tiers and the `--effort` flag.
- Q10: Parallel execution of distinct `agy` jobs across different repositories.
- Q11: Persistent state directory layout under `~/.gemini/antigravity-cli/` and `--log-file` redirection.
- Q12: Kernel requirements and fallback behavior for `--sandbox` and `--mode auto`.

## Result

### Operational Findings (Q1–Q12)

1. **Allow-rules (Q1):** `permissions.allow` matches command strings by prefix: `command(<binary> [subcommand])`.
   - `command(git status)` permitted `git status` (duration 6.75s, 13,853 total tokens).
   - `command(git add)` permitted `git add foo.txt` (duration 5.61s, 13,741 total tokens).
   - `command(git commit)` permitted `git commit -m "initial commit"` (duration 5.03s, 13,825 total tokens).
   - `command(python3)` permitted `python3 -c "print(1)"` (duration 68.77s, 13,742 total tokens).
   - Unlisted commands (`git push`, `rm foo.txt`, `curl https://example.com`) were denied immediately.
   - Denials exited with return code `0`, logged `jetski: no output produced — a tool required the "command" permission...` to stderr, and returned JSON stdout with `status: "SUCCESS"`, empty `response: ""`, and `denied_actions: [{"action": "command", "display_name": "RunCommand"}]`.
2. **Accept-edits baseline (Q2):** With zero allow-rules configured, `--mode accept-edits` permitted:
   - `write_file` (created `foo.txt`).
   - `edit_file` (replaced content in `foo.txt`).
   - `list_dir` (inspected directory contents).
   - `read_file` (read `foo.txt`).
   - Safe read-only inspection commands (`echo 123`, `git status`) via internal heuristics.
   - Mutating shell commands (`mkdir -p newfolder`, `git add`) were denied (exit code 0, `denied_actions` non-empty).
3. **Resumption (Q3):**
   - Call 1 set a secret token (`PINEAPPLE777`): duration 2.50s, 1 turn, 6,800 tokens.
   - Call 2 resumed via `--conversation <id>`: recalled `PINEAPPLE777`, duration 19.57s, 2 turns, 13,818 tokens.
   - Call 3 resumed via `-c`: recalled token, duration 37.64s, 3 turns, 21,050 tokens.
   - Context is preserved across calls without re-emitting previous turns in stdout.
4. **stream-json (Q4):** Input and output NDJSON streams operated reliably. Turn 1 took 2.35s (6,779 tokens); turn 2 took 4.44s cumulative (13,631 tokens). Sending a second turn while turn 1 was running was buffered cleanly by `agy` and processed as turn 2 after turn 1 emitted its intermediate result event.
5. **Failure shapes (Q5):**
   - Permission denials exit with return code `0` (both text and JSON modes). Under `--output-format json`, stdout contains `status: "SUCCESS"` and `denied_actions: [{"action": "command", "display_name": "RunCommand"}]`.
   - Invalid `--model bad-model-foo-123` exited `1`, stderr listed available models.
   - Invalid `--mode badmode` printed a warning to stderr, fell back to default mode, and exited `0`.
   - Empty prompt string (`-p ""`) exited `1` with `error: Error: empty prompt`.
   - Missing flag argument (`-p` with no value) exited `2` with `flag needs an argument: -p`.
   - Deleted/non-existent current working directory exited `0` normally.
6. **Print timeout (Q6):**
   - `--print-timeout` requires explicit time units: `120` failed with return code `2` (`missing unit in duration`); `120s` and `2m` succeeded.
   - A 20s timeout on 5 long essays halted cleanly at 20s with return code `0`, stderr `[agy] print timeout after 20s with turn in progress; returning partial output`, stdout JSON `status: "SUCCESS"` preserving the conversation ID (`e699d7d1-2305-4f31-af5d-e87f8e119886`), and zero partial files written.
7. **Instruction files (Q7):** `AGENTS.md` and `GEMINI.md` are automatically discovered and loaded from the repository hierarchy; global instructions are read from `~/.gemini/GEMINI.md`. `CLAUDE.md` is ignored.
8. **Process detachment (Q8):** Execution succeeded detached from any TTY via `setsid nohup agy ... < /dev/null` in 14.00s with return code 0. Execution strictly requires `HOME` (fails immediately if missing) and `PATH`; terminal variables (`TERM`, `DISPLAY`) are not required.
9. **Model and effort (Q9):** `--model gemini-3.8-flash-medium` succeeded. Specifying a conflicting effort tier (`--model gemini-3.8-flash-medium --effort low`) failed with exit code `1` (`model conflicts with --effort`). Token usage on identical prompts: low (6,795 tokens), medium (6,834 tokens), high (6,882 tokens).
10. **Parallel execution (Q10):** Concurrent headless jobs in separate repository directories completed simultaneously in 15.82s (return code 0) without database collisions or SQLite locks.
11. **Storage layout (Q11):** Persistent data is organized under `~/.gemini/antigravity-cli/`:
    - `conversations/<id>.db` (per-session SQLite store, ~21 MB total observed).
    - `brain/<id>/.system_generated/logs/transcript.jsonl` (trajectory logs, ~2.6 MB total).
    - `conversation_summaries.db` (central SQLite index, ~68 KB).
    - `jetbox_summaries_proto.pb` (protobuf cache, ~28 KB).
    - `log/cli-YYYYMMDD_HHMMSS.log` (session logs, ~2.5 MB total).
    - `--log-file <path>` redirects the CLI session log to a custom file (39 KB produced in test).
12. **Sandbox and auto mode (Q12):** On standard Linux without unprivileged user namespaces (`CLONE_NEWUSER`), `bwrap --ro-bind / / true` failed with `bwrap: setting up uid map: Permission denied`. Invoking `agy --mode auto` produced stderr warning: `warning: --mode auto is not available because the terminal sandbox is not available on this host; continuing in the default mode` and exited `0`.

### Aggregate Token Cost

- Turns executed: 60
- Input tokens: 405,561
- Output tokens: 2,723
- Thinking tokens: 0
- Total tokens: 408,284

## Conclusion

The probe established core operational invariants adopted across the server design:
1. **Runner invocation ([D1](../design/decisions.md#d1-threat-model--pluggable-runner-architecture), [D7](../design/decisions.md#d7-sanitized-execution-environment)):** The wrapper executes `agy` non-interactively with `--mode accept-edits --output-format json`, `start_new_session=True`, and a sanitized environment preserving `HOME`, `PATH`, and `LANG`.
2. **Exit code zero on failure ([D25](../design/decisions.md#d25-denied-actions-do-not-force-terminal-denied-state), [D30](../design/decisions.md#d30-resilient-result-parsing-precedence)):** The wrapper cannot rely on process exit codes to detect denials or timeouts; it inspects `denied_actions` in the parsed JSON stdout and scans stderr for timeout markers.
3. **Forbidden flags (`src/agy_mcp/runner.py:22-29`):** The runner explicitly bans `--sandbox`, `--effort`, `-c`, and `--dangerously-skip-permissions` to prevent configuration collisions and security bypasses.
4. **Log capture ([D5](../design/decisions.md#d5-isolated-worktrees-and-security-auditing)):** The wrapper supplies `--log-file <jobdir>/cli.log` to isolate engine debug logs per job.
5. **Zero custom allow-rules ([D2](../design/decisions.md#d2-zero-custom-allow-rules)):** Because `accept-edits` permits all necessary file creation and editing tools natively, no custom shell execution rules are required for basic coding jobs.

## Evidence

| Claim | Repo file and line or report path |
|---|---|
| Q1: allow-rules prefix matching and denial shape | `docs/AGY_FINDINGS.md:8-70` |
| Q1: `git status` 6.75s, 13,853 tokens | `docs/AGY_FINDINGS.md:34` |
| Q1: `git add` 5.61s, 13,741 tokens | `docs/AGY_FINDINGS.md:38` |
| Q1: `git commit` 5.03s, 13,825 tokens | `docs/AGY_FINDINGS.md:42` |
| Q1: `python3` 68.77s, 13,742 tokens | `docs/AGY_FINDINGS.md:46` |
| Q2: accept-edits file tool allowances and shell denials | `docs/AGY_FINDINGS.md:74-112` |
| Q3: resume token counts (6,800 / 13,818 / 21,050) and turns | `docs/AGY_FINDINGS.md:116-146` |
| Q4: stream-json NDJSON events and turn pipelining | `docs/AGY_FINDINGS.md:150-176` |
| Q5: failure exit codes (0 for denial, 1 for bad model, 2 for missing arg) | `docs/AGY_FINDINGS.md:180-244` |
| Q6: `--print-timeout` unit requirement and 20s exit 0 | `docs/AGY_FINDINGS.md:248-274` |
| Q7: `AGENTS.md` and `GEMINI.md` loaded, `CLAUDE.md` ignored | `docs/AGY_FINDINGS.md:278-299` |
| Q8: detached execution in 14.00s without TTY | `docs/AGY_FINDINGS.md:303-319` |
| Q9: `--model` and `--effort` conflict error (exit 1) | `docs/AGY_FINDINGS.md:323-345` |
| Q10: parallel multi-repo execution in 15.82s | `docs/AGY_FINDINGS.md:349-363` |
| Q11: storage layout sizes and `--log-file` redirect (39 KB) | `docs/AGY_FINDINGS.md:367-387` |
| Q12: `--mode auto` fallback warning and `bwrap` denial | `docs/AGY_FINDINGS.md:391-414` |
| Aggregate token counts: 60 turns, 408,284 total tokens | `docs/AGY_FINDINGS.md:434-439` |
| Runner forbidden flags definition | `src/agy_mcp/runner.py:22-29` |
| Date of probe 1 run | not recorded in document text (git commit 2026-10-24) |
| Exact `agy` CLI version for probe 1 | not recorded in document text (inventory notes v1.4.7 in `doc/audits/2026-11-02-part2a-inventory.md:96`) |

## How to reproduce

Exact CLI commands are recorded under sections Q1–Q12 of `docs/AGY_FINDINGS.md`. To reproduce permission denial JSON output:
```bash
agy --mode accept-edits --output-format json --model gemini-3.8-flash-low -p "Run git push"
```

## Sources

- `docs/AGY_FINDINGS.md`
- `src/agy_mcp/runner.py:22-29`
- `doc/design/decisions.md`
