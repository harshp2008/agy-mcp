# 04 — Real AGY Smoke Test and Lifecycle Validation

Status: current
Audience: maintainers, contributors

Empirical findings from the first end-to-end execution of the real `agy` CLI driven
through the `agy-mcp` FastMCP server over the official stdio MCP protocol, evaluating
multi-step workflows, tool denials, decision pausing, resumption, preflight checks,
observability, and protocol purity.

## Question

Does the `agy-mcp` wrapper successfully drive the real `agy` CLI over stdio MCP
across complete lifecycles (happy path commits, parent directories, denial handling,
decision pauses and resume, preflight, observability, protocol health)?

## Method

Executed on 2026-10-25 using `agy` v1.4.7 with model `gemini-3.8-flash-medium` on Linux x86_64.
The test operated through an official MCP client (`mcp.client.stdio`) against a throwaway
fixture repository (`~/agy-smoke/repo`) and isolated state directory (`~/agy-smoke/state`).
Exactly 5 real `agy` CLI invocations were executed across 7 defined test scenarios (S1–S7),
evaluating 9 core validation questions. Worktrees were cleanly pruned upon completion.

## Result

### Scenario Breakdown (S1–S7)

1. **S1. Happy path with commits:**
   - Prompt: Add `src/mathutil.py`, `tests/test_mathutil.py`, and `README.md` in three separate steps.
   - Result: In headless print mode (`-p`), `agy` attempted to execute shell commands (`RunCommand`), which were soft-denied by the host environment (`toolPermission=request-review`). `agy` logged `Print mode: soft-denying tool confirmation "RunCommand" at step 8` and immediately stopped with return code 0 and empty response.
   - Wrapper categorized the job as `needs_review(blocked_by_permission)` because zero commits were created.
   - Metrics: Duration 16.07s (elapsed 28.1s), token usage 32,134 input, 927 output (33,061 total), 1 turn.
   - Raw report: [`docs/reports/smoke/s1-result.json`](../../docs/reports/smoke/s1-result.json).
2. **S2. Parent directories:**
   - Prompt: Create `docs/guide/deep/nested/notes.txt` containing one sentence using file tools.
   - Result: `agy` used built-in file tools to automatically create all nested parent directories without shell `mkdir`.
   - Resulting state: `done`, 1 git commit created (`4ac2ded46b23d9cec17a09652bd54926db824e37`), commit subject `"Add nested notes documentation file"`, 1 file changed (+1 insertion).
   - Metrics: Duration 42.71s (elapsed 53.17s), token usage 87,358 input, 2,756 output (90,114 total).
   - Raw report: [`docs/reports/smoke/s2-result.json`](../../docs/reports/smoke/s2-result.json).
3. **S3. Denial probe:**
   - Prompt: Sequentially execute shell commands (`cat README.md`, `pwd`, `pgrep`, `mkdir`, `rm`, `ls`, `grep`).
   - Result: State `needs_review(blocked_by_permission)`. `README.md` was preserved intact.
   - Observation: Headless `agy` does not continue past a denial; it halts immediately on the first soft-denial at step 8. `denied_actions` contained only coarse categorization `[{"action": "command", "display_name": "RunCommand"}]` with no command text.
   - Metrics: Duration 13.50s (elapsed 24.11s), token usage 23,715 input, 939 output (24,654 total).
   - Raw report: [`docs/reports/smoke/s3-result.json`](../../docs/reports/smoke/s3-result.json).
4. **S4. Decision and resume:**
   - Prompt: Add a configuration file, choosing between TOML and YAML as an owner decision.
   - Step 1: Paused in state `needs_decision`, final line: `NEEDS_DECISION: Which configuration format should be used for the repository config file? | TOML, YAML | TOML`.
   - Step 2: Resumed via `--conversation 8b202219-915f-40fe-98f0-0a41e06c82b3` with `"TOML"`. Context preserved in `.agy/PLAN.md`.
   - Final state: `done`, 1 commit created (`367b468fad1197b91381ec39bb1d9a15008ac337`), subject `"feat: add config.toml configuration file"`.
   - Metrics: Duration 145.51s (elapsed 155.42s cumulative), tokens 251,412 input, 8,874 output (260,286 total, including 175,165 cached read tokens).
   - Raw report: [`docs/reports/smoke/s4-resume-result.json`](../../docs/reports/smoke/s4-resume-result.json).
5. **S5. Permission preflight:**
   - Executed `inspect_permission_rules` against `~/.gemini`.
   - Result: `permission_rules_effective: []`, `dangerous_allow_rules: []`, `warnings: []`. Safe inspection without secret leakage.
   - Raw report: [`docs/reports/smoke/s5-preflight.txt`](../../docs/reports/smoke/s5-preflight.txt).
6. **S6. Observability:**
   - `agy_status` polled at T+0s and T+15s during active execution.
   - `last_cli_log_mtime` updated from `0.0` to `1792909960.38`.
   - `recent_log_tail` returned redacted log lines directly from `cli.log`.
   - Raw report: [`docs/reports/smoke/s6-observability.txt`](../../docs/reports/smoke/s6-observability.txt).
7. **S7. Protocol health:**
   - Exact stdout lines captured: 33 frames, 100% valid JSON-RPC 2.0.
   - Zero non-protocol characters or log leaks on stdout. Server stderr captured 33 clean log messages with zero unexpected warnings.
   - Raw report: [`docs/reports/smoke/server-stdout.log`](../../docs/reports/smoke/server-stdout.log).

### Core Evaluation Questions Summary

| Question | Verdict | Observed Result |
|---|---|---|
| (1) COMMIT_MSG instruction followed? | YES | Real `agy` wrote `.agy/COMMIT_MSG` in S2 and S4 when file tools were used. |
| (2) Exact command in `denied_actions`? | NO | JSON emitted only `{"action": "command", "display_name": "RunCommand"}`. |
| (3) agy stops on denial or continues? | STOPS | Conversation stream halts immediately upon the first soft-denial at step 8. |
| (4) NEEDS_DECISION marker survives? | YES | Emitted verbatim on the final response line; parsed by wrapper. |
| (5) `--conversation` resume keeps context? | YES | Prior context and decisions maintained in `.agy/PLAN.md`. |
| (6) Host settings modified? | NO | SHA-256 hashes of `~/.gemini/antigravity-cli/settings.json` and configs matched before/after. |
| (7) Total tokens and wall time? | TRACKED | Total tokens: 408,115 (S1: 33,061; S2: 90,114; S3: 24,654; S4: 260,286); Wall time: 260.81s. |
| (8) `.agy/` committed into repo? | NO | Git diff confirmed only product files (`notes.txt`, `config.toml`) committed. |
| (9) Content filter blocks or warnings? | NONE | Zero filter blocks, rate limit warnings, or safety rating trips. |

## Conclusion

The smoke run verified the fundamental wrapper architecture while correcting critical assumptions:
1. **Preamble and watcher validated ([D15](../design/decisions.md#d15-pre-provisioning-agy-and-planmd), [D24](../design/decisions.md#d24-commit_msg-settle-window-validation-and-consume-step)):** When instructed to use file tools, `agy` writes `.agy/PLAN.md` and `.agy/COMMIT_MSG`, enabling the watcher to make clean git commits.
2. **Denial schema corrected ([D25](../design/decisions.md#d25-denied-actions-do-not-force-terminal-denied-state), [D30](../design/decisions.md#d30-resilient-result-parsing-precedence)):** The wrapper cannot rely on `denied_actions` for command text or expect execution to continue after a denial.
3. **Standing prompt guidance ([D9](../design/decisions.md#d9-standing-prompt-preamble)):** Added guidance in `src/agy_mcp/preamble.md` directing `agy` to prefer file tools over shell commands for reading, creating, and modifying files.
4. **Interactive resume validated ([D26](../design/decisions.md#d26-last-line-parsing-for-needs_decision), [D28](../design/decisions.md#d28-resumption-guarding--context-rigor)):** Confirmed `--conversation <id>` preserves full context across interactive turn boundaries.
5. **Real-smoke bug fixes ([D63](../design/decisions.md#d63-real-smoke-fixes--permission-config-model-resolution-python--m-pytest-token-fields-path-sanitization)):** Resolved permission preflight rules, model resolution, token fields in envelope, and path sanitization.

## Evidence

| Claim | Repo file and line or report path |
|---|---|
| S1: print mode soft-denial on shell command, exit 0 | `docs/SMOKE_FINDINGS.md:20-48` |
| S1: raw result JSON | `docs/reports/smoke/s1-result.json` |
| S2: parent directory auto-creation and commit creation | `docs/SMOKE_FINDINGS.md:51-78` |
| S2: raw result JSON | `docs/reports/smoke/s2-result.json` |
| S3: denial probe immediate halt and lack of command string | `docs/SMOKE_FINDINGS.md:80-106` |
| S3: raw result JSON | `docs/reports/smoke/s3-result.json` |
| S4: `NEEDS_DECISION` marker format and resume via `--conversation` | `docs/SMOKE_FINDINGS.md:108-139` |
| S4: resume result JSON | `docs/reports/smoke/s4-resume-result.json` |
| S5: preflight execution against real config | `docs/SMOKE_FINDINGS.md:141-154` |
| S6: `agy_status` polling and `last_cli_log_mtime` tracking | `docs/SMOKE_FINDINGS.md:156-170` |
| S7: stdio protocol health (33 valid JSON-RPC frames) | `docs/SMOKE_FINDINGS.md:172-185` |
| Total tokens (408,115) and wall time (260.81s) | `docs/SMOKE_FINDINGS.md:259-270` |
| `.agy/` never committed confirmation | `docs/SMOKE_FINDINGS.md:271-273` |

## How to reproduce

The automated mock suite reproduces the protocol and watcher lifecycles:
```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/server/test_smoke.py -q
```
Real smoke testing against the live binary requires an authorized environment running the driver script with `gemini-3.8-flash-medium`.

## Sources

- `docs/SMOKE_FINDINGS.md`
- `docs/reports/smoke/s1-result.json`
- `docs/reports/smoke/s2-result.json`
- `docs/reports/smoke/s3-result.json`
- `docs/reports/smoke/s4-resume-result.json`
- `docs/reports/smoke/server-stdout.log`
- `doc/design/decisions.md`
