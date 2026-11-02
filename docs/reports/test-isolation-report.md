# Test isolation and safe fixture cleanup — report

- Branch: `fix/test-isolation` (not merged; Harsh merges).
- Base: `main` at `23bb1aa` ("merge: calibrated quota estimate (D85)").
- Frozen contract commit: `f1c1295` ("test: freeze live-state isolation and cleanup contract").
- Gated implementation HEAD: `beba554` ("fix: keep utime calls working through the dir_fd guard").
- Date: 2026-10-31 (Asia/Calcutta).
- I did not read, write, list or delete the real store with filesystem tools,
  and I deleted no real job row or conversation. `agy_list`/`agy_status` were
  used only for this job's lifecycle; I never opened a state file.

## 1. What was wrong

`docs/reports/stale-jobs-report.md` found 332 live `job.json` records, 312 of
them pytest fixtures under `/tmp/pytest-of-<user>/pytest-*/...`, plus 40 stale
queued fixture records. The resolver is `jobs.get_base_state_dir`
(`src/agy_mcp/jobs.py:28-34`): explicit `base_state_dir`, else
`$XDG_STATE_HOME/agy-mcp`, else `$HOME/.local/state/agy-mcp`. Call sites that
omitted the explicit directory (historically `tests/test_f2.py:42/74/122` and
`tests/test_f9.py:65`, before `aaf7408`) fell through to the real HOME.

The current suite has no `JobManager(...)`/`create_job(...)` call without an
explicit state dir (AST scan: 0), but the isolation from `aaf7408` was a
write-only audit hook installed by a session fixture: it started after
test-module import and did not fail at resolution.
`docs/reports/test-isolation-findings.md` has the file:line analysis.

## 2. Task A — tests can never touch the live store

`tests/conftest.py` (test harness only; no production behaviour change):

1. Captures the real state path at conftest import, before any redirect, and
   stores it in `_AGY_MCP_CAPTURED_REAL_LIVE_STATE` so a module reload or an
   xdist worker cannot recapture the already-redirected HOME.
2. Creates a bootstrap temp HOME at conftest import and points `HOME`,
   `XDG_STATE_HOME` and `AGY_MCP_STATE_DIR` at it. Test modules import later,
   so no import observes the real default. The session-scoped autouse
   `isolated_state` fixture re-points them at a per-session tree under the
   pytest temp root and exposes `home`, `xdg_state`, `state_env`,
   `real_live_state`.
3. Installs an in-process resolution guard before test modules import:
   `agy_mcp.jobs.get_base_state_dir(...)` (and the copies bound in
   `agy_mcp.manager` and other already-imported modules) raises `RuntimeError`
   containing `live agy-mcp state` when it resolves the real state dir or a
   path under it.
4. Keeps the `sys.addaudithook` write barrier (`BLOCKED by test isolation
   guard`).
5. Adds dir_fd wrappers for `os.open`, `os.unlink`/`remove`, `os.rmdir`,
   `os.mkdir`, `os.chmod`, `os.chown`, `os.utime`, `os.symlink`, `os.link`,
   `os.rename` and `os.replace`. `sys.audit` does not expose `dir_fd` for
   relative path arguments, so the wrappers resolve the fd via
   `/proc/self/fd`. This closed the HIGH-severity `dir_fd` bypass found in the
   first review.
6. Exempts the checked-out repo root (`_REPO_ROOT`) from the write guard. The
   agy wrapper verifies in a worktree that itself lives under the live store
   (`jobs/<id>/work`); pytest must be able to write its cache/pyc there. This
   is a no-op when the checkout is outside the store, and explicit real-state
   paths outside the repo still raise.

Frozen tests (`tests/test_live_state_isolation.py`): (a) resolved default is
under the pytest temp root; (b) canary `get_base_state_dir(real)` and
`JobManager(base_state_dir=real)` raise; (c) a sentinel HOME never resolves to
real; (d) static scan for tests hardcoding/re-deriving the real path.

## 3. Task B — safe fixture-row lister/cleaner

New `src/agy_mcp/cleanup_fixtures.py`, runnable as
`python -m agy_mcp.cleanup_fixtures`.

- `--store PATH` is always required. Default is a read-only dry run.
  Deletion needs both `--yes` and `--store`; `--min-age-days` default 1.0;
  `--json` prints one JSON object.
- Refuses (exit 2): missing/non-directory store, symlinked store,
  symlinked/missing `jobs/`, resolved store outside resolved `$HOME`, negative
  age.
- **Candidate signature (ALL required)**: direct child of `store/jobs/`; real
  directory, canonical lowercase UUID via `fullmatch`; `job.json` regular file
  <= 1 MiB with matching `job_id`; terminal state (`done`/`failed`/`cancelled`);
  absolute `repo_path` under `tempfile.gettempdir()` with
  `pytest-of-*` → `pytest-<n>` → `test_...<n>` → at least one component; store
  and repo must not share the same pytest basetemp; pid absent or a positive
  non-live integer; age > threshold from timezone-aware `updated_at`
  (fallback `created_at` only when `updated_at` is absent; unparseable values
  keep the row); no symlinks and no unreadable subtrees; no follow-up edge from
  a kept job; if any kept real job's `job.json` is unreadable/unparseable, all
  candidates are kept (fail closed).
- Deletion re-validates every candidate, writes an atomic ids-only manifest
  `store/jobs/.cleanup-manifest-<UTC>.json` (`{"removed_ids": [...]}`, mode
  0600) **before** deleting, then removes only the enumerated job directory
  bottom-up without following symlinks. It never touches agy conversations or
  anything outside `store/jobs/`.

The signature is deliberately conservative. The only unambiguous marker that a
row was produced by the suite is a repository under a pytest per-test temp
directory; everything else is "kept (ambiguous)" with a reason, and Harsh
reviews the dry run before the deletion command. Frozen tests:
`tests/test_cleanup_fixtures.py` (14 tests, temp stores only).

## 4. Review

### Round 1 (fresh subagent, no agy/gate context)

10 defects, every check re-run by me. Confirmed and repaired:

| # | Sev | Defect | Fix | Re-check |
|---|---|---|---|---|
| A1 | high | `dir_fd`-relative `os.open`/`os.unlink`/… bypassed the audit hook | `/proc/self/fd` wrappers | blocked |
| A3 | low | reload recaptured the redirected HOME | capture sentinel | reload keeps real path |
| B4 | med | unparseable `updated_at` fell back to `created_at` and became a candidate | strict timestamp rule | 0 candidates |
| B5 | med | kept job with symlinked `job.json` hid its follow-up edge | fail closed on unreadable kept record | 0 candidates |
| B6 | med | `test_...` ancestor could match a repo under a temp HOME | same-basetemp exclusion | 0 candidates |
| B7 | low | trailing newline accepted as UUID | `fullmatch` | 0 candidates |
| B8 | low | `pid: 0` treated as dead | non-positive pid kept | 0 candidates |
| B9 | low | relative `repo_path` resolved against the CLI cwd | absolute-path requirement | 0 candidates |
| B10 | low | unreadable subtree hid a symlink | `os.walk(onerror=…)` fails closed | 0 candidates |

Residual from round 1 (not repaired): **A2 (medium)** — the guard is in-process
only; a child that scrubs `HOME`/`XDG_STATE_HOME` resolves the real path
unguarded. The suite's env-scrubbing children currently run the fake `agy`,
which does not touch state.

### Round 2 (fresh subagent, repaired commit)

Nine further defects, every check re-run by me. **Confirmed, not repaired: the
two-round repair cap was reached.** They are adversarial (deliberate `posix`/
fd/hardlink use in tests) or malformed-input/contrived-store cases, not the
normal CLI path:

| # | Sev | Finding (file:line) | Check result |
|---|---|---|---|
| R1 | high | `guarded_link` checks only `dst_dir_fd`; `src_dir_fd` can hardlink a live file into tmp (`conftest.py:228-237`) | `A1 link: PWNED` |
| R2 | high | direct `posix.open/unlink/rmdir/mkdir(..., dir_fd=…)` bypasses the `os.*` wrappers (`conftest.py:259-270`) | `A2 posix: False True` |
| R3 | med | fd forms (`os.utime(fd, …)`, `os.fchmod`, `os.fchown`, `os.ftruncate`) mutate live metadata through a read fd (`conftest.py:102-121`) | `mtime zeroed through read fd: True` |
| R4 | med | in-process-only guard / scrubbed-HOME child (`conftest.py:124-125`) | prints real path; no write |
| R5 | low | guard is mutable: `_GUARD_ACTIVE=False`, or env+reload retargets it (`conftest.py:16-22`) | `A4 global off: True` |
| R6 | med | huge pid (`10**30`) raises `OverflowError`, aborting the scan with exit 1 (`cleanup_fixtures.py:131`) | rc 1, no report |
| R7 | med | `--min-age-days nan` passes `<0`; `age < nan` is false, so `--yes` deletes a fresh row (`cleanup_fixtures.py:472-474`) | deleted 1 fresh row |
| R8 | low | kept UUID dir with no `job.json` is skipped in the edge scan, so candidates are deleted (`cleanup_fixtures.py:366-369`) | candidate gone |
| R9 | low | ancestor symlink in the store path is followed (`cleanup_fixtures.py:478-497`) | deleted through ancestor symlink |

Impact: the default dry-run path, the frozen contract and every normal test
path are safe; the R-defects require deliberate adversarial test code (R1-R5)
or corrupt/malformed input/contrived store paths (R6-R9). The next branch
should close at least R1, R2, R6, R7 and R9 before treating the in-process
guard as a hard boundary.

## 5. Gate (scripted; logs kept out of this report)

| Check | Command | Result |
|---|---|---|
| Frozen files unchanged | `git diff f1c1295 HEAD -- tests/test_live_state_isolation.py tests/test_cleanup_fixtures.py` | empty |
| New tests 20× in a row | `gate-run … --repeat 20 -- .venv/bin/python -m pytest -q tests/test_live_state_isolation.py tests/test_cleanup_fixtures.py` | ALL 20 PASS |
| Full suite 5× in a row (final) | `gate-run … --repeat 5 -- .venv/bin/python -m pytest -q` | ALL 5 PASS, 809 tests each |
| Ruff | `.venv/bin/ruff check src tests scripts` | All checks passed |
| Scope | `git diff --name-status main...HEAD` | only `tests/conftest.py`, new `src/agy_mcp/cleanup_fixtures.py`, the two frozen test files, `docs/**`, `SPEC.md`, `CHANGELOG.md` |

Frozen sha256:
`85bd8c37c70521788f6b5d76bf3510c3fe366eda389444a2229633b513faf62c`
(`tests/test_live_state_isolation.py`) and
`e4fc8136be27eca13408fb65c2a00bc654a5767d0af6378c09934c0601b74dc1`
(`tests/test_cleanup_fixtures.py`).

Full-suite runs on the way there: 2 PASS on an intermediate commit before
review repairs (discarded), 1 FAIL on an intermediate commit (the `utime`
wrapper bug, diagnosed from the gate log and fixed), then 5 PASS on the final
commit.

**How many times the suite ran:** the full suite ran 8 times (2 intermediate
PASS, 1 intermediate FAIL, 5 final). Targeted runs: the freeze run on base
(16 failed / 2 passed), the agy wrapper verify (1 failed), focused runs after
each repair, the 20× batches twice, a broad 140-test subset and a 57-test
failing subset after the `utime` fix.

## 6. The agy job

- id `9e74c60a-bb30-4983-a805-d05e340196d6`, title "test isolation + safe
  fixture cleanup", branch `agy/test-isolation-safe-fixture-cleanup-4f411ea5`,
  based on `3ff2f4a`, **3 commits** (`3b1ceaf` test harness, `a17dbd8` cleaner,
  `ac27061` docs). Final state: **cancelled**.
- First-run wall clock **764.003 s**; elapsed at cancellation 1213.9 s; final
  job lifetime as listed by agy_list 3837.15 s.
- Measured quota (authoritative `usage_cost`): **4.21 pp of the 5h bucket,
  1.2 pp of the weekly bucket** (`concurrent_jobs=1`). Tokens: 451,058 total
  (input 312,527; output 138,531; thinking 113,025; cache-read 6,783,335).
- The wrapper verify ran once and failed (3 failed, 15 passed, 11 teardown
  errors). Diagnosis: the "real" repo in one frozen test sat under the session
  HOME, which is itself under `/tmp/pytest-of-<user>/pytest-N`, and the audit
  hook resolved `shutil.rmtree`'s dir_fd-relative names against the worktree
  cwd (inside the live store). The job then reported plan complete and sat in
  `job_finalizing`; three `agy_resume` attempts returned `job_finalizing`, so I
  merged its branch into the feature branch and finished the repair manually.
  The job was cancelled; no real job was harmed.
- Manual repair commits: `ae7530e` (signature + repo-root exemption), `2fe7120`
  (review defects), `beba554` (utime wrapper). Two repair rounds total.
- Merged with `git merge --no-ff` into `fix/test-isolation` only; `main` was
  never touched and nothing was pushed.

## 7. What was not verified

- The contents, count or age distribution of Harsh's real store. The tool has
  only ever run against temp stores; the real dry run is Harsh's step.
- Real-run cleanup of actual rows (by design; I deleted nothing real).
- A subprocess that scrubs HOME and writes state (in-process guard only, A2/R4).
- xdist/forkserver workers (no xdist in the venv) and non-Linux `/proc`.
- The final agy wrapper verify after the manual repairs; the gate above ran in
  the main checkout, which is the tree Harsh merges.
- The R1-R9 adversarial/malformed-input defects above (repair cap reached).

## 8. Commands for Harsh

1. **Merge** (from the main checkout; no push from me):

   ```bash
   cd "~/Documents/GITHUB PROJECTS/agy-mcp"
   git switch main
   git merge --no-ff fix/test-isolation
   .venv/bin/python -m pytest -q
   ```

2. **Dry run against the real store** (read-only; paste this output back):

   ```bash
   cd "~/Documents/GITHUB PROJECTS/agy-mcp"
   .venv/bin/python -m agy_mcp.cleanup_fixtures \
     --store "$HOME/.local/state/agy-mcp"
   ```

   Add `--json` for the machine-readable report. It prints the candidate count,
   every candidate id/state/repo, and the "kept (ambiguous)" count with reasons.
   Nothing is written.

3. **Delete only after reviewing that dry-run output** (writes the manifest
   first, then removes only the enumerated job folders):

   ```bash
   cd "~/Documents/GITHUB PROJECTS/agy-mcp"
   .venv/bin/python -m agy_mcp.cleanup_fixtures \
     --store "$HOME/.local/state/agy-mcp" --yes
   ```

   The manifest is `$HOME/.local/state/agy-mcp/jobs/.cleanup-manifest-<UTC>.json`
   and contains only `{"removed_ids": [...]}`. Anything ambiguous is kept and
   explained in the output.

Note: the 40 stale *queued* fixture rows and any fixture row whose repository
is not under a pytest per-test directory will be shown as kept (ambiguous), not
deleted. The dry-run count may therefore be lower than the historical 312.
