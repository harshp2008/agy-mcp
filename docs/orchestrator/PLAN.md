# PLAN — test isolation and safe fixture cleanup

Workflow: **GATED** (frozen acceptance tests, then one agy implementation job,
then scripted gates, then a read-only review). Tests can check the result, so
GATED is the appropriate workflow per the repository's agy rules.

## Goal

1. **Task A** — make it impossible for the pytest suite to touch the live
   `~/.local/state/agy-mcp` store: redirect at conftest import time, raise at
   resolution of the real path, keep the write audit hook, and freeze tests
   (a)-(d) from the brief.
2. **Task B** — add `python -m agy_mcp.cleanup_fixtures`, a dry-run-first,
   whitelist-only lister/deleter for historical pytest fixture rows, tested
   only against temp stores. Harsh runs it against the real store himself.

## Status

- [x] Branch `fix/test-isolation` from clean `main` (`23bb1aa`).
- [x] A1 findings: `docs/reports/test-isolation-findings.md`.
- [x] Frozen acceptance tests:
  - `tests/test_live_state_isolation.py`
  - `tests/test_cleanup_fixtures.py`
- [x] Freeze commit `f1c1295`; sha256 recorded in the report.
- [x] One agy job implemented A2 + B1 + docs (job `9e74c60a`; verify failed, repair finished manually).
- [x] Gate: frozen files unchanged, new tests 20x, full suite 5x, ruff, scope (gated HEAD `beba554`).
- [x] Fresh read-only reviews: round 1 defects repaired; round 2 defects confirmed, not repaired (2-round cap), listed in the report.
- [x] `docs/reports/test-isolation-report.md` with commands for Harsh.

## Constraints in force

- Never read/write/list/delete `~/.local/state/agy-mcp` in this task.
- Never touch `~/.config/ai-stack` or `~/.gemini`.
- Never delete agy conversations. Never run agy jobs against clipsync-lab.
- Never print an API key.
- Do not merge; Harsh merges.
- Frozen tests are not edited after the freeze commit (agy included). A frozen
  test that contradicts the spec is a `needs_decision`, reported to Harsh.

## Decisions

- D-task-1: The existing `aaf7408` isolation is kept and hardened rather than
  replaced; the freeze tests target the gaps (resolution guard, import-time
  redirect, HOME swap, static tripwire).
- D-task-2: The cleaner's decisive fixture marker is a repository under a
  pytest temp root (`.../pytest-of-*/pytest-<n>/...`) plus terminal state, no
  live pid, old enough, no symlinks, no follow-up edge from a kept row. All
  conditions are required; anything else is "kept (ambiguous)".
- D-task-3: The manifest is `{"removed_ids": [...]}` written inside
  `<store>/jobs/.cleanup-manifest-<UTC>.json` so the tool never deletes outside
  `jobs/`.
- D-task-4: Tests invoke the cleaner through its module CLI with `--json`
  rather than importing internals, so the user-facing contract is what is
  frozen.

## Verification commands

```bash
.venv/bin/python -m pytest -q tests/test_live_state_isolation.py tests/test_cleanup_fixtures.py
node ~/work/dsh-plugins/scripts/gate-run.mjs --log-dir ~/gate-logs/test-isolation --repeat 20 -- \
  .venv/bin/python -m pytest -q tests/test_live_state_isolation.py tests/test_cleanup_fixtures.py
node ~/work/dsh-plugins/scripts/gate-run.mjs --log-dir ~/gate-logs/test-isolation --repeat 5 -- \
  .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
```
