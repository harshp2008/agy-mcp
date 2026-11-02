# 06 — AGY CLI Capability Inventory and Argv Contract

Status: current
Audience: maintainers, contributors

Comprehensive audit of the `agy` CLI binary (version 1.4.10) command-line interface,
enumerating top-level flags, subcommands, the exact argv constructed by `agy-mcp`,
forbidden flags, environment variables passed, and tracking the conversation-ID extraction
defect superseded by D81.

## Question

What is the complete command-line interface surface of `agy` (v1.4.10), which flags
and subcommands does it expose, what exact argv does `agy-mcp` construct, which flags
are forbidden, and what subtle bugs were discovered during capability inspection?

## Method

An inventory of `~/.local/bin/agy` (ELF 64-bit stripped binary, version 1.4.10,
227,840,232 bytes) was performed on 2026-10-30 on Linux x86_64. The evaluation inspected
help and version flags exclusively without starting any model turns, invoking networks,
or altering user configuration:
- Verified top-level help flags and subcommands (`agy --help`, `agy --version`, `agy help`).
- Enumerated subcommand help surfaces (`agent`, `agents`, `changelog`, `install`, `mcp`, `mic-serve`, `models`, `plugin`, `plugins`, `remote-control`, `update`).
- Compared observed CLI options against wrapper code in `src/agy_mcp/runner.py` and `src/agy_mcp/util.py`.
- Replayed supervisor conversation-ID regex logic against recorded real session logs (`docs/reports/smoke/s1-cli.log`).

## Result

### CLI Surface Summary

1. **Top-level flags (25 total):** `agy --help` defines 25 flags including `--add-dir`, `--agent`, `-c`/`--continue`, `--conversation`, `--cost-limit`, `--dangerously-skip-permissions`, `--effort`, `-h`/`--help`, `--input-format`, `--log-file`, `--mode`, `--model`, `--output-format`, `-p`/`--print`/`--prompt`, `--print-timeout`, `--prompt-interactive`, `--resume`, `--sandbox`, `--token-limit`, and `--version`.
2. **Subcommands (12 total):** `agent`, `agents`, `changelog`, `help`, `install`, `mcp`, `mic-serve`, `models`, `plugin`, `plugins`, `remote-control`, `update`.
   - No `usage`, `auth`, `login`, `logout`, `conversation list`, or `hook` subcommands exist.
   - Quota usage is not a CLI subcommand; `agy-mcp` obtains quota metrics via a non-interactive `/usage` prompt.
3. **Exact argv built by `agy-mcp` (`src/agy_mcp/runner.py:144-166`):**
   ```bash
   agy --mode accept-edits --output-format json --print-timeout <N>s --log-file <abs jobdir>/cli.log --model <model> [--conversation <id>] -p <prompt>
   ```
4. **Forbidden flags (`src/agy_mcp/runner.py:22-29`):**
   - Banned flags: `--sandbox`, `--effort`, `-c`, `--dangerously-skip-permissions`.
   - Valid modes: strictly `{"accept-edits"}`.
   - Valid output formats: strictly `{"json"}`.
5. **Sanitized environment passed (`src/agy_mcp/util.py:80-89`):**
   - Strictly passes `HOME`, `PATH`, `LANG`, `TMPDIR`, and `GIT_CONFIG_*`. No API keys or tokens are forwarded.
6. **Command execution anomaly:**
   - When probing subcommands, `agy plugin uninstall --help` did not recognize `--help` as a help request, executed the command, and printed `Uninstalled plugin "--help"` with exit code 0.
7. **Conversation ID extraction vulnerability:**
   - The regex previously used by `supervisor.py` to scrape conversation IDs from `cli.log` matched the line `Conversation using project ID: default-cli-project`, extracting the literal word `"using"` as the conversation ID.
   - A subsequent `agy_resume` call passed `--conversation using`, failing to resume the original session.
   - *Superseded by [D81](../design/decisions.md#d81-strict-conversation-identity-early-persistence-honest-resume-and-explicit-follow-up):* The loose regex was removed and replaced with strict UUID pattern matching and early persistence in `job.json`.

## Conclusion

The inventory verified that `agy-mcp`'s argv construction and flag constraints precisely
match the actual CLI capabilities of `agy` v1.4.10. The discovery of the `"using"` token
extraction bug led directly to architectural decision [D81](../design/decisions.md#d81-strict-conversation-identity-early-persistence-honest-resume-and-explicit-follow-up),
enforcing strict UUID validation across all conversation persistence and resumption paths.

## Evidence

| Claim | Repo file and line or report path |
|---|---|
| Binary inspection: `agy` v1.4.10 ELF 64-bit (227,840,232 bytes) | `docs/reports/agy-capability-inventory.md:5` |
| 25 flags and 12 subcommands in `agy --help` | `docs/reports/agy-capability-inventory.md:21` |
| Top-level help verbatim reproduction | `docs/reports/agy-capability-inventory.md:95-132` |
| Missing subcommands (no usage, auth, login) | `docs/reports/agy-capability-inventory.md:23` |
| Exact argv built by runner | `src/agy_mcp/runner.py:144-166` |
| Forbidden flags list (`--sandbox`, `--effort`, `-c`, `--dangerously-skip-permissions`) | `src/agy_mcp/runner.py:22-29` |
| Minimal environment allowlist (`HOME`, `PATH`, `LANG`, `TMPDIR`, `GIT_CONFIG_*`) | `src/agy_mcp/util.py:80-89` |
| `plugin uninstall --help` incident (exit 0) | `docs/reports/agy-capability-inventory.md:58-66` |
| `using` conversation ID regex extraction failure | `docs/reports/agy-capability-inventory.md:29`, `docs/reports/agy-capability-inventory.md:465-481` |
| Strict conversation identity rule superseding regex flaw | `doc/design/decisions.md#d81-strict-conversation-identity-early-persistence-honest-resume-and-explicit-follow-up` |
| Whether a plugin named `--help` existed prior to uninstall | not recorded (UNKNOWN per `docs/reports/agy-capability-inventory.md:66`) |

## How to reproduce

Verify version and help output on the host:
```bash
agy --version
agy --help
```
Inspect wrapper argv construction in `src/agy_mcp/runner.py`.

## Sources

- `docs/reports/agy-capability-inventory.md`
- `src/agy_mcp/runner.py:22-29`
- `src/agy_mcp/runner.py:144-166`
- `src/agy_mcp/util.py:80-89`
- `doc/design/decisions.md`
