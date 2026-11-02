# Part 1b — File size, responsibility, comments and docstrings

Status: current

Metrics produced by `~/agy-review/audit/metrics.py` (AST walk, read-only):
`env -u PYTHONPATH .venv/bin/python ~/agy-review/audit/metrics.py src/agy_mcp
> ~/agy-review/audit/metrics-src.json`.
`top` = module-level functions, `cls` = classes, `all` = every function
including methods and nested defs, `doc%` = share of `all` carrying a docstring,
`cmt` = full-line `#` comments.

---

## B1 — Every source file, measured

| # | File | Lines | top | cls | all | doc% | cmt | What it does (one line) |
|---|---|---|---|---|---|---|---|---|
| 1 | `cleanup_jobs.py` | **1810** | 16 | 0 | 16 | 100.0 | 42 | User-facing cleaner CLI: hand-parses git internals to decide whether an old job row is safe to delete. |
| 2 | `manager.py` | **1737** | 1 | 2 | 30 | 76.7 | 89 | `JobManager`: start/status/wait/result/cancel/list/resume/review/cleanup + resume-policy hooks. |
| 3 | `supervisor.py` | **1622** | 3 | 1 | 32 | 93.8 | 113 | `JobSupervisor`: tick loop, interruption detection, verification, finalize pipeline. |
| 4 | `models.py` | **1077** | 12 | 9 | 24 | 100.0 | 32 | Envelope dataclasses, caps, untrusted wrapping, `next_actions` derivation, `job.json` schema. |
| 5 | `gitops.py` | **1049** | 27 | 4 | 39 | 82.1 | 46 | Hardened `git` execution, staging filters, commit/checkpoint pipeline, read-only git queries. |
| 6 | `usage.py` | **917** | 33 | 1 | 37 | 78.4 | 29 | `/usage` parsing, cache, cost deltas, quota estimate, `usage.jsonl` log, daily cap. |
| 7 | `watcher.py` | **859** | 13 | 1 | 21 | 95.2 | 53 | `.agy/COMMIT_MSG` settle window, validation, consume/stage/commit sequence. |
| 8 | `verify.py` | **808** | 24 | 1 | 27 | 70.4 | 27 | Verify allowlist + per-tool flag/path validation, shell-free execution, failure diagnosis. |
| 9 | `cleanup_fixtures.py` | **758** | 19 | 0 | 20 | 95.0 | 12 | Pytest-fixture-row cleaner CLI **and** the store primitives `cleanup_jobs` borrows (A2). |
| 10 | `worktree.py` | **705** | 14 | 1 | 15 | 93.3 | 57 | Repo preflight, branch allocation, `git worktree add/remove`, permission-rule inspector. |
| 11 | `audit.py` | **653** | 10 | 6 | 16 | 93.8 | 30 | Security snapshots and the read-only audit detector. |
| 12 | `server.py` | **615** | 19 | 0 | 30 | 63.3 | 22 | Ten FastMCP tool wrappers, error-envelope translation, CLI bootstrap, shutdown. |
| 13 | `jobs.py` | **585** | 19 | 0 | 19 | 100.0 | 21 | State-dir layout, atomic writes, audit log, transition gate, retention, disk usage. |
| 14 | `scratchpad.py` | **454** | 17 | 1 | 18 | 94.4 | 7 | Seeds `.agy/` files and reads them back as untrusted, confined data. |
| 15 | `runner.py` | **361** | 3 | 5 | 15 | 73.3 | 17 | argv building, `/proc` inspection, process-group launch and kill. |
| 16 | `recovery.py` | **356** | 3 | 0 | 3 | 100.0 | 32 | Startup reconciliation and liveness. |
| 17 | `parse_result.py` | **346** | 6 | 2 | 6 | 100.0 | 26 | Ordered precedence rules turning an agy exit into a job state. |
| 18 | `config.py` | **335** | 2 | 2 | 2 | 100.0 | 17 | Config defaults + strict TOML validation (one 204-line validator). |
| 19 | `cli_log.py` | **302** | 10 | 1 | 12 | 91.7 | 14 | Bounded `cli.log` reads and denial/conversation-id/model extraction. |
| 20 | `util.py` | **297** | 14 | 0 | 14 | 100.0 | 11 | Pure helpers: slug, env allowlist, truncation, redaction, hashing, prompts. |
| 21 | `transcript.py` | **263** | 4 | 1 | 6 | 83.3 | 14 | The only code allowed to read an agy transcript (denied-call detail). |
| 22 | `states.py` | 180 | 0 | 2 | 0 | — | 7 | `JobState` enum + transition table (pure data, no functions). |
| 23 | `__main__.py` | 14 | 0 | 0 | 0 | — | 0 | `python -m agy_mcp` → `server.main`. |
| 24 | `__init__.py` | 9 | 0 | 0 | 0 | — | 0 | Package docstring + `__version__`. |

Aggregate: **13 of 24 modules are over 500 lines and hold 13,195 of 16,112
lines (82%)**. Under-500 modules total 2,917 lines.

```
$ awk (from metrics-src.json):
over500: 13 of 24 files; 13195 lines of 16112
under500 sum: 2917
```

---

## B2 — `cleanup_jobs.py` (1,810 lines): four jobs in one file, one function owns 473 of them

**Severity:** high · **Effort:** L · **Risk of action:** medium

The file has exactly 16 top-level functions — and no classes — but they fall
into four unrelated responsibilities:

| Responsibility | Functions (line spans) | Lines |
|---|---|---|
| Git metadata validation (a `.git` parser) | `_read_git_metadata_file` 56-150, `_validate_git_head_file` 153, `_validate_git_config_file` 185, `_validate_git_packed_refs_file` 255, `_has_git_dir_markers` 323, `_validate_git_dir` 335, `_validate_full_git_dir` 348 | 339 |
| Worktree registration decision | `_check_worktree_status` 391-868, `_is_worktree_registered` 866 (dead, C1) | 478 |
| Eligibility + edge protection | `_classify_job_for_cleanup` 871, `_collect_kept_strings` 998 | 196 |
| Deliberately destructive pipeline + CLI | `run_cleanup` 1070, `parse_args` 1403, `_list_projects` 1458, `main` 1543 | 740 |

Four responsibilities is one too many for a CLI, and the first (a from-scratch
git metadata parser) has nothing to do with "delete stale job rows".

**Proposed split** (public surface stays `run_cleanup(store, *, project, job_ids,
dry_run, older_than_days, before_delete)` and `main(argv)`):

| New module | Contents | Rationale |
|---|---|---|
| `cleanup/store_io.py` | the 9 helpers currently imported from `cleanup_fixtures` | shared with the fixture cleaner (A2) |
| `cleanup/git_metadata.py` | lines 50-388 verbatim (`MAX_GIT_METADATA_SIZE`, 3 regexes, `_read_git_metadata_file`, `_validate_git_*`, `_GIT_DIR_MARKERS`, `_has_git_dir_markers`, `_validate_full_git_dir`) | pure functions over paths; testable in isolation |
| `cleanup/worktree_status.py` | lines 391-868 (`_check_worktree_status`) | D1 addresses its internals |
| `cleanup/jobs_cli.py` | `normalize_repo_path`, `_classify_job_for_cleanup`, `_collect_kept_strings`, `run_cleanup`, `parse_args`, `_list_projects`, `main` | what a CLI module should be: classify → report → act |

`cleanup_jobs.py` becomes a 15-line shim (`from agy_mcp.cleanup.jobs_cli import
_check_worktree_status, _classify_job_for_cleanup, main, run_cleanup  # noqa:
F401`) so the 11 frozen `python -m agy_mcp.cleanup_jobs` invocations (A11/R1)
and the three private-name imports (R10, R11) keep working untouched.
Line counts: 1810 → 40 (shim) + 333 + 478 + 196 + 740.

**Is the split worth it?** Yes for `git_metadata` (333 lines of pure parsing
with no cleanup knowledge) and `store_io` (dedupe). For
`worktree_status`, splitting the *file* helps only if D1 is also done;
otherwise it just relabels 473 lines.

---

## B3 — `manager.py` (1,737 lines, 30 methods): the manager also owns three resume policies

**Severity:** high · **Effort:** L · **Risk of action:** medium

`JobManager` (from `manager.py:123`) holds:

| Group | Methods |
|---|---|
| Job launch | `start` :228 (182 lines), `_provision_and_launch` :411 (191), `_run_supervisor` :603 (no docstring, 23 branches) |
| **Resume policy** | `_handle_plan_nudge` :688, `_handle_denial_auto_resume` :715, `_handle_verification_auto_fix` :747 — these decide *whether to resume a job*, not how to manage one |
| **Usage policy** | `_apply_usage_guard` :781 (52), `_read_usage` :832 |
| Envelope presentation | `status` :855, `wait` :921, `result` :1077 (133 lines), `review` :1609, `list` :1272, `_build_state_block` :1303 |
| Lifecycle | `reconcile` :1000, `cancel` :1211, `resume` :1375 (233 lines), `cleanup` :1694, `shutdown` :1729 |
| Plumbing | 8 small `_get_*`/`_validate_*` helpers |

**Proposed action, in this order:**

1. Move the three `_handle_*` methods to a new `resume_policy.py` as free
   functions taking `(job, supervisor, config)` — they are already
   self-contained enough to be called from one place (`_run_supervisor`), so
   this is mechanical.
2. Move `_apply_usage_guard` + `_read_usage` to `usage.py` (a `guard(reading,
   model, cap)` function). `usage.py` already owns the cache.
3. Leave the nine tool-facing methods alone: they are the manager's actual job,
   and `test_server_unit.py` / `test_manager.py` pin their envelopes.

**Not worth it:** moving `start`/`resume` out. They orchestrate locks, worktree,
audit and supervisor in a fixed order; splitting them would scatter that order
across modules — exactly the "convoluted logic" the complaint is about.

---

## B4 — `models.py` (1,077 lines) contradicts its own docstring: it does decide policy

**Severity:** high · **Effort:** M · **Risk of action:** low-medium

`models.py:1-7` states: *"It must never perform I/O, start processes, or decide
policy: callers pass fully-formed state in, and this module only caps, wraps and
serializes it."* But:

```
models.py:445:def derive_next_actions(...)        147 lines, 34 branches  <-- policy
models.py:594:def get_effective_verify_envelope  88 lines
models.py:682:def get_plan_envelope              25 lines
models.py:707:def build_facts                   50 lines
models.py:774:def build_standard_envelope       49 lines
models.py:919:def build_result_response         66 lines
models.py:1011:def build_review_response        37 lines
models.py:1062:class JobListResponse
```

`derive_next_actions` is 147 lines of user-facing advisory text keyed on state
and review reason — the definition of policy. Either the docstring is wrong or
the code is in the wrong module; today both are true, so a reader cannot use
the docstring to predict where to add logic.

**Proposed action:** move `derive_next_actions` (and only it) to
`next_actions.py`; amend the `models.py` docstring to say "envelopes, caps,
serialization, and the `next_actions` *rendering* lives in `next_actions.py`".
Risk: `derive_next_actions` is referenced 11 times in `tests/test_models.py`
(19 times across the suite) — keep a re-export from `models.py`.

**Worth splitting further?** No. The remaining 900 lines are dataclasses plus
builders that share the cap constants; splitting them would duplicate those
constants.

---

## B5 — `server.py` (615 lines): ten byte-identical tool wrappers (~350 lines of boilerplate)

**Severity:** medium · **Effort:** M · **Risk of action:** low

Each of the ten tools is the same 6-line body:

```
server.py:218:    def _run() -> dict[str, Any]:
server.py:219:        mgr = get_manager()
server.py:220:        try:
server.py:221:            envelope = mgr.start(...)
server.py:231:            return envelope.to_dict()
server.py:232:        except Exception as exc:
server.py:233:            return _format_error(exc, "agy_start", title=title)
server.py:237:    return await anyio.to_thread.run_sync(_run)
```

repeated at `_run` definitions on lines 218, 258, 284, 309, 331, 353, 380, 405,
432, 459 — ten closures, none documented (they are 10 of the 11 docstring gaps
in E4), plus `anyio.to_thread.run_sync(_run)` ten times.

**Proposed action:** one helper

```python
def _run_tool(tool_name: str, fn: Callable[..., Any], **ids: str) -> dict[str, Any]:
    """Offload a blocking manager call and translate any exception to an envelope."""
    def _run() -> dict[str, Any]:
        try:
            return fn(**ids).to_dict()
        except Exception as exc:
            return _format_error(exc, tool_name, **ids)
    return anyio.to_thread.run_sync(_run)
```

plus `await _run_tool("agy_status", get_manager().status, job_id=job_id,
tail_lines=tail_lines)`. Removes ~120 lines and one class of copy-paste bug
(a tool forgetting the offload or the wrap). The MCP docstrings stay untouched
— they are the tool contract.

**Do not** split `server.py` into `server/tools.py` + `server/cli.py`: the file
would drop to ~350 lines, which is fine, but it would break
`pyproject.toml:21` (`agy_mcp.server:main`) and add a subpackage for one module.

---

## B6 — `cleanup_fixtures.py` (758) and the remaining 500+ files: honest verdicts

**Severity:** medium · **Effort:** M (fixtures) / none (rest) · **Risk:** low

| File | Verdict |
|---|---|
| `cleanup_fixtures.py` (758, 19 funcs) | Mixes three jobs: (a) store primitives used by another module, (b) the pytest-fixture signature classifier (`_is_pytest_temp_repo` :67, `_pytest_basetemp` :119, `_classify_job` :308), (c) its own CLI (`parse_args` :448, `run_cleanup` :481, `main` :637). **Split** along A10/M2-M3: primitives → `store_io.py`, (b)+(c) → `cleanup/fixtures_cli.py`. |
| `gitops.py` (1,049, 39 funcs) | Large but coherent: 15 pure helpers (`is_build_artifact` :162, `is_sensitive_path` :206, `sanitize_commit_subject` :660, `parse_porcelain_z` :399) + 8 execution functions + 8 read-only queries. **Optional split**: the 8 read-only queries (`get_merge_base` :918 … `diff_git_config_snapshots` :1037, ~130 lines) into `gitinfo.py`. Low value — they share `run_git`. **Recommend: leave.** |
| `usage.py` (917, 37 funcs) | Parser (`_parse_*` :76-284), policy helpers (`low_buckets` :323, `annotate_low_flags` :382), cost/quota math (:425-568), cache + reader (:569-802), log (:808-917). **Optional split**: the parser (~230 lines) is pure and independently testable (`test_usage_parser.py`, `test_usage_read.py` already treat it as a unit). **Recommend: leave until a behavior change touches it.** |
| `verify.py` (808, 27 funcs) | A validation table (`_validate_*_args` :214-353, 7 near-identical functions) + execution (`run_verify` :512) + diagnosis (:658-808). Split only with D6. **Recommend: restructure the table in place**, do not create a subpackage. |
| `watcher.py` (859) | Single coherent pipeline (settle → validate → consume → stage → commit). `CommitWatcher` :375 is the anchor. **Recommend: leave.** |
| `worktree.py` (705), `audit.py` (653), `jobs.py` (585), `scratchpad.py` (454), `runner.py` (361), `recovery.py` (356), `parse_result.py` (346), `config.py` (335), `cli_log.py` (302), `util.py` (297), `transcript.py` (263), `states.py` (180) | All under or near the threshold and single-purpose. **Recommend: leave all.** |

---

# E — Comments and docstrings

## E1 — Docstring coverage is strong; the complaint does not match the measurement

**Severity:** low (positive finding, recorded to prevent a wrong fix) · **Effort:** — · **Risk:** —

```
$ metrics: doc% per module
cleanup_jobs 100.0   manager 76.7   supervisor 93.8   models 100.0
gitops 82.1          usage 78.4     watcher 95.2      verify 70.4
cleanup_fixtures 95.0 worktree 93.3 audit 93.8        server 63.3
jobs 100.0           scratchpad 94.4 runner 73.3      recovery 100.0
parse_result 100.0   config 100.0   cli_log 91.7      util 100.0
transcript 83.3      states —       __main__ —        __init__ —
```

- **24 of 24 modules have a module docstring** (`module_doc: true` for every
  entry in `metrics-src.json`), and they are unusually good: each states what
  the module owns **and what it must never do** (e.g. `models.py:1-7`,
  `gitops.py:1-7`, `states.py:1-7`).
- 12 of 22 function-bearing modules are at 100%; weighted total is
  **86.6% of 402 functions** (`348/402` from `~/agy-review/audit/metrics-src.json`).

So "missing docs" is not the defect. The defects are narrow (E2/E4): the
*why* is missing exactly where the code is hardest, and the lowest-coverage
files are the two largest interfaces (`server.py` 63.3%, `verify.py` 70.4%).

## E2 — Missing WHY comments at the three hardest sites

**Severity:** medium · **Effort:** S · **Risk of action:** none (comments only)

| Site | What a reader cannot recover today |
|---|---|
| `cleanup_jobs.py:663-713` | The docstring lists *what* is checked (5 numbered points) but not *why the union of candidate worktree roots is intentional*. The one useful hint is inline at :663-665 ("Bare-repo detection and union. Never let one branch shadow the other.") — it should be in the docstring, because the union is the reason the function has 8 nested branches. |
| `cleanup_jobs.py:1205-1220` | The fixpoint loop ("referenced by kept job" closure) has **no comment at all**; a reader must infer that adding newly-kept candidates' strings can cascade. `cleanup_fixtures.py:551` has the same loop with the same gap. |
| `cleanup_jobs.py:1168, 1202, 1330` | Three bare `assert` statements used as control-flow narrowing (`assert job_ids is not None`). Under `python -O` they vanish and the code proceeds with `None`; nothing says the invariant is guaranteed by the `modes_count` check in `main` (`:1550-1562`). |

## E3 — Phase-narration comments will rot when the pipeline is split

**Severity:** low · **Effort:** S · **Risk of action:** none

`run_cleanup` numbers its phases in comments (`# 1. Re-check classification` :1254,
`# 2. In project mode, re-check repo_path (Requirement 1)` :1267,
`# 3. Recompute edge protection…` :1313, `# 4. Safely delete` :1339), and
`main` does the same (`# 1. --list mode`, `# 2. Pre-flight checks`,
`# 3. Execute cleanup / scan`, `# 4. Output results`, `# 5. Determine exit code`).

These are *what* comments and they are load-bearing: they describe the safety
order. That order is a real invariant ("re-check immediately before delete").
Convert each to a named helper (`_recheck_candidate_before_delete(...)`) whose
docstring states the invariant, instead of a comment that a future edit will
leave behind.

No narration-only or stale comments were found elsewhere; see C6 for the
negative result on stale TODOs.

## E4 — Docstring gaps that matter (not dunders, not closures)

**Severity:** medium · **Effort:** S · **Risk of action:** none

Full list from `metrics-src.json`; excluding `__init__`/`__eq__`/`__str__`/
`__repr__`/`__hash__`/`__getitem__`/`__new__`/`__getitem__`/closures, these are
real gaps:

| file:line | function | Size | Why it matters |
|---|---|---|---|
| `supervisor.py:126` | `JobSupervisor.__init__` | **122 lines**, 9 branches | The single most important constructor in the codebase (wires 20+ collaborators); no docstring, no argument map. |
| `manager.py:603` | `_run_supervisor` | 63 lines, 23 branches | The thread body that applies the three resume policies; no docstring. |
| `manager.py:126` | `JobManager.__init__` | 25 lines | Dependency injection contract undocumented while tests inject fakes. |
| `verify.py:240` | `_validate_cargo_args` | 36 lines, **16 branches**, **4.1× the branch count** of its siblings (`_validate_ruff_args` :227, 11 lines) | The one tool-specific validator with no explanation; a reader cannot tell why cargo needs 16 branches. |
| `verify.py:214/227/278/291/341` | `_validate_{pytest,ruff,go,npm,other}_args` | 11-2 lines each | Five identical-shape functions without a shared docstring stating the rule they implement. |
| `verify.py:431` | `VerifyResult.__new__` | 22 lines | Tuple-subclass construction; how the fields map to the tuple is undocumented. |
| `verify.py:529` | `_result` | 18 lines | Builds the result record. |
| `manager.py:715/747` | `_handle_denial_auto_resume`, `_handle_verification_auto_fix` | 31/33 lines | Policy decisions with thresholds; see B3. |
| `audit.py:565` | `_relativize_host_path` | 12 lines, 4 branches | Security-relevant path masking with no statement of its confinement rule. |
| `runner.py:354` | `_close_process_files` | 8 lines | File-descriptor hygiene; a leak here is invisible. |
| `cleanup_fixtures.py:143` | `_onerror` | 2 lines | Silently swallows unlink errors during safe delete. |
| `server.py:595` | `_sigterm_handler` | 6 lines | Explains nothing about why `os._exit(0)` is used instead of returning. |

## E5 — Proposed comment standard (10 lines)

```markdown
1. Module docstring: what this module owns, and what it must never do. Keep the
   "must never" clause — it is the most useful line in the file.
2. Every public function/class: one imperative line; add a second paragraph only
   for a non-obvious invariant, ordering, or fail-closed rule.
3. Private helpers get a docstring when they make a *decision* (thresholds,
   fail-closed branches); trivial accessors and dunders may omit it.
4. Comments explain WHY (lock order, safety gate, spec/D-number), never WHAT the
   next line does.
5. Cite the contract: `SPEC §N` or `D<number>` at the decision, not at the top.
6. Never number phases in comments; name the helper instead.
7. Never leave commented-out code; delete it (git is the archive).
8. No TODO without an owner and a D-number; prefer an entry in
   `docs/orchestrator/NEEDS_HUMAN.md`.
9. No bare `assert` for control flow; use an explicit raise or a typed narrowing
   helper, because `-O` removes asserts.
10. If a docstring promise is violated by the code (e.g. `models.py` "no
    policy"), fix one of the two in the same commit.
```

## Sources

- `src/agy_mcp/`
- `tests/`
- `doc/audits/evidence/2026-11-02-part1/metrics-src.json`
