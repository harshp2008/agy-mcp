# Helper scripts

Status: current
Audience: contributors, maintainers

`scripts/` holds the developer tooling used by the gates plus `watch_orphans.py`,
a manual evidence helper. None is imported by the server; `agy-mcp` behaves
identically if the directory is absent. Two scripts have a stable 0/1/2
contract, and none is part of the pytest suite (AGENTS rule 4).

```bash
env -u PYTHONPATH .venv/bin/python scripts/<name>.py [flags]
```

## Script table

| Script | Flags | Reads / writes | Real `agy`? |
|---|---|---|---|
| `check_no_behavior_change.py` | `--save FILE` or `--compare FILE` (exactly one) | Reads every `src/agy_mcp/**/*.py`; writes the snapshot on `--save`. | no |
| `gen_codemap_index.py` | `--write`, `--check`, `--stdout` | Reads the package; writes the generated block in `doc/project/code-layout.md` on `--write`. | no |
| `watch_orphans.py` | none (`PYTEST_BIN` env) | Runs pytest per test file, monitors `fake_agy`/`agy_mcp` processes; writes `docs/reports/smoke-fixes-2/orphans.txt`. | no |

## Contract-bearing scripts

### `check_no_behavior_change.py`

```bash
.venv/bin/python scripts/check_no_behavior_change.py \
  --save docs/reports/docs-codemap/ast-baseline.json
.venv/bin/python scripts/check_no_behavior_change.py \
  --compare docs/reports/docs-codemap/ast-baseline.json
```

It parses every file under `src/agy_mcp/`, removes module/class/function
docstrings, and prints the SHA-256 of the docstring-free AST dump. `--save`
writes `{relative_path: sha256}` JSON; `--compare` prints `same`/`CHANGED`/
`ADDED`/`REMOVED` and exits 1 on any difference. Exit 2 means a missing/invalid
baseline or unparseable source. Docstring and comment edits leave it unchanged;
anything executable changes it.

### `gen_codemap_index.py`

```bash
.venv/bin/python scripts/gen_codemap_index.py --write
.venv/bin/python scripts/gen_codemap_index.py --check
.venv/bin/python scripts/gen_codemap_index.py --stdout
```

`--write` replaces only the text between `<!-- BEGIN GENERATED -->` and
`<!-- END GENERATED -->` in `doc/project/code-layout.md`; `--check` exits 1 when it is
stale; `--stdout` prints the block. Exit 2 means a missing file, missing markers
or a usage error. The same renderer is pinned by `tests/test_codemap_fresh.py`.

## Evidence scripts without flags

`watch_orphans.py` is a one-off evidence helper. It has no flag parser and no
formal exit-code contract: `0` when the script reaches its end, non-zero when a
subprocess or assertion fails.

The six one-shot round drivers (`real_smoke.py`, `smoke_real.py`,
`smoke_fixes_real.py`, `run_f1.py`, `run_f2.py`, `run_mutations.py`) were
removed after their evidence rounds; their outputs stay under `docs/reports/`
and the Part 1 audit records them as finished. They drove a real `agy`, spent
real quota and hardcoded a maintainer-specific virtualenv path (Part 1 audit
G2), so they were never portable commands.

**Safety notes:**

- `watch_orphans.py` uses `PYTEST_BIN` when set; the fallback path may not
  exist on another machine.
- Never add a real-`agy` driver to the pytest suite or CI (AGENTS rule 4).

## Sources

- `scripts/check_no_behavior_change.py` — `--save`/`--compare` and the 0/1/2
  returns.
- `scripts/gen_codemap_index.py` — `--write`/`--check`/`--stdout` and the
  marker replacement.
- `scripts/watch_orphans.py` — `PYTEST_BIN` and the write path.
- `docs/reports/` — committed output of the six removed round drivers.
- `doc/audits/2026-11-02-part2c-coverage.md` — §4.4, the nine-script inventory
  (COV-10).
- `doc/audits/2026-11-02-part1-index.md` — G2, the hardcoded-path finding.
- `AGENTS.md` — rule 4 (never run the real `agy` in the suite).
