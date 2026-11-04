# Run jobs on more than one Google account

Status: current
Audience: users, orchestrators, maintainers

How to add a second (or further) Google account that you legitimately own, how
`agy-mcp` isolates it from the primary login and the Antigravity desktop app, and
what the wrapper will and will not do with it. The normative rules are D102–D114
in [the decisions log](../design/decisions.md); the full design is
[the multi-account design](../design/multi-account.md).

> **Implementation status (Phase 8 of the multi-account work).** The registry,
> per-account usage, the quota-aware scheduler, account selection in `agy_start`,
> pinning across resumes, conversation transfer, the summary handoff and the
> read-only `--doctor` account checks are implemented.

## What the wrapper never does

- It never logs in or out of an account.
- It never reads, copies or prints a token, keyring entry, cookie or credential
  file.
- It never creates or registers a Google account, and never automates sign-up.
- It never prints a full email address; logs, job records and MCP responses show
  a masked form (`head***tail@domain`: first three and last two local-part
  characters at most, never more than half the local part).

Only use accounts you are entitled to use. The registry is a selection list, not
a credential store.

## Recommended setup: all accounts isolated

The implicit `default` account is the one agy reads through the **shared desktop
keyring**. The Antigravity chat app writes that keyring whenever the user
switches accounts, so `default` can silently become the same login as a
configured account. On 2026-11-05 a plain `agy -p "/usage"` returned
`account-b`'s exact quota while the wrapper still saw two names, and the
scheduler double-counted one quota pool as two accounts. That is why the
recommended setup is: **every** account agy-mcp uses — including the main
account — gets its own isolated home, and the shared-keyring `default` is
disabled.

This is the same verified isolation recipe as [Recipe: add an account](#recipe-add-an-account),
applied to the primary login once:

1. Create a private home for the main account. Use a name other than `default`:

   ```bash
   MAIN_HOME="$HOME/.local/share/agy-accounts/main"
   mkdir -p "$MAIN_HOME"
   chmod 700 "$MAIN_HOME"
   ```

2. Prove the home is isolated **before** logging in:

   ```bash
   HOME="$MAIN_HOME" DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null \
     timeout 45 agy -p "/usage"
   ```

   Expected: `Authentication required. Please visit the URL to log in:` and no
   quota rows. Quota rows here mean the isolation is broken — stop.

3. Log in as the human, in the isolated home:

   ```bash
   HOME="$MAIN_HOME" DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null agy
   ```

   Complete the browser login for the main account, then exit. The wrapper never
   does this step.

4. Verify the login shows the main account's own pools and reset times:

   ```bash
   HOME="$MAIN_HOME" DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null \
     agy -p "/usage"
   ```

5. Copy the permission policy the primary login uses (D106):

   ```bash
   mkdir -p "$MAIN_HOME/.gemini/config"
   cp "$HOME/.gemini/config/config.json" "$MAIN_HOME/.gemini/config/config.json"
   chmod 600 "$MAIN_HOME/.gemini/config/config.json"
   ```

6. Declare it and disable the shared-keyring default:

   ```toml
   max_accounts = 5

   [[accounts]]
   name = "main"
   home = "~/.local/share/agy-accounts/main"
   expected_email_sha256 = "<sha256 of the main address, lowercase>"

   [default_account]
   enabled = false
   ```

   `main` is an ordinary registry name; `default` stays reserved for the
   implicit account. The expectation makes a wrong login fail closed. With the
   default disabled, an explicit `account="default"` is refused with
   `default_account_disabled`, a job pinned to `default` fails on resume with the
   same code rather than moving, and at least one enabled account must remain
   (`account_none_enabled`). Any later account you add follows the same recipe.

7. Check the result:

   ```bash
   ./installer/install.sh --doctor
   ```

   The doctor probes every enabled account and reports a `shared_keyring`
   warning if the default is still enabled without an expectation, and a
   `shared_identity` warning when two enabled names report the same login.
   Neither warning fails the doctor; both mean "fix the setup", not "the install
   is broken".

The wrapper never touches the shared keyring for a declared account, because
every spawn gets `HOME=<account home>` plus
`DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`. The default account's env stays
the historical one, which is exactly why it is the one to disable.

## Recipe: add an account

Run these steps yourself in a terminal. Replace `account-b` and the path as
needed. The path below is the one used in the 2026-11-03 human test.

### 1. Create a private home

```bash
ACCOUNT_HOME="$HOME/.local/share/agy-accounts/account-b"
mkdir -p "$ACCOUNT_HOME"
chmod 700 "$ACCOUNT_HOME"
```

The home must be mode 0700. agy will keep its file token there once the keyring is
isolated.

### 2. Prove isolation before logging in

```bash
HOME="$ACCOUNT_HOME" DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null \
  timeout 45 agy -p "/usage"
```

Expected: `Authentication required. Please visit the URL to log in:` and **no
quota rows**. If quota rows appear, the isolation is broken — stop; `agy` is
using another account's keyring token. (The command may start an OAuth wait; the
`timeout` ends it. No login has happened.)

### 3. Log in as the human

```bash
HOME="$ACCOUNT_HOME" DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null agy
```

Complete the browser login with the second account, then exit. The wrapper does
not do this step for you.

### 4. Verify the new login

```bash
HOME="$ACCOUNT_HOME" DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null \
  agy -p "/usage"
```

You should see that account's own two pools and reset times. With the desktop app
still open, run `agy -p "/usage"` with your normal environment and confirm the
primary account is unchanged.

### 5. Copy the permission policy

`agy` headless runs rely on the policy at `$HOME/.gemini/config/config.json`
(human test 2026-11-03: a fresh account auto-denied a command tool). Copy the
primary policy into the new home so the account has the same allow-rules:

```bash
mkdir -p "$ACCOUNT_HOME/.gemini/config"
cp "$HOME/.gemini/config/config.json" "$ACCOUNT_HOME/.gemini/config/config.json"
chmod 600 "$ACCOUNT_HOME/.gemini/config/config.json"
```

The wrapper verifies before every start that the account policy matches the
primary's; a missing or drifted policy refuses the job. It never writes this
file itself.

### 6. Declare the account

Add it to `~/.config/agy-mcp/config.toml`:

```toml
max_accounts = 5

[[accounts]]
name = "account-b"
home = "~/.local/share/agy-accounts/account-b"
enabled = true
max_concurrent_jobs = 1
# Optional: detect a wrong login before a job runs.
expected_email_sha256 = "<sha256 of the full address, lowercase>"
```

Use `expected_email` (the full address) **or** `expected_email_sha256`, not
both. The hash keeps the address out of the file. An account sharing one quota
pool with another (for example family members) may set the same `pool` label:

```toml
[[accounts]]
name = "family-a"
home = "~/.local/share/agy-accounts/family-a"
pool = "family"

[[accounts]]
name = "family-b"
home = "~/.local/share/agy-accounts/family-b"
pool = "family"
```

The implicit `default` account is never declared in `[[accounts]]`. Without a
`[default_account]` table it is enabled with no identity expectation, exactly as
before; the optional table can disable it or give it the same
`expected_email`/`expected_email_sha256` pair as a declared account (D116).
`max_accounts` counts it too, so the example allows at most four extra accounts.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | `[a-z0-9][a-z0-9-]{0,31}`, unique, not `default` |
| `home` | yes | Absolute path after `~` expansion; must exist and be mode 0700 at start |
| `pool` | no | Optional quota-bucket label; accounts with the same label are one bucket |
| `enabled` | no | Default `true` |
| `max_concurrent_jobs` | no | Per-account cap; default is the global `max_concurrent_jobs` |
| `expires_at` | no | Informational billing expiry; boosts scheduling urgency |
| `expected_email` / `expected_email_sha256` | no | At most one; verifies the observed login |

The implicit account's optional table has its own small schema:

| Field | Meaning |
|---|---|
| `enabled` | Default `true`; `false` removes the default from scheduling and from `agy_usage`'s no-argument choice |
| `expected_email` / `expected_email_sha256` | At most one; the identity check then applies to default exactly like any account |

## Check the setup with `--doctor`

`./installer/install.sh --doctor` (or `--doctor --client generic`) runs a
read-only account check after the existing installer checks. When the account
doctor module is present it prints one line per check, for example:

```text
account -: registry: ok: 1 extra account(s)
account default: default_home: ok: the invoking user's real home
account default: policy: ok: primary login
account default: isolation_and_login: ok: isolated /usage ok as jo***81@example.com
account default: email: ok: jo***81@example.com (unchecked)
account account-b: home: ok: exists, mode 0700
account account-b: policy: ok: matches the primary
account account-b: isolation_and_login: ok: isolated /usage ok as doc***er@example.com
account account-b: email: ok: doc***er@example.com (matched)
```

It never logs in or out and never writes an account home. A missing home, a home
that is not mode 0700, a missing/drifted policy, an expected-email mismatch, or
a failed isolated `/usage` (not logged in, timeout, command failure) is reported
as a problem and makes the doctor exit 1. Emails are masked; the full address is
never printed. With no `[[accounts]]` and no `[default_account]` table the check
is a single healthy "single-account install" line, so an existing install is
unaffected.

Two advisory lines do **not** fail the doctor:

- `shared_keyring` appears when the default is enabled, has no identity
  expectation, and another account exists: "shared keyring identity can change
  when the desktop app switches accounts; disable default or set an expectation".
- `shared_identity` appears when two enabled accounts report the same login. The
  scheduler treats a verified duplicate as one quota pool; the line names the
  masked login so you can decide which name to disable.

The doctor's probe, the account scheduler's `/usage` probe and the readings the
supervisor takes at launch and at finalization all run with the same isolated
child environment as the job itself (`HOME` plus
`DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`). Before 2026-11-03 the
scheduler probe did not, so a second-account start could fail with
`account_identity_mismatch` even though the job's own environment was correct.
The fix is recorded in
[the usage-env audit](../audits/2026-11-03-multi-account/05-usage-env-bug.md).
The 2026-11-05 shared-keyring collision is recorded in
[finding 16](../findings-and-experiments/16-shared-keyring-default-collision.md).

The same module can be run directly:

```bash
~/.config/ai-stack/agymcp/venv/bin/python -m agy_mcp.accounts.doctor \
  --config ~/.config/agy-mcp/config.toml --agy-binary ~/.local/bin/agy
```

## How to read the decision trace

Every job started on a registry account writes an `account_selected` line to
`<state>/jobs/<job-id>/audit.jsonl`. When a registry is configured that line
also carries a `decision_trace` object, so you can see **why** an account won
without re-running the scheduler (D115). With no `[[accounts]]` registry the
event is not written, exactly as before.

```bash
python -c 'import json,sys; [print(json.dumps(json.loads(l)["detail"], indent=2))
  for l in open(sys.argv[1]) if json.loads(l)["event"] == "account_selected"]' \
  "$STATE/jobs/<job-id>/audit.jsonl"
```

The trace has one row per registered account considered:

- `eligible: false` rows carry a `rejection` code such as `disabled`,
  `quota_exhausted`, `account_concurrency` or `usage_unavailable`.
- `eligible: true` rows carry the ranking inputs: the shared `pool`, the
  effective `weekly_remaining_pct` / `five_hour_remaining_pct` (after
  reservations), `hours_to_binding_reset`, `binding_window`, `urgency_score`
  (urgency plus the optional `expires_at` boost), `expiry_boost` and `rank`
  (`1` is the winner).
- `winner` is the chosen registry name and `rule` is the first criterion that
  separated it from the runner-up: `urgency`, `expires_at`, `load`, `lru`,
  `only_candidate`, `explicit_account` or `force`.

A worked read of the 2026-11-04 human test:

```json
{"winner": "default", "rule": "urgency", "accounts": [
  {"account": "default", "eligible": true, "weekly_remaining_pct": 58.0,
   "hours_to_binding_reset": 0.6667, "urgency_score": 87.0, "rank": 1},
  {"account": "account-b", "eligible": true, "weekly_remaining_pct": 88.0,
   "hours_to_binding_reset": 96.0, "urgency_score": 0.9167, "rank": 2}
]}
```

The default account won because 58% of its weekly quota was resetting in 40
minutes (87 points/hour) while `account-b`'s 88% was four days away (0.92
points/hour). A `rule` of `expires_at` means the optional billing-expiry boost,
not raw quota, flipped the order. A `rule` of `lru` is the deterministic
last-used/name tie-break.

A window reported at 100% remaining is **full**: its `reset_time` is a rolling
placeholder, not a real expiry, so its urgency component is 0 (the score can
still carry a separate `expires_at` boost) and it is never the binding window
for a background burn (D115). If a full window shows a `binding_window` other
than `weekly` with no other real constraint, that would be a bug.

The trace is capped at `MAX_TRACE_ACCOUNTS` (32) rows, the maximum
`max_accounts`; a `truncated: true` key would appear only if a future change
exceeded it. Emails appear only in the masked form already used by
`usage.jsonl`; the full address is never written.

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `/usage` shows the primary's quota with the account HOME | `DBUS_SESSION_BUS_ADDRESS` missing | Always set `unix:path=/dev/null` for the account |
| `Authentication required` during a job | The account was never logged in, or the token expired | Log in again with the isolated command |
| `account_home_permissions` | Home is group/other accessible | `chmod 700 "$ACCOUNT_HOME"` |
| `account_permission_policy_missing` / `_drift` | Policy not copied or differs from the primary | Re-copy the primary policy (step 5) |
| `account_identity_mismatch` | Wrong login for the configured expectation | Check the account, or fix the expectation |
| `default_account_disabled` | `[default_account] enabled = false`, but a caller named `default` or a job is pinned to it | Pass an enabled account; the job is not moved automatically |
| `account_none_enabled` | The default and every declared account are disabled | Enable the default or one `[[accounts]]` entry |
| Doctor says `shared_keyring` or `shared_identity` | The default still uses the shared keyring, or two names are one login | Use [Recommended setup: all accounts isolated](#recommended-setup-all-accounts-isolated) |
| Command tool auto-denied in a job | Account policy is weaker than the primary | Copy the primary policy; do not weaken it |

## Sources

- `doc/design/multi-account.md` — the registry, isolation and policy rules.
- `doc/design/decisions.md` — D102–D116.
- `doc/audits/2026-11-03-multi-account/03-experiments.md` — the isolation and policy evidence.
- `doc/findings-and-experiments/15-multi-account-recon.md` — the feasibility summary.
- `doc/findings-and-experiments/16-shared-keyring-default-collision.md` — the 2026-11-05 observation.
- `src/agy_mcp/accounts/registry.py` — validation rules and the implicit default.
- `src/agy_mcp/accounts/env.py` — the dead D-Bus override.
- `src/agy_mcp/accounts/identity.py` — masking and verification.
- `src/agy_mcp/config.py` — the `accounts` and `max_accounts` keys.
