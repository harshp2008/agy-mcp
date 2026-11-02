# installer-fixes: report

Branch: `exp/installer-fixes` (from `main` at `07d706a`; never pushed, `main` untouched).

## Part A — the F22 flake (D68)

**Not test interference.** Facts (raw files in this directory):

- `collect-order.txt`: `tests/test_f22.py` is collected at items 170-175 and
  `tests/test_installer_chapter.py` at items 269+, so the installer tests run
  **after** F22. They cannot affect it at run time.
- `plugins-and-addopts.txt`: pytest 9.1.1, the only third-party plugin is
  `anyio`; `addopts` only disables ROS plugins. No randomizing/reordering plugin.
- `baseline-full.txt`: the full suite passed 622/622 before any change — the
  flake did not fire on that run.
- `baseline-deselect.txt`: 599 passed, 23 deselected with the installer chapter
  deselected.
- `f22-loop-alone.txt` / `f22-repro-alone.txt`: the shipped test fails **alone**
  (3/20 and 1/20 runs), so the installer chapter is not the trigger.
- `f22-plus-installer.txt`: `pytest tests/test_f22.py tests/test_installer_chapter.py`
  passed (29 passed).

**Mechanism (real src race).** `Supervisor._finalize_terminated_process` persisted
the terminal state (`transition_job_state`) and only then appended
`usage.jsonl`, on a background thread. `f22_instrument.py` (1 ms polling)
recorded the terminal state in `job.json` **4.274-7.057 ms before** `usage.jsonl`
existed in **10/10** runs (`f22-instrument-before.txt`). The shipped test polls
with 50 ms sleeps, so it lands in that window only sometimes — that is the flake.

**Fix.** Accumulate execution time, append the usage record, then persist the
state. `usage.record_usage` gained an optional `state` keyword so the record
still names the terminal state (other callers unchanged). After the fix the
harness reports **0/10** races (`f22-instrument-after.txt`).

**Ordering of commits:** failing deterministic test (`6a9140b`) → fix
(`03d4833`) → AST baseline re-save (`d7d2046`) → mutation check (`205b8c9`).
Final full suite: `full-final-1.txt` and `full-final-2.txt`, both 632 passed.

## Part B

| item | failing-test commit | fix commit | evidence |
| --- | --- | --- | --- |
| B1 upgrade | `0802166` | `22c415f` + `fb7de82` (re-apply) | b1-failing/b1-passing/b1-mutation |
| B2 uninstall safety | `e19c767` | `0be7001` | b2-failing/b2-passing/b2-mutation |
| B3 flow-style guard | `0aab776` | `7e755bb` | b3-failing/b3-passing/b3-mutation |
| B4 generic JSON | `b3fc9d3` | `f8ebff7` | b4-failing/b4-passing/b4-mutation |
| B5 shellcheck | n/a (no real finding) | n/a | b5-shellcheck-* |
| B6 docs | n/a | `59dba1e` | — |

## Failures during the work

1. The baseline full suite **passed** (622) instead of failing; the flake is
   timing-dependent (see above), which is itself part of the finding.
2. The first post-fix full run failed `test_codemap_fresh` because
   `docs/CODEMAP.md` had not been regenerated yet (`full-after-fix-1` was
   overwritten by the next run; the fix was commit `d60664a`).
3. The next full run failed `test_manager.py::test_manager_verify_command_not_found_clean_error`,
   a **pre-existing** auto-resume race: it also fails ~1/18 on a pristine copy of
   `main` (`manager-verify-flake-*.txt`). Not fixed here.
4. The B1 mutation check used `git checkout -- installer/install.sh` while the B1
   edits were still uncommitted, which wiped them; commit `22c415f` therefore
   contained only evidence. Re-applied as `fb7de82`. Later mutation checks copy
   the file to `/tmp` first.
