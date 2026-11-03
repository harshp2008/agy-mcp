# Real agy calls made during the multi-account investigation

Every invocation of the real `agy` binary (`~/.local/bin/agy`) in this session is
listed below with its purpose and exit code. Account emails in captured logs
were masked (first three + last two local-part characters); no token, keyring or
cookie material was read or printed. `agy` is not on this shell's `PATH`; its
absolute path is used.

`--log-file` always pointed into `/tmp/agy-multi-test`, never at `~/.gemini`.

| # | When (IST) | Command (abridged) | Purpose | Exit |
|---|---|---|---|---|
| 1 | 19:44 | `agy --version` | Confirm the CLI version (1.4.12) | 0 |
| 2 | 19:44 | `agy --log-file /tmp/... --output-format json -p "/usage"` | Primary `/usage` format and quota | 0 |
| 3 | 19:45 | `HOME=.../account-b DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null agy ... -p "/usage"` | Second-account isolation and independent quota | 0 |
| 4 | 19:45 | `HOME=/tmp/agy-multi-test/fresh-home DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null agy ... -p "/usage"` | Isolation check on a fresh HOME (must require auth) | 124 (killed by `timeout 45` while waiting for OAuth) |
| 5 | 19:46 | `agy --mode accept-edits ... -p "Use your file-creation tool ..."` | Turn 1: create a tool-using conversation | 0 |
| 6 | 19:47 | `agy ... --conversation b9c7c56e-... -p "Without using any tools, answer ..."` | Turn 2: multi-turn recall on the primary | 0 |
| 7 | 19:47 | `HOME=.../account-b DBUS=... agy ... --conversation b9c7c56e-... -p "Reply with OK."` | Missing-id control on account-b (before copying the db) | 0 |
| 8 | 19:48 | `HOME=.../account-b DBUS=... agy ... --model gemini-3.8-flash-medium --conversation b9c7c56e-... -p "..."` | First transfer attempt; model rejected on account-b | 1 (no tokens) |
| 9 | 19:48 | `HOME=.../account-b DBUS=... agy ... --model gemini-3.8-flash --conversation b9c7c56e-... -p "..."` | Turn 3: recall on account-b from the copied db | 0 |

## Non-agy calls that touched account paths

| Command | Why | Exit |
|---|---|---|
| `ls -1d ~/.local/share/agy-accounts/*/` | Directory **names** only, to find `account-b` | 0 |
| `ls -1 ~/.gemini/antigravity-cli/conversations/ \| grep b9c7...` | Read-only file-name listing for the conversation db | 0 |
| `ls -l <db>` | Size/mode of the one conversation file (not read) | 0 |
| `cp -p <primary db> <account-b db>` | The transfer experiment's one-file copy | 0 |
| `rm -f <account-b copy>` | Clean up the copy made by this experiment (exact path) | 0 |

## Model-consuming calls

Four calls reached a model (turn 1, turn 2, the missing-id control and turn 3),
totalling 75,377 tokens by agy's own counts. The missing-id control was expected
to fail without a model call. The task allowed at most three throwaway prompts;
the overage is disclosed in `03-experiments.md` and `15-multi-account-recon.md`.

No other real `agy` invocation (login, logout, model listing, plugin, mcp) was
made. All remaining tests in this work use `tests/fake_agy/agy`.

## Sources

- `doc/audits/2026-11-03-multi-account/03-experiments.md` — the experiments these calls served.
- `doc/audits/2026-11-03-multi-account/evidence/conversation-transfer.txt` — the transfer evidence.
- `doc/audits/2026-11-03-multi-account/evidence/isolation-fresh-home.txt` — the fresh-HOME evidence.

