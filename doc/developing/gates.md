# Gates: the commands a change must pass

Status: current
Audience: contributors, maintainers

Every gate below is a command that exists in this repository today. Run the
gates that cover the files you touched; run all of them before a merge. CI
(`.github/workflows/ci.yml`) runs the lint, test and docs gates automatically on
every push and pull request.

All commands run from the repository root. `env -u PYTHONPATH` is required in
this environment because an unrelated ROS Jazzy tree is on `PYTHONPATH`; the CI
runner does not need it, but the command is harmless when the variable is unset.

## Test suite

```bash
env -u PYTHONPATH .venv/bin/python -m pytest -q
```

The suite uses the fake `agy` at `tests/fake_agy/agy`; it never runs a real
`agy`. `tests/conftest.py` points `HOME`, `XDG_STATE_HOME` and
`AGY_MCP_STATE_DIR` at temporary directories, so the suite cannot read or write
the operator's live store (D86/D87).

Focused variants:

```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_manager.py -q
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_jobs.py::test_name -q
```

Run a long suite once in the foreground and tee it to a file; do not poll it.

## Lint

```bash
.venv/bin/ruff check src tests scripts
```

Ruff is configured in `pyproject.toml` with `E`, `F`, `I` and `T20` (no `print`
in `src/`). `scripts/*` deliberately ignores `T20`, `E501`, `F401`, `I001` and
`E741`; `tests/*` ignores `T20`; `tests/test_cleanup_jobs_fixes6.py` keeps a
file-specific `E501` ignore because it is frozen by sha256.

## Publish hygiene

```bash
env -u PYTHONPATH .venv/bin/python scripts/check_publishable.py
# publish hygiene: OK (<n> tracked text files checked)
```

Scans every text file returned by `git ls-files` and fails (exit 1) on: an email
address whose domain is not allowlisted; an absolute `home` or `Users` path
whose directory name is not a documented placeholder; a hostname under
`ts.net`; or a credential-shaped string (Google, OpenAI-style, GitHub and AWS
key shapes, and PEM private-key blocks). Exit 2 means the allowlist is
malformed, not clean. Findings print a path and line, never a full address or a
credential value, and binary files are skipped.

Only public placeholder domains and names are hardcoded in the checker.
Repository-specific exceptions live in `scripts/publishable-allowlist.txt`,
one reviewed entry per line with a `# justification`:

| Kind | Value | Used for |
|---|---|---|
| `credential` | repo-relative path | the three fake-redaction test fixtures whose tokens are asserted to be masked |
| `email-domain` | domain (suffix match) | the GNOME extension-id suffix quoted in committed agy logs, which is not an account email |

An entry with no justification or an unknown kind fails closed.
`tests/tooling/test_check_publishable.py` covers the pass/fail cases.

## Behaviour preservation (AST baseline)

```bash
.venv/bin/python scripts/check_no_behavior_change.py \
  --compare docs/reports/docs-codemap/ast-baseline.json
```

Passing output ends with `OK: <n> files match the AST baseline (no behavior
change)`. The script hashes the docstring-free AST of every file under
`src/agy_mcp/`, so a docstring or comment edit leaves it unchanged and an
executable change (a constant, control flow, a name, an argument) fails it. Run
it for every docs or comment-only commit; it is not a substitute for tests.

## Cleanup module structural guard

```bash
env -u PYTHONPATH .venv/bin/python -m pytest -q \
  tests/test_cleanup_modules_structural_guard.py tests/tooling/test_moved_code.py
```

`tests/test_cleanup_modules_structural_guard.py` applies the frozen
no-swallowed-exceptions AST rule to every module in `src/agy_mcp/cleanup/` plus
the two re-export shims, so the guard still inspects the code that runs after
the wave-2a split. It also asserts that at least 65 `except` clauses were
scanned, which keeps the check from passing vacuously if the code moves again,
and it proves the rule fires on a temporary copy with an injected
`except Exception: pass`.

### Known debt: swallowed exceptions (fix in a later wave)

Three handlers were inherited verbatim from `main`'s `cleanup_fixtures.py` and
are allowlisted in the test as debt, keyed by file, enclosing function and
handler text (not by line number):

| Location | What it swallows | Why it is debt |
|---|---|---|
| `src/agy_mcp/cleanup/fixtures_cli.py::_candidate_strings` | An unreadable or invalid kept `job.json` | Silently protects fewer strings on the best-effort edge-collection path |
| `src/agy_mcp/cleanup/store_io.py::_get_kernel_pid_max` | A `/proc/sys/kernel/pid_max` read error | Falls back to 4194304 without recording why |
| `src/agy_mcp/cleanup/store_io.py::_parse_iso_ts` | An ISO-8601 parse error | Treats a malformed timestamp as absent |

Remove an entry only together with the `src/` fix. Any new offender fails the
guard, and a stale allowlist entry fails `test_known_debt_allowlist_is_not_stale`.

## Wave-2a move proof

```bash
env -u PYTHONPATH .venv/bin/python scripts/check_moved_code.py
env -u PYTHONPATH .venv/bin/python -m pytest tests/tooling/test_moved_code.py -q
```

The old `cleanup_jobs.py` and `cleanup_fixtures.py` source text is saved as
`tests/fixtures/old_cleanup_jobs.py.txt` and
`tests/fixtures/old_cleanup_fixtures.py.txt`; both sides are parsed and dumped
at run time with the running interpreter. The gate needs no git history and no
stored `ast.dump`, so it passes on any supported Python (`--from-git` reads the
same files from `main` for a manual cross-check).

## Frozen test manifest

```bash
sha256sum -c tests/frozen.sha256
```

The manifest freezes 13 cleanup test files. The expected check count is:

```bash
sha256sum -c tests/frozen.sha256 | grep -c ': OK'
# 13
```

A failure means a frozen file changed. Restore it; do not edit a frozen test to
make a change pass. `tests/frozen.sha256` is itself not frozen and may be
extended only by the maintainer.

## Codemap freshness

```bash
env -u PYTHONPATH .venv/bin/python scripts/gen_codemap_index.py --check
# CODEMAP.md generated section is fresh

env -u PYTHONPATH .venv/bin/python -m pytest tests/test_codemap_fresh.py -q
```

The test checks four things: `doc/project/code-layout.md` exists with both markers, every
module under `src/agy_mcp/` is mentioned, the generated block is byte-identical
to a fresh render, and the path is still `doc/project/code-layout.md`. If a source edit
changes modules or line counts, regenerate and include the result in the same
commit:

```bash
.venv/bin/python scripts/gen_codemap_index.py --write
```

Do **not** guess the generated block or edit it by hand.

## Documentation checks

```bash
.venv/bin/python scripts/gen_reference.py --check
env -u PYTHONPATH .venv/bin/python scripts/check_docs.py
```

`gen_reference.py --check` fails when a generated reference block (error codes,
environment variables, config keys, MCP tools, `job.json` fields, audit events)
is stale. Refresh it after a source change that adds or removes one of those:

```bash
.venv/bin/python scripts/gen_reference.py --write
```

`check_docs.py` fails on: a broken relative markdown link, a `doc/` page without
`## Sources`, a Sources path that does not exist, an error code in
`src/agy_mcp/` absent from `doc/reference/errors.md`, an environment variable or
config key absent from its reference page, and a stale generated block. While a
migration is in progress, `--report` prints the same findings and exits zero:

```bash
env -u PYTHONPATH .venv/bin/python scripts/check_docs.py --report
```

`tests/tooling/test_check_docs.py` pins the checker's detection logic and asserts that
the real tree passes. Both docs commands are pure standard library and need no
network.

## CI mapping

`.github/workflows/ci.yml` has two jobs:

| Job | Steps | Local equivalent |
|---|---|---|
| `lint` | install `-e ".[dev]"`, `ruff check src tests scripts`, `python scripts/check_publishable.py`, `python scripts/check_docs.py` | the lint, publish-hygiene and docs gates above |
| `test` | install `-e ".[dev]"`, `python -m pytest -q --durations=30` on Python 3.10 and 3.12 | the test gate above |

The AST baseline gate is not a CI step; the frozen manifest is also enforced by
`tests/test_frozen_manifest.py` inside the pytest job. Both local commands remain
the fastest way to see the per-file hash/digest detail.

## When to run what

| Change | Minimum gates |
|---|---|
| `src/agy_mcp/**` | pytest, ruff, AST baseline (if no behaviour change was intended), codemap `--write`, reference `--write`; cleanup modules also run the structural guard and move proof |
| `tests/**` (not frozen) | pytest for the touched file, then the full suite; ruff |
| Frozen test file | none: do not edit it |
| `doc/**` only | `check_docs.py`, `gen_reference.py --check`, ruff, AST baseline |
| `scripts/**` | ruff; run the script; `check_docs.py` when it is a docs checker; `check_moved_code.py` with its test for the move proof |
| `scripts/check_publishable.py`, `scripts/publishable-allowlist.txt` | publish hygiene on the real tree, then its pytest file (the injected leak cases run in a temporary repository) |
| `README.md`, `SECURITY.md`, `CONTRIBUTING.md` | publish hygiene, `check_docs.py`, ruff is not needed for prose |
| `installer/**` | installer tests (`tests/test_installer*.py`), pytest, ruff |
| `doc/design/spec.md` / `doc/design/decisions.md` | `check_docs.py` if a `doc/` link changed; the relevant tests |

## Sources

- `AGENTS.md` — the commands, the no-behaviour-change rule and the codemap rule.
- `pyproject.toml` — the ruff selection, per-file ignores and pytest options.
- `scripts/check_no_behavior_change.py` — the AST hashing gate and its exit
  codes.
- `scripts/check_moved_code.py` — the wave-2a move proof and `--from-git`.
- `tests/test_cleanup_modules_structural_guard.py` — the package-wide
  swallowed-exception guard and its known-debt allowlist.
- `tests/tooling/test_moved_code.py` — the move proof run from pytest.
- `tests/fixtures/old_cleanup_jobs.py.txt` — old `cleanup_jobs.py` source text.
- `tests/fixtures/old_cleanup_fixtures.py.txt` — old `cleanup_fixtures.py`
  source text.
- `scripts/gen_codemap_index.py` — the `--write`/`--check` codemap generator.
- `tests/test_codemap_fresh.py` — the four codemap assertions.
- `tests/frozen.sha256` — the 13 frozen test digests.
- `scripts/gen_reference.py` — the generated reference blocks and their
  freshness check.
- `scripts/check_docs.py` — the docs findings and the `--report` mode.
- `tests/tooling/test_check_docs.py` — the checker's tests.
- `scripts/check_publishable.py` — the publish-hygiene patterns, exit codes and
  allowlist parser.
- `scripts/publishable-allowlist.txt` — the reviewed credential and
  email-domain exceptions.
- `tests/tooling/test_check_publishable.py` — the pass/fail cases, including
  the injected leak in a temporary repository.
- `.github/workflows/ci.yml` — the CI jobs and steps.
- `tests/conftest.py` — the temporary HOME/state isolation the test gate relies
  on.
