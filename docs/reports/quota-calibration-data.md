# Quota estimate calibration data (2026-10-31)

Calibration set for `compute_quota_estimate()` (`src/agy_mcp/usage.py`). Job data was
read only through the `agy_list` / `agy_status` MCP tools; no state files were opened.

## Why

The previous coefficients (`weekly_per_1k_tokens=0.00075`,
`five_hour_per_1k_tokens=0.0027`, `D83`) under-reported measured usage by roughly 4x on
the reference job `6d9be49b`. This document records the observations used to re-fit the
coefficients and the fit that was chosen.

## Method

Sample: every job in the wrapper's job store that carries **both** a stored
`quota_estimate` and a measured `usage_cost` (`measured=true`). The store held 339 job
records; only 7 had both. That is the whole population, not a sample of 40 — the other
jobs either never reached finalization with a live usage reading or were cancelled /
fixture rows.

* `tokens` = `tokens.total_tokens` from agy's JSON payload (input + output; it does not
  include `cache_read_tokens`).
* `wall clock s` = `wall_clock_seconds` from `agy_status` (sum of the job's runs).
* `est 5h` / `est weekly` = the stored `quota_estimate` values written by the old
  coefficients.
* `measured 5h` / `measured weekly` = `usage_cost.five_hour_pct_points` /
  `usage_cost.weekly_pct_points` (delta of `remaining_fraction` between the launch and
  finalization readings). This is the authoritative figure.
* Two rows carry `concurrent_jobs=2` (`35ce4421`, `3875253f`); their measured value is
  approximate by construction.

## Observations

| job id | tokens | wall clock s | est 5h | est weekly | measured 5h | measured weekly | measured/est 5h | measured/est weekly |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `6d9be49b` | 809,853 | 1375.575 | 2.1866 | 0.6074 | 8.67 | 2.48 | 3.97 | 4.08 |
| `d817bb06` | 216,849 | 382.533 | 0.5855 | 0.1626 | 1.66 | 0.47 | 2.84 | 2.89 |
| `9bdf04c7` | 212,403 | 450.471 | 0.5735 | 0.1593 | 1.78 | 0.51 | 3.10 | 3.20 |
| `35ce4421` | 236,639 | 212.345 | 0.6389 | 0.1775 | 2.36 | 0.68 | 3.69 | 3.83 |
| `fc1e5a76` | 170,216 | 303.411 | 0.4596 | 0.1277 | 1.52 | 0.43 | 3.31 | 3.37 |
| `3875253f` | 226,453 | 104.170 | 0.6114 | 0.1698 | 0.80 | 0.23 | 1.31 | 1.35 |
| `d9c76277` | 291,178 | 195.318 | 0.7862 | 0.2184 | 0.89 | 0.25 | 1.13 | 1.14 |

Derived quantities (% points per 1k tokens):

* 5-hour: min 0.003057, median 0.008380, max 0.010706 — a 3.5x spread.
* Weekly: min 0.000859, median 0.002401, max 0.003062 — the same spread.
* `measured weekly / measured 5h` is stable at 0.281–0.288 across all 7 jobs, so the
  two buckets are proportional and are driven by the same underlying consumption.

The old coefficients are inside the observed range but near its *bottom* (0.0027 vs
median 0.0084 for the 5-hour bucket), which is why the estimate reads about 4x low on
the big jobs.

## Fit

Candidate models, fitted on all 7 rows (`T` = `tokens / 1000`):

1. **Least squares through the origin** — `0.009305` (5h) / `0.002660` (weekly).
   Minimises error but *under*-estimates the reference job by 13% and the two
   low-ratio rows by 2–3x. Median absolute relative error 13.1%, worst 204%.
2. **Least squares with a per-job constant** (`a + b*T`) — the fit returns a
   *negative* intercept (`-1.13` 5h, `-0.325` weekly), which would make small jobs
   negative and is unusable. Rejected.
3. **Upper-envelope per-token coefficient** (max observed ratio, rounded up) —
   `0.011` (5h) / `0.0031` (weekly). Never under-estimates any observed job.
   Median absolute relative error 31.3% (all over-estimates), worst 260% (the two
   low-ratio rows).

**Chosen: model 3 (upper envelope), base = 0.0.** The data is sparse (n=7) and
inconsistent (3.5x spread, two approximate concurrent rows), so precision is not
available and is not claimed; under-estimating is the harmful direction for a quota
guard, so the conservative (over-estimating) coefficient was chosen. The output stays
explicitly labelled as an estimate and the measured `usage_cost` remains authoritative.
If accuracy is preferred over safety later, model 1 is the least-error alternative and
is recorded here for re-fitting.

## Constants to freeze

```
quota_estimate_five_hour_per_1k_tokens = 0.011   # % points per 1k tokens
quota_estimate_weekly_per_1k_tokens   = 0.0031  # % points per 1k tokens
quota_estimate_base_pct_points        = 0.0     # documented base (zero-token) value
quota_estimate_basis                  = "calibrated-2026-10-31, n=7"
```

Formula: `pct_points = base + tokens / 1000 * coefficient`, rounded to 4 decimals.

Result of the chosen coefficients (new estimate vs measured; `+` means the new
estimate is above the measurement, i.e. conservative):

| job id | new est 5h | new est weekly | new/measured 5h | new/measured weekly |
|---|---:|---:|---:|---:|
| `6d9be49b` | 8.9084 | 2.5105 | +2.7% | +1.2% |
| `d817bb06` | 2.3853 | 0.6722 | +43.7% | +43.0% |
| `9bdf04c7` | 2.3364 | 0.6584 | +31.3% | +29.1% |
| `35ce4421` | 2.6030 | 0.7336 | +10.3% | +7.9% |
| `fc1e5a76` | 1.8724 | 0.5277 | +23.2% | +22.7% |
| `3875253f` | 2.4910 | 0.7020 | +211.4% | +205.2% |
| `d9c76277` | 3.2030 | 0.9027 | +259.9% | +261.1% |

Fit error of the chosen model: median absolute relative error **31.3%** (5-hour) /
**29.1%** (weekly); worst **260%** (5-hour) / **261%** (weekly); rests on **7 jobs**.
No model tried fit better than ~13% median without under-estimating, and the two
`concurrent_jobs=2` rows are the dominant source of the residual spread.

## What is deliberately not modelled

* Wall clock: `measured/tokens` does not track `wall clock` (the highest-rate job,
  `35ce4421`, ran 212 s; the lowest-rate job, `d9c76277`, ran 195 s). A `tokens + wall
  clock` regression had a *worse* median error (36%) and was rejected.
* Cache reads: `cache_read_tokens` ranges from 45k to 21.9M. Adding a cache term
  improved the worst case to 97% but kept the median above 28% and is not explainable
  as a quota rule, so it is not used.
* Per-model pricing: `model_resolved` is `null` on every job, so a per-model coefficient
  cannot be fitted yet.
* Anything time-window dependent (e.g. quota replenishment during long jobs) — the
  measurement is a delta of a rolling reading, not an attributable meter.

## Data quality notes

* Only 7 of 339 stored jobs carry both fields; 312 of the 339 are cancelled/failed
  records created by this repo's own test fixtures (`F2 …`, `F9 …`), which pollute the
  store and the `agy_list` output.
* `usage_cost` is rounded to 2 decimals by `compute_usage_cost`, so each measurement
  carries up to ±0.005 percentage points of quantisation.
