# Raw Evidence Directory Index

Status: current
Audience: maintainers, contributors

This directory contains committed evidence artifacts, briefs, and reproduction logs
directly supporting findings and experiments in `doc/findings-and-experiments/`.

Personal paths and email were normalized for publication.

## Directory Contents

| Directory / File | Description | Supporting Experiment | Related Decisions |
|---|---|---|---|
| `test-isolation-2026-10/` | Test isolation job specification brief and requirements | [09 — Test Store Contamination](../09-test-store-contamination.md) | [D86, D87](../../design/decisions.md) |
| `test-isolation-2026-10/agy-brief-test-isolation.md` | Autonomous task brief defining the state isolation contract and safe fixture cleaner requirements | [09 — Test Store Contamination](../09-test-store-contamination.md) | [D86, D87](../../design/decisions.md) |

## Related Evidence Repositories

Evidence is partitioned between experimental investigations, code audits, and historical logs:

1. **Adversarial Audit Evidence (`doc/audits/evidence/2026-11-cleanup-jobs/`):**
   Full multi-reviewer evidence, repro scripts, and fuzzing harnesses supporting
   [12 — Cleanup-Jobs Differential Test Oracle](../12-cleanup-jobs-differential.md)
   and decisions D89–D100. See [Audit Evidence Index](../../audits/evidence/2026-11-cleanup-jobs/README.md).
2. **Code Structure Audit Evidence (`doc/audits/evidence/2026-11-02-part1/`):**
   Raw metrics (`metrics-src.json`, `unused.json`), scripts, and test runs supporting
   the Part 1 codebase audit.
3. **Historical Fix-Round Logs (`docs/reports/`):**
   Archived logs, failure traces, and mutation records from October 2026 fix rounds
   (rounds 1–13) supporting [14 — Fix-Round Evidence](../14-fix-round-evidence-2026-10.md).

## Rules for Adding Evidence

- Keep evidence files small (< 200 KB). Multi-megabyte fixture directories, git repos,
  and transient worktrees remain outside git.
- Sanitize personal filesystem paths (`~/...`) and personal email identifiers (`<user-email>`).
- Always link the supporting experiment page and relevant D-numbered decisions.
