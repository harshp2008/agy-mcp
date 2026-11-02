# Part 1c — Dead/duplicated code (C) and logic complexity (D)

Status: current

Method: `ruff check src tests scripts` (rules enabled: `E,F,I,T20`), a symbol
scanner (`~/agy-review/audit/unused.py`, 410 module-level definitions, refs
counted over `src/ tests/ scripts/`), AST body-hash comparison, and manual
`grep` confirmation for every reported symbol.

---

## C1 — Five module-level symbols have zero references

**Severity:** medium · **Effort:** S · **Risk of action:** low

Scanner output (`~/agy-review/audit/unused.json`, `"unused"` list, `counts:
{defs: 410, unused: 5}`), confirmed by a repository-wide grep (7 hits total:
5 definitions + 2 doc mentions):

| Symbol | Definition | Refs in `src/` | Refs in `tests/`, `scripts/` | Refs in docs |
|---|---|---|---|---|
| `_is_pid_alive` | `cleanup_fixtures.py:241` | 0 | 0 | 0 |
| `_is_worktree_registered` | `cleanup_jobs.py:866` | 0 | 0 | 0 |
| `MAX_SCRATCHPAD_FILES` | `scratchpad.py:33` | 0 | 0 | 0 |
| `clear_usage_cache` | `usage.py:803` | 0 | 0 | 2 (`docs/CODEMAP.md:578,747`) |
| `DANGEROUS_RULE_PREFIXES` | `worktree.py:34` | 0 | 0 | 0 |

```
$ grep -rn "_is_pid_alive|_is_worktree_registered|MAX_SCRATCHPAD_FILES|clear_usage_cache|DANGEROUS_RULE_PREFIXES" . --include=*.py --include=*.md
Found 7 matches
  usage.py:803, scratchpad.py:33, worktree.py:34, cleanup_fixtures.py:241,
  cleanup_jobs.py:866, docs/CODEMAP.md:578, docs/CODEMAP.md:747
```

### C1a — `DANGEROUS_RULE_PREFIXES` is worse than dead: it has already drifted from the live logic

**Severity:** medium (latent security drift) · **Effort:** S · **Risk:** low

`worktree.py:34-50` defines a 15-entry tuple of dangerous allow-rule prefixes,
including `"command(uv"`. The function that is supposed to use it,
`is_dangerous_allow_rule` (`worktree.py:486`), instead re-implements the whole
policy inline with hardcoded sets and prefix checks:

```
worktree.py:505    if bin_name in {"bash", "sh", "zsh", "ksh", "csh", "tcsh", "fish", "dash"}:
worktree.py:509    if bin_name.startswith(("python", "node", "ruby", "perl", "php", "bun", "deno")):
```

`uv` appears in the dead tuple but **not** in the live interpreter prefixes:
a rule allowing `uv` would be flagged by the constant and allowed by the code.
Deleting the constant is the correct fix; keeping it "for later" would let the
two lists keep diverging. (Also note the tuple's "15" count vs 30 lines because
entries wrap — measured with `sed -n '34,52p' | grep -c '"'`.)

### C1b — `clear_usage_cache` is documented as public API but is unreachable

`usage.py:803` defines it; `docs/CODEMAP.md:578` documents it ("reset the
process-wide cache") and `:747` lists it in the generated index. Nothing calls
it, including tests. Either add a test that exercises it (it is a legit test
seam for the 60 s cache) or delete it and update the CODEMAP. Right now the
CODEMAP's "public API" table is inaccurate, which is the failure mode the
`test_codemap_fresh` gate was built to prevent — it only checks that module
*names* appear, not that listed functions exist.

---

## C2 — `_record_cleanup_error` is byte-identical in two modules

**Severity:** high (cheap to fix, easy to diverge) · **Effort:** S · **Risk:** low

```
$ sed -n '96,108p' src/agy_mcp/supervisor.py > /tmp/a; sed -n '101,113p' src/agy_mcp/manager.py > /tmp/b; diff /tmp/a /tmp/b
$ echo $?
0
```

This is not a look-alike: the 13 lines are identical, including the docstring
and the `except Exception:` swallow. It is the kind of copy that silently
diverges the day one caller adds a cleanup code. Move to
`jobs.py` (which already owns `append_audit_event`) as
`record_cleanup_error(job_dir, code)`; both modules import it.

---

## C3 — `cleanup_jobs.py` re-implements the same `job.json` validation five times, and `main()` imports `sys` seventeen times

**Severity:** high · **Effort:** M · **Risk of action:** low

### C3a — five copies of "stat → 1 MiB cap → read → safe-parse → is-dict"

| # | Site | Context |
|---|---|---|
| 1 | `cleanup_jobs.py:904-911` | `_classify_job_for_cleanup` |
| 2 | `cleanup_jobs.py:1033-1043` | `_collect_kept_strings` |
| 3 | `cleanup_jobs.py:1119-1126` | `run_cleanup`, project-mode scan |
| 4 | `cleanup_jobs.py:1271-1278` | `run_cleanup`, pre-delete re-check |
| 5 | `cleanup_jobs.py:1483-1496` | `_list_projects` |

```
$ grep -n "1024 \* 1024\|exceeds 1 MiB" src/agy_mcp/cleanup_jobs.py
905:        if st.st_size > 1024 * 1024:
906:            return False, ["job.json exceeds 1 MiB limit"], None, None
1034:            if st.st_size > 1024 * 1024:
1120:                if not stat.S_ISREG(st.st_mode) or st.st_size > 1024 * 1024:
1272:                    if not stat.S_ISREG(st.st_mode) or st.st_size > 1024 * 1024:
1484:            if st.st_size > 1024 * 1024:
```

The literal `1024 * 1024` is repeated 5 times and `jobs.py:180` already defines
`MAX_JOB_FILE_BYTES` for the same concept. Worse, the five copies are not
equivalent: #2 and #4 add `stat.S_ISREG`, #1 and #5 do not. A single
`load_job_json(child, *, max_bytes=MAX_JOB_FILE_BYTES) -> tuple[dict|None, reason|None]`
in `cleanup/store_io.py` removes the duplication *and* the inconsistency.

### C3b — the two branches of the post-run reporting differ only in their condition

```
cleanup_jobs.py:1351    if mode == "job" and job_ids:
cleanup_jobs.py:1352        for jid in job_ids:
cleanup_jobs.py:1353            if dry_run:
cleanup_jobs.py:1354                if jid not in candidates:
...                                    (6 lines, identical)
cleanup_jobs.py:1360            else:
cleanup_jobs.py:1361                if jid not in deleted_ids:
...                                    (6 lines, identical)
```

The `if`/`else` bodies are character-for-character the same except the
membership test. Collapse to
`seen = candidates if dry_run else set(deleted_ids)`.

### C3c — 17 local `import sys` statements in one module

```
$ grep -c "import sys" src/agy_mcp/cleanup_jobs.py
17
```

15 of them are inside `main()` (lines 1552, 1557, 1565, 1573, 1578, 1587, 1601,
1609, 1615, 1622, 1628, 1636, 1643, 1651, 1659), one at 1664, one at 1808.
`cleanup_fixtures.py:17` simply imports `sys` at module level. A top-level
`import sys` costs nothing and removes 17 lines plus the unusual style that
makes `main()` read as if it were avoiding an import cycle that does not exist.

---

## C4 — The edge-protection fixpoint is duplicated between the two cleaners

**Severity:** medium · **Effort:** S · **Risk of action:** low

```
cleanup_jobs.py:1205-1220      while True: … cid in kept_text … new_strings.extend(_extract_strings(c_data))
cleanup_fixtures.py:551-570    while True: … cid in kept_text … new_strings.extend(_extract_strings(c_data))
```

Both implement "a candidate referenced by a kept job (transitively) must be
kept". Today they agree; nothing enforces that they keep agreeing, and they are
the last line of defence against deleting a job that a kept row still points
at. Extract to `cleanup/edge_protection.py` (A5) with a docstring stating the
fail-closed rule, and let the 887 frozen cleanup tests cover both callers.

---

## C5 — `make_error_envelope` duplicates `StandardEnvelope` and has already drifted by ten fields

**Severity:** medium · **Effort:** S-M · **Risk of action:** low-medium

```
$ python (ast): make_error_envelope top-level keys: 17
  ['job_id','state','elapsed_seconds','title','branch','base_commit','head_commit',
   'commits_count','files_modified','skipped_files','denials','denied_commands',
   'warnings','tokens','next_actions','error','verify']
$ StandardEnvelope fields: 27
  missing from the error dict: ['model_resolved','denied_tools','usage_cost',
   'quota_estimate','conversation_continued','resume_after','plan','facts',
   'agent_report','scratchpad']
```

`server.py:52-99` hand-builds the error envelope field by field while
`models.build_standard_envelope` (`models.py:774`) builds the same shape from
`StandardEnvelope`. Ten fields exist in one and not the other. Either the
error envelope is intentionally minimal (then the divergence needs a comment
saying which fields are deliberately absent and why a client may rely on them
being absent) or it is drift (then it should be
`dataclasses.replace(StandardEnvelope(...), error=...)`).

`tests/test_server_unit.py:123` only asserts five keys, so this can be fixed
without touching tests — but the fix must preserve the 17-key `verify`
sub-dict built at `server.py:83-98`, which that test does pin.

---

## C6 — Negative results (recorded because they contradict the brief)

**Severity:** — (evidence against "dead code everywhere") · **Effort:** —

```
$ .venv/bin/ruff check src tests scripts --output-format=concise
All checks passed!
RUFF_EXIT=0
```

With `E` (pycodestyle errors), `F` (pyflakes: **F401 unused imports, F841
unused locals**, F821 undefined names), `I` and `T20` enabled, the tree is
clean. So there are **no unused imports and no unused local variables**,
contrary to the expectation in the brief.

Likewise:

```
$ grep -rnE '(TODO|FIXME|XXX|HACK)\b' src tests scripts installer --include=*.py --include=*.sh | wc -l
5          # all five are mktemp templates "XXXXXX" in installer/install.sh:179,586,633,642,705
$ grep -rnE '^\s*#\s*(return|if |for |while |def |class |import |from |print\(|self\.)' src/agy_mcp/*.py
4 hits, all prose: cleanup_fixtures.py:725, manager.py:1669, models.py:367, usage.py:797
```

**Zero TODO/FIXME markers and zero commented-out code in `src/`, `tests/`,
`scripts/`, `installer/`.** The "leftover scaffolding / commented-out code"
complaint is not supported by the repository as it stands.

What *is* dead is the small set in C1, plus one lint-config workaround in C7.

---

## C7 — A permanent ruff exception exists for one frozen test

**Severity:** low · **Effort:** S · **Risk of action:** low (needs Harsh)

```
pyproject.toml:40:[tool.ruff.lint.per-file-ignores]
pyproject.toml:41:"tests/*" = ["T20"]
pyproject.toml:42:"tests/test_cleanup_jobs_fixes6.py" = ["E501"]
pyproject.toml:43:"scripts/*" = ["T20", "E501", "F401", "I001", "E741"]
```

```
$ awk 'length > 100 {c++} END {print c}' tests/test_cleanup_jobs_fixes6.py
5
$ .venv/bin/ruff check tests/test_cleanup_jobs_fixes6.py --config "lint.per-file-ignores={}" | tail -1
Found 5 errors.
```

Five over-long lines in a frozen file force a permanent, file-specific lint
exception that will silently cover every *future* line in that file. Options:
(a) accept and add a comment in `pyproject.toml` saying it exists because the
file is frozen, (b) ask Harsh to approve a whitespace-only reformat of those 5
lines (it changes the sha256, so the frozen-test contract must say content
changes are allowed for line wrapping), or (c) move the exception to a
`# noqa: E501` on the 5 lines — also a frozen-file edit. Recommend (a) now,
(b) at the next approved unfreeze.

---

## C8 — `scripts/*` ignores `F401` (four rules) and hides real dead imports

`pyproject.toml:43` disables `F401` for `scripts/`, so unused imports in the
nine scripts are invisible to lint. Manual check of the one script with real
imports is cheap; this is a low-severity note, not a defect on its own.

---

# D — Logic complexity

Measured with `~/agy-review/audit/metrics.py`: `branches` = cyclomatic-ish
decision points (`if/elif/for/while/except/with/IfExp/Match`, `and`/`or` terms,
comprehension `for`+`if`, `assert`), `depth` = maximum nesting of control-flow
nodes.

## D1 — `_check_worktree_status`: 473 lines, 115 branches, depth 12

**Severity:** high · **Effort:** L · **Risk of action:** medium

```
$ metrics (cleanup_jobs.py)
br 115  len 473  depth 12  _check_worktree_status  line 391
$ grep -c "worktree registrations unreadable" src/agy_mcp/cleanup_jobs.py
35
$ sed -n '391,868p' src/agy_mcp/cleanup_jobs.py | grep -c "return (\|return True\|return False"
38
$ grep -c "worktree registered (run 'git worktree prune'" src/agy_mcp/cleanup_jobs.py
8
```

What makes it convoluted — concretely:

1. **35 copies of the same message prefix**, each an inline f-string inside a
   4-line `return (True, ("worktree registrations unreadable " f"..."))`
   tuple. The 8 identical 3-line "worktree registered (run 'git worktree
   prune' …)" returns are copy-paste; one constant + one helper would do.
2. **Two independent axes of nesting interleaved**: what shape is `repo/.git`
   (dangling symlink / pointer file / directory / other / absent) and what
   shape is the *target* git dir (linked worktree with `commondir`, standalone,
   bare, corrupt). The code enumerates combinations inline rather than
   dispatching to one function per shape.
3. **`candidate_wt_roots` is filled by side effect** in six different branches
   (`candidate_wt_roots.append` at `:607, :609, :617, :653, :679, :691`), then consumed in a loop at `:742` —
   a reader must hold the whole function in mind to know which roots can exist.
4. **`unreadable_entry_reason` is a "first error wins" accumulator** guarded by
   9 copies of `if unreadable_entry_reason is None` (`:757, :768, :802, :811,
   :821, :826, :835, :847, :852`).
5. **Errors are handled twice**: `except (OSError, UnicodeDecodeError)` and then
   a bare `except Exception` with the same message (`:710-713`, `:858-861`; the
   narrow handler itself appears 6 times in the function, at `:710, :748, :801,
   :825, :851, :858`), so every failure path is duplicated.
6. **`return (True, reason)` means "registered"**, i.e. "do not delete", and it
   is also the value returned for "I could not tell" — the fail-closed rule.
   That is correct, but the tuple's second element carries three different kinds
   of meaning (registered / unreadable / invalid), which is why the strings are
   so heterogeneous.

**Proposed simplification (behavior-preserving):**

```
W_UNREADABLE = "worktree registrations unreadable"
W_REGISTERED = "worktree registered (run 'git worktree prune' in repository to unregister)"

def _unreadable(detail: str) -> tuple[bool, str]:        # one constructor
    return True, f"{W_UNREADABLE} ({detail})"

def _resolve_git_dirs(repo_path, job_dir) -> tuple[list[Path], tuple[bool,str]|None]:
    """Return every <gitdir>/worktrees root implied by repo_path, or a fail-closed reason."""
    ...   # the repo/.git shape dispatch, 5 named helpers, one per shape

def _scan_worktree_entries(roots, owned_paths) -> tuple[bool, str] | None:
    """Walk registration entries; return a fail-closed reason at the first problem."""
```

Expected: 473 → ~230 lines, 115 → ~45 branches, unchanged messages (which the
frozen tests substring-match, see D7).

## D2 — `supervisor._finalize_terminated_process`: 588 lines, 126 branches

**Severity:** high · **Effort:** L · **Risk of action:** medium-high

```
br 126  len 588  depth 6  supervisor.py:928 _finalize_terminated_process
```

Twenty phase comments inside one method (measured line numbers):
`# Check cli.log denials before parsing` (:947),
`# Parse agy output per SPEC section 15` (:969), `# Post-completion audit check`
(:992), `# Check verification if verify command configured` (:1044),
`# Post-verify security audit gate` (:1198), `# Check safety-net commit on
normal completion` (:1241), `# Update job telemetry` (:1292), `# Determine
effective agy_home` (:1315), `# Update files_modified vs base_commit` (:1373),
`# C1 rule: No false success when denied without progress` (:1380),
`# Interruption detection` (:1397), `# Plan accounting` (:1415),
`# Agent report` (:1436), `# Per-job usage cost` (:1440), `# State transition
target` (:1446), `# D68: append usage.jsonl BEFORE the terminal state becomes
observable` (:1473).

Each header is a phase that can become a method; the method then reads as an
ordered pipeline (which is the real invariant — *order matters*):

| Phase → method | Measured span (phase comment at) |
|---|---|
| `_read_generation_streams()` | 931-946 (comment at :936) |
| `_read_cli_log_signals()` | 947-968 (comments at :947, :952, :958) |
| `_parse_and_audit()` | 969-1043 (comments at :969, :982, :992) |
| `_run_verify_gate()` | 1044-1237 (comment at :1044; nested D80 diagnostics at :1083, :1160, :1168) |
| `_post_verify_audit_gate()` | 1198-1240 (comment at :1198) |
| `_safety_net_gate()` | 1241-1291 (comments at :1241, :1242 "Ordering matters") |
| `_update_telemetry()` | 1292-1314 (comment at :1292) |
| `_resolve_agy_home()` | 1315-1372 (comment at :1315) |
| `_apply_completion_vetoes()` (C1 denial rule, interruption, plan) | 1373-1445 (comments at :1380, :1397, :1415, :1431, :1436, :1440) |
| `_persist_terminal_state()` (usage log before state, per D68) | 1446-1516 (comments at :1446, :1463, :1473) |

**Why this one is riskier than it looks:** the ordering comments *are* the
contract (D68: `usage.jsonl` before the state transition; the safety-net commit
after both audits). A split must preserve phase order exactly, and the frozen
`test_f*`/`test_resume_and_commitmsg` tests assert observable ordering, not
internals. Recommend splitting only with a supervisor-level integration test
run before/after (`tests/test_supervisor.py`, `tests/test_resume_and_commitmsg.py`,
`tests/test_server_e2e.py`).

## D3 — `config._validate_config_dict`: depth 17

**Severity:** medium · **Effort:** M · **Risk of action:** low

```
br 73  len 204  depth 17  config.py:88 _validate_config_dict
```

A 27-key `known_keys` set, an `if key in (...)` / `elif key in (...)` ladder with
a nested `if key == "auto_fix_attempts": if val < 0: … elif …` chain reaching
nesting level 17. Every branch raises `ConfigError` with a hand-written message.

**Proposed:** a table-driven validator —
`_RULES: dict[str, Callable[[Any], Any]]` with small named validators
(`_int_min(n)`, `_int_range(lo, hi)`, `_bool`, `_regex_list`), plus
`_INT_FIELDS = {"auto_fix_attempts": (0, None), "auto_resume_on_denial": (0, 3),
…}`. Expected: 204 → ~90 lines, depth 17 → 4, and adding a config key becomes
one table row (which is the actual maintenance risk today — the ladder is
where the "invented key" bugs come from).

## D4 — `cleanup_jobs.run_cleanup`: 331 lines, 94 branches, one `while True`

**Severity:** medium · **Effort:** M · **Risk of action:** medium

```
br 94  len 331  depth 6  cleanup_jobs.py:1070 run_cleanup
```

It performs, in one body: argument validation, mode selection (`project` vs
`job`), a full store scan, edge-protection closure, manifest planning, a
per-candidate destructive loop with three re-checks, post-run accounting, and
report construction. The three re-checks in the delete loop (classification,
repo match, edge protection) are the safety core and are the part that will be
touched next.

**Proposed:** split into `_plan_candidates(...) -> (candidates, kept, mode)` and
`_execute_deletions(planned_ids, ...) -> (deleted, failed, skipped)`; keep the
public return dict identical (887 frozen tests pin its keys). See also C3b for
the branch that can simply disappear.

## D5-D6 — Secondary complexity

| ID | Function | br | len | depth | Note |
|---|---|---|---|---|---|
| D5 | `recovery.reconcile_on_startup` :100 | 59 | 257 | 8 | Three recovery paths (live, orphaned, stale-queued) inline; medium |
| D6 | `verify._validate_tool_args` :86 | 6 | 20 | 1 | Dispatch ladder by tool name to 7 near-identical `_validate_*_args` (`pytest`/`ruff`/`go`/`other` are each 11 lines/4 branches, `npm` is 2 lines); `_validate_cargo_args` (:240) alone has 36 lines/**16 branches** and no docstring (E4). Replace with a `_TOOL_RULES` table. |
| — | `audit.check_host_path_snapshot` :227 | 20 | 137 | 4 | Acceptable |
| — | `transcript.read_denied_calls` :87 | 42 | 157 | 7 | Confinement rules; acceptable, well-commented |
| — | `parse_result.parse_agy_output` :152 | 34 | 195 | 4 | Ordered precedence; acceptable |
| — | `gitops.stage_changes` :431 | 27 | 136 | 3 | Acceptable |
| — | `worktree.is_dangerous_allow_rule` :486 | 29 | 69 | 2 | High branch density but flat and readable — leave |

## D7 — Hand-parsed git internals vs `git worktree list --porcelain`

**Severity:** high (design) · **Effort:** L · **Risk of action:** high · **Decision needed from Harsh**

Today `_check_worktree_status` never runs `git`; it re-implements:
`repo/.git` pointer parsing (`cleanup_jobs.py:489-513`), `commondir` resolution
(`:532-596`), bare-repo heuristics (`:663-709`), and a walk of
`<gitdir>/worktrees/*/gitdir` comparing resolved paths (`:742-800`). Meanwhile
`git worktree list --porcelain` answers the registration question directly, and
the repo already parses porcelain elsewhere (`gitops.parse_porcelain_z` :399).

**What a git-based version would buy:** ~300 fewer lines, no re-implementation
of gitdir/commondir/bare rules, and — most importantly — agreement with the
oracle the tests already use (`tests/test_cleanup_jobs_fixes7.py` runs
`git worktree list --porcelain` as ground truth; `tests/test_cleanup_jobs_differential.py`
has `check_git_registered`).

**Frozen constraints that force extra strictness** (each is a real assertion,
not a guess):

| # | Constraint | Evidence |
|---|---|---|
| K1 | `_check_worktree_status(job_dir, data)` must keep existing as `agy_mcp.cleanup_jobs._check_worktree_status`, return `(bool, str\|None)`, and its "yes" reason must contain the substring `"worktree registered"`. | `tests/test_cleanup_jobs_differential.py:34-38, 969-972` (`assert "worktree registered" in (base_reason or "")`) |
| K2 | It must never block on a FIFO/socket/device used as `HEAD`/`config`/`commondir`/`gitdir` — that is why the code `stat()`s before opening (`cleanup_jobs.py:82-83, 90-91`). A `git` subprocess **would** block on such a file; only a `subprocess(timeout=…)` would save it, adding a failure mode the pure-Python path does not have. | `tests/test_cleanup_jobs_fixes7.py:769` (`_MUTATIONS` includes `fifo`), 11 FIFO cells that each cost ~4.1 s today because *the oracle* git call times out at 2 s |
| K3 | Every corrupted-metadata case must fail **closed** (keep the row), and the failure reason must be a single-line string with no traceback. | `tests/test_cleanup_jobs_fixes7.py` (R7-R9), `_fixes8.py` (R10-R12), `_fixes9.py` (R13-R16) |
| K4 | A job whose `repo_path` no longer exists must remain a **candidate** (deletable), because that is the main use case for `--project` cleanup after a repo is removed. | current code path: `:665` → `_has_git_dir_markers` false → `(False, None)` → not registered |
| K5 | Over-blocking is explicitly allowed by the differential harness ("If cleaner decides kept … that is an OVER-BLOCK … passes"), which means **a fail-closed regression would not be caught** by the frozen suite. Any git-based rewrite needs a new positive test that a deleted-repo row is still deleted. | `tests/test_cleanup_jobs_differential.py:4-8` |

**Recommendation:** do **not** replace the decision with a git call. Instead:

1. Keep the pure-Python, stat-before-open parser (K2 is a real hardening win and
   is the reason the FIFO tests pass without waiting on a timeout).
2. Reduce it exactly as D1 describes (named constructors, one helper per
   `.git` shape, one entry-scan helper) — no behavior change, so 887 tests stay
   green.
3. Add `git worktree list --porcelain` as a **second opinion** in the
   differential harness only (test-side), where a disagreement is reported but
   does not change the decision. That gives the oracle coverage without
   introducing a subprocess into the destructive path.
4. Revisit the full replacement only if a real-world corruption case is found
   where the hand parser disagrees with git — and only with Harsh's decision,
   because it changes the fail-closed surface.

## D8 — Bare `assert` used as control flow

**Severity:** low · **Effort:** S · **Risk of action:** low

```
cleanup_jobs.py:1168    assert job_ids is not None
cleanup_jobs.py:1202    assert kept_strings is not None
cleanup_jobs.py:1330    assert dyn_kept_strings is not None
```

`python -O` (or `PYTHONOPTIMIZE=1`) removes them, after which `run_cleanup`
would iterate `None`. The invariants are real (enforced by the mode-selection
block at `:1550-1562` and by `_collect_kept_strings`'s return contract), so the
fix is to make the narrowing explicit — e.g. raise `ValueError("internal:
job_ids required in job mode")` — or restructure so the type is non-optional.
Low severity because nothing in the repo runs with `-O`, but it is a
destructive code path.

## Sources

- `src/agy_mcp/`
- `tests/`
- `scripts/`
- `doc/audits/evidence/2026-11-02-part1/unused.json`
