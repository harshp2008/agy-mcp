# Which agy spawn still bypassed the per-account child env, and what now prevents a repeat?

Status: current
Audience: maintainers, contributors

The multi-account isolation bug found in live use (`human test 2026-11-03`): the
scheduler's `/usage` probe for a non-default account ran without the account
child env, so agy reached the desktop keyring as the primary login and the
start failed closed with `account_identity_mismatch`. This page records every
agy spawn path, its state before and after the fix, and the guard that keeps a
new path from reintroducing the leak.

Revision: 1 (2026-11-03)
Mode: read-only code audit plus fake-agy reproduction; no real agy was run
Scope: every `src/` site that spawns agy, reads `/usage`, or launches a job
Method: read the modules, `grep` for `Popen`/`subprocess`/`build_argv`/
`get_usage`/`read_usage_once`/`env_allowlist`/`build_account_env`, then drive the
fake agy with an env probe and a simulated desktop keyring.

## Root cause

`env_allowlist` builds the child env from scratch (`HOME`, `PATH`, `LANG`,
`TMPDIR`, git overrides) and never sets `DBUS_SESSION_BUS_ADDRESS`
(`src/agy_mcp/util.py:54-99`). Dropping the parent's value is **not** isolation:
agy falls back to the ambient session bus, which is the same desktop
SecretService keyring. `build_account_env` is the one place that adds
`unix:path=/dev/null` for a non-default account (`src/agy_mcp/accounts/env.py:23-45`),
and the live failure happened wherever a non-default `/usage` probe skipped it.

Observed before the fix (`tests/test_account_usage_env.py`, fake agy env probe):

```
E       AssertionError: assert {'HOME': '/tm...DDRESS': None} == {'HOME': ..., 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/dev/null'}
```

The probe had the account `HOME` but no D-Bus override, matching the live
report. With the fix the same test reads `unix:path=/dev/null`.

## Spawn paths

"Builder" means `accounts.env.build_account_env`; "allowlist" means
`util.env_allowlist` with only a `HOME` swap.

| # | Path | Site (main → fix) | Env before | Env after | Status |
|---|---|---|---|---|---|
| 1 | Scheduler `/usage` probe for a selected account | `accounts/selection.py:135` → `:135-148` | allowlist with account `HOME`, **no dead D-Bus** | `get_usage(account=account)` → builder | fixed |
| 2 | `/usage` at launch (`usage_at_launch`) | `manager.py:784` → `:784` | `_read_usage()` with **no account**: default `HOME` and no dead D-Bus | `_read_usage(..., account=account)` | fixed |
| 3 | `/usage` preflight quota guard | `manager.py:1118` → `:1113-1185` | account `HOME`/identity, still no dead D-Bus | account threaded through `_read_usage` → builder | fixed |
| 4 | `/usage` final cost/quota refresh | `supervisor.py:591-603` → `:591-604` | account `HOME`/name, still no dead D-Bus | `get_usage(account=account)`; manager passes it at all three supervisor sites | fixed |
| 5 | Doctor `/usage` probe | `accounts/doctor.py:74-83` → `:74-84` | ad-hoc `env_overrides={DBUS...}` (correct, duplicated logic) | `get_usage(account=account)` → builder | consolidated |
| 6 | Fresh job launch | `manager.py:769` → `:769`; `runner.py:240` | builder | builder | already correct |
| 7 | Resume / follow-up | `manager.py:1856` → `:1856` | builder | builder | already correct |
| 8 | Verification auto-fix | `manager.py:1067-1104` calls `resume` | builder (via #7) | builder (via #7) | already correct |
| 9 | Handoff / conversation transfer | `manager.py:599-709`, `accounts/transfer.py` (no spawn) then `resume` | builder (via #7) | builder (via #7) | already correct |
| 10 | Crash-recovery reattach | `manager.py:1378-1400`; reattaches the live process | no new agy spawn; final `/usage` had the flaw | supervisor now gets `account` | fixed via #4 |
| 11 | `agy_usage` MCP tool | `manager.py:1188-1198` (`_read_usage()` only) | default-account allowlist | unchanged, default account | intentionally unchanged |
| 12 | Verify command | `verify.py:528-611` | allowlist, server `HOME` | unchanged | not an agy spawn |
| 13 | Git helpers | `gitops.py:277-305` | allowlist | unchanged | not an agy spawn |

`accounts/transfer.py`, `accounts/handoff.py`, `recovery.py` and `server.py`
contain no agy spawn, `/usage` read or job launch.

## Fix

- `usage.read_usage_once` takes `account`; when present it calls
  `build_account_env(account, tmp_dir)` and nothing else. Without an account the
  pre-multi-account `env_allowlist` path is unchanged.
- `usage.get_usage` takes `account`, derives `home` from the account and passes
  it down; the cache key logic is unchanged.
- `selection`, `doctor`, `manager` (guard, at-launch, launch, resume) and
  `supervisor` (final reading) pass the account. The default account's builder
  output is still exactly `env_allowlist` (see
  `tests/test_accounts_single_account_equivalence.py` and the end-to-end default
  test).
- `tests/test_account_spawn_env_guard.py` statically rejects a new
  `get_usage`/`read_usage_once` call without `account=`, an agy argv builder
  without `build_account_env`, and an agy spawn whose env is neither the builder
  nor a caller-supplied parameter.

## Tests

- `tests/test_account_usage_env.py` — scheduler probe, job launch, resume,
  leak-vs-isolated identity, default-account equivalence.
- `tests/fake_agy/agy` — records `HOME`/`DBUS_SESSION_BUS_ADDRESS`/argv per spawn
  in `env-probe.jsonl`; `.fake_agy_keyring_email` simulates the shared keyring
  and is honoured only when D-Bus is not the dead address.
- `tests/test_account_spawn_env_guard.py` — AST guard over `src/`.

## Limits and unverified items

- The real-agy behavior (keyring fallback, `Authentication required` on a fresh
  HOME) can only be confirmed by the human; this audit used the fake agy only.
- `verify.py` and `gitops.py` intentionally keep the server `HOME`: they run the
  project's verify command and git, not agy, and have no credential surface.
- The `agy_usage` MCP tool still reads the default (primary) account; exposing a
  per-account `agy_usage` is out of scope and would be a new tool contract.
- The supervisor's final `/usage` reading still does not pass an identity
  expectation; with the isolated env it reads the pinned account's home, but a
  future identity check there is a separate decision.

## Sources

- `src/agy_mcp/util.py:54-99` — `env_allowlist` never sets the D-Bus variable.
- `src/agy_mcp/accounts/env.py:23-45` — `build_account_env`, the single builder.
- `src/agy_mcp/usage.py:658-716` — `read_usage_once` account env branch.
- `src/agy_mcp/usage.py:823-872` — `get_usage` account threading.
- `src/agy_mcp/accounts/selection.py:133-150` — scheduler probe.
- `src/agy_mcp/accounts/doctor.py:74-84` — doctor probe.
- `src/agy_mcp/manager.py:769-784` — fresh launch env and at-launch reading.
- `src/agy_mcp/manager.py:1157-1186` — manager usage reader.
- `src/agy_mcp/manager.py:1851-1856` — resume env.
- `src/agy_mcp/supervisor.py:591-604` — final usage reading.
- `src/agy_mcp/runner.py:220-250` — the delegated spawn.
- `src/agy_mcp/verify.py:575-600`, `src/agy_mcp/gitops.py:277-305` — non-agy spawns.
- `tests/test_account_usage_env.py`, `tests/test_account_spawn_env_guard.py` — the regression and guard contracts.
- `tests/fake_agy/agy` — the env probe and keyring simulation.
- `doc/findings-and-experiments/15-multi-account-recon.md` — the live-use note.
