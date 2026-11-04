# Run jobs on more than one Google account

Status: current
Audience: users, orchestrators, maintainers

How to add a second (or further) Google account that you legitimately own, how
`agy-mcp` isolates it from the primary login and the Antigravity desktop app, and
what the wrapper will and will not do with it. The recommended path is the
human-run `agy-mcp account` command (D117); the manual recipe it automates is
kept below as **what the command does**. The normative rules are D102–D117 in
[the decisions log](../design/decisions.md); the full design is
[the multi-account design](../design/multi-account.md).

> **Implementation status (Phase 9 of the multi-account work).** The registry,
> per-account usage, the quota-aware scheduler, account selection in `agy_start`,
> pinning across resumes, conversation transfer, the summary handoff, the
> read-only `--doctor` account checks and the human `agy-mcp account` CLI are
> implemented.

## What the wrapper never does

- It never logs in or out of an account and never automates sign-up. `account
  add` hands **your terminal** to an isolated `agy`; you complete the browser
  login.
- It never reads, copies or prints a token, keyring entry, cookie or credential
  file.
- It never creates or registers a Google account.
- It never prints a full email address; logs, job records, MCP responses and the
  account CLI show a masked form (`head***tail@domain`: first three and last two
  local-part characters at most, never more than half the local part). `account
  add` asks for the expected email with hidden input and stores only its
  SHA-256.

Only use accounts you are entitled to use. The registry is a selection list, not
a credential store.

## Recommended setup: every account isolated

The implicit `default` account is the one agy reads through the **shared desktop
keyring**. The Antigravity chat app writes that keyring whenever the user
switches accounts, so `default` can silently become the same login as a
configured account. On 2026-11-05 a plain `agy -p "/usage"` returned
`account-b`'s exact quota while the wrapper still saw two names, and the
scheduler double-counted one quota pool as two accounts. That is why the
recommended setup is: **every** account agy-mcp uses — including the main
account — gets its own isolated home, and the shared-keyring `default` is
disabled.

### 1. Add the main account under a non-`default` name

Run this yourself in a terminal. `add` needs a terminal because it launches the
login; without `--yes` it only prints the plan.

```bash
agy-mcp account add main --pool main --max-concurrent 2 --yes
```

`python -m agy_mcp account add main --yes` is the same command when the console
script is not on `PATH`. The command:

1. creates `~/.local/share/agy-accounts/main` with mode 0700;
2. proves the fresh home is isolated by running the isolated `/usage`
   (`HOME=<home>` plus `DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`) and
   requiring `Authentication required` with **no** quota rows. If quota rows
   appear, it stops with `account_cli_isolation_leak` and does not log in;
3. launches interactive `agy` in that isolated environment. Complete the browser
   login for the main account, then exit;
4. verifies the login, asks for the expected email with hidden input, and stores
   only `expected_email_sha256`;
5. copies the primary permission policy and writes a minimal
   `.gemini/antigravity-cli/settings.json` (trusted workspaces from
   `allowed_workdir_roots`), both mode 0600 and neither overwriting an existing
   file;
6. appends the `[[accounts]]` block and runs the per-account doctor checks.

If any step fails after the home exists, the home is kept and the command prints
what was done plus the exact command to resume. Re-running it is safe: an
identical block is not duplicated.

### 2. Disable the shared-keyring default

```bash
agy-mcp account disable default --yes
```

This writes `[default_account] enabled = false`. The default is still reserved
and can never be declared in `[[accounts]]`; disabling it removes it from
scheduling and makes an explicit `account="default"` a refusal.

### 3. Check the result

```bash
agy-mcp account list --probe
./installer/install.sh --doctor
```

`list` is offline by default. `--probe` runs one isolated `/usage` per account
and shows the masked observed email and quota. The doctor probes every enabled
account and reports a `shared_keyring` warning if the default is still enabled
without an expectation, and a `shared_identity` warning when two enabled names
report the same login. Neither warning fails the doctor; both mean "fix the
setup", not "the install is broken".

The wrapper never touches the shared keyring for a declared account, because
every spawn gets `HOME=<account home>` plus
`DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`. The default account's env stays
the historical one, which is exactly why it is the one to disable.

### 4. Add further accounts

```bash
agy-mcp account add account-b --pool family --max-concurrent 1 --yes
```

Use `--home PATH` for a different location (the registry requires an absolute
path after `~` expansion). Use the same `--pool` label for accounts that share
one quota pool (for example family members).

## What the commands do

### `agy-mcp account add`

| Flag | Meaning |
|---|---|
| `name` | Registry name: `[a-z0-9][a-z0-9-]{0,31}`, unique, not `default`. |
| `--pool L` | Optional shared quota-bucket label. |
| `--max-concurrent N` | Optional per-account cap (integer >= 1). |
| `--home PATH` | Optional home; default `~/.local/share/agy-accounts/<name>`. |
| `--yes` | Apply. Without it the command is a dry run. |

`add` validates `max_accounts` (the total includes the implicit default),
refuses a duplicate name, a symlinked home and a non-terminal shell. It never
supplies credentials; the browser login is yours. `expected_email_sha256` is the
only identity value written, and it is the SHA-256 of the address you type at
the hidden prompt (normalized with `strip().casefold()`), compared with the
digest of the login agy reports.

A failed run is resumable. The home is left in place and the command prints a
resume line such as:

```text
to resume: agy-mcp account add main --yes
```

### `agy-mcp account remove`

```bash
agy-mcp account remove account-b --yes
```

`remove` deletes **only** the `[[accounts]]` block, after a timestamped
`config.toml.<UTC>.bak`; it never deletes the home or the login in this mode.

- If any job pinned to the account is `queued` or `running`, it refuses (exit 3):
  `A task is currently running on this account: <ids/titles>. Wait for it to
  finish or cancel it first.`
- If `needs_decision`, `needs_review` (including a quota-paused job) or
  `timeout` jobs are pinned, it warns that they will become unresumable and
  requires you to type `remove account <name>`.
- `remove default` is refused; use `account disable default`.

`--delete-home` also removes the home, but only when the resolved path is a real
directory (not a symlink) directly under the agy-accounts root (default
`~/.local/share/agy-accounts`, override with `--accounts-root`), and no
non-terminal job is pinned to it. It requires the extra phrase
`delete account home <name>`, never follows symlinks, and prints that the
Google login may still be valid server-side and can be revoked on the Google
account security page.

### `agy-mcp account enable / disable`

```bash
agy-mcp account disable account-b --yes
agy-mcp account enable account-b --yes
```

These flip only `enabled`, preserving comments and every other line. `disable`
never refuses, but warns when active jobs are pinned: they finish and no new
ones start. It also warns if no enabled account would remain. `disable default`
writes `[default_account] enabled = false`.

### `agy-mcp account list`

```text
account default: enabled=no pool=- max_concurrent=2 home=real-home active=0 paused=0 identity=none expected_hash=no
account main: enabled=yes pool=main max_concurrent=2 home=~/.local/share/agy-accounts/main mode 0700 active=1 paused=0 identity=sha256-present expected_hash=yes
```

`active` counts `queued`/`running`; `paused` counts `needs_decision`,
`needs_review` (including quota-paused) and `timeout`. `identity` reports
`sha256-present`, a masked legacy `expected_email`, or `none`. No full email is
ever printed.

## What `account add` does (the manual recipe)

The command is a convenience wrapper around these steps; run them yourself if
you prefer, or to understand a failure. Replace `account-b` and the path as
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

### 5. Record the expected email as a hash

The command asks for the expected email with hidden input, compares the SHA-256
of what you typed with the digest agy reports for the login, and stores only the
hash. To compute it yourself:

```bash
python -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.readline().strip().casefold().encode()).hexdigest())'
```

Then add `expected_email_sha256 = "<64 lowercase hex>"` to the block. Use
`expected_email` (the full address) **or** the hash, never both. The hash keeps
the address out of the file.

### 6. Copy the permission policy

`agy` headless runs rely on the policy at `$HOME/.gemini/config/config.json`
(human test 2026-11-03: a fresh account auto-denied a command tool). Copy the
primary policy into the new home so the account has the same allow-rules:

```bash
mkdir -p "$ACCOUNT_HOME/.gemini/config"
cp "$HOME/.gemini/config/config.json" "$ACCOUNT_HOME/.gemini/config/config.json"
chmod 600 "$ACCOUNT_HOME/.gemini/config/config.json"
```

The command does this (if the primary file exists) and never overwrites an
existing account policy. The wrapper verifies before every start that the account
policy matches the primary's; a missing or drifted policy refuses the job. It
never writes this file itself at runtime.

### 7. Write the settings file

The command writes `.gemini/antigravity-cli/settings.json` if absent:

```json
{"trustedWorkspaces": ["/path/from/allowed_workdir_roots"]}
```

mode 0600, never overwriting an existing file.

### 8. Declare the account

The command appends the block; the manual equivalent is:

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

An account sharing one quota pool with another (for example family members) may
set the same `pool` label:

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

**CLI-written blocks** are wrapped in `# agy-mcp:account <name> begin/end`
markers. The CLI finds a **hand-written** block by parsing its `name` and only
edits the span from its `[[accounts]]` header to the next table header (or EOF),
so comments and unrelated keys are preserved. It refuses ambiguous or corrupt
blocks rather than guessing.

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
account default: isolation_and_login: ok: isolated /usage ok as pla***on@example.com
account default: email: ok: pla***on@example.com (unchecked)
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

`account add` ends by running the same per-account checks and printing them, so
a fresh account shows the same `home`, `policy`, `isolation_and_login` and
`email` lines.

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
| `account_cli_isolation_leak` during `account add` | The fresh home reached a keyring login | Do not log in; check `DBUS_SESSION_BUS_ADDRESS` and that the home is new/empty |
| `Authentication required` during a job | The account was never logged in, or the token expired | Log in again with the isolated command |
| `account_home_permissions` | Home is group/other accessible | `chmod 700 "$ACCOUNT_HOME"` |
| `account_permission_policy_missing` / `_drift` | Policy not copied or differs from the primary | Re-run `account add` (it does not overwrite an existing policy) or copy the policy manually |
| `account_identity_mismatch` | Wrong login for the configured expectation | Check the account, or fix the expectation |
| `default_account_disabled` | `[default_account] enabled = false`, but a caller named `default` or a job is pinned to it | Pass an enabled account; the job is not moved automatically |
| `account_none_enabled` | The default and every declared account are disabled | Enable the default or one `[[accounts]]` entry |
| `account_cli_jobs_active` on `remove` | A job is still running on that account | Wait or cancel it, then retry |
| `account_cli_delete_home_unsafe` | The home is a symlink, outside the root, or has paused jobs | Remove the config block only, or fix the path first |
| Doctor says `shared_keyring` or `shared_identity` | The default still uses the shared keyring, or two names are one login | Use [Recommended setup](#recommended-setup-every-account-isolated) |
| Command tool auto-denied in a job | Account policy is weaker than the primary | Copy the primary policy; do not weaken it |

## Sources

- `doc/design/multi-account.md` — the registry, isolation and policy rules.
- `doc/design/decisions.md` — D102–D117.
- `doc/reference/cli-accounts.md` — the account CLI flags, exit codes and errors.
- `doc/audits/2026-11-03-multi-account/03-experiments.md` — the isolation and policy evidence.
- `doc/findings-and-experiments/15-multi-account-recon.md` — the feasibility summary.
- `doc/findings-and-experiments/16-shared-keyring-default-collision.md` — the 2026-11-05 observation.
- `src/agy_mcp/accounts/cli.py` — the human-run commands and safety gates.
- `src/agy_mcp/accounts/config_edit.py` — the conservative config edits.
- `src/agy_mcp/accounts/provision.py` — home, isolation probe, login handoff and files.
- `src/agy_mcp/accounts/registry.py` — validation rules and the implicit default.
- `src/agy_mcp/accounts/env.py` — the dead D-Bus override.
- `src/agy_mcp/accounts/identity.py` — masking and verification.
- `src/agy_mcp/config.py` — the `accounts` and `max_accounts` keys.
