# Test isolation and fixture cleanup — findings (Task A1)

- Generated: 2026-10-31 (Asia/Calcutta).
- Method: read-only inspection of the repository. I did **not** read, list or
  delete anything under `~/.local/state/agy-mcp` in this task.
- Baseline: `791` tests collected on `main` (`23bb1aa`).

## 1. How the state directory is resolved

There is exactly one default resolver. Everything else derives from it or
receives an explicit `base_state_dir`.

| Layer | Location | Behaviour |
|---|---|---|
| Resolver | `src/agy_mcp/jobs.py:28-34` (`get_base_state_dir`) | explicit `base_state_dir` wins; else `$XDG_STATE_HOME/agy-mcp`; else `Path.home()/.local/state/agy-mcp` |
| Job dir | `src/agy_mcp/jobs.py:37-39` (`get_job_dir`) | `get_base_state_dir(...)/jobs/<job_id>` |
| Manager | `src/agy_mcp/manager.py:137-138` | `JobManager.__init__` stores `self.state_dir = get_base_state_dir(base_state_dir)` and `self.home = (home or Path.home()).resolve()` |
| Server | `src/agy_mcp/server.py:543` | `setup_server` resolves `--state-dir` or the default and `mkdir`s it at startup |
| Worktree preflight | `src/agy_mcp/worktree.py:79`, `:228` | resolves the state dir to reject repositories that overlap it |
| Recovery | `src/agy_mcp/recovery.py:116` | `list_jobs(base_state_dir=state_dir)` and `get_job_dir(..., base_state_dir=state_dir)` |
| Persistence | `src/agy_mcp/jobs.py:172-214` (`save_job`, `get_job`, `list_jobs`) | every write/read derives from the resolver |

`AGY_MCP_STATE_DIR` is **not read** by `jobs.py`; it is an installer/run.sh
convention only (`tests/conftest.py:121-123`). `$XDG_STATE_HOME` and `$HOME`
are the two environment knobs production actually honours.

## 2. How the test suite reached the real store (historical)

`docs/reports/stale-jobs-report.md` documents the result: **332** live
`job.json` records, **312** of them pytest fixtures (`repo` under
`/tmp/pytest-of-<user>/pytest-*/...`), plus 40 stale queued fixture records.

The direct cause was call sites that created or saved jobs without an explicit
state dir, so `get_base_state_dir(None)` fell through to the real HOME. The
isolation commit `aaf7408` ("test(state): isolate live state and fail on
live-state writes", 2026-10-30) fixed the two known offenders:

- `tests/test_f2.py` — three `create_job(...)` calls added `base_state_dir=state_dir`
  (pre-fix lines 42, 74, 122 in `aaf7408^`).
- `tests/test_f9.py` — one `create_job(...)` call added `base_state_dir=state_dir`
  (pre-fix line 65 in `aaf7408^`).

Current check (AST scan of `tests/**/*.py`): **0** `JobManager(...)` or
`create_job(...)` call sites omit `base_state_dir`. All explicit-state tests
pass a `tmp_path`-derived directory.

## 3. What the current isolation (commit `aaf7408`) does

`tests/conftest.py`:

- `:20` captures `_REAL_LIVE_STATE_DIR = Path.home()/.local/state/agy-mcp` at
  conftest import time, before the fixture changes HOME.
- `:96-136` provides a session-scoped autouse fixture `isolated_state` that
  creates `home/`, `xdg-state/`, `xdg-state/agy-mcp/`, then sets `HOME`,
  `XDG_STATE_HOME` and `AGY_MCP_STATE_DIR` (`:118-123`).
- `:63-94` installs a `sys.addaudithook` write guard (`:125`) that raises
  `RuntimeError("BLOCKED by test isolation guard...")` for write-opens and
  mutating filesystem events under the real state dir.
- `tests/test_state_isolation_guard.py` pins the env redirect and the audit
  hook.

This is a good write barrier, but it is not the full guarantee Task A asks for.

## 4. Remaining gaps against the Task A spec

1. **Redirect starts too late for imports.** `isolated_state` is a fixture, so
   it runs after pytest has imported every test module. Any module-level
   default/store constructed during collection still resolves the real path;
   only writes are caught, and only after the hook is installed at fixture
   time.
2. **Resolution is not guarded.** `get_base_state_dir(real_path)` (and
   `JobManager(base_state_dir=real_path)`) returns the real path without
   raising; the first failure happens only when something writes. The spec asks
   for an immediate failure at resolution.
3. **No canary for an explicit real path.** No test constructs a store with the
   real default path and asserts that the guard rejects it.
4. **No HOME-swap test.** Nothing proves that a test which monkeypatches HOME
   to another temp dir cannot land on the operator's real home.
5. **No static tripwire.** A future test can hardcode `Path.home()` or
   `~/.local/state/agy-mcp` and nothing in the suite notices.
6. **Subprocesses rely on inherited env only.** e.g.
   `tests/test_server_e2e.py:800` launches `python -m agy_mcp` without
   `--state-dir`; it is safe only because the session fixture exported HOME to
   the child. A child that scrubs HOME falls back to the passwd entry
   (`Path.home()`), i.e. the real home.

## 5. Tests that depend on the environment redirect

These do not pass a state dir and are therefore the ones the guard must keep
honest (all are safe today because the session fixture runs before them):

- `tests/test_server_e2e.py:800` (`python -m agy_mcp` startup-refusal test; no
  `--state-dir`; inherits the session env).
- Every `--state-dir`-less subprocess test would regress the moment HOME is
  scrubbed; the static tripwire in A3(d) and the resolution guard are the
  defence.

## 6. What Task A2 must add

- Move the temp redirect to conftest **import time** (or `pytest_configure`,
  which is still before test-module import), so no import can observe the real
  default; keep the session fixture for a per-session path under the pytest
  temp root and for cleanup.
- Wrap the resolver in the test harness so that both
  `agy_mcp.jobs.get_base_state_dir(...)` and `JobManager(base_state_dir=...)`
  raise immediately with a message containing `live agy-mcp state`.
- Keep the audit hook as the write barrier.
- Freeze tests (a)-(d) in `tests/test_live_state_isolation.py` (see the task
  brief); they are committed before the implementation.

## 7. What distinguishes historical fixture rows (input for Task B)

From `docs/reports/stale-jobs-report.md` §1 and §7:

- `repo_path` under a pytest temp tree: `/tmp/pytest-of-<user>/pytest-<n>/...`.
- Terminal states (`failed` 272, `done` 15) or stale `queued` (40).
- `pid=null` and no live process for every fixture row.
- Titles are test descriptions ("F2 Review Test", "F9 Resume Verify Test", …).
- Real rows are under user repos (`clipsync-lab`, `agy-scratch`), some with
  `conversation_id`, some in `needs_review`.

The only unambiguous marker that a job record was produced by the suite is the
repository living under a pytest temp root. Task B therefore makes that marker
necessary (not sufficient on its own): terminal state, no live process, older
than the retention window, no symlinks, not referenced by a kept job, and an
explicit `--store`/`--yes` opt-in are all required before anything is deleted.
