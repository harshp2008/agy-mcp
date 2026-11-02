# 10 — Investigation of NEEDS_DECISION State Stops

Status: current
Audience: maintainers, contributors

Empirical analysis of the `needs_decision` job state in `agy-mcp`, examining why
autonomous jobs stop for operator intervention, classifying store-wide stops
across 27 in-scope jobs, analyzing the one observed incident, and evaluating
mechanisms that could induce unnecessary stops.

## Question

Why do autonomous jobs transition to the `needs_decision` state, what real-world
conditions trigger it, and how can the wrapper distinguish legitimate stops from
unnecessary stalls?

## Method

A read-only investigation was conducted on 2026-10-31 using the `agy_list`,
`agy_status`, and `agy_result` MCP tools. The investigation surveyed all 339
job records stored in the live state store:
- `total_active_jobs`: 0
- State distribution: 21 `done`, 272 `failed`, 40 `cancelled`, 5 `needs_review`, 1 `needs_decision`.

From this population:
1. The newest 40 records (ordered ascending by `elapsed_seconds`) were sampled.
2. Every record in `needs_decision` and `needs_review` was evaluated regardless of age.
3. Test fixture records (312 total rows titled `F2 ...` or `F9 ...` with 0 commits, 0 tokens, and no verify) and 5 `needs_review` jobs belonging to an external repository (`clipsync`/`meshcore`) were segmented out.
4. The remaining 27 non-fixture jobs formed the in-scope analysis set.

### Measurement Limits

- **Absence of state transition history:** The state store preserves only the current state; jobs that stopped in `needs_decision` but were subsequently resumed or cancelled do not reflect that stop.
- **Retention pruning:** The store prunes terminal jobs older than `job_retention_days = 14`.
- **Workdir visibility:** Tool responses omit the repository workdir; user repository classification was inferred from `title`, `branch`, and `files_modified`.
- **Lower bound:** Because historical decisions that resumed are lost, the observed population represents a lower bound on the frequency of `needs_decision`, not a rate.

## Result

### Sample Composition and Classification

| Group / Category | Count | Classification / Notes |
|---|---:|---|
| Store-wide total records | 339 | All terminal (`total_active_jobs=0`) |
| Test fixture rows (`failed` + `cancelled`) | 312 | Leaked pytest fixtures (`F2 ...` / `F9 ...`) |
| Out-of-scope `needs_review` jobs | 5 | All clipsync/meshcore repository jobs |
| In-scope non-fixture jobs | 27 | Evaluated population |
| Completed without stop (`done`) | 21 | Control group |
| Frozen-test contradiction | 1 | Observed stop (`9bdf04c7`) |
| Sandbox / verify denial | 0 | Not observed |
| Missing allowlisted command | 0 | Not observed |
| Unnecessary agent question | 0 | Not observed |
| Ambiguous brief | 0 | Not observed |
| Wrapper bug | 0 | Not observed for `needs_decision` |
| Model refusal / confusion | 0 | Not observed |

### The Observed `needs_decision` Job

Exactly one job in the store exhibited the `needs_decision` state:
- **Job ID:** `9bdf04c7-f585-4b39-b04f-a3f4498b0bcd`
- **Title:** `"T7 agy-mcp-preset generator"`
- **Metrics:** 1 commit, 3 files, plan 6/6, verify requested but `not_run`, no denials, no warnings, wall clock 450s, measured quota delta 1.78% (5-hour) / 0.51% (weekly).
- **Incident details:** The agent implemented the preset generator, but stopped when it detected that the frozen acceptance test (`tests/acceptance/agy-mcp-preset/preset.test.mjs`) contained a structural bug: `const STANDARD = presetRow(parseYaml(FIXTURE_TEXT));` was referenced as if it were `STANDARD.config`, causing `STANDARD.order` and `STANDARD.plugins` to evaluate to `undefined`. Obeying standing instructions not to edit frozen acceptance tests, the agent halted and reported the contradiction under "Open issues".
- **Corroboration:** The agent's deduction was correct. The acceptance test was amended in the external repository (`~/work/dsh-plugins`) by commit `f341b16` (`"test: fix preset STANDARD extraction"`) authored at 00:38:34. The job's final commit had been created at 00:37:32. A subsequent job (`d817bb06`, `"T7 fix drift and artifact"`) was resumed and completed the work.
- **Verdict:** Legitimate stop conforming to the prompt directive that agents must halt when a frozen test contradicts the specification.

### Out-of-Scope `needs_review` Comparisons

For comparison, the five `needs_review` jobs in the store were examined:
- `16f0bd05`: verify timeout (`python3 -m pytest -q` killed after 900s, rc 124, 2 attempts)
- `0f813431`: verify timeout + `plan_incomplete` (`pytest tests/acceptance/test_meshcore_lan.py` killed after 900s, rc 124; plan 6/9)
- `4e1b3b79`: verify failure (rc 124, 1 attempt)
- `14186961`: review needed (verify not run; empty agent report)
- `cecc329b`: review needed (verify not run; empty agent report)

Verify timeouts accounted for 3 of 5 stops, but all routed to `needs_review`, not `needs_decision`.

### Potential Failure Mechanisms (Hypothetical Guesses)

Although no trivial job stopped unnecessarily in the evaluated population, five code paths could induce false stops:
1. **Open-ended decision trigger:** `src/agy_mcp/preamble.md:9-10` (Rule 7) instructs agents to stop on "an irreversible or costly decision" without concrete examples or explicit permission to assume reasonable defaults for reversible choices.
2. **Lack of non-blocking path for test contradictions:** The decision stop was the only prescribed mechanism to surface a frozen-test error, even though recording an issue in the report and proceeding to verification would surface the contradiction without stalling.
3. **Denial steering:** Rule 10 in `src/agy_mcp/preamble.md:13` and auto-resume guidance in `src/agy_mcp/manager.py:737` tell agents to request `NEEDS_DECISION: need command ...` when shell execution is denied, creating a sanctioned path to stop rather than using file tools.
4. **Single-line detection boundary:** `src/agy_mcp/parse_result.py:270-276` matches `^NEEDS_DECISION:` only on the last non-empty line; mid-report questions are ignored.
5. **Envelope visibility gap:** `decision_prompt` is recorded in audit logs (`supervisor.py:1003-1004`) but omitted from `agy_status` envelopes (`models.py:462`), forcing orchestrators to call `agy_result` to read the prompt.

## Conclusion

The investigation confirmed that the wrapper's `needs_decision` trigger operates safely and that the sole observed incident was a legitimate pause triggered by a defective acceptance test. Recommended structural improvements include:
1. Tightening `preamble.md` Rule 7 to define an explicit closed list of irreversible actions (data loss, credentials, spending money, destructive git commands) and instructing agents to document assumptions for reversible actions.
2. Adding `decision_prompt` directly to the `agy_status` envelope to eliminate the extra round-trip to `agy_result`.
3. Advising agents encountering frozen-test discrepancies to record findings in the report and permit verification to run.

## Evidence

| Claim | Repo file and line or report path |
|---|---|
| Investigation date (2026-10-31) | `docs/reports/needs-decision-investigation.md:1` |
| Store-wide total job count (339 records, total active jobs 0) | `docs/reports/needs-decision-investigation.md:8` |
| Store-wide state distribution (21 done, 272 failed, 40 cancelled, 5 needs_review, 1 needs_decision) | `docs/reports/needs-decision-investigation.md:9` |
| Job retention threshold (14 days) | `docs/reports/needs-decision-investigation.md:19` |
| Newest-40 sample composition (7 non-fixture, 33 fixture cancelled rows) | `docs/reports/needs-decision-investigation.md:27-28` |
| Total test fixture rows polluting the store (312 records) | `docs/reports/needs-decision-investigation.md:31` |
| Evaluated in-scope population size (27 non-fixture jobs) | `docs/reports/needs-decision-investigation.md:39` |
| Control group completed without stops (21 done) | `docs/reports/needs-decision-investigation.md:49` |
| Observed `needs_decision` job ID (`9bdf04c7-f585-4b39-b04f-a3f4498b0bcd`) | `docs/reports/needs-decision-investigation.md:55` |
| Observed job metrics (1 commit, 3 files, plan 6/6, verify not_run, 450s, 1.78% / 0.51%) | `docs/reports/needs-decision-investigation.md:55-57` |
| Observed job last commit timestamp (`00:37:32`) | `docs/reports/needs-decision-investigation.md:63` |
| External acceptance test fix commit `f341b16` authored timestamp (`00:38:34`) | `docs/reports/needs-decision-investigation.md:62-63` |
| Recent log tail excerpt for observed job (`file_watcher.go:194`) | `docs/reports/needs-decision-investigation.md:125` |
| Verify timeout durations on comparison jobs (900s, rc 124) | `docs/reports/needs-decision-investigation.md:92-93` |
| Preamble Rule 7 decision stop directive | `src/agy_mcp/preamble.md:9-10` |
| Preamble Rule 10 shell denial guidance | `src/agy_mcp/preamble.md:13` |
| Auto-resume denial guidance in manager | `src/agy_mcp/manager.py:737` |
| `NEEDS_DECISION` regex parser matching logic | `src/agy_mcp/parse_result.py:270-276` |

## How to reproduce

The investigation analyzed production state records via stdio MCP tools. To inspect a job status envelope:
```bash
python -m agy_mcp --state-dir ~/.local/state/agy-mcp
```
Invoke tool `agy_status` with `{"job_id": "<job_id>"}`.

## Sources

- `docs/reports/needs-decision-investigation.md`
- `src/agy_mcp/preamble.md`
- `src/agy_mcp/parse_result.py`
- `src/agy_mcp/manager.py`
- `doc/design/decisions.md`
