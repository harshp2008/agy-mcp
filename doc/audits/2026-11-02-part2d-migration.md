# Part 2d — Documentation conventions and migration plan

Status: current

**Repository:** `agy-mcp` @ `main` = `a7ed98aa502b1a5ed84551aff7aa1c705156dedf`
**Mode:** read-only. **Companions:** `part2a-inventory.md`,
`part2b-target-structure.md`, `part2c-coverage.md`, `part2-index.md`.

**Path shorthand:** a bare module name (`manager.py`) means
`src/agy_mcp/manager.py`; a bare document name (`DSH_SETUP.md`) means
`docs/DSH_SETUP.md`; `install.sh` means `installer/install.sh`.

---

## E. Documentation conventions (one page)

### E.1 Page template

Every page in `doc/` starts with a title that is a sentence, then:

```markdown
# <Title: what the page answers>

Status: current | historical (as of <date/commit>) | superseded by D<n>
Audience: users | orchestrators | contributors | maintainers

<1–3 sentence purpose.>

## <sections>

## Sources
- `src/agy_mcp/foo.py:12-40` — what this page took from it
- `tests/test_foo.py` — the contract it pins
```

Rules:

- **One concern per page.** If a page needs "and" in its title, it is two pages.
- **`## Sources` is mandatory.** It lists repository files, not external URLs
  (external links go in the body). A page with no Sources is a page whose
  freshness cannot be checked.
- **Status line is mandatory** when the page describes something that changed.
  A historical page keeps its content; a new page supersedes it.
- **Links are relative to the page**, e.g. from `doc/using/cli.md` to
  `doc/reference/exit-codes.md` use `../reference/exit-codes.md`. Never write a
  repo-root path in a link (that mistake broke 28 of 31 links, INV-11).
  Link to code as `` `src/agy_mcp/foo.py` `` (inline code, no link) unless the
  platform supports a stable line anchor.

### E.2 Naming

| Kind | Pattern | Example |
|---|---|---|
| Folder index | `README.md` | `doc/using/README.md` |
| How-to / concept page | `kebab-case.md` | `quick-start.md`, `files-and-paths.md` |
| Reference page | `kebab-case.md`, noun first | `config-keys.md`, `log-formats.md` |
| Experiment | `NN-slug.md`, two digits, never reused | `08-quota-calibration.md` |
| Experiment sub-record | heading `NN.M` inside the page | `## 12.3 Layout D` |
| Audit | `YYYY-MM-DD-topic.md` | `2026-11-02-documentation.md` |
| Audit with long appendices | file + same-named folder | `2026-11-02-documentation/part2a.md` |
| Decision | `D<n>` ID only, page per 20 IDs | `doc/design/decisions/d081-d100.md` |
| Release note | `<version>.md` or `<date>-<series>.md` while untagged | `unreleased.md`, `0.1.0-cleanup-jobs.md` |
| Release evidence | `evidence/` subfolder + `README.md` | `doc/audits/evidence/README.md` |

No `UPPER_SNAKE.md` in `doc/` (today `CODEMAP.md`, `DECISIONS.md`,
`TEST_MATRIX.md`, `DSH_SETUP.md` mix cases). Scripts and runtime assets keep
their existing names (`installer/agents_block.md`, `preamble.md`).

### E.3 Experiments

- Numbering is sequential and permanent. `NN` is never reused, even if the
  experiment is retracted; a retracted page says `Status: retracted` and the
  index keeps the row.
- A new run of the same question gets a new `NN` and links the old one; it does
  not edit the old result.
- Each page has: Question, Method (exact commands, build/sha, sample size),
  Result (numbers, quoted output), Conclusion, Not verified, Sources.
- Raw artifacts live under `evidence/NN-slug/` (or an external archive named in
  the page). The page is written, not generated from the logs.
- The index `README.md` is the only place that orders the pages; the
  `findings-summary.md` is the only place that states verdicts.

### E.4 Audits

- `doc/audits/YYYY-MM-DD-topic.md`; never rename or rewrite a published audit.
  A later audit supersedes it and the index says so.
- Header: `Revision`, `Mode` (read-only or fixing), `Scope`, `Method`.
- Every finding: `ID | severity | evidence | action | risk | effort`, where
  evidence is a `file:line` or a command + output.
- Evidence too large or sensitive for git stays outside; the page names the
  archive path.
- The index lists date, topic, scope, status (open/closed/superseded).

### E.5 Decisions

- One `D<n>` per design decision; IDs are stable forever. A wrong decision is
  corrected by a new `D` number or a dated `Correction (YYYY-MM-DD)` note under
  the original — never by silently editing the rule.
- Each entry: title, rationale (why, alternatives), rule (what to do), tests
  (paths), links (SPEC section, experiment page).
- `doc/design/decisions/README.md` has two indexes: by topic and by number.
  It also records the `D64` gap.
- A code change that alters a rule updates the rule once (in the decision) and
  links it; it does not copy it into SPEC, CHANGELOG and the experiment page.

### E.6 Who updates what when code changes

| Change | Must update, in the same commit |
|---|---|
| MCP tool signature/return | `doc/reference/mcp-tools.md`, `tests/fixtures/tools_schema.json`, `doc/design/spec.md` §8, `doc/design/decisions/` if policy |
| CLI flag | `doc/reference/cli-<tool>.md`, `doc/using/cli.md` or cleaner how-to, `--help` text |
| Config key | `Config` dataclass, `known_keys`, `doc/reference/config-keys.md` |
| Env var | `doc/reference/environment.md` |
| Error code | `doc/reference/errors.md` |
| State field/format | `doc/reference/state-layout.md` / `log-formats.md` |
| Module/function | `doc/project/code-layout.md` + `scripts/gen_codemap_index.py --write` |
| State machine row | `states.py`, `doc/design/state-machine.md`, SPEC §7 |
| Test suite shape | `doc/project/test-matrix.md` (generated), `doc/project/test-plan.md` |
| Behaviour change | `CHANGELOG.md` + `doc/release-notes/` |
| New investigation | a new `doc/findings-and-experiments/NN-*.md` + index row |
| Audit | a new `doc/audits/YYYY-MM-DD-*.md` + index row |

The rule of thumb: **the decision/experiment page owns the "why", the
reference owns the "what", the release note owns the "when".** SPEC owns the
normative "must".

### E.7 Freshness check

One script, `scripts/check_docs.py`, run in the gate alongside
`gen_codemap_index.py --check`. It should fail on any of:

1. A page without a `## Sources` section (excluding `README.md` indexes).
2. A `Sources` path that does not exist.
3. A relative link that does not resolve.
4. A config key in `Config` missing from `doc/reference/config-keys.md`, or
   vice versa.
5. A tool name in `tests/fixtures/tools_schema.json` missing from
   `doc/reference/mcp-tools.md`.
6. An error code literal in `src/agy_mcp/*.py` missing from
   `doc/reference/errors.md`.
7. An `argparse` flag in `server.py` / `cleanup_*.py` missing from the matching
   CLI reference.
8. An `os.environ` / `os.getenv` name in `src/agy_mcp/*.py` missing from
   `doc/reference/environment.md`.
9. A `D<n>` cited anywhere that has no heading in
   `doc/design/decisions/`.
10. A module under `src/agy_mcp/*.py` not named in `doc/project/code-layout.md`
    (the existing `tests/test_codemap_fresh.py` already does this for
    `doc/project/code-layout.md`).
11. A `doc/release-notes/` index row missing for the latest `CHANGELOG.md`
    date/version.

Scripts that already read docs today and can be extended instead of replaced:

- `tests/test_codemap_fresh.py` — 4 tests: file exists, every module is
  mentioned, generated block is fresh, path is `doc/project/code-layout.md`.
- `tests/test_b4_preamble.py` — asserts rule strings inside the packaged
  `preamble.md` (does not compare it to SPEC).
- `tests/test_installer_chapter.py` — asserts the consumer guidance text
  (including `docs/orchestrator/PLAN.md`, which is a *user project* path, not
  this repository's).
- `scripts/gen_codemap_index.py --check` — the only mechanical doc gate today
  (`CODEMAP.md generated section is fresh`, exit 0).

There is **no CI** (`.github` absent, Part 1 G4), so `check_docs.py` must be
listed in `AGENTS.md` and `doc/developing/gates.md`; adding it to a
pre-commit hook is optional until a CI exists.

---

## F. Migration plan

### F.1 What reads the documents today

Every test, script and config that touches a doc path (verified with `grep -rn`
over `tests/`, `scripts/`, `installer/`, `pyproject.toml`, `AGENTS.md`):

| Consumer | What it reads | Breaks if… | Action |
|---|---|---|---|
| `tests/test_codemap_fresh.py:12,50` | imports `CODEMAP_PATH`; asserts `REPO_ROOT/"docs"/"CODEMAP.md"` | CODEMAP moves | Change the constant in `gen_codemap_index.py` and the assertion in the same commit; 4 tests must pass |
| `scripts/gen_codemap_index.py:27` | `ROOT/"docs"/"CODEMAP.md"` | CODEMAP moves | Change one line; then `--write` |
| `AGENTS.md:32,40,50,93,110` | commands and paths | any move it names | Edit in the same commit |
| `tests/test_installer_chapter.py:615` | asserts `docs/orchestrator/PLAN.md` appears in the **consumer** guidance | **must not break** | Do **not** rename this string: it is the path in the user's project, not this repo |
| `installer/agents_block.md`, `installer/install.sh:421-452` | same consumer guidance text | same | Do not change for this migration |
| `tests/test_b4_preamble.py` | packaged `preamble.md` via `load_preamble()` | package data moves | Unaffected; add a separate SPEC-copy check if §14 is split |
| `scripts/real_smoke.py:29` | `ROOT/"docs"/"reports"/"real-smoke-1"` | reports move | Parameterise `REPORT_ROOT` or leave reports in place |
| `scripts/smoke_real.py:32` | `ROOT/"docs"/"reports"/"smoke"` | reports move | Same |
| `scripts/smoke_fixes_real.py:30` | `ROOT/"docs"/"reports"/"smoke-fixes"` | reports move | Same |
| `scripts/run_mutations.py:7` | `docs/reports/smoke-fixes-2/mutations.txt` | reports move | Same |
| `scripts/watch_orphans.py:36` | writes `docs/reports/smoke-fixes-2/orphans.txt` | reports move | Same |
| `tests/test_quota_calibration.py:4,37` | comment cites `docs/reports/quota-calibration-data.md`; constants copied | comment only | Update the comment if the file moves; constants stay |
| `tests/test_smoke_fix2_model_resolved.py:5,22,27` | comments cite real-smoke-1 | comment only | Update comments |
| `tests/conftest.py:99`, `tests/test_state_isolation_guard.py:3` | comments cite stale-jobs-report | comment only | Update comments |
| `tests/test_gitops.py:42` | `is_sensitive_path("docs/secrets.md")` is a string, not a file read | never | None |
| `pyproject.toml:8-9` | `readme = "README.md"` | README moves | Keep `README.md` at root |
| `.github/workflows/*` | — | — | **No CI exists** |
| `docs/reports/docs-codemap/ast-baseline.json` | `check_no_behavior_change.py --compare` target (via AGENTS command) | reports move | Update the AGENTS command, not the script (it takes a path) |

`grep -rn "SPEC.md\|CHANGELOG.md\|docs/" tests scripts installer pyproject.toml AGENTS.md`
returns 49 lines (38 excluding comment lines); the real path reads are the ten
rows in the table above (the rest are comments, string fixtures, or the
consumer guidance). No test reads `doc/design/spec.md`, `CHANGELOG.md`,
`DECISIONS.md`, or any `docs/*.md` except CODEMAP.

### F.2 Ordered steps

Each step is one commit (or a small series) with the gate at the end. No step
changes behaviour. "Gate" = `env -u PYTHONPATH .venv/bin/python -m pytest -q`,
`.venv/bin/ruff check src tests scripts`, `gen_codemap_index.py --check`,
`check_no_behavior_change.py --compare` (for any commit that touches `src/`).

**Phase 0 — decisions (Harsh; 0.5 day).**
Decide F.4 items 1–6. Nothing moves until these are answered.

**Phase 1 — skeleton, no moves (0.5 day).**
1. Create `doc/` with the tree in `part2b` §C.2, empty pages replaced by
   `README.md` indexes that say "moved from X (not yet)".
2. Add `doc/developing/writing-docs.md` (this Part 2d §E), `doc/developing/gates.md`.
3. Add `scripts/check_docs.py` in report-only mode (`--report`) and an
   `AGENTS.md` line.
4. Gate: all existing tests; `check_docs.py --report` prints the expected 28
   broken links and 0 source failures.

**Phase 2 — move the low-risk prose (1.5 days).**
1. `git mv docs/ARCHITECTURE.md doc/design/architecture.md` (fix lock order in
   the same commit).
2. `git mv docs/DESIGN_MODES.md doc/design/modes.md`.
3. Split `docs/ORCHESTRATION.md`: §1–§4 → `doc/project/orchestrator-workflow.md`;
   §5 → `doc/findings-and-experiments/findings-summary.md`; §6 →
   `doc/findings-and-experiments/05-real-dsh-runs.md` (written in Phase 3).
   Until Phase 3, keep a stub at the old path.
4. `docs/ROADMAP.md` → `doc/project/roadmap.md` + `doc/project/history.md`;
   `docs/QUESTIONS.md` + `docs/CHANGELOG_SPEC.md` + `docs/SCAFFOLD_NOTES.md` →
   `doc/project/history.md` (one page, sections, each with a status line).
5. `docs/DSH_SETUP.md` → merge into `doc/using/install.md` +
   `doc/reference/mcp-tools.md` (fix the 9-tool list).
6. `docs/orchestrator/PLAN.md` and `agy-brief-test-isolation.md` →
   `doc/findings-and-experiments/evidence/test-isolation-2026-10/` (or delete;
   Phase 0 decision 5).
7. `tests/fake_agy/README.md` → `doc/project/fake-agy.md`; leave a 5-line
   pointer at the old path.
8. Gate: `grep -rn "docs/ARCHITECTURE\|docs/DSH_SETUP\|docs/SCAFFOLD" .` → only
   the history page and the mapping table; tests green.

**Phase 3 — experiments (3–4 days).**
1. Write `doc/findings-and-experiments/README.md`,
   `findings-summary.md`, and pages `01`–`15` per `part2b` §B.4.
2. Link raw evidence (`docs/reports/...`) rather than copying it.
3. Copy the sanitised `FINDINGS.md` + repro scripts from
   `~/agy-review/{opus,gemini2,gemini3,gemini4,ds}` into
   `doc/audits/evidence/2026-11-cleanup-jobs/` (Phase 0 decision 4).
4. Add the status line to each old report file ("superseded by D81", etc.) or
   delete it once the new page exists (Phase 0 decision 2).
5. Gate: every experiment page has `## Sources`; `check_docs.py` link check
   passes for `doc/`.

**Phase 4 — audits (1 day).**
1. Create `doc/audits/README.md` and the five pages in `part2b` §C.4.
2. `git mv docs/DS_REVIEW_1.md doc/audits/2026-10-24-spec-review-1.md`, etc.
3. Add `## Status` to `AUDIT_ORGANIZATION` (A3 fixed by D61, A4 resolved by
   D62) and a revision line to each.
4. Copy `~/agy-review/audit/part1*` and `part2*` into
   `doc/audits/2026-11-02-{code-structure,documentation}/` with the index page
   linking the parts; sanitise `~/...` paths per AGENTS rule 6.
5. Gate: `doc/audits` index lists every file; no broken links.

**Phase 5 — reference pages (3–4 days, ~2 with generators).**
1. Add `scripts/gen_reference.py` with markers (`<!-- BEGIN GENERATED -->`) in
   `config-keys.md`, `mcp-tools.md`, `errors.md`, `environment.md`,
   `exit-codes.md`, `state-layout.md`, `log-formats.md`, `manifests.md`,
   `cli-*.md`.
2. Generate the mechanical rows from `Config` + `known_keys`,
   `tests/fixtures/tools_schema.json`, `code="..."` literals, `os.environ`,
   `argparse` actions, `Job` fields. Hand-write the "meaning/next action"
   column.
3. Move SPEC §8, §14, §18, §22, §23 into the reference pages; replace them in
   `doc/design/spec.md` with links (keep §N headings as anchors).
4. Gate: `check_docs.py` (all 11 checks); `gen_codemap_index.py --check`;
   tests green.

**Phase 6 — SPEC/decisions/release notes (3–5 days; needs Phase 0).**
1. `git mv SPEC.md doc/design/spec.md` and add a 15-line root stub with the
   section map; update every `SPEC section N` prose reference to
   `doc/design/spec.md#n-...` only where it is a link (code docstrings keep
   "SPEC section N" and the stub explains the mapping).
2. Split `doc/design/decisions.md` into 5 ID ranges + index; keep IDs and text,
   trim the round narrative to a link.
3. Create `doc/release-notes/unreleased.md`,
   `doc/release-notes/0.1.0-cleanup-jobs.md`; keep `CHANGELOG.md` at root as
   the raw log and state the relationship in `doc/README.md`.
4. Gate: `check_docs.py` D-number check; every D cited resolves.

**Phase 7 — code layout and archive (0.5–1 day, needs Phase 0).**
1. `git mv docs/CODEMAP.md doc/project/code-layout.md`; change
   `gen_codemap_index.py:27` and
   `tests/test_codemap_fresh.py:18-50`; update `AGENTS.md`; run `--write`;
   gate the 4 codemap tests.
2. `docs/reports/` → archive branch, `docs/archive/`, or keep (decision 2).
   If kept, add `docs/reports/README.md` explaining it is raw evidence and not
   current documentation.
3. Delete the root stubs once no test or docstring path depends on them
   (decision 1).

### F.3 What can be automated

| Task | Automatable? | Tool |
|---|---|---|
| Move files | yes | `git mv` per commit (script the list, review each) |
| Rewrite relative links | yes | one-off script mapping old→new paths; then `check_docs.py` |
| Config-key table | yes | AST of `Config` + `known_keys` + validator branches |
| MCP tool table | yes | `tools_schema.json` + `make_error_envelope` call sites |
| Error-code table | 80% | `grep code="..."` + source file; hand-write meaning/action |
| Env-var table | yes | `grep os.environ` + installer `AGENT_MCP_*` reads |
| Exit-code table | 80% | `sys.exit`/`--help` sources; hand-write semantics |
| Job/log field tables | 80% | AST of `Job`, `AuditEvent`, `TokenUsage`; hand-write sensitivity |
| Test matrix | yes | `pytest --collect-only -q` + SPEC section tags in test docstrings |
| Codemap module block | already automated | `scripts/gen_codemap_index.py` |
| Experiment pages | **no** | must be written from the logs by a human/agent and reviewed |
| Audit pages | **no** | must be written |
| Release notes | 60% | group CHANGELOG entries by decision; hand-write user-facing text |

### F.4 Decisions needed from Harsh

1. **Root stubs or full move** for `doc/design/spec.md`, `DECISIONS.md`, `CODEMAP.md`?
   Recommendation: keep `CHANGELOG.md` and `README.md` at root (packaging and
   convention), move the other three with stubs for one release.
2. **`docs/reports/` (527 files, 4.2 MB, 73% of tracked files):** keep in the
   tree, move to `docs/archive/`, or move to an archive branch? Recommendation:
   keep in the tree until the experiment pages exist, then archive the 17
   reportless rounds and keep only the evidence the pages cite.
3. **Codemap gate change:** may `scripts/gen_codemap_index.py:27` and
   `tests/test_codemap_fresh.py:50` change in the same commit as the move?
   (Not a frozen test; listed here because it is the only mechanical doc gate.)
4. **Committing `~/agy-review` evidence:** may the sanitised
   `FINDINGS.md` + repro scripts (not the 16 MB fixtures) be committed under
   `doc/audits/evidence/`? Personal paths and a mode-000 fixture must be
   removed first.
5. **One-off logs:** delete or archive
   `docs/orchestrator/{PLAN,agy-brief-test-isolation}.md`?
6. **Corrections to historical decisions:** append a dated `Correction` note
   (recommended) or edit the rule text in place? The audit assumes append-only.

### F.5 Effort estimate

| Phase | Work | Person-days | Can be shortened by |
|---|---|---:|---|
| 0 | decisions | 0.5 | — |
| 1 | skeleton + conventions + report-only checker | 0.5 | — |
| 2 | move low-risk prose, split ORCHESTRATION, merge history | 1.5 | agent doing `git mv` + link rewrite |
| 3 | 14 experiment pages + index + summary + evidence copies | 3–4 | each page is 1–3 hours; the differential/adversarial pages are the long ones |
| 4 | audits folder, 5 pages, sanitise Part 1/2 | 1 | Part 1 already written |
| 5 | 10 reference pages + 1 generator | 3–4 (2 with generator) | generator first, then hand-write meaning columns |
| 6 | SPEC/decisions split + release notes | 3–5 | keep SPEC as one page and only split §8/§14/§18/§22/§23 → 2–3 days |
| 7 | codemap move + reports archive | 0.5–1 | — |
| **Total** | | **13–18** | **6–9 focused days with an agent and the generators** |

The highest-value 2 days are Phase 5 (reference pages): they close 12 of the
32 `N` items and every error/env/config gap.

### F.6 Where this audit's own reports go

- `doc/audits/2026-11-02-documentation.md` — the index/summary
  (`part2-index.md` content), with the finding table and counts.
- `doc/audits/2026-11-02-documentation/part2a-inventory.md` … `part2d-migration.md`
  — the four detail files, linked from the index. This is the documented
  exception to `YYYY-MM-DD-topic.md` for long audits (`part2b` §C.4).
- `doc/audits/evidence/2026-11-02-documentation/` — the raw command outputs
  quoted here (link check, key diff, error-code scan). Keep them text-only;
  no state-store data.
- `~/agy-review/audit/` remains the working copy; it is not the published one.
  The published pages must drop `~` paths and any masked-account
  fragments (AGENTS rule 6).

---

## G. Findings for this part

| ID | Sev | Finding | Evidence | Proposed action | Risk | Effort |
|---|---|---|---|---|---|---|
| CONV-01 | M | No page template, no `## Sources` rule, no current/historical marker; stale line refs are the symptom | `ARCHITECTURE.md:62` cites moved lines; `CODEMAP.md:5` cites a dead branch | Adopt §E.1 and apply it during the moves | Low | S |
| CONV-02 | M | The only mechanical doc gate is the CODEMAP block; links, keys, tools, errors, env are unchecked | `tests/test_codemap_fresh.py` is the only doc test; `check_docs.py` does not exist | Add `scripts/check_docs.py` with the 11 checks in §E.7; list it in `AGENTS.md` | A noisy check gets ignored; start report-only | M |
| CONV-03 | L | `AGENTS.md` says where SPEC/CODEMAP/DECISIONS go, but not where a new finding or audit goes | `AGENTS.md:93-98` | Add one rule and a link to `doc/developing/` | Low | S |
| MIG-01 | H | The CODEMAP path is pinned in three places; a move without all three breaks 4 tests and the freshness gate | `gen_codemap_index.py:27`; `test_codemap_fresh.py:18-50`; `AGENTS.md:32,40,50` | Move in one commit with the path change, `--write`, and `AGENTS.md`; run the 4 tests | Silently stale generated block if `--write` is skipped | S |
| MIG-02 | M | Five scripts write into `docs/reports/<fixed>/`; moving reports breaks them | `real_smoke.py:29`; `smoke_real.py:32`; `smoke_fixes_real.py:30`; `run_mutations.py:7`; `watch_orphans.py:36` | Parameterise `REPORT_ROOT` or leave `docs/reports/` in place | Low if reports stay | S |
| MIG-03 | M | No CI, so no migration step is automatically enforced | `.github` absent (Part 1 G4) | Add the docs check to `AGENTS.md`/`doc/developing/gates.md`; a CI is a separate decision | Manual gates are skipped under time pressure | S |
| MIG-04 | M | 28 broken links will multiply if pages move before a link rewrite | link scan (INV-11) | Rewrite links with a mapping script in the same commit as each move | Mechanical; verify with `check_docs.py` | S |
| MIG-05 | L | Published audit pages need path/account sanitisation and must not include mode-000 fixtures | `du ~/agy-review` 16 MB; 6 permission-denied subtrees; AGENTS rule 6 (mask emails) | Curate evidence; keep large fixtures outside git | Medium: a missed personal path is a privacy leak | S |
| MIG-06 | L | `tests/test_installer_chapter.py:615` and `installer/agents_block.md` name `docs/orchestrator/PLAN.md` as a **user-project** path; a blanket rename would be wrong | `test_installer_chapter.py:615`, `installer/install.sh:421-452` | Exclude these from the rename; document why in `doc/developing/writing-docs.md` | A blanket `sed docs/→doc/` would break the consumer contract | S |

**Part 2d counts: 9 findings — 1 high, 5 medium, 3 low.**

## Sources

- `doc/audits/2026-11-02-part2-index.md`
- `src/agy_mcp/`
- `tests/`
- `scripts/`
- `AGENTS.md`
- `installer/install.sh`
