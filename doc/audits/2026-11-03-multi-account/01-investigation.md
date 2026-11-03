# Multi-account investigation: what the code and the real CLI do

Status: current
Audience: maintainers, contributors

Phase 1 asked five questions: (1) where the code assumes one account, (2) what
the fake agy can already simulate, (3) what the real CLI does across two
accounts and across a conversation transfer, (4) how to estimate a job's quota
cost before launch, and (5) which facts remain unverified. This page records the
method and the answer to each; raw output is under `evidence/`.

## Method

- Read every production module that touches `HOME`, `~/.gemini`, state paths,
  `/usage`, exhaustion, concurrency or the installer, and tabulated each line
  ([02-code-map.md](02-code-map.md)).
- Read `tests/fake_agy/agy` for the scenarios it already supports.
- Ran a bounded set of real `agy` calls (nine invocations, `evidence/real-agy-calls.md`)
  on the primary account and on `account-b` with
  `HOME=<account-b> DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`.
- Ran a multi-turn, tool-using conversation on the primary, then copied only
  `conversations/<id>.db` into account-b's store and asked account-b to recall
  the earlier turns.
- Derived the pre-launch cost model from
  [08-quota-calibration.md](../../findings-and-experiments/08-quota-calibration.md)
  and `compute_quota_estimate` (`src/agy_mcp/usage.py:500-560`).

## Results

### 1. Single-account assumptions

The code map finds **24 assumption-bearing lines** across seven areas: child
env, `~/.gemini` paths, state/caps, usage cache/log, exhaustion/resume,
worktree preflight/audit and the installer. The prior recon's nine assumptions
all hold, with four line-number corrections. The read also found four facts the
recon missed: per-account model availability, silent fresh-conversation
fallback for a missing id, `/usage` triggering interactive OAuth on an
unauthenticated HOME, and global-concurrency cost accounting.

### 2. What the fake agy supports today

| Capability | Supported | How |
|---|---|---|
| `/usage` JSON shape | Yes | Committed `tests/fixtures/real_usage.json` |
| Fixed usage variants | Yes | `FAKE_AGY_SCENARIO` or `$HOME/.fake_agy_usage_scenario`: `usage_real`, `usage_zero`, `usage_low`, `usage_zero_claude`, `usage_auth_required`, `usage_hang`, `usage_empty`, `usage_garbled` |
| Per-account usage values | Partially | The scenario marker is read from `$HOME`, so two HOMEs can differ, but only among the fixed variants and the email is one synthetic constant |
| Conversation id | Yes | Deterministic UUIDv5 from the log path; `--conversation` echoed, or ignored with a marker |
| Conversation db file | **No** | It never creates or checks `conversations/<id>.db`; a missing id is not modelled |
| `cli.log` | Yes | Appends, with `Created conversation`/`resuming conversation` lines |
| HOME handling | Partial | Reads `$HOME` for the scenario marker and record pointer; no per-HOME store |
| Model validation | No | Any `--model` string is accepted |

What must be added (Phase 4 and 7): a per-account `/usage` payload file under
`$HOME` with arbitrary pools/windows and a per-account email line, and a
conversation store under `$HOME` with `conversation "<id>" not found` warning +
new-id fallback when the file is absent.

### 3. Real CLI behaviour

**`/usage` format (both accounts, exit 0).** stdout is one JSON object with
`status`, `response`, `conversation_id`, `usage`, and
`command.data.groups[]`. Each group has `name` and `buckets[]`
(`window`, `remaining_fraction`, `reset_time`). The `response` is four
tab-separated rows: `<pool>\t<window label>\t<pct>%\t<ISO reset>`. Primary
Gemini weekly was 58.7%, account-b Gemini weekly 88.2%, with different reset
times — independent quota (evidence `evidence/real-usage-rows.txt`).

**Isolation.** A fresh HOME with the D-Bus override prints
`Authentication required`, starts the OAuth flow and waits; it does **not** show
another account's quota (evidence `evidence/isolation-fresh-home.txt`). The run
was killed at 45 s (exit 124), so the scheduler must never probe an
unauthenticated account without a short timeout.

**Conversation transfer.** Copying only `conversations/<id>.db` into account-b's
store let account-b continue the same id (`num_turns=3`) and recall the earlier
filename and marker written by a file tool on the primary
(`evidence/conversation-transfer.txt`). The `brain/` directory and
`conversation_summaries.db` were not copied and were not needed for this test.

**Missing id.** agy warns `conversation "<id>" not found`, then starts a **new**
conversation with a new id and exits 0. This is a silent semantic failure, not
an error.

**Per-account model set.** `gemini-3.8-flash-medium` was rejected on account-b
(exit 1, no tokens) while `gemini-3.8-flash` worked. Model validity is account
state.

### 4. Cost estimate method

`compute_quota_estimate` converts tokens to percentage points with the frozen
conservative coefficients `0.011` (5 h) and `0.0031` (weekly) points per 1k
tokens (`src/agy_mcp/usage.py:500-560`, D85, finding 08). A scheduler needs a
cost **before** a job's tokens exist, so estimate minutes, then tokens, then
points:

```text
estimated_minutes   = min(max_minutes, default_expected_minutes)   # default 30
estimated_tokens    = tokens_per_minute_default * estimated_minutes
cost_5h_pct         = estimated_tokens / 1000 * 0.011
cost_weekly_pct     = estimated_tokens / 1000 * 0.0031
```

From the seven calibration jobs (finding 08, table), tokens per wall-clock
minute were:

| Job | Tokens | Minutes | Tokens/min |
|---|---:|---:|---:|
| `6d9be49b` | 809,853 | 22.93 | 35,325 |
| `d817bb06` | 216,849 | 6.38 | 34,011 |
| `9bdf04c7` | 212,403 | 7.51 | 28,290 |
| `35ce4421` | 236,639 | 3.54 | 66,862 |
| `fc1e5a76` | 170,216 | 5.06 | 33,660 |
| `3875253f` | 226,453 | 1.74 | 130,446 |
| `d9c76277` | 291,178 | 3.25 | 89,455 |

Median ≈ 35,300 tokens/min → **0.39 5 h points/min** and **0.11 weekly
points/min**. With the default 30-minute horizon that is **11.7 / 3.3 points**
per job before the safety factor.

Limits (all UNVERIFIED where stated):

- n=7, one account, one model family; no per-model split (finding 08 excluded it
  because `model_resolved` was null).
- Wall clock did not correlate with token rate (3.5× spread), so a per-minute
  rate carries large error.
- `max_minutes` is a ceiling, not an expected duration; using it directly would
  predict ~93 5 h points at the median rate for the default 240 minutes and
  block almost every job.
- `default_expected_minutes=30` is a judgement call, not measured.
- The estimate is a scheduling heuristic, never a factual claim about
  consumption; the measured delta remains authoritative after a run.

### 5. Open questions

| # | Question | Default to proceed with |
|---|---|---|
| Q1 | Does the permission policy need to be byte-identical per account, or only contain the required allow-rules? | Require the same required allow-rules and a parseable config; report drift, do not mutate |
| Q2 | Is a per-account agy binary ever needed? | No; one binary, many HOMEs |
| Q3 | Should `account` be accepted on `agy_start`? | Yes, optional override; still verified/filtered unless `force` |
| Q4 | Does `brain/` need copying for transfer? | Not for text recall; copy only the db, and fall back to a summary handoff if the db is missing/corrupt |
| Q5 | How to handle different model sets per account? | Verify the requested model is accepted by the account before launch; clear error, no silent substitution |
| Q6 | Do paused minutes count against `max_minutes`? | Read the code: `cumulative_minutes` adds run time only (Phase 2 confirms) |

## Evidence index

| Claim | Evidence |
|---|---|
| Nine real calls, four model-consuming | `evidence/real-agy-calls.md` |
| `/usage` rows and pools per account | `evidence/real-usage-rows.txt` |
| Fresh-HOME isolation | `evidence/isolation-fresh-home.txt` |
| Transfer, missing id, model rejection | `evidence/conversation-transfer.txt` |

## Sources

- `src/agy_mcp/usage.py` — parser, coefficients and cache.
- `src/agy_mcp/util.py` — child env builder.
- `src/agy_mcp/worktree.py` — permission inspection and HOME preflight.
- `tests/fake_agy/agy` — supported fake scenarios.
- `doc/findings-and-experiments/08-quota-calibration.md` — the seven-job calibration.
- `doc/audits/2026-11-03-multi-account/evidence/real-agy-calls.md` — call log.
- `doc/audits/2026-11-03-multi-account/evidence/real-usage-rows.txt` — usage rows.
- `doc/audits/2026-11-03-multi-account/evidence/isolation-fresh-home.txt` — isolation output.
- `doc/audits/2026-11-03-multi-account/evidence/conversation-transfer.txt` — transfer output.
