# Findings and Empirical Evidence Summary

Status: current
Audience: maintainers, orchestrators, contributors

Consolidated empirical findings across all exploratory probes, security evaluations,
smoke runs, capability audits, and real-client sessions conducted on the `agy` CLI
and `agy-mcp` wrapper server.

## Overview

The design of `agy-mcp` is governed by empirical investigation rather than assumptions
about the closed-source `agy` binary. Early probes established command-line behavior,
permission scoping, and process boundaries, while subsequent smoke tests and real-client
sessions verified end-to-end integration and exposed defects.

## Settled Findings

| Topic | Empirical Finding | Settled By | Experiment Page |
|---|---|---|---|
| **Command Allow-Rules** | Prefix matching `command(<target>)` strictly matches word boundaries; compound chaining (`&&`, `;`, `|`, `$()`, backticks, `\n`) is denied. | [D2](../design/decisions.md#d2-zero-custom-allow-rules), [D7](../design/decisions.md#d7-sanitized-execution-environment) | [01](01-agy-probe-1-cli-behavior.md), [02](02-agy-probe-2-security.md) |
| **Accept-Edits Baseline** | `--mode accept-edits` natively permits file creation (`write_file`), file editing (`edit_file`), and directory listing (`list_dir`) without allow-rules. | [D1](../design/decisions.md#d1-threat-model--pluggable-runner-architecture), [D2](../design/decisions.md#d2-zero-custom-allow-rules) | [01](01-agy-probe-1-cli-behavior.md) |
| **Parent Directory Creation** | Built-in file tools create all intermediate parent directories automatically without shell `mkdir`. | [D1](../design/decisions.md#d1-threat-model--pluggable-runner-architecture) | [04](04-smoke-real-agy-1.md) |
| **Failure Exit Codes** | In headless mode (`-p`), tool permission denials and timeouts exit with return code `0`. Wrapper must inspect `denied_actions` and stderr. | [D25](../design/decisions.md#d25-denied-actions-do-not-force-terminal-denied-state), [D30](../design/decisions.md#d30-resilient-result-parsing-precedence) | [01](01-agy-probe-1-cli-behavior.md), [04](04-smoke-real-agy-1.md) |
| **Denial Halting Behavior** | In headless print mode, any unapproved tool call triggers an immediate soft-denial that halts the conversation turn with empty response. | [D25](../design/decisions.md#d25-denied-actions-do-not-force-terminal-denied-state) | [04](04-smoke-real-agy-1.md) |
| **Denial Schema Precision** | `denied_actions` in JSON stdout outputs coarse categorization (`"action": "command", "display_name": "RunCommand"`) omitting exact argv. | [D25](../design/decisions.md#d25-denied-actions-do-not-force-terminal-denied-state) | [04](04-smoke-real-agy-1.md) |
| **Interactive Questions** | `ask_question` auto-resolves with `"A1: User Skipped"` in headless mode. Interactive pauses require the `NEEDS_DECISION:` last-line protocol. | [D9](../design/decisions.md#d9-standing-prompt-preamble), [D26](../design/decisions.md#d26-last-line-parsing-for-needs_decision) | [02](02-agy-probe-2-security.md), [04](04-smoke-real-agy-1.md) |
| **Conversation Resumption** | `--conversation <uuid>` preserves full trajectory context across turns without re-emitting prior turns in stdout. | [D28](../design/decisions.md#d28-resumption-guarding--context-rigor), [D81](../design/decisions.md#d81-strict-conversation-identity-early-persistence-honest-resume-and-explicit-follow-up) | [01](01-agy-probe-1-cli-behavior.md), [04](04-smoke-real-agy-1.md) |
| **Conversation ID Integrity** | Scraped conversation IDs must strictly match a full UUID to prevent capturing tokens like `"using"`. | [D81](../design/decisions.md#d81-strict-conversation-identity-early-persistence-honest-resume-and-explicit-follow-up) | [06](06-agy-capability-inventory.md) |
| **Forbidden Flags** | Wrapper bans `--sandbox`, `--effort`, `-c`, and `--dangerously-skip-permissions` to protect execution integrity. | [D1](../design/decisions.md#d1-threat-model--pluggable-runner-architecture), `runner.py:22-29` | [01](01-agy-probe-1-cli-behavior.md), [06](06-agy-capability-inventory.md) |
| **Instruction Files** | `AGENTS.md` and `GEMINI.md` are discovered hierarchically; `CLAUDE.md` is ignored. Global rules read from `~/.gemini/GEMINI.md`. | [D9](../design/decisions.md#d9-standing-prompt-preamble) | [01](01-agy-probe-1-cli-behavior.md) |
| **Filesystem Boundaries** | Direct path traversal (`../`) is blocked; writes to `/tmp` are permitted as open scratch space; directory symlinks cannot escape workspace. | [D5](../design/decisions.md#d5-isolated-worktrees-and-security-auditing), [D7](../design/decisions.md#d7-sanitized-execution-environment) | [02](02-agy-probe-2-security.md) |
| **Unconfined Inspection Tools** | Safe whitelist commands (`head`, `grep`, `git diff --no-index`) lack workspace path confinement and can read external files. | [D7](../design/decisions.md#d7-sanitized-execution-environment), [D13](../design/decisions.md#d13-threat-model-honesty--host-path-snapshot-f01) | [03](03-agy-probe-3-escapes.md) |
| **Environment Sanitization** | `agy` inherits parent environment variables. Spawning requires a strict allowlist (`HOME`, `PATH`, `LANG`, `TMPDIR`, `GIT_CONFIG_*`). | [D7](../design/decisions.md#d7-sanitized-execution-environment) | [02](02-agy-probe-2-security.md), [03](03-agy-probe-3-escapes.md) |
| **Branch Commands Safety** | `command(git branch)` enables destructive `-D` and `-f`. The wrapper alone manages branch creation and deletion. | [D3](../design/decisions.md#d3-wrapper-managed-git-commits), [D4](../design/decisions.md#d4-hardened-git-execution) | [03](03-agy-probe-3-escapes.md) |
| **.git/ Tampering Protection** | File tools can modify `.git/config` and `.git/hooks/`. Wrapper executes pre- and post-run audit checkpoints to detect tampering. | [D5](../design/decisions.md#d5-isolated-worktrees-and-security-auditing), [D14](../design/decisions.md#d14-audit-checkpoint-hard-gating--pre-launch-execution) | [03](03-agy-probe-3-escapes.md) |
| **Same-Repo Concurrency** | Running concurrent jobs in the same repository corrupts files and causes `.git/index.lock` collisions. Exactly 1 job per worktree. | [D1](../design/decisions.md#d1-threat-model--pluggable-runner-architecture), [D5](../design/decisions.md#d5-isolated-worktrees-and-security-auditing) | [02](02-agy-probe-2-security.md) |
| **Git Exclude Provisioning** | Provisioning `.agy/` into `.git/info/exclude` ensures scratchpad files and prompt text are never committed into repository branches. | [D16](../design/decisions.md#d16-pathspec-exclusion-for-agy) | [03](03-agy-probe-3-escapes.md), [04](04-smoke-real-agy-1.md), [07](07-real-jobs-1.md) |
| **Watcher Commit Pipeline** | Pre-provisioned `.agy/PLAN.md` and `.agy/COMMIT_MSG` enable clean atomic git commits while avoiding `git commit` allow-rules. | [D3](../design/decisions.md#d3-wrapper-managed-git-commits), [D15](../design/decisions.md#d15-pre-provisioning-agy-and-planmd), [D24](../design/decisions.md#d24-commit_msg-settle-window-validation-and-consume-step) | [04](04-smoke-real-agy-1.md), [07](07-real-jobs-1.md) |
| **Real DSH Client Integration** | Transport, generated `run.sh`, venv, and MCP server work end-to-end under approval policy `never` with preset `danger-full-access`. | [D1](../design/decisions.md#d1-threat-model--pluggable-runner-architecture), [D70](../design/decisions.md#d70-cleanup-branch-reachability-includes-upstream-and-main-a1)–[D73](../design/decisions.md#d73-tighten-orchestrator-guidance-integration-branch-force-model-flags) | [05](05-real-dsh-runs.md) |
| **Timing Metric Canonicalization** | Internal `agy` `duration_seconds` measures conversation age; wrapper wall-clock elapsed time is canonical for budgets and usage rows. | [D83](../design/decisions.md#d83-per-run-tokens-wrapper-wall-clock-runtime-and-a-configurable-quota-estimate) | [07](07-real-jobs-1.md) |

## Open Items and UNSURE Register

The following operational questions remain open and are settled by reviewing external
client specifications or targeted host probes (not by ad-hoc agent inspection):

### DSH Client Register

1. **Policy `"ask"` semantics:** What `dsh`'s `"ask"` policy prompts for, and whether a denied MCP tool call reaches the server at all. (**UNSURE**)
2. **Policy `"never"` auto-approval:** Whether `dsh`'s `"never"` policy auto-approves or auto-denies MCP tool calls under presets other than `danger-full-access`. (**UNSURE**; settled for `never` + `danger-full-access` in [05](05-real-dsh-runs.md))
3. **Permission presets:** The formal names and semantics of `dsh` permission presets, and which preset allows the ten `mcp__agy__*` tools. (**UNSURE**)
4. **`toolCallTimeoutMs` configuration:** Whether `toolCallTimeoutMs` is the exact key honored by the installed `dsh` version, and its default value. (**UNSURE**)
5. **Client reload requirement:** Whether a `dsh` restart is required, or a new chat is sufficient, for updated patch files to take effect. (**UNSURE**)
6. **UI behavior:** New-chat and project-folder UI behavior in `dsh`. (**UNSURE**; only absolute `repo_path` is established)
7. **Dollar cost attribution:** Precise dollar cost per job. (**UNSURE**; only quota percentage points and token counts are recorded)
8. **Host executable:** Whether the host environment includes a separate `agy-usage` executable. (**UNSURE**; the server uses print-mode `/usage`)

### Security and Runtime Open Items

1. **`git diff --output=<path>` write primitive:** Whether `git diff --output=<path>` allows writing to arbitrary host locations. (Assumed exploitable; see `doc/design/spec.md:894`)
2. **`.agents/hooks.json` reload timing:** Whether writing a lifecycle hook mid-session triggers execution during subsequent tool calls. (Assumed exploitable; see `doc/design/spec.md:896`)
3. **Quota/auth error patterns:** Reliable text signatures for auth expiry or rate limit exhaustion. (Defaults remain empty; `/usage` readings provide the reliable signal per `doc/design/spec.md:902`)
4. **Linux sandbox requirements:** Host kernel configuration required for unprivileged user namespaces (`CLONE_NEWUSER`) to enable `bwrap` containerization in protected mode.

## Sources

- `docs/AGY_FINDINGS.md`
- `docs/AGY_FINDINGS_2.md`
- `docs/AGY_FINDINGS_3.md`
- `docs/SMOKE_FINDINGS.md`
- `docs/ORCHESTRATION.md`
- `docs/reports/agy-capability-inventory.md`
- `docs/reports/real-smoke-1/REPORT.md`
- `doc/design/decisions.md`
- `doc/design/spec.md:875-903`
- `src/agy_mcp/runner.py:22-29`
- `src/agy_mcp/preamble.md`
