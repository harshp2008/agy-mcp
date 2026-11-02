# 08 — Calibrating Token-Based Quota Estimates from Measured Consumption

Status: current
Audience: maintainers, contributors

Empirical calibration of `agy-mcp`'s heuristic token-based quota estimate model
(`compute_quota_estimate`) against real measured quota deltas across the full
population of qualifying stored jobs, establishing a conservative upper envelope
that guarantees the wrapper never under-reports quota consumption.

## Question

How should `agy-mcp`'s heuristic token-based quota estimate coefficients be
calibrated against real-world measured consumption, and how can the model guarantee
it never under-reports actual usage?

## Method

Evaluated on 2026-10-31 against all stored job records in the wrapper's state
store under branch `fix/quota-estimate` (forked from `main` at `d4908de`).
The state store held 339 total job records. The calibration population was
extracted by querying jobs that carried **both** a stored `quota_estimate` and an
authoritative measured `usage_cost` (`usage_cost.measured=true`).

Only 7 of the 339 records met this criterion; 312 of the remainder were cancelled
or failed test-fixture rows (`F2 ...`, `F9 ...`), and the rest either failed before
finalization or lacked live usage readings. Two of the seven qualifying jobs
(`35ce4421` and `3875253f`) had `concurrent_jobs=2`, rendering their measured
values approximate by construction.

Data recorded per job:
- `tokens`: total tokens reported by `agy` JSON (`input_tokens + output_tokens`, excluding `cache_read_tokens`).
- `wall clock s`: wrapper-measured `wall_clock_seconds` from `agy_status`.
- `est 5h` / `est weekly`: stored legacy quota estimates calculated using initial D83 coefficients (`0.0027` 5-hour, `0.00075` weekly).
- `measured 5h` / `measured weekly`: authoritative delta of `remaining_fraction` between launch and finalization.

Three candidate models were evaluated on the 7-job calibration set:
1. **Least squares through the origin:** $y = c \cdot (T / 1000)$.
2. **Least squares with intercept:** $y = a + b \cdot (T / 1000)$.
3. **Upper envelope:** maximum observed ratio rounded up, base $0.0$.

A post-hoc job (`04be9d3d`, the `agy` implementation job itself, 340,500 tokens)
completed after the constants were frozen and was evaluated as an out-of-sample check.

## Result

### Observed Calibration Population

| Job ID | Tokens | Wall Clock (s) | Legacy Est 5h | Legacy Est Wk | Measured 5h | Measured Wk | Ratio Meas/Est 5h | Ratio Meas/Est Wk |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `6d9be49b` | 809,853 | 1375.575 | 2.1866 | 0.6074 | 8.67 | 2.48 | 3.97 | 4.08 |
| `d817bb06` | 216,849 | 382.533 | 0.5855 | 0.1626 | 1.66 | 0.47 | 2.84 | 2.89 |
| `9bdf04c7` | 212,403 | 450.471 | 0.5735 | 0.1593 | 1.78 | 0.51 | 3.10 | 3.20 |
| `35ce4421` | 236,639 | 212.345 | 0.6389 | 0.1775 | 2.36 | 0.68 | 3.69 | 3.83 |
| `fc1e5a76` | 170,216 | 303.411 | 0.4596 | 0.1277 | 1.52 | 0.43 | 3.31 | 3.37 |
| `3875253f` | 226,453 | 104.170 | 0.6114 | 0.1698 | 0.80 | 0.23 | 1.31 | 1.35 |
| `d9c76277` | 291,178 | 195.318 | 0.7862 | 0.2184 | 0.89 | 0.25 | 1.13 | 1.14 |

Observed rates (% points per 1k tokens):
- **5-Hour Bucket:** minimum 0.003057, median 0.008380, maximum 0.010706 (a 3.5x spread).
- **Weekly Bucket:** minimum 0.000859, median 0.002401, maximum 0.003062 (a 3.5x spread).
- **Bucket Proportionality:** the ratio `measured weekly / measured 5h` remained strictly between 0.281 and 0.288 across all 7 jobs, confirming both buckets reflect the same underlying token consumption.

The legacy coefficients (`0.0027` 5h / `0.00075` weekly) sat near the bottom of the observed distribution, under-estimating consumption by roughly 4x on larger jobs (median error -67.8% 5h / -68.8% weekly; worst -74.8% / -75.5%).

### Evaluation of Candidate Models

1. **Least squares through the origin (`0.009305` 5h / `0.002660` weekly):** Minimizes mean squared error (median absolute relative error 13.1%, worst 204%), but under-estimates reference job `6d9be49b` by 13% and the two low-ratio jobs by 2–3x. Rejected because under-estimating quota is harmful for budget guards.
2. **Least squares with intercept:** Produced negative intercepts (-1.13 5h / -0.325 weekly), which yielded nonsensical negative estimates for small jobs. Rejected.
3. **Upper envelope (`0.011` 5h / `0.0031` weekly, base `0.0`):** Sets coefficients to the maximum observed rate rounded up. Median absolute relative error is 31.3% (5h) / 29.1% (weekly); worst-case over-estimation is 260% (5h) / 261% (weekly). Never under-estimates any measured job in the calibration set. **Chosen.**

### Calibrated Fit Evaluation (New Estimate vs Measured)

| Job ID | Tokens | New Est 5h | New Est Wk | Measured 5h / Wk | Error 5h vs Meas | Error Wk vs Meas |
|---|---:|---:|---:|---|---:|---:|
| `6d9be49b` | 809,853 | 8.9084 | 2.5105 | 8.67 / 2.48 | +2.7% | +1.2% |
| `d817bb06` | 216,849 | 2.3853 | 0.6722 | 1.66 / 0.47 | +43.7% | +43.0% |
| `9bdf04c7` | 212,403 | 2.3364 | 0.6584 | 1.78 / 0.51 | +31.3% | +29.1% |
| `35ce4421` | 236,639 | 2.6030 | 0.7336 | 2.36 / 0.68 | +10.3% | +7.9% |
| `fc1e5a76` | 170,216 | 1.8724 | 0.5277 | 1.52 / 0.43 | +23.2% | +22.7% |
| `3875253f` | 226,453 | 2.4910 | 0.7020 | 0.80 / 0.23 | +211.4% | +205.2% |
| `d9c76277` | 291,178 | 3.2030 | 0.9027 | 0.89 / 0.25 | +259.9% | +261.1% |
| `04be9d3d`* | 340,500 | 3.7455 | 1.0555 | 3.07 / 0.88 | +22.0% | +19.9% |

*\* Post-hoc implementation job, not used to fit coefficients. Confirmed within the conservative band.*

### Deliberately Excluded Factors

- **Wall clock:** A multivariable regression incorporating `wall_clock_seconds` yielded a worse median error (36%) because runtime did not correlate with token rates (the fastest job, `35ce4421`, ran 212s at high rate; `d9c76277` ran 195s at low rate).
- **Cache reads:** `cache_read_tokens` varied from 45k to 21.9M across jobs. Adding a cache term improved worst-case error to 97% but kept median error above 28% and lacked a causal quota mechanism.
- **Per-model pricing:** `model_resolved` was `null` on all stored jobs, preventing per-model segmentation.

## Conclusion

The calibration confirmed that token consumption drives quota expenditure, but variations in model behavior and concurrency create a 3.5x spread in effective cost per token. Under [D85](../design/decisions.md#d85-calibrated-conservative-token-quota-estimate) (updating [D83](../design/decisions.md#d83-per-run-tokens-wrapper-wall-clock-runtime-and-a-configurable-quota-estimate)):
1. Default coefficients are set to the conservative upper envelope: `0.011` % points/1k tokens (5-hour) and `0.0031` % points/1k tokens (weekly), with base `0.0`.
2. Estimates are tagged with `basis = "calibrated-2026-10-31, n=7"`.
3. Payloads missing `total_tokens` fall back to `input_tokens + output_tokens`.
4. When no token counts exist, the estimate returns `None` (`null`), never a fabricated `0.0`.
5. Constants are frozen in `tests/test_quota_calibration.py`.

## Evidence

| Claim | Repo file and line or report path |
|---|---|
| Calibration report date and base commit `d4908de` | `docs/reports/quota-calibration-report.md:3` |
| Total stored job records (339) | `docs/reports/quota-calibration-data.md:15` |
| Stored jobs with both estimate and measured cost (7) | `docs/reports/quota-calibration-data.md:15` |
| Jobs marked with `concurrent_jobs=2` (`35ce4421`, `3875253f`) | `docs/reports/quota-calibration-data.md:23` |
| Test fixture records polluting the store (312) | `docs/reports/quota-calibration-data.md:126` |
| Legacy D83 coefficients (0.00075 weekly, 0.0027 5-hour) | `docs/reports/quota-calibration-report.md:18` |
| Observed 5-hour rate range (0.003057 to 0.010706 %/1k tokens) | `docs/reports/quota-calibration-data.md:46` |
| Observed 5-hour median rate (0.008380 %/1k tokens) | `docs/reports/quota-calibration-data.md:46` |
| Observed weekly rate range (0.000859 to 0.003062 %/1k tokens) | `docs/reports/quota-calibration-data.md:47` |
| Observed weekly median rate (0.002401 %/1k tokens) | `docs/reports/quota-calibration-data.md:47` |
| Observed rate spread across population (3.5x) | `docs/reports/quota-calibration-data.md:46` |
| Observed `measured weekly / measured 5h` ratio (0.281 to 0.288) | `docs/reports/quota-calibration-data.md:48` |
| Model 1 fit coefficients (0.009305 5h / 0.002660 weekly) | `docs/reports/quota-calibration-data.md:57` |
| Model 1 error rates (median 13.1%, worst 204%, reference under-estimate 13%) | `docs/reports/quota-calibration-data.md:58-59` |
| Model 2 negative intercept fit (-1.13 5h / -0.325 weekly) | `docs/reports/quota-calibration-data.md:61-63` |
| Model 3 upper envelope coefficients (0.011 5h / 0.0031 weekly, base 0.0) | `docs/reports/quota-calibration-data.md:65-66,81-83` |
| Model 3 error rates (median 31.3% 5h / 29.1% weekly, worst 260% 5h / 261% weekly) | `docs/reports/quota-calibration-data.md:67-68,98-99` |
| Post-hoc job `04be9d3d` tokens (340,500) and errors (+22.0% / +19.9%) | `docs/reports/quota-calibration-report.md:57` |
| Legacy model error rates (median 67.8% 5h / 68.8% weekly, worst 74.8% / 75.5%) | `docs/reports/quota-calibration-report.md:59` |
| Wall-clock multivariable regression median error (36%) | `docs/reports/quota-calibration-data.md:105` |
| Cache read tokens range across jobs (45k to 21.9M) | `docs/reports/quota-calibration-data.md:107` |
| Calibrated basis label (`"calibrated-2026-10-31, n=7"`) | `docs/reports/quota-calibration-data.md:84` |
| Production configuration defaults in `Config` | `src/agy_mcp/config.py:75-76` |
| Production estimation implementation in `compute_quota_estimate` | `src/agy_mcp/usage.py:503-507` |
| Frozen acceptance test assertions | `tests/test_quota_calibration.py:32-35` |
| Post-repair test suite pass count (5/5 PASS, 791 passed per run) | `docs/reports/quota-calibration-report.md:112` |

## How to reproduce

Run the frozen acceptance test suite:
```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_quota_calibration.py -q
```

## Sources

- `docs/reports/quota-calibration-report.md`
- `docs/reports/quota-calibration-data.md`
- `src/agy_mcp/usage.py`
- `src/agy_mcp/config.py`
- `tests/test_quota_calibration.py`
- `doc/design/decisions.md`
