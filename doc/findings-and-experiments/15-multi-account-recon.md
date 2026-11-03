# 15 — How can agy-mcp run jobs across several owned Google accounts safely?

Status: current
Audience: maintainers, contributors

Feasibility investigation for multi-account support: what separates two `agy`
logins on one host, what each account's quota looks like, what a conversation
needs to move between accounts, and which safety policy must travel with an
account. The conclusions feed the multi-account design (`doc/design/multi-account.md`,
Phase 2) and the implementation record under
`doc/audits/2026-11-03-multi-account/`.

## Question

Can `agy-mcp` select among several Google accounts the user legitimately owns,
run each with safe isolation, schedule by remaining quota, and resume a job on a
different account when its own is exhausted — without ever touching credentials
or changing single-account behavior?

## Method

- Read the production modules that resolve `HOME`, state, quota and permissions,
  and the prior recon report (`~/agy-review/recon/multi-account.md`).
- Nine bounded real `agy` calls on 2026-11-03 (version, `/usage` on the primary
  and account-b, a fresh-HOME isolation check, one tool-using conversation on the
  primary, one recall turn, a missing-id control on account-b, and one transfer
  turn on account-b). Full log: `evidence/real-agy-calls.md` in the audit folder.
- Account-b ran with
  `HOME=~/.local/share/agy-accounts/account-b DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`;
  no login or logout was performed.
- Conversation transfer copied exactly one file,
  `conversations/<id>.db`, from the primary store to account-b's store.

## Result

### Isolation

Overriding `HOME` alone is unsafe: the CLI silently authenticates against the
desktop app's D-Bus SecretService keyring and uses the primary account. With
`HOME` **and** a dead `DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`, a fresh
HOME says `Authentication required` (no other account's quota), and a
logged-in second HOME authenticates to a different account with independent
quota. A real `/usage` on account-b succeeded and showed a different email local
part and different percentages from the primary.

### Found in live use (2026-11-03)

The human test found that the wrapper's own scheduler probe did not apply this
rule. `get_usage(home=<account home>)` swapped only `HOME`, so the probe for a
non-default account still reached the ambient session bus and authenticated as
the primary login; `agy_start(account="account-b")` then failed closed with
`account_identity_mismatch`. The doctor passed because its probe passed the dead
D-Bus as an override, and the fake agy ignored D-Bus so no test caught it. Every
agy spawn for a non-default account now builds its env with
`accounts/env.py:build_account_env`; the audit and the guard test are in
[05-usage-env-bug.md](../audits/2026-11-03-multi-account/05-usage-env-bug.md).

### Quota shape

`agy -p "/usage"` returns one JSON object whose `response` is four tab-separated
rows (`pool`, `window label`, `pct%`, ISO reset) and whose
`command.data.groups[]` carries `remaining_fraction` and `reset_time` per
bucket. The two pools are `Gemini Models` and `Claude and GPT models`; the two
windows are weekly and five-hour. Both accounts expose the same shape with
different values. The CLI exposes no "shared pool" identifier.

### Conversation portability

A multi-turn conversation that used a file-creation tool on the primary
(`num_turns=2`) continued on account-b after copying only its `.db`
(`num_turns=3`) and correctly recalled the earlier filename and marker. No
`brain/` copy was needed for text recall. A missing id is **not** an error: agy
warns `conversation "<id>" not found`, starts a new conversation with a new id,
and exits 0 — so the wrapper must verify the file, not the exit code.

### Permission policy

Human testing on 2026-11-03 established that a fresh account HOME auto-denies a
command tool because headless mode cannot prompt:
`a tool required the 'command' permission ... Add an allow-rule under permissions.allow in settings.json`.
The wrapper's safety therefore depends on each account HOME carrying the required
policy. This session did not run a command tool on account-b, so account-b's
specific policy is UNVERIFIED; the failure mode is human-verified on a fresh
login.

### Per-account model set

`gemini-3.8-flash-medium` was rejected on account-b (`invalid model selection`,
exit 1, no tokens) while `gemini-3.8-flash` worked: model identifiers are
per-account state.

## Conclusion

Multi-account support is feasible with four non-negotiable rules:

1. Non-default accounts always run with their own `HOME` **and**
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`; the isolation check precedes
   any login.
2. Quota is parsed per account and per pool; only an operator-declared `pool`
   label makes two accounts one bucket.
3. A conversation moves only by copying its `db` while no process uses it, after
   verifying it exists; a missing or corrupt db falls back to a summary handoff.
4. Each account home must carry the same required permission policy, verified
   before every start; weaker policy is refused.

Experiment cost is disclosed: four model-consuming prompts (75,377 tokens), one
more than the three-prompt cap, because the missing-id control unexpectedly
started and answered a fresh conversation.

## Evidence

| Claim | Evidence |
|---|---|
| Nine real calls, purposes and exit codes | `doc/audits/2026-11-03-multi-account/evidence/real-agy-calls.md` |
| Per-account `/usage` rows and pools | `doc/audits/2026-11-03-multi-account/evidence/real-usage-rows.txt` |
| Fresh-HOME isolation output | `doc/audits/2026-11-03-multi-account/evidence/isolation-fresh-home.txt` |
| Transfer, missing id, model rejection, token counts | `doc/audits/2026-11-03-multi-account/evidence/conversation-transfer.txt` |
| Fake-agy gap analysis and experiment budget | `doc/audits/2026-11-03-multi-account/03-experiments.md` |
| Code assumptions and required changes | `doc/audits/2026-11-03-multi-account/02-code-map.md` |

## How to reproduce

The real-run part is not a committed repro (it needs two logged-in accounts).
The isolation check is:

```bash
mkdir -p /tmp/agy-fresh-home && chmod 700 /tmp/agy-fresh-home
HOME=/tmp/agy-fresh-home DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null \
  timeout 45 agy -p "/usage"
```

Expect `Authentication required` and no quota rows; kill it with the timeout.
The hermetic part (fake-agy per-account usage, missing-db fallback) is reproduced
by the Phase 4 and Phase 7 tests.

## Sources

- `doc/audits/2026-11-03-multi-account/01-investigation.md` — the Phase 1 synthesis.
- `doc/audits/2026-11-03-multi-account/02-code-map.md` — the line-by-line code map.
- `doc/audits/2026-11-03-multi-account/03-experiments.md` — experiment detail and limits.
- `doc/audits/2026-11-03-multi-account/evidence/real-agy-calls.md` — the call log.
- `doc/audits/2026-11-03-multi-account/evidence/real-usage-rows.txt` — usage rows.
- `doc/audits/2026-11-03-multi-account/evidence/isolation-fresh-home.txt` — isolation output.
- `doc/audits/2026-11-03-multi-account/evidence/conversation-transfer.txt` — transfer output.
- `src/agy_mcp/util.py` — child env builder.
- `src/agy_mcp/usage.py` — `/usage` parser.
- `src/agy_mcp/config.py` — permission config path.
- `tests/fake_agy/agy` — fake capabilities and gaps.
