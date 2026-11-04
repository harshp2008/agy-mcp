# Why did the implicit default account double-count account-b's quota?

Status: current
Audience: maintainers, users, orchestrators

A 2026-11-05 human observation showed the implicit `default` account using the
shared desktop keyring and silently becoming the same Google login as the
configured `account-b`. The scheduler therefore counted one quota pool as two
accounts. This page records the observation, the reproduction with the fake agy,
and why the fix makes the implicit default optional and verifiable (D116).

## Observation (human test, 2026-11-05)

Reported by the operator during a real run; these are the observed facts, not
commands this repository re-ran:

- The implicit `default` account has no isolated `HOME`; it runs as the invoking
  user and reads credentials through the shared desktop keyring.
- The Antigravity chat app writes that same keyring when the user switches
  accounts.
- After a switch, plain `agy -p "/usage"` returned `account-b`'s exact quota.
- `default` had no identity expectation, so nothing in the wrapper noticed the
  change.
- The scheduler saw two registry names with quota and counted one pool twice.

The last point is the failure: the account names are a selection list, not a
promise that each name is a distinct login.

## Reproduction (fake agy, hermetic)

The fake agy models the keyring: `.fake_agy_keyring_email` is honoured only when
the child environment does **not** point `DBUS_SESSION_BUS_ADDRESS` at the dead
socket, and every registry-scoped `/usage` probe records a SHA-256 digest of the
observed address (never the address itself). The new tests show both halves:

```bash
env -u PYTHONPATH .venv/bin/python -m pytest -q \
  tests/test_accounts_shared_identity.py tests/test_accounts_default_optional.py
```

- An unverified default (no expectation) and a verified `account-b` that report
  the same digest get the same pool key; the stale 90% default reading no longer
  outranks the account's real 2%. Counted separately, the default would win.
- Two verified accounts that report the same login are one pool: the minimum
  remaining governs both. Counted separately, the 90% account would win.
- The doctor reports the unverified shared-keyring case as a `shared_keyring`
  advisory and every duplicate login as a `shared_identity` advisory.

## Conclusion

`default` was special in two ways that only made sense while it was the only
account: it always existed and it never had to prove who it was. The fix keeps
the pre-multi-account behavior when no account configuration exists, and makes
the implicit account optional and verifiable:

1. `[default_account]` can disable it or give it the same email/hash expectation
   as an `[[accounts]]` entry.
2. A disabled default is never scheduled; an explicit `default` and a resume of a
   default-pinned job fail with `default_account_disabled` instead of moving.
3. Every enabled account must exist: `account_none_enabled` otherwise.
4. Two enabled accounts that report the same verified login share one pool key;
   the doctor reports unverified duplicates and the shared-keyring risk.
5. The recommended setup gives the main account its own isolated home and
   disables `default`, so no agy-mcp account depends on the desktop keyring.

## Limits

- The observed quota values were reported by the human, not re-measured here.
- The scheduler merges a duplicate group only when at least one member verified
  an identity expectation. A group where every name is unverified is reported by
  the doctor but not merged, because the same masked/legacy reading shape cannot
  prove the two names are one login. Setting any expectation (including on the
  declared account) makes the automatic merge apply.
- Real desktop-app keyring writes are outside the wrapper's control and are not
  simulated beyond the fake agy marker described above.

## Sources

- `src/agy_mcp/accounts/registry.py` — `DefaultAccountConfig`, `parse_default_account`, `build_registry`.
- `src/agy_mcp/accounts/selection.py` — identity/collision pool keys and the default gate.
- `src/agy_mcp/accounts/doctor.py` — `shared_keyring` and `shared_identity` warnings.
- `src/agy_mcp/accounts/env.py` — the dead D-Bus isolation for declared accounts.
- `src/agy_mcp/usage.py` — the observed-identity digest and expectation verification.
- `tests/test_accounts_shared_identity.py` — the collision reproduction.
- `tests/test_accounts_default_optional.py` — the optional/verifiable default.
- `tests/fake_agy/agy` — the keyring simulation.
- `doc/design/decisions.md` — D116.
