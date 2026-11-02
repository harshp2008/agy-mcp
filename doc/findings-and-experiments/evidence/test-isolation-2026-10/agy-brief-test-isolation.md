# agy brief — test isolation and safe fixture cleanup

Status: historical (as of 2026-10-31)
Audience: maintainers, contributors

This is the spec for one agy job. Read it fully, then implement. The frozen
tests are the contract; the wrapper runs them after your change and sends you
the failures.

## User demand

The test suite must be unable to touch the operator's live state directory
`~/.local/state/agy-mcp`. Separately, add a safe, dry-run-first tool that lists
historical pytest fixture rows and, only with explicit opt-in, removes them.
Nothing real is to be removed by this job.

## Frozen contract (DO NOT EDIT)

- `tests/test_live_state_isolation.py`
- `tests/test_cleanup_fixtures.py`

They are committed before you start. Do not edit, rename, skip, xfail,
monkeypatch around, or special-case them. If a frozen test contradicts this
brief or the codebase, **stop and report `needs_decision`** with the test name
and the exact reason. Never weaken or special-case a frozen test.

Verify command (the wrapper runs it):
`~/.venv/bin/python -m pytest -q tests/test_live_state_isolation.py tests/test_cleanup_fixtures.py`

Do not run the test suite yourself; the wrapper runs the verify command and
sends you the result. You may run `ruff` and the codemap generator.

## Allowed scope (change nothing else)

- `tests/conftest.py` — the only test file you may edit.
- `src/agy_mcp/cleanup_fixtures.py` — new module (Part B).
- `docs/**`, `doc/design/spec.md`, `CHANGELOG.md` — documentation.
- `scripts/gen_codemap_index.py` may be run with `--write`; it edits
  `doc/project/code-layout.md`.

Do **not** touch `src/agy_mcp/jobs.py`, `manager.py`, `server.py`, any other
production module, any other test file, or `tests/isolation/test_state_isolation_guard.py`.
Part A must be implemented entirely in `tests/conftest.py` (test harness), with
no production behaviour change.

## Part A — tests can never touch the live store

Requirements:

1. Capture the operator's real home/state path at conftest **import time**,
   before anything redirects HOME:
   `real_live_state = Path.home() / ".local" / "state" / "agy-mcp"`.
2. At conftest import time (before pytest imports any test module), create a
   bootstrap temp home and point `HOME`, `XDG_STATE_HOME` and
   `AGY_MCP_STATE_DIR` at it. Nothing imported later may observe the real
   default. The existing session-scoped autouse `isolated_state` fixture must
   re-point these to a per-session tree under `tmp_path_factory` and expose the
   same attributes it does today (at least `home`, `xdg_state`, `state_env`,
   `real_live_state`).
3. Install an in-process **resolution guard** before test modules are imported:
   any call to `agy_mcp.jobs.get_base_state_dir(...)` that returns the real
   state dir or anything under it must raise `RuntimeError` whose message
   contains `live agy-mcp state`. This must cover:
   - `import agy_mcp.jobs; agy_mcp.jobs.get_base_state_dir(real)`
   - `JobManager(base_state_dir=real)` (patch the name already imported into
     `agy_mcp.manager` too, defensively patch already-imported `agy_mcp.*`
     modules that bound the function).
   Compare resolved realpaths (`Path.resolve(strict=False)`); never block the
   temp paths.
4. Keep the existing `sys.addaudithook` write barrier for writes/renames/mkdirs
   under the real state dir. Its message must keep containing
   `BLOCKED by test isolation guard`. Exempt the checkout under test
   (`_REPO_ROOT`) so the wrapper can verify in a worktree that itself lives under
   the live store; this is a no-op outside the store.
5. Remove the fixture's re-capture of `_REAL_LIVE_STATE_DIR` (it currently
   overwrites the import-time value with the already-redirected HOME).
6. All existing tests must keep passing; do not change their behaviour.

Do not add an environment-gated branch to production code. The redirect and
guard are test-harness concerns.

## Part B — `python -m agy_mcp.cleanup_fixtures`

### CLI

- `--store PATH` is **always required**. Missing it (including `--yes` without
  it) exits 2 and mentions `store` on stderr.
- Default mode is a **dry run**: list, print counts/reasons, change nothing.
- `--yes` enables deletion; it still requires `--store`.
- `--min-age-days FLOAT` (default `1.0`).
- `--json` prints exactly one JSON object to stdout (no human lines, no
  logging to stdout). Without `--json`, print a human summary with the store,
  candidate/kept counts, and each id with its reasons.
- Exit `0` on success (dry run or completed delete), `2` on any refusal, `1`
  on unexpected internal error.

Refuse (exit 2) when:

- the store is missing or not a directory;
- the store path itself is a symlink, or `store/jobs` is a symlink;
- the resolved store is not inside the resolved `$HOME` (containment, not
  string prefix).

Never follow a symlink; never touch anything outside `store/jobs/`. Never
touch `~/.gemini`, agy conversations, or any non-store path. Never invoke agy.

### Candidate signature (ALL must hold)

A direct child of `store/jobs` is a candidate only if every one of these holds;
anything else is reported in **kept (ambiguous)** with a reason:

1. It is a real directory (not a symlink), its name is a canonical UUID, and
   `job.json` is a regular file no larger than 1 MiB whose `job_id` equals the
   folder name.
2. `state` is terminal: one of `done`, `failed`, `cancelled`. (So running,
   queued, needs_review, needs_decision are always kept.)
3. `repo_path` resolves under a pytest temp root: under `tempfile.gettempdir()`,
   with components matching `pytest-of-*`, then `pytest-<digits>`, then a
   per-test `test_...<n>` component, with at least one component after it
   (for example `.../repo`), and the store must not share that pytest basetemp.
4. `pid` is absent/null, or that pid is not alive (`os.kill(pid, 0)`; treat
   `PermissionError` as alive). No live process may be referenced.
5. The job is older than `--min-age-days`, using `updated_at` (fallback
   `created_at`, both timezone-aware ISO 8601). Unparseable/missing timestamps
   are kept.
6. No symlink exists anywhere inside the job directory (refuse to enumerate
   through one); an unreadable subtree is ambiguous and kept.
7. No **non-candidate** job record references this job id: scan every raw
   `job.json` string value of every kept job; if any equals the candidate id,
   keep the candidate with a "referenced by kept job" reason. (This protects a
   follow-up edge from a kept row to a candidate.) If a kept real job's
   `job.json` cannot be read/parsed, keep every candidate (fail closed).

Candidate entries in the JSON report must include a non-empty `reasons` list;
kept entries must include a non-empty `reasons` list explaining why they were
kept.

### Report JSON (exact keys the frozen tests read)

```json
{
  "store": "<resolved absolute path>",
  "dry_run": true,
  "min_age_days": 1.0,
  "candidates": [{"job_id": "...", "title": "...", "state": "...", "repo_path": "...", "reasons": ["..."]}],
  "kept":       [{"job_id": "...", "reasons": ["..."]}],
  "candidate_count": 0,
  "kept_count": 0,
  "deleted_ids": [],
  "deleted_count": 0,
  "manifest": null
}
```

In delete mode `dry_run` is `false`, `deleted_ids` is the list actually removed,
and `manifest` is the absolute path of the manifest (or `null` if none).

### Deletion

- Re-validate every candidate immediately before writing the manifest (real
  dir, no symlink, no symlink entries). Anything that fails becomes kept.
- Write the manifest **before** deleting, inside `store/jobs/` as
  `.cleanup-manifest-<UTC timestamp>.json` (atomic replace, mode 0600). The
  content is exactly `{"removed_ids": ["<id>", ...]}` — ids only, no paths, no
  file contents.
- Remove only the enumerated job directory contents: unlink regular files,
  rmdir directories, bottom-up, never following a symlink, never touching
  `store/jobs` itself or any sibling. Verify each target is under
  `store/jobs/<job_id>`.
- Dry run writes nothing (no manifest, no files, no directories).

## Non-goals

- No real-store access, no cleanup of the real store, no agy job runs, no
  network, no changes outside the allowed scope.
- Do not add new tests; do not edit frozen tests or other tests.
- Do not change the production state resolver or any existing MCP tool.

## Docs to update

- `doc/design/spec.md`: a short section/appendix for the new CLI contract (safety rules,
  JSON shape, exit codes, signature).
- `CHANGELOG.md`: one entry.
- `doc/design/decisions.md`: one D-number for the cleaner signature and one for the
  test-harness isolation guard.
- `doc/project/code-layout.md`: regenerate with
  `~/.venv/bin/python scripts/gen_codemap_index.py --write`
  from the repo root (the wrapper's venv) and add the new module to the
  navigation table if the generator leaves manual sections untouched.
- Do not rewrite unrelated docs.

## Return contract

Reply with, at most 150 tokens, no pass/fail claims:

- commit subject(s) and sha(s);
- the exact list of files changed;
- anything you could not do and why.

The wrapper audits the diff and runs the verify command; it reports the
result, not you.

## Sources

- `tests/test_live_state_isolation.py` — frozen test isolation suite.
- `tests/test_cleanup_fixtures.py` — frozen fixture cleaner test suite.
- `tests/conftest.py` — live state guard implementation.
