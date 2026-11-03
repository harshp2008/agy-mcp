# Multi-account design: selecting among several owned Google accounts

Status: proposed
Audience: maintainers, contributors, orchestrators

Design for running `agy` jobs across several Google accounts the user
legitimately owns, with per-account isolation, quota-aware selection, job
pinning and conversation transfer. Every choice names a recommendation and the
alternative it rejects; the normative rules are recorded as
[D102](decisions.md#d102-multi-account-registry-and-implicit-default-account)–[D112](decisions.md#d112-cleaner-compatibility-with-new-job-fields).
With no `[accounts]` configuration the existing single-account behavior is
unchanged; that is a testable invariant, not an intention.

## A. Registry in `config.toml`

**Recommendation.** A `[[accounts]]` array of tables plus one integer key:

```toml
max_accounts = 5

[[accounts]]
name = "account-b"
home = "~/.local/share/agy-accounts/account-b"
pool = "family"                 # optional; same label => one quota bucket
enabled = true                  # optional, default true
max_concurrent_jobs = 1         # optional per-account override
expires_at = "2026-12-01T00:00:00Z"  # optional billing expiry, informational
expected_email = "<account-b full address>"  # optional, or:
# expected_email_sha256 = "<64 hex chars>"
```

Validation (all fail closed with a stable code in
[errors.md](../reference/errors.md); see §B for email rules):

| Rule | Error code |
|---|---|
| `accounts` is an array of tables; each entry has `name` and `home` | `account_config_invalid` |
| `name` matches `[a-z0-9][a-z0-9-]{0,31}`, is unique, is not `default` | `account_name_invalid` / `account_name_duplicate` |
| `1 + len(accounts)` ≤ `max_accounts` (default 5, range 1–32) | `account_limit_exceeded` |
| `home` is absolute after `~` expansion, exists, is a directory, mode 0700 | `account_home_invalid` / `account_home_permissions` |
| `enabled` is bool; `max_concurrent_jobs` is int 1..global cap | `account_config_invalid` |
| `pool`, when set, is a non-empty slug; accounts may share it | `account_config_invalid` |
| `expires_at` is an ISO-8601 timestamp | `account_config_invalid` |
| at most one of `expected_email` / `expected_email_sha256`; the hash is 64 lowercase hex | `account_config_invalid` |

The **implicit `default` account always exists**, using today's real
`home`/`agy_home` and no isolation override; it cannot be declared, renamed or
disabled. `[[accounts]]` entries are *additional* accounts, so the total is
`1 + len(accounts)` and must be ≤ `max_accounts` (default 5, i.e. up to four
extra). Every N≥1 path (registry, scheduler, envelopes, tests) is exercised for
N=1, 2 and 5 total accounts, including two accounts sharing a `pool`. More than
`max_accounts` is a startup error, not a silent truncation.

**Rejected alternatives.** A separate per-account config file (two sources of
truth, harder installer); an environment-only registry (invisible, untestable);
making `default` declarable (breaks the "no registry = today" invariant).

## B. Email handling

**Recommendation.** One helper, `mask_email(email, head=3, tail=2)`, in
`src/agy_mcp/accounts/identity.py`. It keeps at most `head` leading and `tail`
trailing local-part characters but **never more than half** the local part
(rounded down), so a short local part reveals less: 1–2 characters → `***`,
length 3–6 reveals at most half, a long address shows at most 3+2. Domain is
kept because it is not identifying on its own.

Places that show or store an email today, and their new rule:

| Place | Today | New rule |
|---|---|---|
| `usage.py:51-64` `mask_email` | first2 `***` | Replaced by the new helper (old shape kept only if a test pins it) |
| `usage.py:608-613` `_account_dict` | masked email in the usage reading | Same, using the new helper |
| `usage.py:737` log parse | full email in memory | Full value stays in memory for verification only |
| `usage.jsonl` (`usage.py:813-870`) | no email | Add `account` name + `masked_email` only when a registry is configured |
| Doctor/doctor output | none | masked only |
| MCP envelopes | none | add `account` name + masked email, never the full value |
| `job.json` / audit logs | none | registry name only; never the email |

Verification compares the **full** email parsed from `applyAuthResult` with
`expected_email`, or its SHA-256 with `expected_email_sha256`; the masked string
is never compared. A mismatch is `account_identity_mismatch` and blocks the
start. A missing auth line is `account_identity_unknown` (blocked only when an
expectation was configured).

**Rejected alternative.** Masking everything as `***` (loses account
recognition in an envelope) and storing the full email (leaks identity into
job records and logs).

## C. Runtime isolation and state

**Recommendation.**

- **Child env.** `accounts/env.py` builds on `util.env_allowlist` and adds
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null` for every non-default account.
  `HOME` is the account home. PATH/LANG/TMPDIR and the git overrides are
  unchanged. The default account gets exactly today's env (no D-Bus key), so the
  single-account path is byte-identical. XDG variables are not passed: agy
  resolves its state under the overridden `HOME`.
- **Worktree preflight.** The worktree stays in the real user's filesystem. The
  HOME containment check uses the **invoking user's real home**, not the account
  home; an account home is never added to `allowed_workdir_roots`. The minimal
  solution is to keep `validate_repo_path(home=<real home>)` for the default
  account and to require either the repo under the real home or an explicit
  `allowed_workdir_roots` entry for every account. Security review: agy runs as
  the same UID in all cases, so redirecting HOME does not change what it can
  read; the worktree location is not a security boundary. The account home holds
  agy's token file and must be 0700.
- **State layout.** One shared state root (`~/.local/state/agy-mcp`) with the
  account **name** on the job and per-account cap counters
  (`daily_cap.<name>.json`, `daily_cap.<name>.lock`; the default account keeps
  the existing `daily_cap.json`/`.lock` names for neutrality). Justification:
  job ids are global, recovery/cleanup/`agy_list` already scan one store, and a
  per-account store would split the concurrency lock and duplicate recovery.
  Per-account state directories are rejected: they make `agy_cleanup` and the
  frozen cleaner's store assumptions harder and leave orphaned stores.
- **Per-account caps.** Global `max_concurrent_jobs` and `daily_jobs_cap` still
  apply; each account additionally has `max_concurrent_jobs` (default: the
  global cap) and the global daily cap. The slot, the daily-cap increment and
  the account reservation happen under the same `concurrency_lock`.
- **Reservations.** A running job reserves its estimated cost against the
  account until finalization; the scheduler subtracts reservations.

**Rejected alternatives.** A per-account state root (split locks, duplicate
recovery); allowing the account home as a worktree root (widens the containment
rule for no isolation gain); inheriting the parent D-Bus for the default account
*with* an override (breaks the "no registry = today" invariant).

## D. Permission policy per account

**Recommendation.** Each account home must carry the same permission policy as
the primary, because `agy`'s headless safety rests on
`$HOME/.gemini/config/config.json` (human test 2026-11-03: a fresh account
auto-denied a command tool). The wrapper:

1. Provisions a copy of the primary's `AGY_PERMISSION_CONFIG_REL` into a new
   account as part of the **manual recipe**, not silently at runtime.
2. Verifies before **every** start on that account that the file exists,
   parses, and its normalized `permissions.allow` set equals the primary's.
3. Refuses a start with `account_permission_policy_missing` or
   `account_permission_policy_drift`; `--doctor` reports the same as a warning.
4. Never mutates an account policy at runtime and never weakens one.

Why an account must never run with a weaker policy: the wrapper's threat model
assumes the policy blocks dangerous commands and grants the file tools. A weaker
policy (for example an added `command(bash)` rule) turns a normal job into
arbitrary code execution that the wrapper's audit cannot prevent. A stronger
policy only causes denials, which the wrapper already reports.

**Rejected alternatives.** Auto-copying the policy on first start (a silent
write into a user's account home and a drift source); accepting "any parseable
policy" (the exact failure human testing found).

## E. Usage and quota

**Recommendation.**

- Parse `/usage` into `PoolReading(name, window, remaining_fraction, reset_time)`
  for both pools and both windows per account. Keep the existing fail-soft
  parser; add a typed view over it in `accounts/pools.py`.
- Cache key: `f"{account.name}|{account.home}|{resolved_binary}"` (the account
  name/home, not only the binary). The default account keeps the old key shape so
  a single-account reading is cached exactly as today.
- `usage.jsonl`: add `account` (registry name) and `masked_email`, **only when a
  registry is configured**; old rows and the no-registry path are unchanged.
- Failure isolation: if one account's `/usage` fails, times out, or is
  unparsable, that account is marked `usage_unavailable` with a reason, skipped
  by the scheduler, and the start still proceeds with the remaining accounts. A
  start with no readable account is `accounts_unavailable` (or falls back to the
  default account when it is the only one and the registry is empty).
- Lazy refresh: only scheduling candidates are refreshed, with a short
  `account_usage_max_age_seconds` (default 60); no background polling.

**Rejected alternative.** One aggregate usage reading (cannot attribute quota or
resets); a persistent usage database (the current file is an append-only audit
log, not a cache).

## F. Scheduler

All thresholds are configuration keys with defaults, calibrated from finding 08
and treated as heuristics, never facts.

### F1 Cost estimate

```text
rate_tokens_per_minute = account_cost_tokens_per_minute (default 35300)
expected_minutes       = account_cost_expected_minutes (default 30)
horizon                = min(max_minutes, expected_minutes)
estimated_tokens       = rate_tokens_per_minute * horizon
cost_5h_pct            = estimated_tokens / 1000 * 0.011
cost_weekly_pct        = estimated_tokens / 1000 * 0.0031
```

When history is available, the scheduler accepts a precomputed
`CostModel(rate_p75_tokens_per_minute, median_minutes)`; it uses
`max(default_rate, history_rate)` and
`min(max_minutes, max(expected_minutes, history_median_minutes))`. The decision
function is pure: history sampling happens before it, and the same inputs always
produce the same choice.

### F2 Eligibility filter

An account is eligible for a job only if **all** hold:

- enabled, email verified (when an expectation is configured), permission policy
  verified, requested model accepted by that account (where checkable),
- the job's pool has both windows; five-hour remaining ≥
  `account_five_hour_floor_pct` (default 15%) **and** weekly remaining ≥ cost ×
  `account_cost_safety_factor` (default 1.5) **and** five-hour remaining ≥ cost
  × safety factor,
- active jobs + reservations on the account < its `max_concurrent_jobs`,
  after treating the job's own reservation as consumed.

Accounts are grouped by `pool`; accounts sharing a pool use the **minimum**
remaining of the group and one shared reservation total.

### F3 Ranking

```text
urgency      = remaining_weekly_pct / max(hours_to_weekly_reset, 0.0167)
expiry_boost = 0
if expires_at is set and hours_to_expires <= 168:
    expiry_boost = (168 - hours_to_expires) / 168 * 100
score        = urgency + expiry_boost
```

Higher score first; ties break by lower recent load (active + reserved), then
oldest `last_used`, then account name ascending. The clock is injected; no
`sleep` participates in a decision.

### F4 Exhaustion

If no account is eligible, the scheduler returns
`SchedulerResult(exhausted=True, resume_after=<earliest UTC when any account
becomes eligible>, reason="quota_exhausted")`. `resume_after` is the earliest
time at which both windows of some account are usable again (for a shared pool,
the group's binding reset). The job stays in the existing
`needs_review(reason=quota_exhausted)` state; there is no fallback to another
tool. An optional `account` on `agy_start` overrides ranking; it still must pass
verification, policy, model and cap. `force=True` skips **only** the quota
filter/reservations, never identity, policy or the model check.

### F5 Use-it-before-it-expires

`priority` is `normal` (default) or `background`, accepted by `agy_start` and
stored on the job. Normal jobs keep the strict F2 filter. Background jobs:
eligible when the longest wait until all relevant windows of the pool are
usable again is ≤ `account_burn_horizon_minutes` (default 90), even if remaining
< cost × safety; ranked by nearest binding reset, then smallest remaining quota;
**anti-starvation**: a background job may not take an account's last free slot
while a normal job is queued for that account. The queue is the set of
`queued`-state jobs plus the current caller's pending normal start.

Worked example: account A is at 96% weekly used (4% left) with that window
resetting in 1 hour; its five-hour window is healthy. A normal job estimated at
12 weekly points cannot fit (4 < 12 × 1.5), so it goes to account B, which has
enough weekly quota. A background job can use A: the longest wait until A's
windows are usable again is 60 minutes ≤ 90, and its remaining weekly quota is
about to be lost, so it burns A first. Paused time does **not** count against
`cumulative_minutes` (the supervisor adds only run time,
`src/agy_mcp/supervisor.py:1471-1480`, `src/agy_mcp/jobs.py:369`), so no budget
change is proposed.

**Rejected alternatives.** Round-robin (ignores expiry and waste); "lowest
remaining first" (burns the account you need later); a hard-coded token cost
(does not age); letting background jobs ignore concurrency caps (starves normal
work).

## G. Job pinning and transfer

**Recommendation.**

- Additive job fields: `account` (registry name; `None` means the implicit
  default), `priority` (`normal`/`background`), `transferred_from`,
  `transferred_to`. `to_dict` emits `account`/`priority` only when they differ
  from the single-account default, and emits transfer fields only when set.
  `from_dict` defaults them. `CURRENT_SCHEMA_VERSION` stays 1:
  `from_dict` already accepts missing fields, the cleaner ignores unknown keys
  (checked in `src/agy_mcp/cleanup/jobs_cli.py:61-90`), and the frozen fixtures
  stay valid. Bumping would change new records for no compatibility gain.
- Resume, auto-fix, plan nudge and follow-up always use the job's pinned
  account and that account's env. A job never silently moves.
- **Transfer.** When a resume would wait longer than
  `account_handoff_min_wait_minutes` (default 120) on the pinned account, or the
  caller asks (`allow_handoff=True` on `agy_resume`): pick an eligible target
  (F2/F3), copy only
  `<source_home>/.gemini/antigravity-cli/conversations/<id>.db` into the target
  store while no process uses it, verify the copy is a SQLite file (header
  check) and that the target now has it, then resume with `--conversation <id>`.
  A per-conversation `.lock` under `<state>/accounts/locks/` prevents two
  accounts from using the same id at once. Record `transferred_from` /
  `transferred_to` and an audit event. Never delete the source copy until the
  new run finishes. If the target also lacks quota, or the db is missing,
  corrupt or locked, fall back.
- **Summary handoff (fallback).** Start a new job on the target whose prompt is
  built only from wrapper facts: original goal/constraints, the plan checklist
  done/remaining, branch, `git log`/diff stat, a capped excerpt of the last
  report as delimited untrusted text, and the verify command. Link the jobs with
  `transferred_from`/`transferred_to`.
- **Worktree.** Reuse the same worktree and branch on the target (same UID).
  Before switching, create a WIP checkpoint commit if the worktree is dirty so
  no work is lost; never touch the default branch.
- **Not supported:** a shared or symlinked conversation store. SQLite WAL files
  on a shared inode, the desktop app writing the same store, and the cleaner's
  per-store assumptions make concurrent access unsafe; the copy-and-lock model
  keeps one writer per store.

**Rejected alternatives.** Always transfer (copies a db for every resume);
never transfer (wastes quota when the pinned account is stalled); run the same
id on two accounts concurrently (divergent histories, SQLite contention); a
shared store (above).

## H. Cleaner impact

The cleaner reads `job.json` fail-closed for size/regularity/root-object/job_id
only (`src/agy_mcp/cleanup/jobs_cli.py:61-90`, `src/agy_mcp/cleanup/store_io.py:350`).
New optional fields are ignored, and old fixtures are byte-identical because
serialization is conditional. The 13 frozen files
(`tests/frozen.sha256`) are untouched. The cleaner's store is shared, so it
needs no per-account logic; the tests that could be affected are the frozen
`test_cleanup_jobs*` family and `tests/test_cleanup_fixtures.py`, which stay
unmodified.

## I. Installer, doctor and user docs

**Recommendation.**

- Manual add-account recipe (in `doc/using/accounts.md`): create the home with
  mode 0700; run the isolation check
  `HOME=<home> DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null agy -p "/usage"`
  and confirm `Authentication required`; the **human** logs in interactively;
  confirm the account's own quota; copy the primary permission policy; add the
  `[[accounts]]` entry. The wrapper never logs in or out.
- `--doctor` (read-only, masked emails, never logs in): registry parses;
  each home exists with mode 0700; permission policy matches; expected email
  matches (via a short `/usage` probe with the account env); isolation env is
  effective; `pool` labels are sane. Findings are warnings, exit 1 on any
  problem, like today's doctor.
- Installer: documents and optionally prints the recipe; it never creates a
  login, never runs `agy auth`/login, never writes into an account home except
  the policy copy the human explicitly requests (`--account-recipe` prints, it
  does not write).

**Rejected alternatives.** Installer-driven login (the wrapper must not touch
credentials); doctor writing anything (read-only is the contract).

## J. Threat model and failure modes

| Failure | Control |
|---|---|
| Wrong account used | Expected email/hash verification before start; masked email in every envelope |
| Keyring leak | Non-default accounts always get the dead D-Bus override; doctor checks the env |
| Token file permissions | Account home 0700; agy's file fallback is 0600 (observed) |
| Desktop-app logout | Never touches the default keyring for non-default accounts; the default path is unchanged |
| Exhaustion races | One `concurrency_lock` for count, cap and reservation; per-account reservations |
| Clock skew on resets | Eligibility and `resume_after` come from `/usage` resets, not the local clock; wait durations are computed from the injected clock and re-checked |
| Corrupt/locked transfer db | SQLite header check, lock, fallback to summary handoff, source kept until success |
| Double-use of a conversation | Per-conversation lock; refuse concurrent use |
| Registry misconfiguration | Fail-closed validation at load; max accounts; no silent truncation |
| Model mismatch | Per-account model check before launch; `account_model_unavailable` |
| Policy drift | Pre-start parity check; `--doctor` warning; start refusal |

Residual risks: the scheduler's cost is a heuristic; a bad estimate can still
start a job that exhausts quota. The wrapper detects exhaustion after the fact
and pauses, so the failure is recoverable. Account email verification depends on
agy logging `applyAuthResult`; a CLI change breaks it (fail-closed when an
expectation is configured). Transfer portability for brain artifacts,
attachments and images is UNVERIFIED; the design falls back to the summary
handoff when the db is absent and does not claim artifact portability.

## K. Terms of service and legitimacy

The feature selects among **existing logins on this host that the user
legitimately owns**. It never creates accounts, never automates sign-up, never
accepts credentials in prompts/config, and never reads, copies or prints a
token, keyring entry or cookie. Adding an account is a human login performed
outside the wrapper; the wrapper only runs jobs on the resulting login. A user
who is not entitled to an account must not configure it; the registry is a
selection list, not a credential store.

## L. Phased implementation plan

| Phase | Change | Tests | Risk |
|---|---|---|---|
| 3 Foundation | `accounts/` errors, registry, identity, env; `Config.accounts` | validation matrix, mask table, env builder, single-account equivalence | low (nothing consumes it) |
| 4 Usage pools | typed per-account pools, cache key, `usage.jsonl.account`, fake-agy per-account payload | malformed/missing/not-signed-in, per-account cache | low |
| 5 Scheduler | pure decision function, reservations, burn mode | table-driven, worked example, N=1/2/5, shared pool, anti-starvation | medium (logic only) |
| 6 Job model + wiring | `account`/`priority`, `agy_start` params, pinning, per-account caps, audit events | end-to-end 1/2/3 accounts, single-account equivalence, schema | medium |
| 7 Exhaustion + transfer | per-account `resume_after`, db copy + lock, summary handoff, WIP | corrupt/locked/collision/also-exhausted, full suite | high |
| 8 Installer/doctor/docs | doctor checks, recipe, reference/docs, regen | installer-style hermetic tests | low |
| 9 Hardening | review, planted-email test, full serial + `-n 4` | full suite, gates | low |

## Open questions

At most eight; each has the default this design proceeds with when unattended.

| # | Question | Default |
|---|---|---|
| OQ1 | Byte-identical policy, or the primary's allow-rules as a required subset? | **Equal normalized allow-rule set**; drift refuses the start. A subset would let a weaker account pass |
| OQ2 | Per-account `agy` binary? | **No**; one binary, many HOMEs. A per-account binary is a follow-up |
| OQ3 | `force` on `agy_start`? | **Yes**, skips quota filter/reservations only; never identity, policy or model |
| OQ4 | Does transfer need `brain/`? | **No** for text recall (Phase 1); copy the db only, fall back otherwise |
| OQ5 | Per-account model check depth? | **Fail-closed only when the account rejects the model**, mapped to `account_model_unavailable`; no pre-flight model listing |
| OQ6 | Handoff trigger default? | **120 minutes**; caller can force with `allow_handoff=True` |
| OQ7 | `usage.jsonl` fields when no registry? | **No new fields**; add `account`/`masked_email` only when a registry is configured |
| OQ8 | Bump `schema_version`? | **No**; additive optional fields, cleaner ignores unknown keys |

## Sources

- `src/agy_mcp/config.py` — config schema, permission path, defaults.
- `src/agy_mcp/util.py` — child env allowlist.
- `src/agy_mcp/usage.py` — `/usage` parsing, cache key, `usage.jsonl`.
- `src/agy_mcp/worktree.py` — HOME preflight and permission inspection.
- `src/agy_mcp/manager.py` — start/resume, caps, usage guard.
- `src/agy_mcp/supervisor.py` — exhaustion detection, run-time accumulation.
- `src/agy_mcp/models.py` — Job fields and schema version.
- `src/agy_mcp/cleanup/jobs_cli.py` — cleaner's job.json handling.
- `src/agy_mcp/cleanup/store_io.py` — fail-closed job.json loader.
- `doc/findings-and-experiments/08-quota-calibration.md` — cost coefficients.
- `doc/findings-and-experiments/15-multi-account-recon.md` — isolation and transfer facts.
- `doc/audits/2026-11-03-multi-account/02-code-map.md` — single-account assumptions.
- `tests/frozen.sha256` — the frozen cleaner contract.
