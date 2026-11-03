# Multi-account audit progress

Status: current
Audience: maintainers, contributors

Phase-by-phase status of the multi-account work on `feat/multi-account`. A
restarted session reads this table, finds the first non-`done` phase, and
continues from its "next step" instead of redoing finished phases.

## Phase table

| Phase | Scope | Status | Commit | Gates | Next step |
|---|---|---|---|---|---|
| 0 | Baseline (no src change) | done | (this commit) | ruff pass, frozen 13, docs OK, publish OK, reference fresh, CODEMAP fresh, moved 0, AST 30 match, tests 1823 serial + `-n 4` | Phase 1 investigation |
| 1 | Investigate and test the system | done | d43b0ca | docs OK (87 pages), ruff pass, frozen 13, no src change so tests unchanged | Phase 2 design |
| 2 | Design docs | done | 2f2aa82 | docs gate | Phase 3 foundation |
| 3 | Foundation (registry, identity, env) | done | 6e2c958 | ruff, frozen 13, docs 89, publish 867, reference/codemap fresh, AST 35, 68 new + 30 config tests | Phase 4 usage pools |
| 4 | Usage pools | done | 5d6830e | ruff, frozen 13, docs 89, publish 877, reference/codemap fresh, AST 36, 103 tests | Phase 5 scheduler |
| 5 | Scheduler | pending | — | — | pure decision function |
| 6 | Job model and wiring | pending | — | — | `account`/`priority` |
| 7 | Exhaustion, transfer, handoff | pending | — | — | conversation copy + lock |
| 8 | Installer, doctor, user docs | pending | — | — | `--doctor` account checks |
| 9 | Hardening and final review | pending | — | — | final gates |

## Baseline results (Phase 0)

- HEAD `dd3b1fe9e584020369de005353d2048d27445595`, clean tree, `main` ancestor.
- 1823 tests pass serially (342.35 s) and with `-n 4` (127.52 s).
- Ruff, frozen hashes (13), docs (79 pages), publish (853 files), reference,
  CODEMAP, moved-code (0) and the 30-file AST baseline all pass.
- Details: [00-baseline.md](00-baseline.md).

## Rules that bind every phase

- No edits to the 13 frozen tests or `tests/frozen.sha256`.
- Existing tests pass unmodified; a genuine conflict stops that item.
- With no `[accounts]` configuration, behavior, job records, files and MCP
  responses stay equivalent (proved by a Phase 3 test class).
- Every new error has a stable code in [errors.md](../../reference/errors.md).
- Each phase commits green and updates this file.

## Open questions (defaults used)

From `doc/design/multi-account.md`: OQ1 equal normalized permission-rule set
(drift refuses start); OQ2 one binary, many HOMEs; OQ3 `force` skips only the
quota filter; OQ4 copy the db only, no `brain/`; OQ5 fail-closed on the
account's own model rejection; OQ6 handoff threshold 120 minutes; OQ7 no new
`usage.jsonl` fields without a registry; OQ8 no `schema_version` bump.

## Sources

- `doc/audits/2026-11-03-multi-account/00-baseline.md` — the baseline numbers this table references.
- `AGENTS.md` — the gate commands and the frozen-test rule.
- `tests/frozen.sha256` — the 13 frozen test digests.

