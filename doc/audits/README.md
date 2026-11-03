# Codebase and Documentation Audits

Status: current
Audience: maintainers, contributors, auditors

Audits are dated, comprehensive point-in-time investigations that assess code health,
architecture, security boundaries, and documentation completeness against explicit
normative standards. Unlike experiment pages (which test specific empirical questions),
audits produce structured registers of findings with severity ratings and remediation plans.

Personal paths and email were normalized for publication (user home directories replaced with `~`, personal accounts with `<user-email>`).

## Audit Index

| Date | Topic | Status Line | One-line result | Page |
|---|---|---|---|---|
| 2026-11-02 | Part 1 Index — Code structure audit index | `Status: current` | 13 architectural findings across layers, imports, dead symbols, and test health; structured remediation plan. | [2026-11-02-part1-index.md](2026-11-02-part1-index.md) |
| 2026-11-02 | Part 1a — Project layout, layout-derived findings, and migration map | `Status: current` | Layering and isolation analysis; identified runner subprocess coupling and entrypoint hardening needs. | [2026-11-02-part1a-structure.md](2026-11-02-part1a-structure.md) |
| 2026-11-02 | Part 1b — File size, responsibility, comments and docstrings | `Status: current` | 402 functions inventoried; 86.6% public API documented; metrics baseline captured. | [2026-11-02-part1b-files.md](2026-11-02-part1b-files.md) |
| 2026-11-02 | Part 1c — Dead/duplicated code (C) and logic complexity (D) | `Status: current` | Identified 5 dead symbols, duplicate path/CLI helpers, and unused exception paths. | [2026-11-02-part1c-deadcode.md](2026-11-02-part1c-deadcode.md) |
| 2026-11-02 | Part 1d — Test organization (F) | `Status: current` | Audited 13 frozen tests, pytest isolation guards, and test coverage distribution. | [2026-11-02-part1d-tests.md](2026-11-02-part1d-tests.md) |
| 2026-11-02 | Part 2 Index — Documentation audit and target `doc/` design | `Status: current` | 28 documentation findings across 68 files; migration roadmap from legacy `docs/` to normative `doc/`. | [2026-11-02-part2-index.md](2026-11-02-part2-index.md) |
| 2026-11-02 | Part 2a — Documentation inventory, accuracy and bloat | `Status: current` | Catalogued 68 markdown files, classified normative vs historical content, identified broken links. | [2026-11-02-part2a-inventory.md](2026-11-02-part2a-inventory.md) |
| 2026-11-02 | Part 2b — Target documentation structure and findings map | `Status: current` | Designed target documentation tree across `using/`, `design/`, `findings-and-experiments/`, `audits/`, `project/`, `reference/`. | [2026-11-02-part2b-target-structure.md](2026-11-02-part2b-target-structure.md) |
| 2026-11-02 | Part 2c — Coverage matrix: "everything has to be documented" | `Status: current` | Verified reference coverage for error codes, environment variables, MCP tools, and job fields. | [2026-11-02-part2c-coverage.md](2026-11-02-part2c-coverage.md) |
| 2026-11-02 | Part 2d — Documentation conventions and migration plan | `Status: current` | Defined documentation conventions, style rules, automated gates, and step-by-step phased migration plan. | [2026-11-02-part2d-migration.md](2026-11-02-part2d-migration.md) |
| 2026-11-03 | Multi-account support — progress, baseline, investigation, experiments and review | `Status: current` | Phase-by-phase record for running agy jobs across legitimate accounts with quota-aware scheduling and conversation transfer. | [2026-11-03-multi-account/README.md](2026-11-03-multi-account/README.md) |

## Where Evidence Lives

Supporting raw evidence, reproduction scripts, and metrics datasets are located in subdirectories under `doc/audits/evidence/`:

- `doc/audits/evidence/2026-11-02-part1/` — Metrics calculation scripts (`metrics.py`, `unused.py`), AST symbol inventory (`metrics-src.json`), unused symbol analysis (`unused.json`), and baseline pytest run output (`pytest-full.txt`) supporting Part 1.
- `doc/audits/evidence/2026-11-cleanup-jobs/` — Multi-reviewer adversarial audit evidence, fuzzing scripts, and reproduction cases across five reviewers (`opus/`, `gemini2/`, `gemini3/`, `gemini4/`, `ds/`). See [evidence/2026-11-cleanup-jobs/README.md](evidence/2026-11-cleanup-jobs/README.md).

## Naming Rule and Conventions

All audit files must adhere to the standard naming rule:
`YYYY-MM-DD-topic.md` where `YYYY-MM-DD` is the audit date in ISO format and `topic` is a lowercase, kebab-case summary slug (e.g., `2026-11-02-part1a-structure.md`).

## How to Add an Audit

Follow these rules when adding an audit to `doc/audits/`:

1. **File Placement & Name:** Place the file directly in `doc/audits/YYYY-MM-DD-topic.md`.
2. **Mandatory Header Fields:**
   - Title: `# <Topic or Part Title>`
   - Status line: `Status: current` (or `Status: superseded by <YYYY-MM-DD-audit.md>`)
   - Metadata lines: `**Repository:** agy-mcp @ main = <sha>`, `**Mode:** read-only`, and scope declaration.
3. **Finding Structure:** Every finding must be structured with explicit attributes:
   `ID | Severity | Evidence | Action | Risk | Effort`.
4. **Immutability of Historical Findings:** Never edit a published audit to change or remove a finding. A later audit supersedes earlier findings, and the index records the supersession.
5. **Sources Footer:** Every audit file must end with a `## Sources` section citing repository files inspected.
6. **Evidence Storage:** Small reproduction scripts and text metrics (< 200 KB) belong in `doc/audits/evidence/<YYYY-MM-topic>/`. Multi-megabyte test environments, active git checkouts, and mode-000 directories remain outside git tracking.
7. **Index Registration:** Add a corresponding row to the table in this `doc/audits/README.md` file and run `check_docs.py` to ensure all links and sources resolve cleanly.
