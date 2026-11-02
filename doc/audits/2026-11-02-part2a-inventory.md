# Part 2a — Documentation inventory, accuracy and bloat

Status: current

**Repository:** `agy-mcp` (`~/Documents/GITHUB PROJECTS/agy-mcp`)
**Audited revision:** `main` @ `a7ed98aa502b1a5ed84551aff7aa1c705156dedf`
**Mode:** read-only. No repository file was created, edited, moved, deleted,
committed, installed or branched. All writes are under `~/agy-review/audit/`.
**Method:** `wc -l/-c`, `git log -1 --format=… -- <path>`, `git ls-files`,
`grep -n`, and `env -u PYTHONPATH .venv/bin/python` one-off AST/text counts whose
scripts are quoted inline. A claim without a quoted command or a `file:line` is
labelled **UNVERIFIED** and is not used as a finding.

**Path shorthand:** a bare module name (`manager.py`) means
`src/agy_mcp/manager.py`; a bare document name (`DSH_SETUP.md`) means
`docs/DSH_SETUP.md`; `install.sh` means `installer/install.sh`.

---

## 1. Dashboard (measured)

```
$ git ls-files '*.md' | wc -l
79
$ git ls-files '*.md' | grep -v '^docs/reports/' | wc -l
28
$ git ls-files 'docs/reports/*.md' | wc -l
51
$ git ls-files '*.md' | grep -v '^docs/reports/' | xargs wc -c | tail -1
757662 total
$ find docs/reports -type f | wc -l
527
$ du -sh docs/reports
4.2M
$ git ls-files | wc -l
724
```

Prose documentation (excluding the 51 report markdown files) is **758 KB across
28 files**. It is not evenly distributed:

| Kind | Files | Bytes | Share of 758 KB |
|---|---:|---:|---:|
| Normative contract (`SPEC.md`) | 1 | 103,971 | 13.7% |
| Decisions (`docs/DECISIONS.md`) | 1 | 140,843 | 18.6% |
| Reviews/audits (`DS_REVIEW_1/2`, `AUDIT_ORGANIZATION`) | 3 | 154,245 | 20.3% |
| Probe/smoke findings (`AGY_FINDINGS*`, `SMOKE_FINDINGS`) | 4 | 101,906 | 13.4% |
| Navigation (`CODEMAP`) | 1 | 51,070 | 6.7% |
| Test matrix | 1 | 47,226 | 6.2% |
| Design/history notes (`ARCHITECTURE`, `SCAFFOLD`, `CHANGELOG_SPEC`, `QUESTIONS`, `DESIGN_MODES`) | 5 | 57,713 | 7.6% |
| User docs (`README`, `DSH_SETUP`, `ORCHESTRATION`) | 3 | 35,963 | 4.7% |
| Changelog + roadmap | 2 | 32,914 | 4.3% |
| Agent instructions (`AGENTS`, `CLAUDE`, orchestrator logs) | 5 | 18,242 | 2.4% |
| Runtime/embedded docs (`preamble.md`, `agents_block.md`, fake-agy README) | 3 | 13,569 | 1.8% |

The 527 evidence files in `docs/reports/` are 73% of the 724 tracked files
(`A3` in Part 1). They are treated as a group in §3.3, not as documents.

---

## 2. Inventory

`Concerns` = does the file mix reference + design rationale + history +
experiments? `Status` = current, historical, or stale (a stale claim is proven
in §4). Sizes are `lines / bytes`.

### 2.1 Root

| Path | Size | Last changed | Purpose | Audience | Concerns | Status |
|---|---|---:|---|---|---|---|
| `README.md` | 143 / 8,201 | 2026-10-26 | entry point: install, MCP tool list, safety table, lifecycle | users, orchestrators | **yes** (install + reference + safety findings + lifecycle) | current; table duplicated from SPEC §20 |
| `SPEC.md` | 1,036 / 103,971 | 2026-11-02 | normative behaviour, tool contracts, state machine, config, caps, two cleaner CLIs | implementers, orchestrators | **yes** (contract + reference + rationale + findings + history) | current; bloated (INV-01) |
| `CHANGELOG.md` | 320 / 26,760 | 2026-11-02 | history of changes, newest first | maintainers | **yes** (history + per-round design detail) | current; 76% one feature (INV-03) |
| `AGENTS.md` | 112 / 5,774 | 2026-10-26 | rules for an AI working in the repo, layout, commands | AI agents | no | current; duplicates repo layout from CODEMAP |
| `CLAUDE.md` | 3 / 86 | 2026-10-26 | one-line pointer to `AGENTS.md` | Claude agents | no | current |

### 2.2 `docs/` — contract, design, history

| Path | Size | Last changed | Purpose | Audience | Concerns | Status |
|---|---|---:|---|---|---|---|
| `docs/CODEMAP.md` | 752 / 51,070 | 2026-11-02 | module map, job flow, "where do I look", generated module index | new contributors, AI agents | no (navigation only) | current content; stale branch/`D` refs (INV-17) |
| `docs/DECISIONS.md` | 735 / 140,843 | 2026-11-02 | D1–D100 design decisions with rationale and rule | maintainers, reviewers | **yes** (design + history + per-round evidence) | current IDs; bloated (INV-02) |
| `docs/ARCHITECTURE.md` | 152 / 8,413 | 2026-10-30 | short narrative of layers, persistence, locks, security | contributors | partly (narrative + policy) | **stale lock order and line refs** (INV-14) |
| `docs/TEST_MATRIX.md` | 388 / 47,226 | 2026-10-30 | SPEC-section → test mapping, 257 rows | maintainers | no | **stale** (INV-16) |
| `docs/DSH_SETUP.md` | 102 / 3,931 | 2026-10-25 | dsh/client setup, timeout, smoke checklist | users | no | **stale tool count** (INV-08) |
| `docs/ORCHESTRATION.md` | 388 / 23,831 | 2026-10-27 | how to drive dsh/non-dsh clients, approval caveats, workflows | orchestrators | **yes** (how-to + two run reports + UNSURE register) | current; mixed (STR-02) |
| `docs/ROADMAP.md` | 119 / 6,154 | 2026-10-26 | planned work with DONE markers | maintainers | **yes** (history + plan) | partly stale (branch names) (INV-21) |
| `docs/QUESTIONS.md` | 33 / 2,841 | 2026-10-24 | six settled architectural questions | maintainers | no | **stale Q2** (INV-09) |
| `docs/DESIGN_MODES.md` | 75 / 4,715 | 2026-10-25 | three execution modes, status NOT BUILT | maintainers | no | current as design; no code |
| `docs/CHANGELOG_SPEC.md` | 44 / 7,824 | 2026-10-24 | F01–F36 review disposition table | reviewers | yes (history) | **stale F04** (INV-10) |
| `docs/SCAFFOLD_NOTES.md` | 329 / 33,920 | 2026-10-25 | build log and SPEC v3 interpretations per scaffold phase | authors only | **yes** (design + history + test evidence) | **stale throughout** (INV-26) |
| `docs/AUDIT_ORGANIZATION.md` | 443 / 27,751 | 2026-10-30 | organization audit A1–A14 | maintainers | no | **stale counts and A3** (INV-15) |

### 2.3 `docs/` — findings, probes, reviews

| Path | Size | Last changed | Purpose | Audience | Concerns | Status |
|---|---|---:|---|---|---|---|
| `docs/AGY_FINDINGS.md` | 498 / 31,462 | 2026-10-24 | Probe 1: 12 CLI questions (Q1–Q12) | implementers | experiments only | current as a record; never indexed |
| `docs/AGY_FINDINGS_2.md` | 426 / 28,381 | 2026-10-24 | Probe 2: security S1–S9, token cost, recommended rules | implementers | experiments + recommendations | current as a record |
| `docs/AGY_FINDINGS_3.md` | 390 / 24,669 | 2026-10-24 | Probe 3: escapes/boundaries T1–T7 | implementers | experiments + recommendations | current as a record |
| `docs/SMOKE_FINDINGS.md` | 275 / 17,394 | 2026-10-25 | real-agy smoke S1–S7 and nine evaluation questions | implementers | experiments + conclusions | current as a record; 22 broken links (INV-11) |
| `docs/DS_REVIEW_1.md` | 479 / 72,622 | 2026-10-24 | external SPEC review F01–F36 | maintainers | audit | historical; 6 broken links (INV-11) |
| `docs/DS_REVIEW_2.md` | 580 / 53,872 | 2026-10-25 | external code review F1–F18 | maintainers | audit | historical |

### 2.4 Work logs, runtime assets, test docs

| Path | Size | Last changed | Purpose | Audience | Concerns | Status |
|---|---|---:|---|---|---|---|
| `docs/orchestrator/PLAN.md` | 65 / 3,263 | 2026-10-31 | one-off work log for the test-isolation task | that session only | history | historical |
| `docs/orchestrator/agy-brief-test-isolation.md` | 198 / 9,119 | 2026-10-31 | the brief handed to one agy job | that job only | instruction + acceptance | historical |
| `installer/agents_block.md` | 43 / 6,383 | 2026-10-27 | **runtime** orchestrator guidance embedded by the installer | users' agents | no | current; canonical source |
| `src/agy_mcp/preamble.md` | 18 / 2,398 | 2026-10-26 | **runtime** prompt prepended to every agy job | the agy agent | no | current; byte-equal to SPEC §14 (INV-06) |
| `tests/fake_agy/README.md` | 68 / 4,788 | 2026-10-26 | test double CLI, 26 scenarios | test authors | no | **verified current** (positive check) |

### 2.5 `docs/reports/` — 527 files, 51 markdown, 4.2 MB

23 directories plus 8 top-level markdown files. **17 of the 23 directories
contain no markdown at all**:

```
$ for d in docs/reports/*/; do
    printf '%-26s %s\n' "$(basename $d)" "$(find "$d" -name '*.md' | wc -l)"
  done
agymcp-fixes-2 0          hardening 0          smoke-fixes 0
docs-codemap 0            installer-agymcp 0   smoke-fixes-2 0
fix-batch1 0              installer-fixes 2    smoke-fixes-3 0
fix-batch2 0              orchestrator-workflows 1  stale-commit-msg 0
fix-batch2b 0             real-smoke-1 9       verify-fixes 0
fix-batch3a 0             server 0             verify-loop 1
fix-batch3b 0             smoke 0              wrapper-verify-commit 1
fix-batch4 0              flaky-f1 29
```

36 of the 51 markdown files are job scratchpad copies (`PLAN.md`, `NOTES.md`,
`LOG.md`, `REPORT.md`) under `flaky-f1` (29) and `real-smoke-1` (9); only 15
are report pages. Dates span 2026-10-25 → 2026-10-29
(`git log -1 --date=short -- docs/reports/<dir>`). There is **no
`cleanup_jobs` or `cleanup_fixtures` evidence directory**, although `D89`–`D100`
are the largest group of changes in the repository (see EXP-02).

---

## 3. What each document is actually for (and where it drifts)

### 3.1 Root documents

- **README.md** is the only user entry point. It is well written for its size
  but carries four jobs: install, tool list, safety model, and lifecycle. Its
  install section and `installer/install.sh --help` are two hand-maintained
  copies that have already drifted (INV-07). Its capability table is a
  reworded copy of `SPEC.md` §20 (INV-05). It links three local files and all
  three resolve.
- **SPEC.md** is the de-facto single document of record. It contains at least
  six kinds of text: normative rules (§1–§19), a tool reference (§8), a config
  reference (§18), a prompt asset (§14), a findings summary (§21), and two
  full CLI manuals (§22–§23). Sections 22–23 alone are 25,306 bytes (24.3% of
  the file) and describe helper tools that are not part of the server.
- **CHANGELOG.md** is the only history. Its 17 entries are 13 cleanup_jobs
  rounds plus four earlier notes; it is a fix-round log, not release notes.
- **AGENTS.md / CLAUDE.md** are short and current. `AGENTS.md` duplicates the
  repo layout from `CODEMAP.md` and the doc-update rule from `SPEC.md` §4;
  that duplication is intentional for an agent entry point and is low risk.

### 3.2 `docs/` prose

- **CODEMAP.md** is the best-maintained document in the repo. Its generated
  table is fresh (`scripts/gen_codemap_index.py --check` → `CODEMAP.md
  generated section is fresh`, exit 0) and a test enforces it. Its hand-written
  prose is also the only place that explains each module's public API line by
  line. Its weaknesses are three stale statements (INV-17), not structure.
- **DECISIONS.md** is a chronological append-only log. It has no table of
  contents, no topic index, and two headings for 99 entries. `D88`–`D100` are
  38 KB (27%) and repeat rules that also live in `SPEC.md` §23 and
  `CHANGELOG.md` (INV-04).
- **ARCHITECTURE.md** is a useful 152-line narrative, but two of its four
  sections are already wrong: the lock order (INV-12) and the line references
  (`manager.py:1065`, `:1153` point at unrelated code).
- **TEST_MATRIX.md** is the clearest example of documentation rot: it claims
  full coverage and references tests that no longer exist (INV-14).
- **DSH_SETUP.md, QUESTIONS.md, CHANGELOG_SPEC.md, SCAFFOLD_NOTES.md** are
  historical snapshots that were never marked as such. Each contains at least
  one statement that the code now contradicts.
- **DS_REVIEW_1.md / DS_REVIEW_2.md / AUDIT_ORGANIZATION.md** are the three
  existing audits. They are substantive but live in three different places
  with three formats and no index (STR-03). `AUDIT_ORGANIZATION.md` still
  reports `checkpoint_commit_minutes` as dead configuration after `D61` wired
  it (INV-13).

### 3.3 `docs/reports/` evidence

This is an evidence store, not documentation. 35 of the 51 markdown files are
in two directories (`flaky-f1` 8, `real-smoke-1` 8) or are raw job
scratchpads; the rest are a single `REPORT.md`/`README.md` per round. The
remaining 492 files are `.txt`, `.json`, `.jsonl`, `.log` and job trees. They
are valuable as evidence and should not be deleted, but they are not a
findings record: 17 directories have no narrative at all, so a reader cannot tell
what question the logs answer.

### 3.4 Inline documentation

- Module docstrings: 24/24 modules carry a "what it owns / what it must never
  do" docstring; 86.6% of functions do (Part 1 E1). This is a real strength and
  should move into `doc/project/code-layout.md`, not be duplicated.
- `states.py:55` says the transition table is "defined exactly as in SPEC.md
  section 7", but `TRANSITION_TABLE` has 16 rows while SPEC §7 has 20
  (`env -u PYTHONPATH .venv/bin/python -c "from agy_mcp.states import
  TRANSITION_TABLE; print(len(TRANSITION_TABLE))"` → `16`; SPEC table rows →
  `20`). Four reason-level rows (`plan_incomplete`, `quota_exhausted`,
  `auth_required`, `verify_timeout`) are missing. The state-level machine is
  equivalent; the claim is not (INV-17b).
- Script help texts: 3 of 9 scripts take flags (`check_no_behavior_change.py`,
  `gen_codemap_index.py`, `real_smoke.py`); none is documented outside its own
  `--help`/docstring (COV-10).
- `installer/install.sh --help` is a documentation surface with a proven
  contradiction (INV-07).

---

## 4. Spot-checks against the code (each finding's evidence)

Every item below is one command or one `file:line` pair. Commands were run from
the repository root. The sections are in the order the evidence was gathered;
their IDs match the finding table in §6.

### INV-07 — installer help contradicts the code and README

`installer/install.sh:913` (help text) says `--upgrade` runs
`pip --upgrade --force-reinstall --no-deps`. The code at
`installer/install.sh:387-389` deliberately does **not** pass `--no-deps`:

```
# Dependencies are deliberately re-resolved: a new version can add
# or raise a requirement, and --no-deps would leave the venv stale.
agy_mcp_run env -u PYTHONPATH "$venv/bin/python" -m pip install ... --upgrade --force-reinstall "${extra[@]}" "$src"
```

`README.md:27-28` agrees with the code ("without `--no-deps`"). The help text
is the only wrong copy.

### INV-08 — `docs/DSH_SETUP.md` lists nine tools

`docs/DSH_SETUP.md:89-98` says "verify that all 9 tools appear" and omits
`agy_usage`. `src/agy_mcp/server.py:449` registers `agy_usage`; the count is ten
and `tests/fixtures/tools_schema.json` has ten entries. `docs/ORCHESTRATION.md:64`
already flags this as stale, which means the stale statement was noticed but
not fixed.

### INV-09 — `docs/QUESTIONS.md` Q2 contradicts D62

`docs/QUESTIONS.md:17`: "Do NOT edit `.git/info/exclude` … Instead, pass an
explicit pathspec". `SPEC.md:402-405` and `worktree.py:274-304`
(`ensure_local_exclude`) now append `.agy/` to the repository-local
`info/exclude` *in addition to* the pathspec. The question is marked
"SUPERSEDED (v3 / F04)" but its resolution is no longer what the code does.

### INV-10 — `docs/CHANGELOG_SPEC.md` F04 repeats the same obsolete statement

`docs/CHANGELOG_SPEC.md:12`: "Replaced `.git/info/exclude` in common dir with
git pathspec … to avoid mutating user repo." The same contradiction as INV-09,
in a second historical file.

### INV-11 — 28 of 31 relative links are broken

```
$ env -u PYTHONPATH .venv/bin/python - <<'EOF'
# resolve every markdown link relative to its own file
...
EOF
relative links 31 broken (resolved from file dir) 28
```

Examples (both sides are in the repo):

- `docs/DS_REVIEW_1.md:3` links `SPEC.md` (`../../SPEC.md`) → `docs/SPEC.md` (missing).
- `docs/SMOKE_FINDINGS.md:27` links
  ``[`docs/reports/smoke/s1-result.json`](../../docs/reports/smoke/s1-result.json)``
  → `docs/docs/reports/...` (missing).

The only three working links are `README.md:5`. The cause is a convention that
writes repo-root paths inside `docs/`; GitHub resolves them relative to the
page.

### INV-12 — `D1` describes a different `Runner`

`docs/DECISIONS.md:14`: "Introduce an abstract `Runner` interface (`launch`,
`poll`, `resume`, `cancel`, `cleanup`)". `src/agy_mcp/runner.py:175-231` defines
`launch`, `poll`, `kill`, `cleanup`; `SPEC.md:91` also says `kill`. There is no
`resume`/`cancel` method.

### INV-13 — `D3` describes a different watcher and safety net

`docs/DECISIONS.md:23-24` says the watcher "polls every 5 seconds" and the
safety-net commit runs on `done`, `timeout`, `failed` and `cancelled`.
`manager.py:132-133` sets `settle_window_seconds=5.0` and
`poll_interval_seconds=1.0`; the live finalization path
(`supervisor.py:1249-1251`) runs `safety_net_commit` only when
`final_state == JobState.DONE`. `watcher.final_commit` (`watcher.py:848-857`)
encodes the same done-only rule but has no callers. `SPEC.md:540` agrees with
the live path.

### INV-14 — `ARCHITECTURE.md` lock order is wrong twice

`docs/ARCHITECTURE.md:49-60` numbers the concurrency flock as tier 1 and the
per-job lock as tier 2, then the caveat at `:62-67` says `manager.resume`
"takes tier 2 before tier 1" and that "SPEC §10 describes the flock as the outer
tier". `SPEC.md:486-487` says the job lock is tier 1 and the flock tier 2.
The code does what SPEC says: `manager.py:1393` takes the job lock,
`manager.py:1509` takes `concurrency_lock`. The cited `manager.py:1065` and
`:1153` are unrelated lines (`t.start()` and a diff parse).

### INV-15 — `AUDIT_ORGANIZATION.md` A3 and its counts are stale

`docs/AUDIT_ORGANIZATION.md:19` says `checkpoint_commit_minutes` "is validated
and documented but never read; the watcher hardcodes 600 s". `manager.py:531`
now passes `checkpoint_interval_seconds=self.config.checkpoint_commit_minutes * 60`
(also `:1049`, `:1567`), so the key is live. The same file's A1 (`:17`) says
`manager.py` 1,341 lines and `supervisor.py` 943; `wc -l` today gives 1,737 and
1,622. A4 is marked resolved in the body (`:100-106`) but A3 is not.

### INV-16 — `TEST_MATRIX.md` references 23 tests that do not exist

```
$ python - <<'EOF'  # extract `tests/x.py::test_y` refs, check def y exists
...
EOF
refs 214
missing files 0
missing funcs 23
test modules not mentioned: 80 of 106
```

Examples: `tests/test_denial_recovery.py::test_auto_resume_on_denial_success`
(the file has `test_auto_resume_on_denial_succeeds_with_one_commit` at
`:218`), `tests/test_verify.py::test_extract_failing_tests`,
`tests/test_runner.py::test_manager_scenario_crash_rc1`. Line 388 then says:
"None. All SPEC requirements … are fully covered by automated tests." That
claim is not supported by the file it sits in.

### INV-17 — `CODEMAP.md` branch and decision refs

`docs/CODEMAP.md:5` says it "describes the code as it exists on branch
`exp/docs-codemap`". `git rev-parse --verify exp/docs-codemap` →
`fatal: Needed a single revision`; the repository is on `main` and no such
branch exists (`git branch -a` has 16 branches, none named that).
`docs/CODEMAP.md:148` cites `docs/DECISIONS.md (D89, D90, D91, D92, D93, D94)`
for `cleanup_jobs`; `D95`–`D100` now exist. The cleanup_jobs **Tests** list at
`docs/CODEMAP.md:279-280` also stops at `fixes5`, while `fixes6`–`fixes10` and
the differential suite exist.

### INV-18 — `agy-capability-inventory.md` reports a bug that is fixed

`docs/reports/agy-capability-inventory.md:29` says "the conversation-ID fallback
regex can persist the literal token `using`; it is not validated before being
passed back as `--conversation`". `D81` removed that regex:
`src/agy_mcp/cli_log.py:191` says the loose match on "using" is excluded, and
`util.strict_uuid` (`util.py:242-252`) accepts only a 36-character UUID.
The report's own "fix direction" was implemented. The report has no
"superseded" marker.

### INV-26 — `SCAFFOLD_NOTES.md` is stale in at least three places

- `docs/SCAFFOLD_NOTES.md:13` gives `agy_wait(job_id, seconds: int = 60)`;
  `server.py:270` and `SPEC.md:292` say 30 (max 300).
- `docs/SCAFFOLD_NOTES.md:27` says "The 13 state transitions … are codified";
  `states.py` has 16 `StateTransition` entries and SPEC §7 has 20 rows.
- `docs/SCAFFOLD_NOTES.md:265` says "Registers exactly nine tools";
  `server.py` registers ten.

### INV-06 — `preamble.md` and SPEC §14 are byte-identical duplicates

```
$ python - <<'EOF'  # compare the §14 fenced block with the packaged file
...
EOF
preamble byte-equal: True len 2397 2397
```

`SPEC.md:630-649` contains the same 2,397 bytes as
`src/agy_mcp/preamble.md`. `tests/test_b4_preamble.py` checks rules *inside*
the preamble but nothing compares the two copies, so an edit to one and not
the other is silent drift.

### INV-05 — `README.md` and `SPEC.md` §20 are the same table twice

Both contain the 7-row capability table with an 8-line diff in wording only
(measured with `difflib`; e.g. "across filesystem" vs "across user files").
`SPEC.md:801` calls the README table a requirement, so the duplication is
policy, but there is no check that the two stay equal.

### INV-19 — `D64` does not exist

`docs/DECISIONS.md` has headings D1–D100 except D64:

```
$ python - <<'EOF'  # regex '^### (D\d+):'
...
EOF
heading count 99
min/max 1 100
missing numbers [64]
```

No file references `D64` (`grep -rn D64 --include=*.md --include=*.py .` → no
hits), so the gap is silent but harmless.

### INV-22 — Configuration reference is missing one live key

```
$ python - <<'EOF'  # compare SPEC §18 table keys with config.known_keys
...
EOF
spec keys: 26
code keys: 27
in code not spec: ['timezone']
```

`timezone` is a real config field (`config.py:84`), validated at `:269-278`,
used at `manager.py:374`, and referenced in prose at `SPEC.md:729` — but it is
not in the §18 table. The same command shows no key present in the table and
absent from the code.

### COV-04 evidence — default state path omits `XDG_STATE_HOME`

`jobs.py:31-34` resolves `$XDG_STATE_HOME/agy-mcp` before
`~/.local/state/agy-mcp`. `README.md:91` and `SPEC.md:430` state only the
`~/.local/state` form. `grep -c XDG_STATE_HOME SPEC.md README.md` → `0` and
`0`. `XDG_CONFIG_HOME` is documented once, in the installer section
(`SPEC.md:827`), not in the config section.

### INV-23 — Installer env vars: 12 read, 9 in `--help`, 6 in README

Input `AGY_MCP_*` variables read by `installer/install.sh`:
`AGY_MCP_AGY_BINARY`, `AGY_MCP_ALLOW_INSIDE_DSH`, `AGY_MCP_CLIENT`,
`AGY_MCP_CONFIG_PATH`, `AGY_MCP_DRY_RUN`, `AGY_MCP_EDITABLE`,
`AGY_MCP_NO_RESTART`, `AGY_MCP_PIP_ARGS`, `AGY_MCP_PYTHON`, `AGY_MCP_SRC`,
`AGY_MCP_STATE_DIR`, `AGY_MCP_UPGRADE`.
`install.sh:918-920` documents 9 (missing `ALLOW_INSIDE_DSH`, `CONFIG_PATH`,
`STATE_DIR`); `README.md:58-59` documents 6 (also missing `PYTHON`, `PIP_ARGS`,
`AGY_BINARY`).

### Positive check — the fake-agy README is current

`tests/fake_agy/README.md` documents 26 scenarios and all 26 names occur in
`tests/fake_agy/agy`; the `/usage` mode table's 7 names also all occur. This is
the only prose document checked that had no stale statement.

---

## 5. Bloat and repetition — the cleanup_jobs tail

### 5.1 SPEC.md

```
$ env -u PYTHONPATH .venv/bin/python - <<'EOF'
# split on '## 22.' / '## 23.'
...
EOF
SPEC sec22 lines 66 chars 5896
SPEC sec23 lines 71 chars 19410
SPEC total chars 103457
```

- §22 + §23 = **25,306 bytes (24.3% of SPEC)** and 137 lines for two optional
  helper CLIs. They are written at a deeper level of detail than the server
  contract itself: §23 has nine numbered eligibility rules with
  `R1`–`R18` sub-rules, a table of git metadata files, and quoted error
  strings.
- The same rules are restated in `D90`–`D100` and again in `CHANGELOG.md`.
  Sentence-level comparison of §23 against `DECISIONS.md` finds **11 identical
  long sentences (>60 chars)**; against `CHANGELOG.md`, 3. Example (identical
  in `SPEC.md:1016`, `DECISIONS.md:703`, `CHANGELOG.md:31-32`): "`packed-refs`
  files (if present) must be terminated by newlines, start with
  `# pack-refs with:`, and contain only comments, refs (`<sha> refs/...`), or
  peeled refs (`^<sha>`)."
- §8 (tool contracts) and §18 (config table) are reference material embedded
  in a normative document. They are correct today (signatures match
  `server.py:193-448`; config keys match except `timezone`), but every code
  change requires editing a 1,036-line file.

**What is essential:** the state machine (§7), tool signatures and envelope
(§8), preflight/worktree (§9), supervisor/commit/audit/verify (§10–§13),
parsing precedence (§15), recovery (§16), caps/usage (§17), and the threat
model (§5). **What is not:** the two cleaner manuals (§22–§23), the config
table (§18), the tool signatures (§8, duplicated in code and
`tools_schema.json`), and the findings summary (§21, duplicated in the probe
docs). A 40–50 KB contract plus three reference pages would carry the same
information.

### 5.2 DECISIONS.md

```
$ python - <<'EOF'  # sizes per '^### (D\d+:'
...
EOF
heading count 99
D88-D100 chars 37979 = 27.0% of 140802
largest: D80 4115; D92 4037; D95 3776; D62 3662; D90 3533
smallest: D42 236; D17 242; D47 252; D27 254
```

- 99 entries, no index, two `##` headings. A reader looking for "how are
  branches cleaned" must `grep D70`.
- Five of the top 15 largest entries are cleanup_jobs rounds (`D90`, `D92`,
  `D93`, `D95`, `D97`), joined by `D62` (batch 4) and `D80` (verify/commit).
- Each round entry repeats the previous round's rule text when it extends it,
  so the same invariant appears 2–4 times. The rules are stable and worth
  keeping; the per-round narrative ("R13", "R15", "the frozen test only passed
  incidentally") is experiment history and belongs in a findings page.

**What is essential per decision:** the ID, title, one-paragraph rationale, the
rule, and the tests. That is ~15–25 lines. D80/D92/D95 at 3.7–4.1 KB are 2–3×
that.

### 5.3 CHANGELOG.md

```
$ python - <<'EOF'  # sizes per '^## '
...
EOF
entries 17
cleanup entries chars 20209 (75.6% of 26726)
```

13 of 17 entries are cleanup_jobs/cleanup_fixtures rounds. Three entries
("safe fixture cleaner fixes", "safe job-record cleaner CLI", "cleanup_jobs
fail-closed pre-delete recheck") are not labelled "round N" but are the same
series. A user asking "what changed in this project" gets 20 KB about one
internal CLI before anything about resume, verify, usage or the installer.

**Release-note shape that would preserve the information:** one page per
version (here: one `unreleased` page plus one `cleanup-jobs` maintenance page)
with 5–10 user-facing bullets, each linking to the experiment page or D number
for detail.

---

## 6. Findings

Severity: **H** high, **M** medium, **L** low. Effort: S < ½ day, M ½–2 days,
L > 2 days. "Risk" is the risk of the proposed action, not of the defect.

| ID | Sev | Finding | Evidence | Proposed action | Risk | Effort |
|---|---|---|---|---|---|---|
| INV-01 | H | `SPEC.md` is a 104 KB, 1,036-line document mixing contract, reference, rationale, findings and two CLI manuals; §22–§23 are 24.3% of it | `wc`, §22/§23 char counts; §23 has R1–R18 | Split: `doc/design/spec.md` (contract), `doc/reference/{mcp-tools,config-keys,cli-cleanup-*}.md`, `doc/findings-and-experiments/`; keep a stable SPEC index | Readers and docstrings cite "SPEC §N"; keep section numbers in the new pages or add a map | L |
| INV-02 | H | `DECISIONS.md` is 141 KB, 99 entries, no index; D88–D100 are 27% | per-entry sizes | Split into 5 ID-range pages + topic index; move round narrative to experiment pages, keep ID/rule/tests | D-numbers are cited everywhere; IDs must not change | M |
| INV-03 | H | `CHANGELOG.md` is 76% cleanup_jobs fix rounds and is not release notes | entry sizes | Keep `CHANGELOG.md` as the mechanical log; add `doc/release-notes/` with one user-facing page per version/maintenance series | Requires deciding what counts as a "release" (no tags today) | S-M |
| INV-04 | M | The same cleanup rules appear in SPEC §23, D88–D100 and CHANGELOG; 11 identical long sentences between SPEC §23 and DECISIONS | sentence-set comparison; packed-refs example at `SPEC.md:1016`, `DECISIONS.md:703`, `CHANGELOG.md:31` | Single source of truth = SPEC/decisions; D and changelog link to it | None if links are used | S |
| INV-05 | M | README capability table duplicates SPEC §20 with a reworded 7×4 table | `difflib` output | One owner (README) and a link from SPEC, or generate both from a fixture | Low | S |
| INV-06 | M | `preamble.md` and SPEC §14 are byte-equal duplicates with no cross-check | byte comparison (2,397 bytes) | SPEC links to the packaged file; add a docs-freshness check | Low | S |
| INV-07 | M | Installer `--help` says `--no-deps`; code and README say the opposite | `install.sh:913` vs `:387-389`, `README.md:27-28` | Fix the two help lines | None | S |
| INV-08 | M | `DSH_SETUP.md` says "all 9 tools" and omits `agy_usage` | `DSH_SETUP.md:89-98` vs `server.py:449` | Regenerate the list or link `reference/mcp-tools.md` | None | S |
| INV-09 | M | `QUESTIONS.md` Q2 says never edit `info/exclude`; D62 and code do edit it | `QUESTIONS.md:17` vs `SPEC.md:402-405`, `worktree.py:274-304` | Mark Q2 "superseded by D62" or move to history | None | S |
| INV-10 | M | `CHANGELOG_SPEC.md` F04 repeats the same obsolete claim | `CHANGELOG_SPEC.md:12` | Mark historical; move to `doc/project/history.md` | None | S |
| INV-11 | M | 28 of 31 relative markdown links are broken on GitHub | link-resolution script output | Rewrite links relative to each page (or to repo root with `/blob/main/`), add a link check | Low; mechanical | S |
| INV-12 | M | D1 describes a Runner with `resume`/`cancel`; code has `kill` | `DECISIONS.md:14` vs `runner.py:175-231`, `SPEC.md:91` | Correct D1 with a dated note (do not rewrite history silently) | Low | S |
| INV-13 | M | D3 says 5 s polling and safety-net on four states; code is 1 s poll + 5 s settle and done-only safety net | `DECISIONS.md:23-24` vs `manager.py:132-133`, `watcher.py:848-857`, `SPEC.md:540` | Correct D3 with a note; SPEC is right | Low | S |
| INV-14 | M | `ARCHITECTURE.md` lock order contradicts SPEC and its own caveat; cited lines are wrong | `ARCHITECTURE.md:49-67` vs `SPEC.md:486-487`, `manager.py:1393/1509` | Rewrite the section from SPEC; drop line-number citations | Low | S |
| INV-15 | M | `AUDIT_ORGANIZATION.md` reports A3 as unfixed (it is fixed) and uses stale line counts | `AUDIT_ORGANIZATION.md:19,17` vs `manager.py:531`; `wc -l` | Add per-finding status and a header "as of commit X"; archive the stale counts | Low | S |
| INV-16 | H | `TEST_MATRIX.md` references 23 nonexistent tests, omits 80 of 106 modules, and claims "None" uncovered | extraction script; `TEST_MATRIX.md:388` | Regenerate from `pytest --collect-only` + SPEC anchors; never hand-maintain 257 rows | New generator can be wrong; review its first output | M |
| INV-17 | M | `CODEMAP.md` cites a nonexistent branch, stops at D94 and omits cleanup test files; `states.py:55` claims "exactly as SPEC §7" but has 16 of 20 rows | `CODEMAP.md:5,148,279-280`; `git rev-parse`; transition counts | Fix the three CODEMAP statements; change `states.py:55` to "state-level subset of SPEC §7" | Low | S |
| INV-18 | M | Capability inventory reports the `using` conversation-id bug as live; D81 fixed it | `agy-capability-inventory.md:29` vs `cli_log.py:191`, `util.py:242-252` | Add "status: fixed by D81" and a date to the report | Low | S |
| INV-19 | L | `D64` is missing and never referenced | heading scan | Note the gap in the decision index | None | S |
| INV-20 | L | README manual install points at `github.com/<user>/agy-mcp` (no remote exists) | `README.md:66`, `git remote -v` empty | Mark as template or remove until a remote exists | None | S |
| INV-21 | L | `ROADMAP.md` mixes done history with future work and stale branch names | `ROADMAP.md:7,113` | Split done history into `project/history.md`; keep future work only | None | S |
| INV-22 | M | Config table omits `timezone` (27 code keys, 26 documented) | key-diff script | Add it to the reference, generated from the dataclass | Low | S |
| INV-23 | L | Installer env vars documented 9 (help) / 6 (README) of 12 | `grep` of `AGY_MCP_*` | One `reference/environment.md` table | Low | S |
| INV-24 | L | One-off orchestrator logs are tracked as prose docs | `docs/orchestrator/PLAN.md` (job IDs, branch, gate commands) | Move to `doc/findings-and-experiments/evidence/` or delete after the report exists | Losing session metadata; keep if it is evidence | S |
| INV-25 | M | 4.2 MB / 527 files of raw evidence sit in `docs/`; 17 directories have no narrative | `du`, `find`, per-dir md count | Keep evidence, add one page per experiment; archive raw logs by Harsh's decision (Part 1 wave 4.1) | Large move; links and script paths | M |
| INV-26 | L | `SCAFFOLD_NOTES.md` (34 KB) is a build log with at least three stale statements (9 tools, 60 s wait, 13 transitions) | `SCAFFOLD_NOTES.md:13,27,265` vs `server.py:270,449`, `states.py` | Move to `doc/project/history.md`; do not keep at top level | None | S |

**Inventory counts: 26 findings — 4 high, 16 medium, 6 low.**

---

## 7. What is *not* wrong with the docs

To avoid a wrong fix:

- Module and API documentation is **good**, not missing: 24/24 modules have a
  docstring and a CODEMAP section; 86.6% of functions have docstrings
  (`metrics-src.json`, Part 1 E1). Do not delete CODEMAP prose.
- `tests/fake_agy/README.md` is accurate (26/26 scenarios).
- `README.md` install options match `installer/install.sh --help` except the
  one `--no-deps` phrase; the six CLI options are otherwise correct.
- `SPEC.md` §8 signatures match `server.py:193-448` parameter-for-parameter,
  including the newer `checklist` and `verify_timeout_seconds`.
- `SPEC.md` §18 defaults match `config.Config` for 26 of 27 keys; the only gap
  is the missing row, not a wrong value.
- The codemap generated block is fresh and test-enforced
  (`gen_codemap_index.py --check` → exit 0).

The problem is **organisation and maintenance**, not missing content: the
right facts are in the repository, spread over 758 KB of prose, 4.2 MB of
evidence, and at least three documents that contradict each other.

## Sources

- `doc/audits/2026-11-02-part2-index.md`
- `docs/`
- `doc/design/spec.md`
- `src/agy_mcp/`
- `tests/`
- `installer/install.sh`
