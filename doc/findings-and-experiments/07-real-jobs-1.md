# 07 — Real Multi-Commit Jobs End-to-End Validation

Status: current
Audience: maintainers, contributors

Empirical validation of the full `agy-mcp` lifecycle across two real autonomous
multi-commit programming jobs executed on a throwaway git repository with real `agy`,
confirming commit creation, `.git/info/exclude` isolation, automated verification,
security audits, and quota envelopes.

## Question

Does the complete `agy-mcp` pipeline finalize multi-commit coding jobs end-to-end
on a real git repository with real `agy`, verifying commits, `.git/info/exclude`
isolation, quota tracking, audit checks, and envelope generation?

## Method

Executed on 2026-10-26 (03:00:57 to 03:21:23+05:30) on Linux x86_64 using `agy`
(binary at `~/.local/bin/agy`, 227,840,232 bytes) with model `gemini-3.8-flash`
against a throwaway git repository `~/agy-smoke-repo` initialized at commit `e4ed764`
(`slug` package skeleton containing `slug/__init__.py`, `tests/test_slug.py`, and `README.md`).
A STEP 0 baseline pytest run verified the test suite (560 passed in 132.11s).
Two distinct jobs were launched sequentially with a 10-minute timeout cap:
- **Job 1 (`job1-slugify-punctuation`):** Implement punctuation stripping, whitespace collapsing, and trimming in `slugify` with automated verification requested (`python -m pytest`).
- **Job 2 (`job2-slug-docs`):** Add a descriptive docstring to `slugify` and usage examples to `README.md` without automated verification.

Quota readings were taken via `/usage` before and after each job (`usage.get_usage(use_cache=False)`),
enforcing budget gates (weekly remaining >= 20%, 5-hour remaining >= 50%).

## Result

### Per-Job Operational Results

| Metric / Dimension | Job 1 (`job1-slugify-punctuation`) | Job 2 (`job2-slug-docs`) |
|---|---|---|
| Process PID / PGID | 37450 / 37450 | 40525 / 40525 |
| Final State | `done` | `done` |
| Review Reason | `null` | `null` |
| Driver Wall Clock | 360.69s | 271.48s |
| Envelope `elapsed_seconds` | 352.25s | 264.24s |
| `agy`-Reported `duration_seconds` | 327.74s | 242.16s |
| Commits Created | 2 | 2 |
| Commit 1 Subject | `Update slugify to strip punctuation, collapse separators, and trim` | `Add descriptive docstring to slugify function` |
| Commit 2 Subject | `Add test cases for punctuation, repeated spaces, and empty input` | `Add usage examples to README` |
| Files Modified | `slug/__init__.py`, `tests/test_slug.py` | `README.md`, `slug/__init__.py` |
| Verification | Requested, passed (rc 0, 4 passed in 0.01s) | Not requested, not run |
| Security Audit | Passed (`audit_findings: []`) | Passed (`audit_findings: []`) |
| Token Usage (Input / Output / Total) | 221,140 / 31,011 / 252,151 | 396,287 / 17,544 / 413,831 |
| Weekly Points Spent (Manual Delta) | 0.3282 (envelope: 0.33) | 0.3868 (envelope: 0.39) |
| 5-Hour Points Spent (Manual Delta) | 1.1488 (envelope: 1.15) | 1.3536 (envelope: 1.35) |
| Branch Created | `agy/slugify-punctuation-90226ef0` | `agy/slug-docs-2bf72c13` |
| Commit Range | `e4ed764` -> `ed3ff1f` | `e4ed764` -> `33fda9e` |

### Git Isolation and `.git/info/exclude`

- **Branch history:** Both branches branched cleanly from `e4ed764` and accumulated exactly two commits each.
- **Path isolation:** Diff stats confirmed that only product files were committed (37 insertions, 3 deletions in Job 1; 17 insertions, 1 deletion in Job 2). Zero `.agy/` scratchpad or prompt files were committed.
- **Exclude provisioning:** `.git/info/exclude` contained `.agy/` (MD5 hash `e919f58e37e058224fc759ca7d47a845` matched across both jobs), verifying idempotent provisioning.

### Timing Discrepancies and Clarifications

Across both jobs, three distinct durations were recorded:
- **Driver wall clock:** 360.69s (Job 1) / 271.48s (Job 2), measuring overall wrapper execution from initial worktree setup to final state persistence.
- **Envelope `elapsed_seconds`:** 352.25s (Job 1) / 264.24s (Job 2), measuring the supervisor's active supervision window.
- **`agy`-reported `duration_seconds`:** 327.74s (Job 1) / 242.16s (Job 2), representing internal engine processing time.
As settled in [D83](../design/decisions.md#d83-per-run-tokens-wrapper-wall-clock-runtime-and-a-configurable-quota-estimate), `agy`'s reported duration reflects model conversation age rather than wrapper execution time; `agy-mcp` relies strictly on wrapper-measured wall-clock seconds for usage logs and budgets.

## Conclusion

This experiment provided the first conclusive proof that `agy-mcp` successfully finalizes
autonomous multi-commit coding workflows on real repositories. Key design adjustments
derived from these runs include:
1. **Model resolution and allowlist ([D63](../design/decisions.md#d63-real-smoke-fixes--permission-config-model-resolution-python--m-pytest-token-fields-path-sanitization)):** Resolved discrepancies between bare model names (`gemini-3.8-flash`) and tier variants.
2. **Verification allowlist ([D63](../design/decisions.md#d63-real-smoke-fixes--permission-config-model-resolution-python--m-pytest-token-fields-path-sanitization)):** Ensured `python -m pytest` passes the verification command allowlist.
3. **Wall-clock vs agy duration ([D83](../design/decisions.md#d83-per-run-tokens-wrapper-wall-clock-runtime-and-a-configurable-quota-estimate)):** Established wrapper-measured wall-clock timing as canonical for all budgets and `usage.jsonl` rows.
4. **Envelope token schema ([D63](../design/decisions.md#d63-real-smoke-fixes--permission-config-model-resolution-python--m-pytest-token-fields-path-sanitization)):** Exposed input, output, and total token breakdowns directly in the final envelope.

## Evidence

| Claim | Repo file and line or report path |
|---|---|
| Run start timestamp (`2026-10-26T03:00:57+05:30`) | `docs/reports/real-smoke-1/REPORT.md:12` |
| Report completion timestamp (`2026-10-26T03:21:23+05:30`) | `docs/reports/real-smoke-1/REPORT.md:128` |
| `agy` binary size (`227,840,232 bytes`) | `docs/reports/real-smoke-1/REPORT.md:14` |
| Repository initial commit `e4ed764` | `docs/reports/real-smoke-1/REPORT.md:52` |
| STEP 0 baseline pytest (560 passed in 132.11s) | `docs/reports/real-smoke-1/REPORT.md:59-65` |
| Budget check and `/usage` readings before and after | `docs/reports/real-smoke-1/REPORT.md:74-86` |
| Job 1 PID / PGID (`37450 / 37450`) | `docs/reports/real-smoke-1/REPORT.md:295` |
| Job 2 PID / PGID (`40525 / 40525`) | `docs/reports/real-smoke-1/REPORT.md:295` |
| Job 1 driver wall clock (`360.69s`) | `docs/reports/real-smoke-1/REPORT.md:299`, `docs/reports/real-smoke-1/job1-slugify-punctuation/summary.json:7` |
| Job 2 driver wall clock (`271.48s`) | `docs/reports/real-smoke-1/REPORT.md:299`, `docs/reports/real-smoke-1/job2-slug-docs/summary.json:7` |
| Job 1 envelope `elapsed_seconds` (`352.25s`) | `docs/reports/real-smoke-1/REPORT.md:300`, `docs/reports/real-smoke-1/job1-slugify-punctuation/envelope-final.json:4` |
| Job 2 envelope `elapsed_seconds` (`264.24s`) | `docs/reports/real-smoke-1/REPORT.md:300`, `docs/reports/real-smoke-1/job2-slug-docs/envelope-final.json:4` |
| Job 1 `agy`-reported `duration_seconds` (`327.74s`, rounded to 2 decimals from full-precision `327.739397336`) | `docs/reports/real-smoke-1/REPORT.md:301` |
| Job 2 `agy`-reported `duration_seconds` (`242.16s`, rounded to 2 decimals from full-precision `242.156969018`) | `docs/reports/real-smoke-1/REPORT.md:301` |
| Job 1 commit count (2) and commit subjects | `docs/reports/real-smoke-1/REPORT.md:302-303` |
| Job 2 commit count (2) and commit subjects | `docs/reports/real-smoke-1/REPORT.md:302-303` |
| Job 1 verification result (rc 0, 4 passed in 0.01s) | `docs/reports/real-smoke-1/REPORT.md:307`, `docs/reports/real-smoke-1/job1-slugify-punctuation/envelope-final.json:78-81` |
| Job 1 token breakdown (221,140 input / 31,011 output / 252,151 total) | `docs/reports/real-smoke-1/REPORT.md:316`, `docs/reports/real-smoke-1/job1-slugify-punctuation/envelope-final.json:20-22` |
| Job 2 token breakdown (396,287 input / 17,544 output / 413,831 total) | `docs/reports/real-smoke-1/REPORT.md:316`, `docs/reports/real-smoke-1/job2-slug-docs/envelope-final.json:20-22` |
| Job 1 weekly quota delta (`0.3282` manual delta, `0.33` envelope) | `docs/reports/real-smoke-1/REPORT.md:309,312`, `docs/reports/real-smoke-1/job1-slugify-punctuation/envelope-final.json:32` |
| Job 2 weekly quota delta (`0.3868` manual delta, `0.39` envelope) | `docs/reports/real-smoke-1/REPORT.md:309,312`, `docs/reports/real-smoke-1/job2-slug-docs/envelope-final.json:32` |
| Job 1 5-hour quota delta (`1.1488` manual delta, `1.15` envelope) | `docs/reports/real-smoke-1/REPORT.md:310,313`, `docs/reports/real-smoke-1/job1-slugify-punctuation/envelope-final.json:31` |
| Job 2 5-hour quota delta (`1.3536` manual delta, `1.35` envelope) | `docs/reports/real-smoke-1/REPORT.md:310,313`, `docs/reports/real-smoke-1/job2-slug-docs/envelope-final.json:31` |
| Job 1 branch diff stat (37 insertions, 3 deletions) | `docs/reports/real-smoke-1/REPORT.md:550` |
| Job 2 branch diff stat (17 insertions, 1 deletion) | `docs/reports/real-smoke-1/REPORT.md:588` |
| Git log for Job 1 branch | `docs/reports/real-smoke-1/REPORT.md:521-564` |
| Git log for Job 2 branch | `docs/reports/real-smoke-1/REPORT.md:566-602` |
| `.git/info/exclude` idempotent provisioning (`e919f58e...`) | `docs/reports/real-smoke-1/REPORT.md:609-624` |
| Raw process argv at launch | not recorded in repo (process exited before capture per `docs/reports/real-smoke-1/REPORT.md:36-37`) |

## How to reproduce

The complete run was driven by `scripts/real_smoke.py` (removed after the round; documented in `docs/reports/real-smoke-1/REPORT.md`). To run the mock equivalence test:
```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/server/test_smoke.py -q
```

## Sources

- `docs/reports/real-smoke-1/REPORT.md`
- `docs/reports/real-smoke-1/job1-slugify-punctuation/envelope-final.json`
- `docs/reports/real-smoke-1/job2-slug-docs/envelope-final.json`
- `docs/reports/real-smoke-1/job1-slugify-punctuation/summary.json`
- `docs/reports/real-smoke-1/job2-slug-docs/summary.json`
- `doc/design/decisions.md`
