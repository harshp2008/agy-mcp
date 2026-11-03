# Multi-account baseline: what the repository looked like before Phase 3

Status: current
Audience: maintainers, contributors

Point-in-time record of the repository state on `feat/multi-account` before any
multi-account source change. Every later phase compares against these numbers,
so a regression can be attributed to a specific change instead of a guess.

## Repository state

| Fact | Value | Where |
|---|---|---|
| Branch | `feat/multi-account` (from `main`) | `git branch --show-current` |
| HEAD | `dd3b1fe9e584020369de005353d2048d27445595` | `git rev-parse HEAD` |
| `main` at start | `dd3b1fe` (same commit) | `git log --oneline main -1` |
| Working tree | clean (`git status --short` empty) | `git status --short` |
| Python | 3.12.3 | `.venv/bin/python --version` |
| Ruff | `All checks passed!` | `.venv/bin/ruff check src tests scripts` |

`feat/multi-account` and `main` pointed at the same commit when the branch was
created. The design therefore starts from the CI-green baseline with no prior
multi-account work to reconcile.

## Test baseline

| Run | Result | Wall clock | Evidence |
|---|---|---|---|
| Serial | 1823 passed | 342.35 s | `evidence/baseline-tests.txt` |
| Parallel `-n 4` | 1823 passed | 127.52 s | `evidence/baseline-tests.txt` |

Both runs used `env -u PYTHONPATH .venv/bin/python -m pytest -q`. ROS Jazzy is
on `PYTHONPATH` in this host environment, so `-u PYTHONPATH` is mandatory
(AGENTS.md "Commands").

No test failed, was skipped or errored at baseline. The suite size (1823) is the
reference for the "existing tests must pass unmodified" rule in every phase.

## Gate baseline

All gates below passed at HEAD before any change. Raw output is in
`evidence/baseline-gates.txt`.

| Gate | Command | Baseline result |
|---|---|---|
| Ruff | `.venv/bin/ruff check src tests scripts` | `All checks passed!` |
| Frozen tests | `sha256sum -c tests/frozen.sha256 \| grep -c ': OK'` | `13` |
| Docs | `env -u PYTHONPATH .venv/bin/python scripts/check_docs.py` | `docs check: OK (79 markdown files checked)` |
| Publish | `env -u PYTHONPATH .venv/bin/python scripts/check_publishable.py` | `publish hygiene: OK (853 tracked text files checked)` |
| Reference | `env -u PYTHONPATH .venv/bin/python scripts/gen_reference.py --check` | `doc/reference generated blocks are fresh` |
| CODEMAP | `env -u PYTHONPATH .venv/bin/python scripts/gen_codemap_index.py --check` | `CODEMAP.md generated section is fresh` |
| Moved code | `env -u PYTHONPATH .venv/bin/python scripts/check_moved_code.py` | missing 0, changed 0, ambiguous 0 |
| AST baseline | `env -u PYTHONPATH .venv/bin/python scripts/check_no_behavior_change.py --compare docs/reports/docs-codemap/ast-baseline.json` | `OK: 30 files match the AST baseline (no behavior change)` |

The 13 frozen files are the cleaner tests and fixtures listed in
`tests/frozen.sha256`; they must not change in this work.

## Generated artifacts

| Artifact | State at baseline | Regenerated when |
|---|---|---|
| `doc/reference/*` generated blocks | fresh | a config key, MCP tool or error code changes |
| `doc/project/code-layout.md` CODEMAP index | fresh | source modules or public API change |
| `docs/reports/docs-codemap/ast-baseline.json` (30 files) | matches | a deliberate behavior change lands |

Each later regeneration must be explained in the commit message that lands the
source change, not in a separate "regen" commit.

## What the baseline does not cover

- Real `agy` behavior is not exercised by the baseline; the suite is hermetic
  and drives `tests/fake_agy/agy`.
- The baseline records no account/registry state because none exists yet. The
  "no `[accounts]` config" behavior-neutrality proof is a Phase 3 test, not a
  baseline measurement.

## Sources

- `tests/frozen.sha256` — the 13 frozen cleaner test digests.
- `AGENTS.md` — the gate commands and the `env -u PYTHONPATH` requirement.
- `doc/developing/gates.md` — the gate definitions this baseline runs.
- `scripts/check_docs.py` — the docs gate whose count (79) is recorded.
- `scripts/check_publishable.py` — the publish gate whose count (853) is recorded.
