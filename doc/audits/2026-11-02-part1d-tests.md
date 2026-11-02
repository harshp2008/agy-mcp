# Part 1d — Test organization (F)

Status: current

Measured, not estimated:

```
$ ls tests/*.py | wc -l
107                      # 106 test_*.py + conftest.py
$ wc -l tests/*.py tests/helpers/*.py | tail -1
  32560 total
$ pytest --collect-only -q | grep -c "::"
1711
$ pytest -q --durations=30
1711 passed in 337.68s (0:05:37)
```

Files: `tests/` (106 `test_*.py`), `tests/conftest.py`, `tests/helpers/{__init__,make_repo}.py`,
`tests/fake_agy/{agy,README.md}`, `tests/fixtures/` (23 files).

---

## F1 — Half the suite tests one module, and one file is a quarter of the suite

**Severity:** high (structure) · **Effort:** L · **Risk of action:** high only if frozen names change

```
$ pytest --collect-only -q | grep "::" > /tmp/collected.txt
$ wc -l < /tmp/collected.txt                                 1711
$ grep -c "test_cleanup_jobs" /tmp/collected.txt             887     (51.8%)
$ grep -cE "test_cleanup_jobs|test_cleanup_fixtures|test_cleaner_" /tmp/collected.txt   916  (53.5%)
```

| Group | Files | Lines | Cases |
|---|---|---|---|
| `test_cleanup_jobs*.py` (12 files) | 12 | **9,461** | **887** |
| of which `test_cleanup_jobs_differential.py` alone | 1 | 1,284 | **414** (24.2%) |
| `test_f*.py` (numbered findings, 27 files) | 27 | 3,099 | 75 |
| `test_b4_*.py` ("ITEM 1-7", 8 files) | 8 | 918 | 47 |
| `test_smoke_fix1..5` | 5 | 570 | 31 |
| `test_agymcp_fixes2_*` | 4 | 692 | 16 |
| everything else (topic-named) | 50 | 17,351 | 655 |
| **Total** | **106** | **32,091** | **1,711** |

`src/agy_mcp/cleanup_jobs.py` is 1,810 of 16,112 source lines (11%) but 51.8% of
the tests. Whatever else is wrong with the codebase, the *test* allocation is
not proportional to risk: `manager.py` (1,737 lines, the actual lifecycle
engine) has 49 cases in `test_manager.py` + 29 in `test_resume_and_commitmsg.py`.

## F2 — Test names encode fix rounds, not topics

**Severity:** high · **Effort:** M · **Risk of action:** medium (frozen files)

Evidence, straight from the docstrings:

```
test_cleanup_jobs.py                 "FROZEN acceptance tests for python -m agy_mcp.cleanup_jobs CLI."
test_cleanup_jobs_differential.py    "Differential review test harness … against real git ground truth."
test_cleanup_jobs_fixes.py           "Acceptance tests for 9 cleanup_jobs review findings."
test_cleanup_jobs_fixes2.py          "… (fix round 2)."
test_cleanup_jobs_fixes3.py          "… (fix round 3)."
test_cleanup_jobs_fixes4.py          "FROZEN … (fix round 4)."
test_cleanup_jobs_fixes5.py          "FROZEN … (fix round 5)."
test_cleanup_jobs_fixes6.py          "FROZEN … (fix round 7 bare-repo cases)."
test_cleanup_jobs_fixes7.py          "FROZEN … round 8 (linked-worktree cases, R7-R9)."
test_cleanup_jobs_fixes8.py          "FROZEN … round 9 (git metadata validation, R10-R12)."
test_cleanup_jobs_fixes9.py          "FROZEN … round 10 (non-git hardening, R13-R16)."
test_cleanup_jobs_fixes10.py         "Round 11 acceptance tests … (D100)."
```

Note the sequence `fixes` → `fixes2` → … → `fixes10` with **no `fixes1`**, and
`fixes6` documenting "round 7". Round numbers in filenames mean a reader cannot
find the test for a behavior without opening files; `grep` for behavior works
only because the *content* happens to be greppable.

Same pattern elsewhere: `test_f1..test_f28` (27 files, numbered by review
finding, `test_f24` absent), `test_b4_plan/report/preamble/…` (by work item),
`test_smoke_fix1..5`, `test_agymcp_fixes2_a1/a2/a3/c4`, `test_quickwins_*`.

## F3 — Real overlap between the round files

**Severity:** medium · **Effort:** M · **Risk of action:** medium

Each round file re-tests the same classifier end to end. Same-shape cases:

| Behavior under test | Files that assert it |
|---|---|
| terminal state (`done/failed/cancelled`) required | `test_cleanup_jobs.py`, `_fixes.py`, `_fixes3.py`, `_fixes8.py` |
| `job.json` symlink / missing / oversized is kept | `_fixes.py`, `_fixes4.py`, `_fixes9.py`, `_fixes10.py` |
| worktree registered ⇒ kept | `_fixes6.py`, `_fixes7.py`, `_fixes8.py`, `_differential.py` |
| `--list` project summary | `_fixes3.py`, `_fixes9.py`, `_fixes10.py` |
| manifest planned/completed written | `_fixes.py`, `_fixes4.py`, `_fixes9.py` |

Quantified overlap: `test_cleanup_jobs_differential.py` alone parameterizes 390
layout×mutation cells (`CASE_PARAMS`) + 24 JSON mutations + 9 layout baselines
= 414 cases; `_fixes7.py` (129 cases) and `_fixes8.py` (176 cases) cover matrix
subsets of the same layouts by hand. That is why 887 cases exist for one CLI.

**Proposed action:** keep all cases (they are the safety net) but reorganize
into topic modules and let the round files become thin compatibility wrappers —
see F7. **Do not** delete duplicates without Harsh: the duplicates are what
caught fail-open bugs in rounds 7-10, and the differential file is frozen.

## F4 — Slow tests: where the 5m38s goes

**Severity:** medium · **Effort:** S-M · **Risk of action:** low

Top of `--durations=30` (full output: `~/agy-review/audit/pytest-full.txt`):

```
7.63s  test_f3.py::test_f3_cancel_during_slow_verify
5.92s  test_f1.py::test_f1_resume_serializes_with_previous_supervisor_finalization
5.49s  test_f1.py::test_f1_invalid_commit_msg_kills_process_and_resumes
4.13s  test_cleanup_jobs_fixes7.py::test_r7_matrix_fails_closed[submodule-config-fifo]
4.13s  …[submodule-HEAD-fifo]
4.10s  …[linked_wt-HEAD-fifo]      (9 of the top 30 are FIFO cells; 11 exist in the file)
3.46s  test_server_e2e.py::test_server_e2e_crash_recovery_over_protocol[asyncio]
3.29s  test_installer_chapter.py::test_second_install_is_byte_identical[generic]
…      (13 of the top 30 are test_installer_chapter.py, ≈2.8-3.3s each)
```

Root causes, verified in the test code:

| Cause | Cost | Evidence |
|---|---|---|
| FIFO cells call **git itself** with `timeout=2`, twice, and git blocks on the FIFO | 11 × ~4.1s ≈ **45s** | `test_cleanup_jobs_fixes7.py:301-307` (mkfifo), `:745-765` (`subprocess.run(..., timeout=2)` twice) |
| Installer chapter runs the real 1,022-line `installer/install.sh` in subprocesses, with pip installs | 44 cases × ~2.8s ≈ **2min** | `test_installer_chapter.py` (1,081 lines), 44 collected cases |
| Supervisor/finalize tests wait on real timing windows | ~10-20s total | `test_f1.py`, `test_f3.py`, `test_f20.py` |

FIFO cells are slow *by construction* (the git oracle must time out to prove
git cannot read the repo) and each cell is a distinct corruption; **do not
optimize by lowering the timeout** without checking that 2s is still
deterministic on a loaded machine. The installer cases are inherently
subprocess-heavy.

## F5 — Shared fixtures live in a 469-line `conftest.py` that mutates the process at import

**Severity:** medium · **Effort:** L · **Risk of action:** high (security machinery)

`tests/conftest.py` is not a fixture file; it is the test-isolation enforcement
layer:

| Lines | What it does |
|---|---|
| 19-21 | raises the interpreter recursion limit to 5000 (needed only to *build* a deep payload) |
| 25-34 | captures the real live state dir **before** anything redirects `HOME` |
| 40-50 | creates a bootstrap temp `HOME`/`XDG_STATE_HOME`/`AGY_MCP_STATE_DIR` and rewrites `os.environ` at import time |
| 59-135 | `IsolatedStatePaths`, `_is_under_live_state`, `_is_write_open`, `_live_state_write_guard` |
| 137-296 | installs an **audit hook** + a `dir_fd` guard that rejects writes into the live store |
| 297-311 | wraps `jobs.get_base_state_dir` |
| 313-356 | session fixture `isolated_state` |
| 357-378 | monkeypatches `subprocess.Popen.__init__` to track spawned PIDs |
| 379-394 | `fake_home`, `safe_git_env` fixtures |
| 395-469 | `is_pid_alive`, `pytest_sessionstart`, `pytest_sessionfinish` |

Only two fixtures (`fake_home`, `safe_git_env`) are the conventional kind. The
other 300 lines are a safety mechanism with real power (an audit hook that can
fail any write) and **no test of its own beyond**
`tests/test_state_isolation_guard.py` (55 lines, 4 cases) and
`tests/test_live_state_isolation.py` (102 lines, 4 cases, frozen).

**Proposed action:** move lines 59-378 into `tests/isolation.py` and import it
from `conftest.py` (one import, same import-time behavior), so the mechanism is
reviewable as a unit and `conftest.py` returns to being a fixture file (~80
lines). Keep the import-time ordering exactly: the guard must be installed
before test modules import `agy_mcp`.

## F6 — Frozen files, measured hashes, and the decision they require

**Severity:** high (process) · **Effort:** S · **Risk of action:** n/a (decision)

The 12 `test_cleanup_jobs*.py` files are declared frozen in their own
docstrings (11 of 12 say `FROZEN`; `_differential.py` and `_fixes10.py` say
"harness"/"round 11" but are covered by the same rule), and
`docs/DECISIONS.md:729` states "frozen `tests/test_cleanup_jobs_fixes9.py`
unchanged". Measured content hashes on this HEAD:

```
$ sha256sum tests/test_cleanup_jobs*.py | sort -k2
e4fc8136be27eca13408fb65c2a00bc654a5767d0af6378c09934c0601b74dc1  tests/test_cleanup_fixtures.py
9a172dd04237f0e76a19796bbb77df2ef5193de4229f4a8cbf85b7f130576d06  tests/test_cleanup_jobs_differential.py
e90656b93697c4c8f293dea701b9a54c08b690e54efa452ee87d5b5acf25e1fc  tests/test_cleanup_jobs_fixes10.py
dc2d3b42f46eb3106149c36533a6c71564a3e988d5f2d8920fc58e0603c9a65a  tests/test_cleanup_jobs_fixes2.py
fbb992452dd3492d41156d3c2e5cd49485cd06eba12050d43e9d0e9df66f5386  tests/test_cleanup_jobs_fixes3.py
e30d927ceeafbfb7d3a5c364f1e85107302837a1dc6889beb926f83642b5f98c  tests/test_cleanup_jobs_fixes4.py
4dcbf3415f0893fdab1196ec6a7d50fae8402ca7492c368f1e484d2d03ae5fd8  tests/test_cleanup_jobs_fixes5.py
32149310c157e32eb98f98d0e1cd23c895e0ae1dcd1b1b46feee56c782420560  tests/test_cleanup_jobs_fixes6.py
d5b5a2a52368e5477cd93c52e7cb3393b2473bc04b64c87549ab676ebf35ecd6  tests/test_cleanup_jobs_fixes7.py
8edb963e15908c3651edeb837b54083e44b45126af4eeeaf60a18b999a826caa  tests/test_cleanup_jobs_fixes8.py
ccb44c53cfec607e6fc436643112b6afb8a0aac7383d218683d299f32a7d5201  tests/test_cleanup_jobs_fixes9.py
0478080ed895d379e5b06a28e076e31232d4d20578f8b82ee434dcf22ab4b940  tests/test_cleanup_jobs_fixes.py
ee05952cc9a436833f9a1580f5fe1cbcc567e295e914e47080c9eb64a42972eb  tests/test_cleanup_jobs.py
```

(13 lines shown: the command was `sha256sum tests/test_cleanup_jobs*.py
tests/test_cleanup_fixtures.py`, so the 13th line is `test_cleanup_fixtures.py`,
which is not part of the 12-file `test_cleanup_jobs*` glob but carries the same
`FROZEN` docstring and is frozen by the same rule.)

**Honest gap:** I could **not** find a committed manifest of these hashes. A
repository-wide search for a 64-hex digest in `docs/*.md` returns exactly one
match, and it is the `~/.gemini/antigravity-cli/settings.json` hash in
`docs/SMOKE_FINDINGS.md:254`:

```
$ grep -roE '\b[0-9a-f]{64}\b' docs/*.md | wc -l
1
```

The freeze is therefore enforced by convention (docstring + `docs/orchestrator/PLAN.md:37`
"Frozen tests are not edited after the freeze commit") and by the round files'
own comments, not by a machine-checkable list. The commit that froze them is
`b615b52 "test: frozen tests for cleanup_jobs CLI (project and job record cleanup)"`.

**Decision needed from Harsh (do not assume):**
1. May these 12 files be **moved/renamed** (content unchanged, hash unchanged)?
   Blocking constraint: the module path is pinned by CLI invocations — 11 files
   run `python -m agy_mcp.cleanup_jobs` and 5 run `python -m agy_mcp.cleanup_fixtures` (17 invocations, 14 distinct files)
   (A11/R1, R2). A pure *file* rename of the test modules is safe as long as the
   `-m agy_mcp.*` module paths are preserved, because the tests invoke the
   package, not the test file.
2. May a committed `tests/frozen.sha256` manifest be added (and a test that
   verifies it) so the freeze is checkable? This is the only way a future agent
   cannot quietly edit a frozen file.
3. Is a whitespace-only reformat of 5 over-long lines in
   `test_cleanup_jobs_fixes6.py` allowed (C7)? It changes the hash.

**I did not move, rename, edit or add anything.** The suite was run as-is.

## F7 — Proposed topic-based test layout

Rule: one module per *behavior area*, with round history kept in docstrings, not
filenames. Frozen files can be *moved* into these names only with decision (1).

| # | Target test module | Absorbs | Cases today |
|---|---|---|---|
| T1 | `tests/cleanup/test_candidate_classification.py` | terminal-state, symlink, job.json integrity, age cases from `test_cleanup_jobs.py`, `_fixes.py`, `_fixes3/4/8/9/10.py` | ~180 |
| T2 | `tests/cleanup/test_worktree_registration.py` | `_fixes6/7/8` layout matrices + FIFO/missing/chmod cases | ~340 |
| T3 | `tests/cleanup/test_git_oracle_differential.py` | `test_cleanup_jobs_differential.py` (unchanged content, moved) | 414 |
| T4 | `tests/cleanup/test_edge_protection.py` | closure/kept-string cases from `_fixes.py`, `_fixes4.py`, `test_cleaner_fixes.py` | ~40 |
| T5 | `tests/cleanup/test_cli_and_manifest.py` | `--list`, `--job`, `--project`, `--store`, exit codes, manifest planned/completed | ~120 |
| T6 | `tests/cleanup/test_fixture_cleaner.py` | `test_cleanup_fixtures.py`, `test_cleaner_hardening.py`, fixture half of `test_cleaner_fixes.py` | ~30 |
| T7 | `tests/supervisor/test_finalize_pipeline.py` | `test_f1`, `test_f8`, `test_f15`, `test_f17`, `test_f21`, parts of `test_supervisor.py` | ~40 |
| T8 | `tests/supervisor/test_resume_and_conversation.py` | `test_resume_and_commitmsg.py`, `test_f4`, `test_f20` | ~60 |
| T9 | `tests/git/test_staging_and_commit.py` | `test_gitops.py`, `test_git_scenarios.py`, `test_f6/f7/f10/f23`, `test_wrapper_verify_commit_fix.py`, `test_stale_commit_msg.py` | ~90 |
| T10 | `tests/verify/test_allowlist.py` | `test_verify.py`, `test_smoke_fix3`, `test_f9`, `test_quickwins_node_allowlist.py` | ~45 |
| T11 | `tests/usage/test_usage_parsing_and_guard.py` | `test_usage*.py` (8 files), `test_quota_*.py`, `test_auth_detection.py` | ~80 |
| T12 | `tests/server/test_tools_and_envelopes.py` | `test_server_unit.py`, `test_models.py`, `test_f5/f26`, `test_quickwins_status_fields.py` | ~60 |
| T13 | `tests/server/test_e2e_protocol.py` | `test_server_e2e.py`, `test_multiprocess_concurrency.py` | ~19 |
| T14 | `tests/installer/` | `test_installer_chapter.py` | 44 |
| T15 | `tests/test_isolation/` | `conftest` guard tests, `test_live_state_isolation.py` | 8 |

Order of work: create directories, **move** (never edit) the frozen files into
T1/T2/T3/T5 names with `git mv` — content hash preserved, and the `-m` module
paths are unaffected. The only hard requirement is Harsh's approval for the 12
frozen filenames (F6 decision 1).

## F8 — Orphan-leaking tests

**Severity:** low · **Effort:** S · **Risk of action:** low

Two frozen tests spawn a child that sleeps 10 s and never reap it:

```
tests/test_cleanup_jobs_fixes9.py:532    time.sleep(10)     # in a forked child
tests/test_cleanup_jobs_fixes9.py:653    time.sleep(10)     # same pattern
```

The test process exits immediately (it only needs the group alive), leaving a
10-second orphan per run. This is why `scripts/watch_orphans.py` (94 lines)
exists — a tool whose job is to detect a problem the tests create. Options:
leave it (harmless, bounded at 10 s) or add a fixture finalizer that kills the
group. Low priority; recorded because it is exactly the kind of "test-side
scaffolding" that makes the suite feel ad hoc.

---

# Appendix — every test file, measured

`cases` = collected test cases at this HEAD (`pytest --collect-only -q`),
`lines` = `wc -l`. Topic = first docstring line, truncated.

| File | Lines | Cases | Docstring topic |
|---|---|---|---|
| `test_agymcp_fixes2_a1.py` | 210 | 9 | A1: agy_cleanup must treat a job branch as merged when its commits are… |
| `test_agymcp_fixes2_a2.py` | 67 | 1 | A2: agy_wait, agy_review and agy_result must agree on the wrapper-owned… |
| `test_agymcp_fixes2_a3.py` | 108 | 2 | A3: the branch named by agy_start must be the branch that is actually… |
| `test_agymcp_fixes2_c4.py` | 307 | 4 | C4: the D68 ordering (usage row before the state becomes observable)… |
| `test_audit.py` | 491 | 21 | Unit and integration tests for security auditing and host path snapshots |
| `test_auth_detection.py` | 176 | 7 | Tests for login-expired detection (Item 7) |
| `test_b4_plan.py` | 120 | 6 | ITEM 4: plan accounting and the single automatic plan nudge |
| `test_b4_preamble.py` | 33 | 2 | ITEM 3: the standing preamble must teach the scratchpad rules |
| `test_b4_provisioning.py` | 126 | 3 | ITEM 1 (audit A4/F24): the production launch path must provision `.agy/` |
| `test_b4_report.py` | 143 | 13 | ITEM 5: wrapper-computed facts, the agent report, unverified claims |
| `test_b4_scratchpad_keep.py` | 113 | 4 | ITEM 7: keep the scratchpad under `<jobdir>/scratchpad/` after cleanup |
| `test_b4_scratchpad_security.py` | 91 | 8 | Security tests for hardened reads of agent-written `.agy/*` files |
| `test_b4_scratchpad_seed.py` | 143 | 6 | ITEM 2: seeded scratchpad files and the optional `checklist` parameter |
| `test_b4_state_block.py` | 149 | 5 | ITEM 6: wrapper-built state-of-the-world block on every resume |
| `test_checkpoint_interval.py` | 85 | 3 | A3: `checkpoint_commit_minutes` must reach the CommitWatcher |
| `test_cleaner_fixes.py` | 522 | 8 | Acceptance tests for 7 cleaner review findings |
| `test_cleaner_hardening.py` | 361 | 7 | FROZEN: cleaner hardening and finalize hang |
| `test_cleanup_fixtures.py` | 399 | 14 | FROZEN: Task B (safe fixture-row lister/cleaner) |
| `test_cleanup_jobs.py` | 1,093 | 38 | FROZEN: `python -m agy_mcp.cleanup_jobs` CLI |
| `test_cleanup_jobs_differential.py` | 1,284 | **414** | Differential harness vs real git ground truth |
| `test_cleanup_jobs_fixes.py` | 688 | 13 | Acceptance tests for 9 cleanup_jobs review findings |
| `test_cleanup_jobs_fixes2.py` | 699 | 12 | … (fix round 2) |
| `test_cleanup_jobs_fixes3.py` | 903 | 19 | … (fix round 3) |
| `test_cleanup_jobs_fixes4.py` | 405 | 6 | FROZEN … (fix round 4) |
| `test_cleanup_jobs_fixes5.py` | 601 | 13 | FROZEN … (fix round 5) |
| `test_cleanup_jobs_fixes6.py` | 1,094 | 35 | FROZEN … (fix round 7 bare-repo cases) |
| `test_cleanup_jobs_fixes7.py` | 862 | 129 | FROZEN … round 8 (linked-worktree, R7-R9) |
| `test_cleanup_jobs_fixes8.py` | 856 | 176 | FROZEN … round 9 (git metadata validation, R10-R12) |
| `test_cleanup_jobs_fixes9.py` | 721 | 20 | FROZEN … round 10 (non-git hardening, R13-R16) |
| `test_cleanup_jobs_fixes10.py` | 255 | 12 | Round 11 acceptance tests (D100) |
| `test_cli_log.py` | 192 | 11 | CLI log denial extraction and tail-reading (SPEC §15) |
| `test_codemap_fresh.py` | 50 | 4 | Freshness checks for the hand-maintained codebase map |
| `test_config.py` | 177 | 30 | Configuration loading and validation (SPEC §18) |
| `test_denial_recovery.py` | 296 | 11 | stop-and-ask preamble rule + auto denial recovery |
| `test_f1.py` | 252 | 3 | F1: watcher-initiated needs_review kills process and enables resume |
| `test_f2.py` | 147 | 3 | F2: audit baseline fails open; review/recovery self-compare |
| `test_f3.py` | 96 | 1 | F3: cancel during verification phase |
| `test_f4.py` | 133 | 2 | F4: resume/launch serialization, post-launch exceptions |
| `test_f5.py` | 93 | 3 | F5: untrusted text wrapping and byte capping |
| `test_f6.py` | 81 | 3 | F6: git status calls hardened and fail closed |
| `test_f7.py` | 58 | 3 | F7: staging deny-list covers AGENTS.md, .agents/, GEMINI.md |
| `test_f8.py` | 129 | 3 | F8: failed jobs carry structured failure reason and error tail |
| `test_f9.py` | 96 | 4 | F9: verify arguments restricted and revalidated on resume |
| `test_f10.py` | 50 | 1 | F10: verify artifacts not committed by the safety net |
| `test_f11.py` | 130 | 2 | F11: kill() signals the process group even if the leader exited |
| `test_f12.py` | 144 | 2 | F12: agy_cancel on queued jobs, cleanup(force=True) on running |
| `test_f13.py` | 120 | 3 | F13: concurrency cap atomicity, queued counting, flock |
| `test_f14.py` | 115 | 2 | F14: crash recovery must not trust stale stdout |
| `test_f15.py` | 74 | 1 | F15: pre-kill checkpoint commits_count increment |
| `test_f16.py` | 87 | 5 | F16: explicit size caps and bounded reads |
| `test_f17.py` | 112 | 1 | F17: verify tail redacted and preserved in verify_tail.txt |
| `test_f18.py` | 158 | 3 | F18: job.skipped_files populated from StagingResult |
| `test_f19.py` | 143 | 3 | F19: daily cap timezone configuration and validation |
| `test_f20.py` | 140 | 3 | F20: auto-fix prompt structure, persistent counters |
| `test_f21.py` | 115 | 1 | F21: watcher commit/cancel pre-kill checkpoint serialization |
| `test_f22.py` | 255 | 7 | F22: disk limits, retention cleanup to trash, usage recording |
| `test_f23.py` | 139 | 4 | F23: stale index.lock deletion liveness callback |
| `test_f25.py` | 81 | 3 | F25: config errors fail closed and refuse to start |
| `test_f26.py` | 83 | 3 | F26: path leaks in envelopes and error messages |
| `test_f27.py` | 27 | 4 | F27: dead code removal and deduplication |
| `test_f28.py` | 41 | 2 | F28: SPEC-vs-code drift reconciliation |
| `test_fake_agy.py` | 564 | 32 | fake agy executable CLI parsing and scenario simulation |
| `test_files_modified.py` | 68 | 2 | files_modified tracking and 200-entry cap |
| `test_git_scenarios.py` | 564 | 13 | Scenario tests B1-B12 for the git layer |
| `test_gitops.py` | 499 | 14 | gitops integration tests on real temporary repos |
| `test_h27_rootcause_repro.py` | 148 | 1 | H27: over-long agent subject repaired, not rejected |
| `test_installer_chapter.py` | 1,081 | **44** | The standalone agy-mcp installer chapter |
| `test_jobs.py` | 464 | 13 | Job persistence, atomic store, audit log, state machine |
| `test_live_state_isolation.py` | 102 | 4 | FROZEN: Task A (tests can never touch the live store) |
| `test_manager.py` | 1,336 | 49 | JobManager coordinating the nine MCP operations |
| `test_models.py` | 319 | 8 | Models, serialization, envelope builders |
| `test_multiprocess_concurrency.py` | 130 | 2 | Multiprocessing concurrency (spawn start method) |
| `test_parse_result.py` | 504 | 36 | Table-driven result-parsing precedence |
| `test_policy_files.py` | 207 | 2 | Extra Item A: policy files (.gitignore, .gitattributes) |
| `test_quickwins_node_allowlist.py` | 209 | 50 | Frozen: `node --test` verify allowlist grammar |
| `test_quickwins_status_fields.py` | 257 | 5 | Frozen: additive `agy_status` conversation/run fields |
| `test_quota_calibration.py` | 248 | 19 | Frozen: calibrated token-based quota estimate |
| `test_quota_detection.py` | 177 | 8 | Quota-exhausted detection |
| `test_quota_estimate_fallback.py` | 57 | 4 | Regression tests after review of D85 |
| `test_recovery.py` | 316 | 8 | Crash recovery, liveness, startup reconciliation |
| `test_resume_and_commitmsg.py` | 952 | 29 | Resume, conversation-id strictness, commit-message repair |
| `test_runner.py` | 575 | 12 | LocalProcessRunner, argv, process isolation |
| `test_server_e2e.py` | 979 | 17 | End-to-end MCP stdio protocol |
| `test_server_unit.py` | 226 | 15 | Server CLI parsing, resolution, error formatting |
| `test_smoke.py` | 118 | 3 | Importability and transition-table smoke |
| `test_smoke_fix1_permissions.py` | 142 | 6 | Dangerous permission-rule inspection |
| `test_smoke_fix2_model_resolved.py` | 135 | 7 | Unqualified model alias, allowlist, resolved model |
| `test_smoke_fix3_verify_python_m.py` | 59 | 5 | Accept canonical `python -m pytest` verify form |
| `test_smoke_fix4_tokens.py` | 123 | 5 | Real stdout token fields reach the envelope |
| `test_smoke_fix5_path_sanitize.py` | 111 | 8 | Strip absolute job paths from the agy response |
| `test_stale_commit_msg.py` | 382 | 9 | D66: invalid COMMIT_MSG discarded on needs_review |
| `test_state_isolation_guard.py` | 55 | 4 | Regression guard: tests never resolve the live store |
| `test_supervisor.py` | 888 | 14 | Supervisor state machine, deadlines, audit integration |
| `test_transcript.py` | 255 | 11 | agy transcript reader |
| `test_usage.py` | 110 | 4 | Locked usage logging and audit events |
| `test_usage_cost.py` | 224 | 7 | Per-job usage cost accounting |
| `test_usage_guard.py` | 225 | 11 | Start/resume usage guard modes |
| `test_usage_parser.py` | 159 | 9 | Parsing agy `/usage` output |
| `test_usage_read.py` | 276 | 14 | Reading `/usage` and masking the account email |
| `test_usage_tool.py` | 145 | 8 | The read-only `agy_usage` tool |
| `test_util.py` | 217 | 10 | Utility helpers (slug, env, truncation, redaction) |
| `test_verify.py` | 328 | 15 | Verify allowlist, execution, fix prompt |
| `test_watcher.py` | 304 | 6 | Watcher commit pipeline |
| `test_watcher_bug.py` | 141 | 2 | Part D: duplicate COMMIT_MSG fix |
| `test_worktree.py` | 280 | 11 | Worktree lifecycle, preflight, permission rules |
| `test_wrapper_verify_commit_fix.py` | 513 | 14 | Wrapper verify/commit fixes (D80) |
| **Total** | **32,091** | **1,711** | 106 files |

## Sources

- `tests/`
- `src/agy_mcp/`
- `doc/audits/evidence/2026-11-02-part1/pytest-full.txt`
