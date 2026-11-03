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
  a masked form (first three and last two local-part characters at most, never
  more than half the local part).

Only use accounts you are entitled to use. The registry is a selection list, not
a credential store.

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

The implicit `default` account (today's login) always exists and is never
declared. `max_accounts` counts it too, so the example allows at most four extra
accounts.

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

## Check the setup with `--doctor`

`./installer/install.sh --doctor` (or `--doctor --client generic`) runs a
read-only account check after the existing installer checks. When the account
doctor module is present it prints one line per check, for example:

```text
account -: registry: ok: 1 extra account(s)
account account-b: home: ok: exists, mode 0700
account account-b: policy: ok: matches the primary
account account-b: isolation_and_login: ok: isolated /usage ok as docer***@example.com
account account-b: email: ok: docer***@example.com (matched)
```

It never logs in or out and never writes an account home. A missing home, a home
that is not mode 0700, a missing/drifted policy, an expected-email mismatch, or
a failed isolated `/usage` (not logged in, timeout, command failure) is reported
as a problem and makes the doctor exit 1. Emails are masked; the full address is
never printed. With no `[[accounts]]` the check is a single healthy
"single-account install" line, so an existing install is unaffected.

The doctor's probe, the account scheduler's `/usage` probe and the readings the
supervisor takes at launch and at finalization all run with the same isolated
child environment as the job itself (`HOME` plus
`DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`). Before 2026-11-03 the
scheduler probe did not, so a second-account start could fail with
`account_identity_mismatch` even though the job's own environment was correct.
The fix is recorded in
[the usage-env audit](../audits/2026-11-03-multi-account/05-usage-env-bug.md).

The same module can be run directly:

```bash
~/.config/ai-stack/agymcp/venv/bin/python -m agy_mcp.accounts.doctor \
  --config ~/.config/agy-mcp/config.toml --agy-binary ~/.local/bin/agy
```

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `/usage` shows the primary's quota with the account HOME | `DBUS_SESSION_BUS_ADDRESS` missing | Always set `unix:path=/dev/null` for the account |
| `Authentication required` during a job | The account was never logged in, or the token expired | Log in again with the isolated command |
| `account_home_permissions` | Home is group/other accessible | `chmod 700 "$ACCOUNT_HOME"` |
| `account_permission_policy_missing` / `_drift` | Policy not copied or differs from the primary | Re-copy the primary policy (step 5) |
| `account_identity_mismatch` | Wrong login for the configured expectation | Check the account, or fix the expectation |
| Command tool auto-denied in a job | Account policy is weaker than the primary | Copy the primary policy; do not weaken it |

## Sources

- `doc/design/multi-account.md` — the registry, isolation and policy rules.
- `doc/design/decisions.md` — D102–D114.
- `doc/audits/2026-11-03-multi-account/03-experiments.md` — the isolation and policy evidence.
- `doc/findings-and-experiments/15-multi-account-recon.md` — the feasibility summary.
- `src/agy_mcp/accounts/registry.py` — validation rules and the implicit default.
- `src/agy_mcp/accounts/env.py` — the dead D-Bus override.
- `src/agy_mcp/accounts/identity.py` — masking and verification.
- `src/agy_mcp/config.py` — the `accounts` and `max_accounts` keys.
