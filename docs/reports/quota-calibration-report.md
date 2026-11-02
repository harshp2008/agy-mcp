# Quota estimate calibration — report (2026-10-31)

Branch `fix/quota-estimate`, created from `main` (`d4908de`, clean) and **not merged**.
Harsh merges. Task B report is on the same branch.

## Result in one paragraph

`quota_estimate` was under-reporting measured usage by ~4x because both per-token
coefficients were near the bottom of the observed range. It is now calibrated from the
7 jobs that carry both a stored estimate and a measured delta: **0.011 %/1k tokens
(5-hour)** and **0.0031 %/1k tokens (weekly)**, base `0.0`, with a
`quota_estimate_basis` label, `null` when no token total can be determined, and an
`input_tokens + output_tokens` fallback when a payload omits `total_tokens`. The
coefficients are deliberately conservative (upper envelope): the estimate no longer
under-reports any measured job, at the price of over-estimating the two lowest-rate
jobs (median absolute error 31.3%, worst 260%). It stays labelled as an estimate and
the measured `usage_cost` remains authoritative.

## Task A — where the old model lived

* `src/agy_mcp/usage.py::compute_quota_estimate` — formula `total/1000 * coefficient`,
  defaults `weekly_per_1k_tokens=0.00075`, `five_hour_per_1k_tokens=0.0027`; no base, no
  wall-clock term.
* The same two defaults were duplicated in `src/agy_mcp/config.py` (Config) and
  `src/agy_mcp/supervisor.py` (`JobSupervisor.__init__`), and copied into `SPEC.md`
  (D83 paragraph + config table).
* The estimate never replaced the measured delta, so the bug was visible but harmless
  for guards; it made the wrapper's own number useless for planning.

## Task A — data and fit

Full table, method and candidate models: `docs/reports/quota-calibration-data.md`.

* Population: **7** stored jobs with both `quota_estimate` and
  `usage_cost.measured=true` (the entire store, not a 40-job sample). Two of the seven
  are flagged `concurrent_jobs=2` and are approximate by construction.
* Observed rate: **0.003057 – 0.010706** %/k (5-hour) and **0.000859 – 0.003062** %/k
  (weekly) — a 3.5x spread. `measured weekly / measured 5h` is stable at 0.281–0.288,
  so the buckets share one underlying consumption.
* **Least squares through the origin** (`0.009305` / `0.002660`): median absolute
  relative error 13.1%, worst 204% — it under-estimates the reference job by 13% and
  the two low-rate jobs by 2–3x. Rejected.
* **Least squares with a per-job constant**: negative intercept (`-1.13` / `-0.325`),
  unusable (negative estimates for small jobs). Rejected.
* **Upper envelope** (max observed rate, rounded up): `0.011` / `0.0031`. Chosen because
  under-estimating is the harmful direction for a quota guard. Median absolute relative
  error **31.3%** (5-hour) / **29.1%** (weekly), worst **260% / 261%**, all over-estimates,
  resting on **n = 7**.

The data is too sparse and inconsistent for precision; that is stated in the data doc
and in the code comment. Nothing in the output claims precision.

### Before / after — the seven fitted jobs and one post-hoc job

`before` is the estimate stored on the job (old coefficients); `after` is the new
formula; percentages are `estimate/measured − 1`, so negative = under-report.

| job | tokens | measured 5h / weekly | before 5h / weekly | after 5h / weekly |
|---|---:|---|---|---|
| `6d9be49b` | 809,853 | 8.67 / 2.48 | 2.1866 / 0.6074 (−74.8% / −75.5%) | 8.9084 / 2.5105 (+2.7% / +1.2%) |
| `d817bb06` | 216,849 | 1.66 / 0.47 | 0.5855 / 0.1626 (−64.7% / −65.4%) | 2.3853 / 0.6722 (+43.7% / +43.0%) |
| `9bdf04c7` | 212,403 | 1.78 / 0.51 | 0.5735 / 0.1593 (−67.8% / −68.8%) | 2.3364 / 0.6584 (+31.3% / +29.1%) |
| `fc1e5a76` | 170,216 | 1.52 / 0.43 | 0.4596 / 0.1277 (−69.8% / −70.3%) | 1.8724 / 0.5277 (+23.2% / +22.7%) |
| `35ce4421` | 236,639 | 2.36 / 0.68 | 0.6389 / 0.1775 (−72.9% / −73.9%) | 2.6030 / 0.7336 (+10.3% / +7.9%) |
| `3875253f` | 226,453 | 0.80 / 0.23 | 0.6114 / 0.1698 (−23.6% / −26.2%) | 2.4910 / 0.7020 (+211.4% / +205.2%) |
| `d9c76277` | 291,178 | 0.89 / 0.25 | 0.7862 / 0.2184 (−11.7% / −12.6%) | 3.2030 / 0.9027 (+259.9% / +261.1%) |
| `04be9d3d`* | 340,500 | 3.07 / 0.88 | 0.9194 / 0.2554 (−70.1% / −71.0%) | 3.7455 / 1.0555 (+22.0% / +19.9%) |

\* post-hoc: the agy implementation job itself, completed after the fit was frozen. It
was not used to fit the coefficients; it lands +22% / +20% over, inside the intended
conservative band.

Old model: median absolute error **67.8% / 68.8%**, worst **74.8% / 75.5%**, always
under. New model: median **31.3% / 29.1%**, worst **260% / 261%**, always over.

## Task A — new behaviour (normative text in SPEC.md, D85)

* `compute_quota_estimate(tokens, weekly_per_1k_tokens=0.0031, five_hour_per_1k_tokens=0.011) -> Optional[dict]`.
* `points = round(base + tokens/1000 * coefficient, 4)`, `base = 0.0`; coefficients
  clamped to `>= 0`; never negative; never raises.
* A usable `total_tokens` wins. A missing / zero / non-numeric / negative total falls
  back to `input_tokens + output_tokens` when both are present (this path is reachable
  because `TokenUsage.from_dict` materializes an absent total as `0`). If no total can
  be determined, the function returns `None` → `quota_estimate: null`.
* Dict keys are the seven D83 keys plus `quota_estimate_basis`
  (`"calibrated-2026-10-31, n=7"`), inside the `quota_estimate` dict. No new top-level
  status field, so the MCP tool inputSchema is unchanged (`server.py` diff = 0 lines).
* Stored estimates of old jobs are never rewritten; `usage_cost` is never touched by
  the estimate code.
* Constants live in `Config` with a comment pointing at
  `docs/reports/quota-calibration-data.md` and the 2026-10-31 calibration date, so they
  can be re-fitted later.

## Task B — needs_decision investigation

`docs/reports/needs-decision-investigation.md` (committed `c4fb4fb`). Headlines:

* The store holds **one** `needs_decision` record (`9bdf04c7`, "T7 agy-mcp-preset
  generator"), and it was a **legitimate** stop: the agent refused to edit a frozen
  acceptance test that was genuinely broken. Evidence: `dsh-plugins` commit `f341b16`
  "test: fix preset STANDARD extraction" landed at **00:38:34**, 62 s after the job's
  last commit (**00:37:32**) — the test was fixed *after* the stop.
* No trivial job in the store stopped for a decision; the other five `needs_review`
  records belong to the clipsync/meshcore repo and were excluded.
* The mechanisms that could make a trivial job stop are in the wrapper, not the model:
  an open-ended "irreversible or costly" trigger (`preamble.md:9-10`), a frozen-test
  contradiction having no non-blocking instruction, and a denial path that scripts
  `NEEDS_DECISION: need command` (`preamble.md:13`, `manager.py:737`). `decision_prompt`
  is parsed but only stored in an audit event, so `agy_status` does not show the
  question (`supervisor.py:1003`, `models.py:462`).
* Five ranked proposals (narrow the marker to a closed list; surface `decision_prompt`;
  soften the denial text; isolate test fixture rows from the live job store; verify
  timeouts) are listed with effort/benefit; none implemented.
* Data-quality note observed in passing: **312 of 339** job records are this repo's own
  test fixtures (`F2`/`F9` titles), which is why `agy_list` returns 761 KB.

## Frozen tests and test counts

* Frozen contract: `tests/test_quota_calibration.py`, **19 tests**,
  sha256 `1f3e54c3395475d57e3365289cfe3e61f0e9cac46e79096b7ae5fc54961136e7`,
  committed in **`5041563` "test: frozen tests for calibrated quota estimate"**.
  Also in that commit: `tests/test_h27_rootcause_repro.py` had one assertion that
  pinned the old `0.00075` coefficient; its expected value was updated to the new
  constant, as disclosed in the commit message.
* Freeze-run evidence (freeze commit, before implementation): `15 failed, 772 passed`
  — exactly the 14 new tests that encode the new behaviour plus the one updated
  constant assertion. Failure reasons were the right ones (old coefficients, missing
  `quota_estimate_basis`, a dict instead of `None` for unknown counts).
* **Baseline:** 768 passing (given); 768 + 19 = 787, matching the freeze-run total
  (772 + 15). I did not get a clean 768-passing run of the pristine tree: a first
  attempt was voided by my own untracked report file tripping
  `tests/conftest.py::pytest_sessionfinish` (repository-clean check); the arithmetic
  above is exact.
* After the review repair, 4 more tests live in
  `tests/test_quota_estimate_fallback.py` (not part of the frozen contract).
* **Total on the branch: 791 tests** (768 baseline + 19 frozen + 4 regression), all
  passing.

## The agy job

* id **`04be9d3d-cf6c-490b-a3e3-a5ebf9c4a288`**, title "calibrated quota estimate (D85)",
  branch `agy/calibrated-quota-estimate-d85-386977a0`, based on `c4fb4fb`, 3 commits,
  `state=done`, audit passed, **0 denials**, plan 5/5.
* Wrapper verify (`pytest tests/test_quota_calibration.py tests/test_h27_rootcause_repro.py -q`):
  20 passed in 1.72 s, 1 attempt.
* **wall_clock_seconds = 582.845 s** (one run, 04:15:54 → 04:25:37 UTC);
  `elapsed_seconds = 651.34`.
* Tokens: 340,500 total (input 297,773, output 42,727, thinking 24,186,
  cache_read 7,154,608).
* **Measured `usage_cost` = 3.07% (5h) / 0.88% (weekly)**, `concurrent_jobs=1`.
  The wrapper stored its own `quota_estimate` for this job with the old coefficients:
  0.9194 / 0.2554 — i.e. **3.3x / 3.5x under** the freshly measured value.
* Self-report was used only for the commit/file list; every claim was checked against
  the diff and the tools.

## Gate (scripted, `gate-run.mjs`)

| check | command | result |
|---|---|---|
| frozen files unchanged | `git diff --stat 5041563 HEAD -- tests/` (empty) + `sha256sum tests/test_quota_calibration.py` | identical `1f3e54c3…` |
| new tests, 20x | `node …/gate-run.mjs --log-dir ~/gate-logs/quota-calib/new --repeat 20 -- env -u PYTHONPATH .venv/bin/python -m pytest tests/test_quota_calibration.py tests/test_quota_estimate_fallback.py tests/test_h27_rootcause_repro.py -q` | **ALL 20 RUNS PASSED** |
| full suite, 5x | `node …/gate-run.mjs --log-dir ~/gate-logs/quota-calib/full --repeat 5 -- env -u PYTHONPATH .venv/bin/python -m pytest -q` | **5/5 PASS, 791 passed per run** |
| ruff | `.venv/bin/ruff check src tests scripts` | All checks passed |
| scope | `git diff --name-only d4908de HEAD` | 12 files: `src/agy_mcp/{config,supervisor,usage}.py`, `SPEC.md`, `CHANGELOG.md`, `docs/{CODEMAP,DECISIONS}.md`, `docs/reports/{quota-calibration-data,quota-calibration-report,needs-decision-investigation}.md`, `tests/test_quota_calibration.py`, `tests/test_quota_estimate_fallback.py`, `tests/test_h27_rootcause_repro.py` |
| codemap | `scripts/gen_codemap_index.py --write` | regenerated by me after the agy commits (`usage.py` 877→917, `config.py` 334→335) |

The `tests/` entries are the freeze commit (`5041563`) and the post-review regression
file; the agy job touched no test. One gate attempt was run before the review repair
(5/5 PASS, 787 tests); the gate table above is the **post-repair** run.

## Independent review

Fresh subagent, given only the spec docs, the frozen tests and `git diff d4908de..HEAD`
(repo read access). It was not given the agy self-report or any gate verdict. Five
findings, each checked by me with its own command:

| # | severity | finding | check result | disposition |
|---|---|---|---|---|
| 1 | major | `TokenUsage.from_dict` materializes an absent `total_tokens` as 0, so the documented input+output fallback was dead in the supervisor path and such a payload estimated 0.0 | `_extract_tokens({"usage":{input:400,output:100}}).to_dict()` → `total_tokens=0`, estimate 0.0 (not 500) | **confirmed, fixed** (`c68bd75`); now 500 → 0.0055 |
| 2 | minor | a negative `total_tokens` with valid counters falls back to their sum instead of `None` | `compute_quota_estimate({total:-5, input:400, output:100})` → tokens 500 | **not a code defect**: this is the documented fallback rule; SPEC/D85 wording was ambiguous and was tightened in `c68bd75` to say so |
| 3 | minor | `docs/DECISIONS.md:524` (D83 rule 3) still states the live defaults are 0.00075/0.0027 with no superseded note | `grep -n "default 0.00075" docs/DECISIONS.md` → hit | **confirmed, fixed** in `c68bd75` (annotated superseded by D85) |
| 4 | nit | D85's `*Tests:*` listed `tests/test_config.py`, which has no quota assertion | `grep -c quota_estimate tests/test_config.py` → 0 | **confirmed, fixed** in `c68bd75` |
| 5 | nit | the Task B report sits inside the reviewed diff | `git log d4908de..HEAD -- docs/reports/needs-decision-investigation.md` → `c4fb4fb` | **not a defect**: Task B explicitly requires it on this branch |

Repair round 1 of at most 2. The reviewer also independently reported
`pytest -q` → 787 passed on the pre-repair tree and ruff clean.

## Did I call the quota tool?

Yes, **exactly once** (`agy_usage`, before starting the job) and it was not called
again, per the one-call limit.

* Before: Gemini Models — weekly **49.43%** remaining (resets 2026-11-01T09:54:57Z),
  5-hour **100.00%** (resets 2026-10-31T08:39:42Z); Claude and GPT models — weekly
  100%, 5-hour 100%. Account masked (`<user-email>`).
* After (derived, **not** a second query): subtracting the job's measured delta gives
  weekly ≈ **48.55%** and 5-hour ≈ **96.93%** at finalization. Treat those as derived
  from `usage_cost`, not as a reading.

## Not verified / open

* The measured deltas are the wrapper's own `/usage` readings; I did not verify the
  account-side meter independently. The two `concurrent_jobs=2` rows are approximate.
* The calibrated coefficients are a **conservative** fit on 7 jobs (8 with the post-hoc
  job). They are not a prediction of the next job's cost; the worst observed
  over-estimate is 2.6x. A re-fit is expected once more non-concurrent jobs exist.
* The `no-total_tokens` fallback has no test in the frozen contract (it was found by
  the review after the freeze); it is covered by
  `tests/test_quota_estimate_fallback.py` instead.
* The 768 baseline is Harsh's figure; my direct observations are the freeze run
  (772 passed / 15 failed) and the branch runs (787 pre-repair, 791 post-repair).
* Task B conclusions are marked observed vs guess in its own document; the wrapper
  fixes were **not** implemented (out of scope for this task).
* `agy_cleanup` was run for the job worktree; the job branch
  `agy/calibrated-quota-estimate-d85-386977a0` is kept (cleanup ran with
  `delete_branch=false`).

## Exact merge commands

```bash
cd "~/Documents/GITHUB PROJECTS/agy-mcp"
git checkout main
git merge --no-ff fix/quota-estimate -m "merge: calibrated quota estimate (D85)"
# optional, after the merge:
git branch -d fix/quota-estimate
git branch -D agy/calibrated-quota-estimate-d85-386977a0
```

Nothing has been merged or pushed. `main` is untouched; the work is on
`fix/quota-estimate` only.
