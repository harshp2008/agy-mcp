# Why trivial jobs end in `needs_decision` — investigation (2026-10-31)

Read-only investigation. No wrapper code was changed for this task; proposed fixes are
listed at the end and are **not implemented**. Job data came only from the `agy_list`,
`agy_status` and `agy_result` MCP tools.

## Method and limits

* `agy_list` returned **339 job records**, all terminal (`total_active_jobs=0`):
  21 `done`, 272 `failed`, 40 `cancelled`, 5 `needs_review`, 1 `needs_decision`.
* Sample: the newest 40 records (by `elapsed_seconds`, ascending), then every record in
  `needs_decision` / `needs_review` regardless of age. Jobs whose title/branch or files
  point at clipsync-lab were excluded from the counts (see "Out of scope").
* Fields collected from the list payload: `state`, `title`, `elapsed_seconds`,
  `warnings`, `error`, `next_actions`, `facts` (verify/audit/plan/denials), `denials`,
  `verify.status`/`cause`, `scratchpad`, `agent_report`. `recent_log_tail` was fetched
  with `agy_status` for the `needs_decision` job only (it held a single unrelated
  `file_watcher.go` line).
* **Limits:** the store keeps only the *current* state; there is no state history, so a
  job that stopped for a decision and was later resumed/cancelled no longer shows
  `needs_decision`. `job_retention_days` is 14, so older decisions are pruned. Job
  `workdir` is not exposed by the tools, so the clipsync-lab exclusion uses title,
  branch and `files_modified` as the signal. **The observed population is therefore a
  lower bound on how often needs_decision happens, not a rate.**

## Sample composition

| group | count |
|---|---:|
| newest-40 sample: non-fixture jobs (`done` / `needs_decision`) | 7 |
| newest-40 sample: cancelled rows titled `F2 …` / `F9 …` | 33 |
| store-wide `needs_decision` | 1 |
| store-wide `needs_review` (all in the clipsync/meshcore repo — out of scope) | 5 |
| store-wide `failed` + `cancelled` rows that are this repo's test fixtures | 312 |

The 312 fixture rows are titled exactly like this repo's tests (`F2 Review Test`,
`F2 Recovery Test`, `F2 Corrupt Snapshot Test`, `F9 Resume Verify Test` — see
`tests/test_f2.py:38`, `tests/test_f9.py:64`), carry 0 commits, 0 tokens and no verify,
and every one is `cancelled` or `failed`. They dominate `agy_list` (761 KB of output
for one call) and make per-state counts noisy. How they reached the live state dir is
**not verified** — the tests in `tests/test_f2.py` use `base_state_dir=tmp_path`.

## Classification (in-scope store, n = 27 non-fixture jobs)

| bucket | count | conclusion |
|---|---:|---|
| frozen-test contradiction (legitimate stop) | 1 | observed — the only `needs_decision` |
| sandbox / verify denial | 0 | not observed |
| missing allowlisted command | 0 | not observed |
| agent asked a question it did not need to ask | 0 | not observed |
| ambiguous brief | 0 | not observed |
| wrapper bug | 0 | not observed for `needs_decision` |
| model refusal / confusion | 0 | not observed |
| unknown | 0 | — |
| `done` (control group) | 21 | completed without a stop |

The five `needs_review` records are **not** `needs_decision`, and all belong to the
out-of-scope meshcore/clipsync work (see below), so they are listed for comparison only.

## The one observed `needs_decision`

**Job `9bdf04c7-f585-4b39-b04f-a3f4498b0bcd` — "T7 agy-mcp-preset generator"**
(`needs_decision`, 1 commit, 3 files, plan 6/6, verify requested but `not_run`,
no denials, no warnings, wall clock 450 s, measured 1.78% / 0.51%).

* The agent implemented the generator, then stopped because it believed the frozen
  acceptance test was wrong: in `tests/acceptance/agy-mcp-preset/preset.test.mjs`
  `const STANDARD = presetRow(parseYaml(FIXTURE_TEXT));` was used afterwards as if it
  were `STANDARD.config`, so `STANDARD.order` / `STANDARD.plugins` would be undefined.
  It reported this under "Open issues" instead of touching the frozen test.
* **It was right — observed:** the test was fixed in `dsh-plugins` by commit
  `f341b16 "test: fix preset STANDARD extraction"`, authored at **00:38:34**; the job's
  last commit was **00:37:32**. The fix added the missing `.config`. A later job
  (`d817bb06`, "T7 fix drift and artifact") then completed the same work.
* **Bucket:** frozen-test contradiction, i.e. the *intended* behaviour of the standing
  rule "if a frozen test contradicts the spec, stop and report". This stop was correct
  and cheap to recover from (one resume/redo). It is not evidence of wrapper
  misbehaviour.

## Why a *trivial* job could still stop there (mechanisms; guesses, not observed)

No trivial job in the store actually did. These are the code paths that would make it
possible, so they are worth closing before they bite:

1. **The decision marker is open-ended.** `src/agy_mcp/preamble.md:9-10` (rule 7) tells
   agy to stop for "an irreversible or costly decision" — no list, no examples, and
   nothing that says "reversible ⇒ decide it yourself and record it under Assumptions".
   An agent that is unsure about anything can rationalise a stop. Rule 8 nominally
   points the other way but is much weaker than rule 7.
2. **A frozen-test contradiction has no non-blocking instruction.** The marker is the
   only sanctioned way to report one, yet the agent does not need a decision to
   continue: the test is not its to fix, and leaving it untouched plus a report line
   would surface the contradiction at verify time anyway. `9bdf04c7` shows the cost:
   a stop plus a full re-run of the task.
3. **The denial path explicitly pushes agents to stop.** `src/agy_mcp/preamble.md:13`
   (rule 10) and the auto-resume guidance in `src/agy_mcp/manager.py:737` tell the agent
   to end with `NEEDS_DECISION: need command …` when it thinks a shell command is
   needed. Since shell is never available, a trivial job that wants `npm install`,
   `git status` or `ls` has a sanctioned way to stop instead of using file tools.
4. **Detection is a single line match.** `src/agy_mcp/parse_result.py:270-276` only
   looks at the last non-empty line for `^NEEDS_DECISION:`; an agent that writes the
   marker mid-report is *not* stopped (so some "asked a question" cases may end up as
   `failed`/`done` instead — another reason the observed count is a lower bound).
5. **The question is not in the status envelope.** `decision_prompt` is parsed
   (`parse_result.py:276`) but only stored in an audit event
   (`supervisor.py:1003-1004`); `agy_status` returns
   `next_actions=["Resume job with decision using agy_resume", …]` without the question
   (`models.py:462`). An orchestrator must call `agy_result` to see what is being asked,
   which makes a decision round-trip look more expensive than it is.

## Out of scope: the five `needs_review` records

All five touch `clipsync/` and/or `meshcore/` paths (a different repository), so they
are excluded from the counts and are **not** analysed as clipsync-lab work. Their stop
reasons are generic wrapper behaviour and are listed for comparison:

| job | reason | detail |
|---|---|---|
| `16f0bd05` | verify timeout | `python3 -m pytest -q` killed after 900 s (rc 124), 2 attempts |
| `0f813431` | verify timeout + `plan_incomplete` | `pytest tests/acceptance/test_meshcore_lan.py` killed after 900 s; plan 6/9 |
| `4e1b3b79` | verify failure | rc 124, 1 attempt |
| `14186961` | review needed | verify not run; empty agent report |
| `cecc329b` | review needed | verify not run; empty agent report |

The verify-timeout pattern (3 of 5) is a real friction source, but it produces
`needs_review`, not `needs_decision`.

## Proposals, ranked by expected benefit / effort

| rank | proposal | bucket it fixes | effort | benefit |
|---|---|---|---|---|
| 1 | Replace preamble rule 7's open-ended trigger with a **closed list** (data loss, credentials/network, push/force, files outside the repo, spending money, installing packages) plus "everything reversible: decide it yourself and record it under Assumptions". Add one sentence: *a frozen test that contradicts the spec is not a decision — leave it untouched and note it in the report; verify will surface it.* | unnecessary stops, frozen-test stops | small (one file, `preamble.md`) | high — closes the only observed trigger and the denial path |
| 2 | Surface `decision_prompt` in `agy_status` (new additive field or merged into `next_actions`) so a stop is answerable without `agy_result`. | decision round-trip cost | small (`models.py`, `manager.py`, SPEC) | medium |
| 3 | Soften the rule-10 / auto-resume denial text: name the built-in file tools first and make `NEEDS_DECISION: need command` a last resort, not the scripted answer. | missing allowlisted command | small (`preamble.md`, `manager.py:737`) | medium |
| 4 | Stop test fixtures from reaching the live job store, and/or filter them from `agy_list` (origin/state-dir tag or pagination). 312 of 339 records are fixtures; `agy_list` output is 761 KB per call. | data hygiene, not a stop cause | medium | medium-high for tool usability |
| 5 | Raise `verify_timeout_seconds` for suites known to be slow, or make the timeout message name the hanging test file (it currently names the command only). | verify timeouts (`needs_review`) | small-medium | medium |

## Evidence

* `agy_list` (2026-10-31): 339 records, states as above; 312 records with fixture
  titles; 7 records with both `quota_estimate` and measured `usage_cost`.
* `agy_status 9bdf04c7-…`: `state=needs_decision`, `verify.status=not_run`,
  `facts.plan={"total":6,"done":6}`, `denials={}`, `warnings=[]`,
  `recent_log_tail=["E1031 04:35:52.907364 54 file_watcher.go:194] skipping empty or temp file:"]`.
* `git -C ~/work/dsh-plugins log -- tests/acceptance/agy-mcp-preset/preset.test.mjs`:
  `f341b16 test: fix preset STANDARD extraction` at 2026-10-31 00:38:34 +0530.
