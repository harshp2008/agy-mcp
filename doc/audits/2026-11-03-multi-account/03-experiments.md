# Multi-account experiments: isolation, portability, permissions and pools

Status: current
Audience: maintainers, contributors

The experiments behind the design, with the method, the exact observed result and
the limit of each. Ground truth recorded by human testing on 2026-11-03 is cited
as such and not re-run; this page adds the machine-verified runs that were still
missing (multi-turn transfer, missing id, per-account model set, fresh-HOME
authentication) and the fake-agy gap analysis.

## 1. Keyring leak and D-Bus isolation

**Question.** Does overriding `HOME` alone isolate an account from the desktop
app's D-Bus SecretService? Does adding a dead D-Bus address isolate it?

**Method (human test 2026-11-03; re-confirmed here).** Ran `/usage` with
`HOME=/tmp/...` only, then with
`HOME=/tmp/... DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`.

**Result.**

- HOME only (human test): exit 0 and the primary account's quota; log shows
  `ChainedAuth: authenticated via keyring (effective: keyring)`.
- HOME + dead D-Bus, fresh HOME (this session): stderr
  `Authentication required. Please visit the URL to log in:`, then the process
  waited for interactive OAuth until killed at 45 s (exit 124). It never showed
  another account's quota. Log:
  `Failed to load stored token from keyring, falling back to file: dial unix /dev/null: connect: connection refused`.
- HOME + dead D-Bus, a **logged-in** account-b (this session): exit 0, and the
  auth email was a different local part from the primary's, with independent
  quota values.

**Conclusion.** Isolation is exactly `HOME` + `DBUS_SESSION_BUS_ADDRESS`; either
alone is unsafe. Evidence: `evidence/isolation-fresh-home.txt`,
`evidence/real-usage-rows.txt`, `evidence/real-agy-calls.md` calls 2–4.

**Limit.** The log's later `keyringAuth: loaded token ... effective: keyring`
line is mislabelled for the file-backed path; the decisive evidence is the
different email and the file fallback warning, not that label.

## 2. Independent quota and pool structure

**Question.** Does each account expose two pools and two windows, and are the
values independent?

**Method.** `agy ... -p "/usage" --output-format json` on both accounts; parsed
the `response` rows and `command.data.groups`.

**Result.**

| Account | Pool | Window | Remaining | Reset (UTC) |
|---|---|---|---:|---|
| primary | Gemini Models | weekly | 58.7% | 2026-11-03T21:12:09Z |
| primary | Gemini Models | 5 h | 95.8% | 2026-11-03T18:07:57Z |
| primary | Claude and GPT | weekly | 49.7% | 2026-11-08T17:06:56Z |
| primary | Claude and GPT | 5 h | 100% | 2026-11-03T19:15:04Z |
| account-b | Gemini Models | weekly | 88.2% | 2026-11-08T09:54:57Z |
| account-b | Gemini Models | 5 h | 100% | 2026-11-03T18:56:46Z |
| account-b | Claude and GPT | weekly | 49.1% | 2026-11-08T17:55:18Z |
| account-b | Claude and GPT | 5 h | 100% | 2026-11-03T19:15:23Z |

Both accounts expose the same shape and different values/resets. A third
account sharing a Google family quota pool is a human-test hypothesis
(`pool` label in the registry); the CLI output has no pool-sharing identifier,
so sharing can only be declared by the operator.

**Conclusion.** Parse both pools and both windows per account; never merge two
accounts into one bucket unless the operator labels them the same `pool`.

## 3. Conversation portability

**Question.** What does account-b need to recall a multi-turn conversation that
used a tool on the primary?

**Method.** Turn 1 asked the primary to create `alpha.txt` with a marker via its
file tool; turn 2 asked it to recall the name and marker (no tools); then only
`conversations/<id>.db` was copied into account-b's conversations directory and
turn 3 asked the same recall question there.

**Result.**

| Step | Account | num_turns | Outcome |
|---|---|---:|---|
| Turn 1 (tool create) | primary | 1 | `SUCCESS`, marker written |
| Turn 2 (recall) | primary | 2 | filename + marker correct |
| Turn 3 (recall after db copy) | account-b | 3 | filename + marker correct, same id |

No `brain/` copy, no `conversation_summaries.db`, no `-wal`/`-shm` (none existed
at copy time). Evidence: `evidence/conversation-transfer.txt`.

**Conclusion.** A single db file is sufficient for multi-turn, tool-result text
recall in this configuration. The source copy can stay in place; the same id may
exist in two stores. The design copies the db only, verifies it exists before
use, locks the id, and never runs it on two accounts at once.

**Limit.** `alpha.txt` still existed in the shared `/tmp` directory during turn
3, so a server-side tool read is not arithmetically excluded; the prompt forbade
tools and the transfer log contains no tool invocation. Brain-artifact and
image/attachment portability remains UNVERIFIED.

## 4. Missing conversation id is silent

**Question.** What happens when `--conversation <id>` targets an id with no file?

**Method.** Ran account-b with the primary's id before copying the db.

**Result.** Exit 0, stderr
`warning: conversation "<id>" not found`, and stdout returned a **new**
conversation id (`3e7411c1-...`, `num_turns=1`). The prompt was answered as a
fresh conversation and consumed 6,853 tokens.

**Conclusion.** The wrapper must check the file's existence before a transfer and
must not treat exit 0 as proof of continuity. This is a correctness trap, not a
crash. Evidence: `evidence/conversation-transfer.txt`.

## 5. Permission auto-deny on a fresh account

**Question.** Does a fresh account HOME run with the wrapper's expected
permission policy?

**Method.** Human test 2026-11-03 on the newly logged-in account: headless run
attempted a command tool.

**Result (human test).** agy auto-denied it:
`a tool required the 'command' permission that headless mode cannot prompt for ... Add an allow-rule under permissions.allow in settings.json`.
The wrapper's safety depends on the policy under the account `HOME`
(`src/agy_mcp/config.py:24`).

**Conclusion.** Every account home must carry the same required allow-rules as
the primary, and the wrapper must verify the policy before a start on that
account, not only scan for dangerous rules. This session did not attempt a
command tool on account-b (out of the real-call scope), so the parity
requirement is UNVERIFIED for account-b specifically; the failure mode is
human-verified on a fresh login.

## 6. Per-account model availability

**Question.** Is the wrapper's default model valid on every account?

**Method.** First transfer attempt used `--model gemini-3.8-flash-medium` on
account-b.

**Result.** Exit 1, `error: invalid model selection ... model
gemini-3.8-flash-medium is not recognized as a known model or custom model in
settings`, no tokens. `gemini-3.8-flash` worked.

**Conclusion.** Model identifiers are account state under `HOME`. The scheduler
must verify the requested model against the account before launch (or surface a
clear `account_model_unavailable` error); it must never silently substitute.

## 7. Fake-agy gap analysis

The fake agy is hermetic and already covers `/usage` shapes, fixed quota
variants, conversation ids, log anchors and HOME-scoped scenario markers. It
does **not** cover:

| Gap | Needed by | Required extension |
|---|---|---|
| Arbitrary per-account `/usage` payload | Phase 4 | Read a JSON file under `$HOME` and emit it; per-account email line |
| Conversation db file per `HOME` | Phase 7 | Create `<HOME>/.gemini/antigravity-cli/conversations/<id>.db` on a fresh run; on `--conversation`, warn + new id when absent, resume when present |
| Per-account model rejection | Phase 6 | Optional `$HOME/.fake_agy_models` allowlist; reject unknown `--model` with exit 1 |
| Corrupt/locked transfer db | Phase 7 | Marker-driven corrupt file / `flock` hold |
| Same id in two stores | Phase 7 | Run the same id under two HOMEs and record which store was read |

## 8. Experiment budget

The task allowed at most three throwaway model prompts. This session used
**four** model-consuming calls (turn 1, turn 2, the missing-id control, turn 3),
totalling 75,377 tokens by agy's counts. The missing-id control was expected to
fail before reaching the model; it did not. The overage is disclosed here and in
`evidence/real-agy-calls.md`. No other real prompt was sent.

## Sources

- `doc/audits/2026-11-03-multi-account/evidence/isolation-fresh-home.txt` — the fresh-HOME run.
- `doc/audits/2026-11-03-multi-account/evidence/real-usage-rows.txt` — both accounts' rows.
- `doc/audits/2026-11-03-multi-account/evidence/conversation-transfer.txt` — the transfer, missing-id and model-rejection runs.
- `doc/audits/2026-11-03-multi-account/evidence/real-agy-calls.md` — the call log.
- `tests/fake_agy/agy` — the fake's current scenarios.
- `src/agy_mcp/config.py` — the permission config path.
- `src/agy_mcp/usage.py` — `/usage` parsing.
