# Multi-account implementation review

Status: current
Audience: maintainers, contributors, auditors

Final review of the multi-account implementation: what was built per phase, where
the code deviates from the Phase 2 design, what remains risky or unverified, and
the follow-ups a maintainer should schedule. Written after re-reading every new
module and running the serial and `-n 4` suites.

## What was built

| Phase | Commit | Delivered |
|---|---|---|
| 0 | `575dcc4` (+`24e7479`) | Baseline record: HEAD, 1823 tests, gates, AST baseline |
| 1 | `d43b0ca` | Code map (24 assumptions), real-agy isolation/transfer/usage experiments, fake-agy gap list, findings 15 |
| 2 | `2f2aa82` | Design (A–L, open questions) and D102–D114 |
| 3 | `6e2c958` | `accounts/` errors, registry, identity, env; `Config.accounts`/`max_accounts`; single-account equivalence tests |
| 4 | `5d6830e` | Typed pools/windows, per-account cache key, identity hook, optional usage-log attribution, fake-agy per-HOME usage/email/models |
| 5 | `a6b146c` | Pure scheduler: cost, strict filter, ranking, shared pools, reservations, burn mode, exhaustion |
| 6 | `c3adc70` | `account`/`priority`/`force` on `agy_start`, pinning, per-account caps, policy parity, audit events, schema update |
| 7 | `f280a00` | Conversation transfer (lock, SQLite/WAL checks), summary handoff, `allow_handoff`, link fields |
| 8 | `1456c6e` | Read-only account doctor wired into `--doctor`, user/reference/CHANGELOG/AGENTS docs |
| 9 | this commit | Email-hygiene test, scheduler type split (531→438 lines), review, final gates |

Total: 12 new modules under `src/agy_mcp/accounts/` (plus `scheduler_types.py`),
new tests in eight files, and additive changes to `config.py`, `jobs.py`,
`models.py`, `manager.py`, `supervisor.py`, `usage.py`, `server.py`, the
installer and the fake agy.

## Single-account equivalence

Proved by `tests/test_accounts_single_account_equivalence.py` and
`tests/test_accounts_wiring.py::TestSingleAccountEquivalence`:

- `Config()` has `accounts == []`, `max_accounts == 5`; loading a file with no
  `[[accounts]]` adds no unknown keys.
- `build_registry(Config(), home)` yields only the implicit default.
- `build_account_env(default, tmp)` is byte-identical to `env_allowlist`.
- A job started with no registry has `account is None`, `priority == "normal"`,
  and no `account`/`priority`/`quota_reservation` keys in `job.json`, the
  envelope or the `usage.jsonl` row.
- The usage cache key without an account is exactly the pre-change binary path.

The 1978-test suite (baseline 1823 + 155 new) passes serially and with `-n 4`.

## Deviations from the design

Each deviation is deliberate and either reflected in the design/decisions or
listed as a follow-up.

1. **Summary handoff reuses the job instead of creating a new job.** A new job
   would provision a new worktree from the base commit and lose the branch's
   uncommitted work. The implementation runs a new generation of the same job
   with a synthesized prompt and `conversation_id=None`, after a WIP checkpoint.
   `transferred_from`/`transferred_to` therefore carry **account names**, not
   job ids. Design G and D111 were corrected.
2. **Background ranking puts burn-eligible accounts before merely-strict ones.**
   Without this the worked example (A at 4% weekly resetting in an hour, B with
   room) would pick B and waste A's expiring quota. Design F5/D109 state it.
3. **Two email mask shapes coexist.** `usage.mask_email` (first 2 + `***`, legacy)
   is pinned by existing tests and stays for readings without an identity
   expectation. `accounts.identity.mask_email` (first 3 + last 2, capped at half)
   is used for identity-configured readings, the doctor and new account output.
   Unifying them would change pinned single-account output; a follow-up can add a
   deliberate, versioned change.
   *Correction (2026-11-04):* the new helper emitted the head and tail in the
   wrong order (`docer***@` instead of `doc***er@`); the reveal counts were
   correct. Fixed on `fix/mask-email-shape`; the shape is now `head***tail@domain`.
4. **`force` skips only the quota filter.** It still requires enabled, identity,
   home, permission policy, model and the per-account cap. This is stricter than
   a literal "force everything" and safer.
5. **Per-account model validation is runtime-only.** There is no cheap,
   side-effect-free model listing; the wrapper relies on agy's own rejection and
   surfaces it as a failed job. The design's OQ5 default.
6. **`account_usage_max_age_seconds` toggles the cache, not its TTL.** The TTL is
   the existing 60 s constant; a value of 0 forces a fresh read. Documented in
   `config-keys.md`.
7. **The fake agy conversation store is opt-in.** `$HOME/.fake_agy_conversation_store`
   enables db creation/missing-id behaviour so the pre-existing resume-echo test
   keeps its contract. Phase 7 tests touch the marker.
8. **The installer skips account checks when the installed package has no
   `accounts.doctor` module.** This keeps a minimal/test wheel healthy; a real
   install always has the module.
9. **The transfer lock is held for the copy, not the whole run.** The job state
   machine already serializes runs of one job, and the transfer happens only
   while the job is paused; a second concurrent copy of the same id is refused.
   A long-held lock would need supervisor plumbing for no additional protection
   found in testing.

## Security review

- **No credential access.** The new code never opens a keyring, token, cookie or
  credential file. `env.py` sets a dead D-Bus address; `transfer.py` copies only
  `conversations/<uuid>.db`; `doctor.py` reads the permission policy and runs
  `/usage`. A grep for `token`/`keyring`/`cookie`/`credential` finds only comments
  and token-count fields.
- **Full emails.** `tests/test_accounts_email_hygiene.py` plants
  `planted.person@example.com`, runs a registry job, and greps `job.json`,
  `audit.jsonl`, `usage.jsonl`, `server.log`, the start/status/wait/result
  envelopes and the doctor output: the full address is absent; a masked form is
  present. agy's own `cli.log` is written by the CLI and is not rewritten.
- **Injection boundaries.** Account names are regex-validated and re-validated
  before becoming a cap-file suffix; conversation ids are strict UUIDs before
  becoming a path component; a malformed conversation is refused, not parsed.
- **Fail-closed.** Missing/drifted policy, wrong email, bad home/mode, invalid
  registry, corrupt/missing/locked db and a dirty-worktree checkpoint failure all
  stop or fall back explicitly; none silently weakens a control.
- **Docs vs code.** `doc/design/spec.md` §8/§17/§18, `config-keys`, `errors`,
  `mcp-tools`, `state-layout`, `log-formats`, `exit-codes`, `environment`,
  `code-layout`, `using/accounts`, `CHANGELOG`, `AGENTS.md` and the decisions page
  were checked against the code and regenerated where mechanical.

## Residual risks

| Risk | Assessment | Mitigation |
|---|---|---|
| Cost estimate wrong | Heuristic from 7 jobs, 3.5× spread; `max_minutes` is a ceiling | Tunable; safety factor; the job pauses on real exhaustion and is recoverable |
| Account probes slow | An enabled but unauthenticated account can take up to the 30 s `/usage` timeout | Disabled/missing accounts are skipped; cache; a doctor timeout (15 s); follow-up to probe candidates only |
| Transfer portability partial | Text recall proven once; brain/attachments/images unverified | Copy only the db; summary handoff fallback |
| Concurrent desktop app writes | Desktop app uses a different store; a shared store is unsupported | Copy under a per-conversation lock; job paused during copy |
| Policy parity strict | Byte-set equality rejects a legitimate extra rule | Deliberate: a weaker/extra rule is visible and fixed by re-copying; doctor reports it |
| Clock skew on resets | Eligibility uses `/usage` reset timestamps, not the local clock | `resume_after` is re-derived from the reading; no sleep-based decisions |
| Model set per account | Runtime rejection only | Clear failed job; OQ5 follow-up |
| `gmail.com` domain in evidence | Real domains are generic, no full address | Evidence masks local parts and redacts domains where the publish gate requires it |

## UNVERIFIED items

- Multi-turn transfer with **brain artifacts, attachments or images** (only text
  recall was tested).
- Real account-b's **permission policy parity** with the primary (human test
  established the auto-deny failure mode; this session did not run a command tool
  on account-b).
- Concurrent use of the same conversation by two live processes/accounts (the
  design forbids it; only the copy-time lock is exercised).
- Real `/usage` failures beyond the fresh-HOME auth wait (malformed output uses
  the fake).
- Behaviour of a shared Google-family pool in the CLI output; sharing is
  operator-declared only.
- macOS/Windows behaviour of the dead D-Bus address and `flock` paths (Linux only).
- The exact cost of a job on a new account/model (the estimate is not calibrated
  per account).

## Follow-ups

1. Unify the two email mask shapes in a deliberate, versioned change with the
   pinned tests updated in the same commit.
2. Add a per-account model probe (a cached `agy models` or settings read) so a
   wrong model fails before a job is created.
3. Extend the fake agy to model a brain/attachment conversation and test that
   portability question.
4. Hold a long-lived conversation lock in the supervisor if a real concurrent
   writer is ever observed.
5. Add a history-based cost model (p50/p75 tokens and duration) fed from
   `usage.jsonl`, keeping the pure scheduler boundary.
6. Re-run the human isolation/transfer recipe on a second host to confirm the
   D-Bus and permission assumptions.

## Reviewer checklist

- `wc -l src/agy_mcp/accounts/*.py`: every module ≤ 438 lines (manager grew to
  2111 lines; the new logic lives in `accounts/selection.py` and helpers).
- No `TODO`/`FIXME`, no `print(` and no bare `except Exception:` in
  `src/agy_mcp/accounts/`.
- Every public module, class and function carries a "why" docstring.
- No directory iteration in new code (the registry/selection read explicit
  paths and `list_jobs`); no new determinism risk.
- Emails: masked in new output; the full value stays a local variable.

## Sources

- `src/agy_mcp/accounts/` — the new package reviewed line by line.
- `src/agy_mcp/manager.py` — account selection, pinning and handoff wiring.
- `src/agy_mcp/supervisor.py` — account home/usage/finalization wiring.
- `src/agy_mcp/usage.py` — per-account cache key and identity hook.
- `src/agy_mcp/models.py` — additive job/envelope fields.
- `tests/test_accounts_registry.py`, `tests/test_accounts_identity.py`,
  `tests/test_accounts_env.py`, `tests/test_accounts_pools.py`,
  `tests/test_accounts_scheduler.py`, `tests/test_accounts_wiring.py`,
  `tests/test_accounts_transfer.py`, `tests/test_accounts_handoff_wiring.py`,
  `tests/test_accounts_doctor.py`, `tests/test_accounts_email_hygiene.py`,
  `tests/test_accounts_single_account_equivalence.py` — the test evidence.
- `doc/design/multi-account.md` — the design this review compares against.
- `doc/design/decisions.md` — D102–D114.
- `doc/audits/2026-11-03-multi-account/PROGRESS.md` — per-phase commits and gates.
