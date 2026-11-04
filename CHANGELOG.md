# Changelog

## Unreleased

- **Change: the implicit `default` account is optional and verifiable.** A
  2026-11-05 human observation showed the default using the shared desktop
  keyring; after the Antigravity chat app switched accounts, plain
  `agy -p "/usage"` returned `account-b`'s exact quota and the scheduler counted
  one pool as two. The new optional `[default_account]` table has `enabled`
  (default `true`) and at most one of `expected_email`/`expected_email_sha256`.
  With `enabled = false` the scheduler never picks default, an explicit
  `account="default"` is `default_account_disabled`, a job pinned to default
  fails resume with that code instead of moving, and no enabled account is
  `account_none_enabled`. An expectation verifies the default exactly like an
  `[[accounts]]` entry. With no table and no `[[accounts]]` behavior is unchanged
  (D116; finding 16).
- **Additive: `agy_usage` takes an optional `account` parameter.** No argument
  keeps the historical default reading; with the default disabled it reads the
  first enabled account by name and reports it in the new `account_name` key. A
  named disabled account is refused, never silently replaced. The non-frozen
  `tests/fixtures/tools_schema.json` and wrapper snapshot were updated
  deliberately.
- **Fix: one verified login is one quota pool (D116).** Every registry-scoped
  `/usage` probe now carries a non-reversible SHA-256 digest of the observed
  login. Enabled accounts with the same digest are merged under one pool key when
  at least one verified an identity expectation, so a duplicate identity cannot
  be scheduled as two independent quota buckets. The doctor reports
  `shared_identity` for any duplicate login and `shared_keyring` (advisory, never
  a failure) when the default is enabled without an expectation and other
  accounts exist.
- **Docs: "Recommended setup: all accounts isolated".** The user recipe now
  explains how to log the main account into its own HOME, copy the permission
  policy, declare it, and disable the shared-keyring default, so agy-mcp never
  depends on the desktop keyring.
- **Fix: a full quota window no longer counts as expiring quota.** Real `/usage`
  reports an untouched window at `100%` remaining with a rolling placeholder
  reset (for example, both five-hour rows on an untouched account show the same
  reset five hours out). The scheduler scored `weekly_remaining / hours_to_reset`,
  so that placeholder could outrank quota that was genuinely about to reset. A
  window at or within `account_full_window_margin_pct` (default `0.1`) of 100%
  now contributes zero urgency and, because its wait is zero, can never be the
  binding window for a background burn (D115).
- **Additive: `account_selected` now records a decision trace.** For every
  registered account considered it records name, eligibility, a rejection code,
  and for eligible accounts the pool, effective weekly/five-hour remaining,
  hours to the binding reset, urgency score and rank, plus the winner and the
  rule that decided it (`urgency`, `expires_at`, `load`, `lru`,
  `only_candidate`, `explicit_account`, `force`). Only masked emails are
  included and the list is capped at 32. With no `[[accounts]]` registry the
  audit event is unchanged (in practice it is absent), so single-account output
  is byte-identical (D115).
- **Fix: `accounts.identity.mask_email` now emits `head***tail@domain`.** The
  helper kept the intended reveal counts (at most the first three and last two
  local-part characters, never more than half) but printed the revealed head
  and tail in the wrong order, so the local part `jordan.lee81` became
  `jor81***` instead of `jor***81`. It now renders `jor***81`; short local
  parts and the fully-masked form are unchanged, and the legacy
  `usage.mask_email` (first two) is untouched.
- **Fix: every non-default account's `/usage` probe now uses the isolated child
  env.** The scheduler's probe ran without the dead D-Bus override, so agy
  authenticated through the desktop keyring as the primary login and a
  second-account `agy_start` failed closed with `account_identity_mismatch`
  (`human test 2026-11-03`). The at-launch reading, the preflight quota guard and
  the supervisor's final quota/cost reading had the same flaw; all now use
  `accounts/env.py:build_account_env` through `usage.get_usage(account=...)`.
  The default account's env is unchanged. An AST guard rejects a future spawn
  that bypasses the builder. Audit:
  `doc/audits/2026-11-03-multi-account/05-usage-env-bug.md`.
- **Multi-account support.** `[[accounts]]` in `config.toml` adds Google accounts
  the user legitimately owns; each runs with its own `HOME` and a dead D-Bus so
  it cannot reach the desktop keyring. Jobs are pinned to an account, scheduled
  by remaining quota across both pools/windows (including a background
  burn-before-expiry mode), and a quota-paused job can move to another account by
  copying its conversation db under a lock, or fall back to a factual summary
  handoff. `agy_start` gains `account`/`priority`/`force`; `agy_resume` gains
  `allow_handoff`; `--doctor` gains read-only account checks. With no
  `[[accounts]]` configuration behavior is unchanged (D102–D114).
- Added pytest-xdist to the dev extras for local parallel runs; CI stays serial.
- Minimum Python is now 3.12 (3.10 is end-of-life).

## 2026-11-02 — Platform-support audit (read-only)

- **`doc/developing/platform-support.md` (new):** inventoried every
  POSIX/Linux-only dependency in `src/`, `scripts/`, `installer/` and `tests/`
  (`fcntl`, process groups and `SIGKILL`/`SIGSTOP`, `/proc`,
  `dir_fd`/`O_NOFOLLOW`/`O_DIRECTORY`/`fchmod`, `0600`/`0700` modes, symlink and
  `.git` assumptions, hard-coded POSIX paths, bash-isms and GNU commands, the
  fake `agy`, and the XDG/`~/.local/bin` assumptions), with a
  works / needs-change / blocked verdict per platform. Only Linux was executed;
  macOS and Windows verdicts were read from the code. No source file changed.
  The README now links the page and keeps the claim at "Linux (tested), macOS
  (untested), Windows via WSL (untested)".

## 2026-11-02 — Packaging prep: publish metadata, public docs and publish-hygiene gate

- **Packaging metadata and artifacts (`pyproject.toml`):** declared Apache-2.0
  as a PEP 639 license expression with `license-files = ["LICENSE"]`, a
  name-only author, keywords, project URLs and a Linux-only classifier. No
  version or dependency change. The sdist now includes only `src/`, `tests/`,
  `doc/`, `scripts/`, `installer/` and the root metadata files, so the 4.4 MB
  historical `docs/` archive is no longer shipped.
- **Public documentation:** rewrote `README.md` for a reader who has never seen
  the project (requirements, verified installer flags, the generated MCP client
  snippet, quick start, safety boundaries, pre-release status, license), and
  added `SECURITY.md` (private GitHub advisories only, no email) and
  `CONTRIBUTING.md`.
- **Publish-hygiene gate (new):** `scripts/check_publishable.py` scans every
  `git ls-files` text file and fails on non-allowlisted email domains, absolute
  home paths, `.ts.net` hostnames and credential-shaped strings. Reviewed
  exceptions live in `scripts/publishable-allowlist.txt` with a justification
  per line; the checker source hardcodes only public placeholders.
  `tests/tooling/test_check_publishable.py` covers the pass/fail cases, the
  gate runs as a CI step, and `doc/developing/gates.md` documents it.

## 2026-11-02 — Wave 2a fixups: cleanup structural guard and Python-independent move proof

- **Tests-only and docs-only; no `src/agy_mcp/` change.**
  - **Package-wide structural guard (`tests/test_cleanup_modules_structural_guard.py`):** The frozen guard only inspected `cleanup_jobs.py`, which is now a handler-free shim, so the rule had gone vacuous. The new test applies the same AST rule to every module in `src/agy_mcp/cleanup/` plus both shims, asserts at least 65 `except` clauses were scanned (69 today), and proves the rule flags an injected `except Exception: pass` in a temporary copy. Three pre-existing handlers from `main`'s `cleanup_fixtures.py` are recorded as known debt keyed by file, enclosing function and handler text; any new offender fails, and a stale entry fails too.
  - **Python-version-independent move proof (`scripts/check_moved_code.py`, `tests/tooling/test_moved_code.py`):** Replaced the stored CPython 3.12 `ast.dump` fixture (`tests/fixtures/old_cleanup_ast.json`, deleted) with the old source text saved from `main` as `tests/fixtures/old_cleanup_jobs.py.txt` and `old_cleanup_fixtures.py.txt`. Both old and new code are now parsed and dumped at run time with the running interpreter, so the gate and test pass on 3.10, 3.11 and 3.12. `--from-git` reads the old files from `main` for a manual cross-check.
  - **Docs:** `doc/developing/gates.md` documents both gates and lists the three known-debt handlers.

## 2026-11-02 — Docs migration phase 2c: audits index, evidence curation, and experiment 12

- **Fifth phase of the Part 2 documentation migration; docs-only, no `src/agy_mcp/` behavior change.**
  - **Adversarial review evidence curated (`doc/audits/evidence/2026-11-cleanup-jobs/`):** Committed 57 sanitized review reports and reproduction scripts from the five-reviewer adversarial review of `cleanup_jobs` (`opus/`, `gemini2/`, `gemini3/`, `gemini4/`, `ds/`). Excluded 16 MB of transient fixture workspaces and unreadable `mode-000` directories per MIG-05. Added `README.md` cataloguing every evidence artifact against its finding, decision, and frozen test.
  - **Experiment 12 authored (`12-cleanup-jobs-differential.md`):** Documented the pure Python git metadata differential test oracle comparing cleaner decisions against real `git` ground truth across 390 layout mutations and 18 `job.json` mutations, detailing the 11 adversarial hardening rounds (D89–D100) and the final 13 frozen cleaner test suites.
  - **Evidence indexes established:** Authored `doc/findings-and-experiments/evidence/README.md` indexing raw evidence, test isolation briefs, and pointers to audits evidence. Updated `doc/findings-and-experiments/README.md` marking Experiment 12 and evidence index as current.
  - **Audits index and sanitization (`doc/audits/README.md`):** Created the central audits index cataloguing all 10 Part 1 and Part 2 audit pages, documenting audit naming rules (`YYYY-MM-DD-topic.md`), evidence storage, and audit conventions. Normalized user home paths (`~/` -> `~/`) and personal accounts in published audit pages. Ensured all audit pages carry explicit `Status:` lines and `## Sources` footers.

## 2026-11-02 — Docs migration phase 2b-2: findings and experiments (pages 08–11, 13–14)

- **Fourth phase of the Part 2 documentation migration; docs-only, no `src/agy_mcp/` behavior change.**
  - **Experiment 07 evidence fix (`07-real-jobs-1.md`):** Added explicit evidence rows citing full-precision `duration_seconds` from `docs/reports/real-smoke-1/REPORT.md:300-301` (rounded to 2 decimals on page) and wall-clock durations from each job's `summary.json`.
  - **Experiment 08 authored (`08-quota-calibration.md`):** Documented empirical calibration of `compute_quota_estimate` across 7 qualifying stored jobs (n=7), candidate model fits, rejected models (least squares origin/intercept), chosen conservative upper envelope (0.011 5h / 0.0031 weekly, base 0.0), and frozen test constants per D85.
  - **Experiment 09 authored (`09-test-store-contamination.md`):** Documented the 2026-10-29 live state store contamination diagnosis (332 records, 312 fixtures, 40 leaked queued records jamming concurrency), safe cancellation via `JobManager.cancel()`, conftest bootstrap redirect, path resolution guard, `dir_fd` `/proc/self/fd` wrappers, and the safe fixture cleaner `cleanup_fixtures.py` (D86, D87, D88).
  - **Experiment 10 authored (`10-needs-decision-investigation.md`):** Documented analysis of 339 stored jobs (27 in-scope non-fixture jobs), classification of the sole observed `needs_decision` stop (job `9bdf04c7`, legitimate frozen-test contradiction corroborated by subsequent fix commit `f341b16`), and evaluation of potential failure mechanisms.
  - **Experiment 11 authored (`11-flaky-f1-resume-race.md`):** Documented investigation of the intermittent F1 resume failure (5/30 failures), root cause supervisor hand-off race (~4 ms overwrite), thread join fix (`_wait_for_prior_supervisor`) per D65, SIGKILL readiness file poll hardening in runner, and discard of invalid `COMMIT_MSG` files per D66.
  - **Experiment 13 authored (`13-installer-and-gate-fixes.md`):** Consolidated four hardening investigations: F22 terminal state usage recording race and ordering fix (D68, D76, D77), orchestrator workflow selection block size budgeting (D79), verify-loop execution context and interpreter PATH resolution (D52, D53), and wrapper-owned commit subject sanitization with repo hook execution and per-job verify timeouts (D80).
  - **Experiment 14 authored (`14-fix-round-evidence-2026-10.md`):** Created a grouped index of the thirteen reportless October 2026 fix rounds (`fix-batch1`, `fix-batch2`, `fix-batch2b`, `fix-batch3a`, `fix-batch3b`, `fix-batch4`, `agymcp-fixes-2`, `hardening`, `installer-agymcp`, `server`, `smoke-fixes`, `stale-commit-msg`, `verify-fixes`), linking raw failure logs, passing suite results, and mutation checks.
  - **Indexes updated:** `doc/findings-and-experiments/README.md` and `doc/README.md` updated with new experiment entries and planned rows for 12 and evidence README.

## 2026-11-02 — Docs migration phase 2b-1: findings and experiments (pages 01–07)

- **Third phase of the Part 2 documentation migration; no `src/agy_mcp/` behavior change.**
  - **`doc/findings-and-experiments/` created:** Added `README.md` index with permanent two-digit numbering (`NN-topic.md`), one-line results, planned phase 2b-2 table, and full authoring guide.
  - **Findings summary consolidated:** Created `findings-summary.md` summarizing settled conclusions from empirical probes, linking corresponding design decisions (D1–D83), and moving the UNSURE register from `docs/ORCHESTRATION.md` section 5 along with runtime open items.
  - **Experiment pages 01–07 migrated:**
    - `01-agy-probe-1-cli-behavior.md`: Q1–Q12 from `docs/AGY_FINDINGS.md` (allow-rules, accept-edits, resume, stream-json, failure shapes, print-timeout, instruction files, detachment, model/effort, parallel execution, storage, sandbox fallback).
    - `02-agy-probe-2-security.md`: S1–S9 from `docs/AGY_FINDINGS_2.md` (compound command AST segmentation, `/tmp` scratch allowance, parent env leakage, safe command whitelist, secondary tools, headless question skipping, timeout recovery, settings scope, same-repo concurrency).
    - `03-agy-probe-3-escapes.md`: T1–T7 from `docs/AGY_FINDINGS_3.md` (unconfined reads via safe commands, echo redirection, `.git/config` modification, destructive `git branch` risks, test runner code execution, workspace hooks, project permissions).
    - `04-smoke-real-agy-1.md`: S1–S7 from `docs/SMOKE_FINDINGS.md` and `docs/reports/smoke/` (happy path, nested directories, denial halting behavior, `NEEDS_DECISION` parsing, resume via `--conversation`, settings hash integrity, protocol health).
    - `05-real-dsh-runs.md`: Owner-verified runs from `docs/ORCHESTRATION.md` sections 5–6 (`dsh` stdio transport, policy `never` with `danger-full-access`, bugs A1–A3 fixed via D70–D72, orchestrator rules D73).
    - `06-agy-capability-inventory.md`: CLI surface of `agy` v1.4.10 from `docs/reports/agy-capability-inventory.md` (25 flags, 12 subcommands, runner argv contract, banned flags, regex capture flaw superseded by D81).
    - `07-real-jobs-1.md`: Two real autonomous multi-commit jobs from `docs/reports/real-smoke-1/` (2 commits per job, `.git/info/exclude` isolation, automated verification pass, quota accounting corroboration, wall-clock timing D83).
  - **Pointer updates:** `docs/ORCHESTRATION.md` sections 5 and 6 updated to redirect to `findings-summary.md` and `05-real-dsh-runs.md`.
  - **Documentation map updated:** `doc/README.md` updated to reflect `findings-and-experiments/` as an existing section.

## 2026-11-02 — Docs migration phase 2a: move core prose into `doc/`

- **Second phase of the Part 2 documentation migration; no `src/agy_mcp/` behavior change.**
  - **Design documents moved (STR-02, INV-14):** `docs/ARCHITECTURE.md` moved to
    `doc/design/architecture.md` with lock-order section corrected to reflect actual
    code execution order (job lock -> concurrency flock -> manager state lock ->
    commit lock); `docs/DESIGN_MODES.md` moved to `doc/design/modes.md`.
  - **Normative spec and decisions moved (STR-02, Decision 1):** `SPEC.md` moved to
    `doc/design/spec.md` and `docs/DECISIONS.md` moved to `doc/design/decisions.md`
    as whole files without splitting; root stubs omitted per maintainer decision.
  - **Project documents moved:** `docs/CODEMAP.md` moved to `doc/project/code-layout.md`
    with generator (`scripts/gen_codemap_index.py`) and test (`tests/test_codemap_fresh.py`)
    updated in the same commit; `tests/fake_agy/README.md` moved to
    `doc/project/fake-agy.md` with a 5-line pointer left at the old path;
    `docs/ROADMAP.md` moved to `doc/project/roadmap.md`.
  - **Historical notes consolidated:** `docs/QUESTIONS.md`, `docs/CHANGELOG_SPEC.md`,
    and `docs/SCAFFOLD_NOTES.md` consolidated into `doc/project/history.md`, each with
    a historical status line and original source attribution.
  - **Orchestration split:** `docs/ORCHESTRATION.md` sections 1–4 and workflow selection
    moved to `doc/project/orchestrator-workflow.md`; sections 5–6 kept in a temporary
    stub at `docs/ORCHESTRATION.md` pending phase 2b experiment pages. Test isolation
    brief moved to `doc/findings-and-experiments/evidence/test-isolation-2026-10/`.
  - **User guide moved:** `docs/DSH_SETUP.md` moved to `doc/using/install.md`, linking
    `doc/reference/mcp-tools.md` rather than duplicating tool lists.
  - **Links and paths rewritten:** All repo-internal links and path references in `README.md`,
    `AGENTS.md`, `doc/`, `docs/`, `scripts/`, and tests updated to the new paths.
    `doc/README.md` updated with existing and planned section states.
  - **Unchanged by instruction:** No file in `src/agy_mcp/` was edited; no frozen test was
    edited (`sha256sum -c tests/frozen.sha256` remains 13/13); `docs/reports/` and
    `docs/orchestrator/PLAN.md` remain untouched.

## 2026-11-02 — Docs migration phase 1: `doc/` index, reference pages, docs gate

- **First phase of the Part 2 documentation migration; no `src/agy_mcp/` behavior change.**
  - **`doc/` skeleton (CONV-01, STR-01):** `doc/README.md` (reading order and the
    target folder map, with only existing pages linked),
    `doc/developing/writing-docs.md` (page template, mandatory `## Sources`,
    current/historical status, naming rules, where a new finding/audit goes) and
    `doc/developing/gates.md` (every gate with its exact command).
  - **Link repair (INV-11):** all 28 broken relative links are fixed — 22 in
    `docs/SMOKE_FINDINGS.md` (`docs/reports/...` → `reports/...`), 6 in
    `docs/DS_REVIEW_1.md` (`SPEC.md` → `../SPEC.md`, `docs/X` → `X`), plus the two
    links in the Part 2a audit. A link scan now reports 0.
  - **Reference pages (COV-01–COV-10):** `doc/reference/` adds
    `errors.md` (42 codes: the 40 stable codes plus the two server fallbacks),
    `environment.md` (16 variables), `exit-codes.md` (5 entry points),
    `config-keys.md` (all 27 keys including `timezone`, with validator/effect),
    `mcp-tools.md` (10 tools with parameters, returns, errors and an example),
    `state-layout.md` (state tree, permissions and the 55 `job.json` fields),
    `log-formats.md` (`audit.jsonl` events, `usage.jsonl`, `server.log`,
    `cli.log`), `manifests.md` (both cleanup manifests and `--json` schemas),
    and three CLI pages (`cli-cleanup-jobs.md`, `cli-cleanup-fixtures.md`,
    `cli-scripts.md`). Tables for error codes, variables, config keys, tools,
    `job.json` fields and audit events are generated by
    `scripts/gen_reference.py` and checked for freshness.
  - **Docs gate (CONV-02, MIG-03):** `scripts/check_docs.py` fails on broken
    relative links, a `doc/` page without `## Sources`, a Sources path that does
    not exist, an undocumented error code/env var/config key/tool/field/event,
    or a stale generated block. `--report` prints the same findings and exits 0.
    It is wired into `.github/workflows/ci.yml` as a `Docs check` step and pinned
    by `tests/tooling/test_check_docs.py`; AGENTS.md lists the command.
  - **Five contradictions fixed (INV-07, INV-08, INV-12, INV-13, INV-22):**
    installer `--help` no longer claims `--no-deps`; `docs/DSH_SETUP.md` lists
    all 10 tools including `agy_usage`; D1 gets a dated correction for
    `launch/poll/kill/cleanup` (no `resume`/`cancel`); D3 gets dated corrections
    for the 1 s poll + 5 s settle window and the done-only safety-net commit;
    SPEC §18 gains the missing `timezone` row.
  - **Conventions (CONV-03):** AGENTS.md rule 14 says where a new finding or
    audit goes and points at `doc/developing/`. The audit pages gained
    `## Sources` footers so the docs gate is enforceable; no audit finding was
    changed.
  - **Unchanged by instruction:** no file under `src/agy_mcp/` was edited; no
    frozen test was edited (`sha256sum -c tests/frozen.sha256` remains 13/13);
    `SPEC.md`/`DECISIONS.md`/`CODEMAP.md`/release notes/`docs/reports/`/
    `docs/orchestrator/PLAN.md` keep their paths.

## 2026-11-02 — Refactor waves 0–1: freeze manifest, CI, dead-code removal, dedupe (D101)

- **Behavior-preserving refactor of the Part 1 audit findings; no SPEC change.**
  - **Freeze is machine-checkable (F6):** `tests/frozen.sha256` pins the 12 `test_cleanup_jobs*` files plus `tests/test_cleanup_fixtures.py`, and `tests/test_frozen_manifest.py` fails with per-file expected/actual digests (and when the manifest and the frozen glob disagree in either direction). No frozen file was edited; the `E501` ignore for the frozen `fixes6` file now has a comment saying why.
  - **Foundations (A6, A11/R6–R8, G4, G7):** the CODEMAP generator walks subpackages and emits dotted names (proven against a temporary `tmp_path` package); the AST baseline was already stale on `main` and is repaired, and is regenerated in the same commit as any src-AST change but never for a comment-only commit; `.github/workflows/ci.yml` runs `ruff check src tests scripts` and `pytest` on push/PR (Python 3.10/3.12, dev extras); `.gitignore` covers `.pytest-basetemp*/` and `.agy/`.
  - **Dead code and a security fix (C1, C1a):** deleted `cleanup_fixtures._is_pid_alive`, `cleanup_jobs._is_worktree_registered`, `scratchpad.MAX_SCRATCHPAD_FILES`, `usage.clear_usage_cache` and `worktree.DANGEROUS_RULE_PREFIXES`. The dead constant listed `command(uv` while the live `is_dangerous_allow_rule` did not, so `command(uv run …)` was reported as safe; it now flags `uv`/`uvx`, with 25 new regression cases.
  - **Dedupe (B5, C2, C3, C4, C5):** one `jobs.record_cleanup_error`, one `load_job_json` + `MAX_JOB_JSON_BYTES` for the five `job.json` sites (keeping every reason string, exit code and fail-closed rule), one edge-protection fixpoint for both cleaners, `make_error_envelope` built from `StandardEnvelope` with an explicit ten-field omission list, and one `server._run_tool` for the ten wrappers. The tool surface, per-tool dispatch and error envelopes are pinned by snapshots taken before the change, so every diff is provably payload-identical.
  - **Comments (E2, E4):** WHY comments at the worktree-root union, the fixpoint and the three asserts; docstrings for `JobSupervisor.__init__`, `JobManager._run_supervisor` and `_validate_cargo_args`.

## 2026-11-02 — cleanup_jobs round 11: raw empty `--store` refusal, no global recursion limit in src (D100)

- **Round 11 finishing pass on the cleanup CLIs (D100).**
  - **Raw empty `--store` refusal (R17):** `cleanup_jobs` and `cleanup_fixtures` parse `--store` as a string and refuse empty/whitespace values before path normalization, with exit 2 and `Error: --store path cannot be empty or whitespace`. Previously `argparse`'s `type=Path` turned `""` into `Path(".")`, so `--store ""` run from inside a store silently operated on that store and `--store "" --yes` deleted a row there. Explicit `--store .` still works.
  - **No global recursion limit in production (R18):** removed the module-level `sys.setrecursionlimit(max(..., 5000))` from `cleanup_fixtures.py`. The test session raises the limit in `tests/conftest.py` only to generate deep fixtures; the CLIs run at the interpreter default and reject 100000-level nested `job.json` files fail-closed without tracebacks or signals.

## 2026-11-02 — cleanup_jobs round 10: non-git cleaner hardening (D99)

- **Safe job-record cleaner round 10 non-git hardening (D99).**
  - **Iterative JSON Depth Caps & Traversal (R13):** `_extract_strings` is iterative using an explicit stack with depth capped at 128 (`MAX_JSON_DEPTH`). `_safe_json_loads` validates nesting depth and catches `RecursionError` from `json.loads`, re-raising `ValueError`. Deep records fail closed without tracebacks across candidate scanning, kept-neighbor collection, pre-delete recheck, and `--list` mode.
  - **Fixtures Duplicate-Key Rejection & Store Refusal (R14):** `cleanup_fixtures.py` shares `_safe_json_loads` with `object_pairs_hook` duplicate-key rejection, failing closed when duplicate keys shadow kept references. Preflights `store/jobs` directory readability and refuses with exit status 2 and single-line error on stderr on unreadable stores.
  - **Empty/Whitespace Argument Refusal (R15):** Arguments for `--project`, `--store`, and `--job` consisting of empty strings or whitespace only are refused with exit status 2 and a clean single-line error on stderr. `normalize_repo_path` rejects empty or whitespace paths.
  - **Process Group pgid Liveness (R16):** Verifies process group liveness via `_check_pgid_status` using `os.killpg(pgid, 0)` mirroring `_check_pid_status` rules (permission denied = alive, ESRCH = dead, invalid/non-positive/bool/string/float/> pid_max = keep fail closed).

## 2026-11-02 — cleanup_jobs round 9: unified git metadata validation, hanging safety, and packed-refs validation (D98)

- **Safe job-record cleaner round 9 metadata validation and hang protection (D98).**
  - **Unified Git Metadata Reader & Validator (R10):** Every git metadata text file read by the cleaner
    (`HEAD`, `config`, `packed-refs`, `commondir`, `gitdir`, `locked`, `.git` pointer file) goes through
    `_read_git_metadata_file`:
    - Checks `stat()` before opening so FIFOs, device nodes, and sockets never block.
    - Strictly validates regular file (or symlink to regular file) and caps file size to 1 MiB (`MAX_GIT_METADATA_SIZE`).
    - Enforces strict UTF-8 decoding, rejecting UTF-8 BOM (`\xef\xbb\xbf` or `\ufeff`) and NUL bytes (`\x00`).
    - Single-line metadata files (`HEAD`, `commondir`, `gitdir`, `locked`, `.git` pointer) reject unstripped
      whitespace, multiple lines, and control characters (category `Cc` or code < 32).
    - `HEAD` content must match `ref: refs/<name>` (symbolic ref) or a 40/64-char hex object ID.
    - `config` files are validated for git config syntax (sections, valid key names, numeric values for
      `repositoryformatversion`, boolean values, no bare keys outside sections).
    - `packed-refs` files (if present) must be terminated by newlines, start with `# pack-refs with:`, and
      contain only comments, refs (`<sha> refs/...`), or peeled refs (`^<sha>`).
    - Any syntax or encoding failure fails closed and retains the job row.
  - **Hanging & Subprocess Safety (R11):** The cleaner never opens non-regular files without `stat()` inspection
    first, caps read sizes, and all test subprocesses enforce explicit execution timeouts.
  - **AST Exception Structural Guard (R12):** Zero broad exception handlers swallow errors; 0 offenders in
    `test_no_swallowed_exceptions_structural_guard`.

## 2026-11-02 — cleanup_jobs round 8: repository structure validation and worktree_path registration matching (D97)

- **Safe job-record cleaner round 8 linked-worktree and custom worktree hardening (D97).**
  - **Comprehensive repository structure validation (R7):** Validates every required file and directory
    needed by git for each repository kind (normal `.git` dir, linked worktree private dir, common dir, bare repo,
    and submodule modules dir):
    - Normal `.git` dir: `HEAD` (regular file, non-empty, readable), `objects` (dir), `refs` (dir), `config` (file).
    - Linked worktree private dir: `HEAD` (file), `commondir` (file), `gitdir` (file). If `commondir` is missing,
      the directory is validated as a standalone git directory / submodule (`objects`, `refs`); if invalid, fails closed.
    - Common dir: `HEAD` (file), `objects` (dir), `refs` (dir), `config` (file).
    - Bare repo: `HEAD` (file), `objects` (dir), `refs` (dir), `config` (file).
    - Submodule modules dir: `HEAD` (file), `objects` (dir), `refs` (dir), `config` (file).
    - Any missing required file, wrong type (dir vs file), dangling symlink, FIFO, empty file, or unreadable item fails closed.
  - **Job-owned paths worktree registration matching (R8):** Compares worktree entry `gitdir` pointers against
    ALL paths owned by the job: `<job_dir>`, `<job_dir>/work`, `<job_dir>/work/.git`, and `<worktree_path>`,
    `<worktree_path>/.git` (resolved deterministically with path normalization, handling relative paths against both
    cwd and `job_dir`), whether or not `worktree_path` exists on disk.
  - **Structural guard compliance (R9):** Zero broad exception handlers swallowing exceptions.

## 2026-11-01 — cleanup_jobs round 7: marker-based union worktree root discovery and bare-repo HEAD validation (D96)

- **Safe job-record cleaner round 7 bare-repo hardening (D96).**
  - **Marker-based union of worktree roots (R6a):** Candidate worktree roots to scan are computed as a union:
    `repo_path/.git/worktrees` if `.git` is a valid git dir or pointer target, AND `repo_path/worktrees` if
    `repo_path` looks like a bare repo. Precedence branching never shadows one root with another.
  - **Bare-repo markers & HEAD validation (R6b):** When `.git` is absent or not a pointer/dir: if `repo_path`
    contains any standard git directory markers (`worktrees`, `objects`, `refs`, `config`, `packed-refs`,
    `commondir`), it looks like a git directory and MUST contain a regular file (or symlink to a regular file)
    named `HEAD`; otherwise the row is kept fail-closed with `"worktree registrations unreadable (repo_path is
    not a git directory: <path>)"`. When none of the markers are present, `repo_path` stays "not a git repo"
    (deletable as before).
  - **Nested/shadowed bare repo handling (R6c):** When `.git` is a directory or pointer file, `repo_path` is also
    checked for bare repository markers. If `(repo_path / "HEAD").is_file()` and git markers are present,
    `repo_path/worktrees` is added to candidate worktree roots. If `repo_path` has bare repository internals
    (`worktrees`, `objects`, `refs`) directly under `repo_path` but `HEAD` is not a regular file, it fails
    closed (`"repo_path is not a git directory"`).
  - **Structural guard compliance (R6d):** Zero broad exception handlers swallowing exceptions.

## 2026-11-01 — cleanup_jobs round 6: validate git dirs, reject BOM/control chars and duplicate keys; differential harness green (D95)

- **Safe job-record cleaner round 6 differential hardening (D95).**
  - **Git directory validation (R1):** Any directory followed into git metadata (pointer target of
    `repo_path/.git`, commondir target, submodule modules directory, bare repo directory, or `repo_path/.git`
    itself) must contain a regular file named `HEAD`. If missing, the row is kept fail-closed with a clear
    reason naming the path and stating `"not a git directory"`.
  - **Git metadata text file validation (R2):** All git metadata text files read by the cleaner (`.git`
    pointer, `commondir`, worktree entry `gitdir`, worktree entry `locked`) are validated against BOM (U+FEFF),
    NUL bytes (`\x00`), and control characters (allowing at most one trailing LF or CRLF). Files with invalid
    characters retain the row fail-closed with an invalid metadata reason; UTF-8 BOM is never stripped silently.
  - **`repo_path/.git` resolution and symlink safety (R3):** Absent `.git` indicates not a git repository
    (checked for a bare repository if `repo_path/HEAD` is a regular file); regular files must be single-line
    pointers to directories containing `HEAD`; directories must contain `HEAD`. Anything else (including
    dangling symlinks, empty directories, directories without `HEAD`, fifos, and sockets) keeps the row fail-closed.
  - **Duplicate keys in `job.json` fail closed (R4):** Duplicate keys in any `job.json` read by the cleaner
    (candidate scan, kept-neighbor collector, `--list` scan, `--job` mode, pre-delete candidate recheck) raise
    `ValueError` via `object_pairs_hook` in `_safe_json_loads` and fail closed through existing corrupt-record paths.
  - **Regular file enforcement for entry files and commondir (R5):** `commondir` and all worktree entry files
    (`gitdir`, `commondir`, `locked`) must be regular files or symlinks resolving to regular files; dangling
    symlinks or directory swaps keep the row fail-closed.
  - **Differential test suite:** Ported review harness into `tests/cleanup/test_git_oracle_differential.py`
    (390 layout cases + 18 job.json cases + 6 baselines) as the normative acceptance oracle against real git;
    all 414 tests pass green with zero fail-open cases and zero crashes.

## 2026-11-01 — cleanup_jobs round 5: validate commondir and worktree entry gitdir files, fail closed (D94)

- **Safe job-record cleaner round 5 hardening (D94).**
  - **`commondir` file validation:** Validates `commondir` in linked worktrees and submodules exactly like
    the repository `.git` pointer: requires exactly one non-empty line (allowing at most one trailing LF
    or CRLF); resolves relative to the gitdir containing it; and requires the target path to exist and be
    a directory. Any violation (unexpected extra lines, empty path, target missing, target not a directory,
    or commondir being a directory/non-regular file) retains the row fail-closed with a reason naming the
    commondir file as invalid and including the path. Unreadable or undecodable commondir retains the row
    fail-closed as well.
  - **Worktree entry `gitdir` validation:** For each entry in `<common>/worktrees/<entry>`, its `gitdir` file
    must contain exactly one non-empty line (after at most one trailing LF or CRLF). Entries with missing
    regular files, unexpected extra lines (such as comments), or empty paths are treated as invalid/unreadable
    and recorded, ensuring the row is kept fail-closed if no other entry matches the job, while preserving
    valid registration matches.
  - **Audit of git metadata paths:** Verified that every path read from git metadata in `_check_worktree_status`
    is strictly validated and fails closed.

## 2026-11-01 — cleanup_jobs round 4: single-line .git pointer, missing target fails closed, no swallowed candidate-scan error (D93)

- **Safe job-record cleaner round 4 hardening (D93).**
  - **Single-line `.git` pointer validation:** A repository `.git` regular file must consist of exactly
    ONE non-empty line starting with `"gitdir:"` (a single trailing LF or CRLF is permitted and ignored;
    surrounding spaces around the path are stripped). Any additional lines (comments, second `gitdir:`
    directives, or trailing blank lines) fail closed with a reason naming the `.git` file as invalid. Single-line
    CRLF pointers with registered worktrees remain kept with `"worktree registered"`; valid single-line pointers
    without registered worktrees remain deletable.
  - **Pointer target directory check:** Resolved target path from `gitdir` must exist and be a directory;
    if nonexistent or not a directory, the row is kept fail-closed with a reason naming the target as missing or
    not a directory and including the resolved path.
  - **Candidate scan exception recording:** Fixed candidate scanning in `run_cleanup` to record unreadable or
    corrupt `job.json` rows as kept with clear reasons (`"job.json unreadable or corrupt: <exc>"` or
    `"job.json root is not an object"`) instead of silently continuing with `except Exception: continue`.
    Zero offending exception handlers reported by the structural guard.
  - **Documented accepted limitations:** Documented that in `--job` mode with invalid or NUL `repo_path`
    repo-level registration lookup is skipped while job-local worktree protections (`work/.git` and
    `worktree_path/.git`) apply; `--list` runs a full dry run per project ($O(\text{projects} \times \text{jobs})$);
    and planned order is sorted deterministically by job ID.

## 2026-11-01 — cleanup_jobs round 3: skipped-candidate recheck, invalid .git files, dry-run reporting, NUL path safety, unreadable store, no-swallow guard (D92)

- **Safe job-record cleaner round 3 hardening (D92).**
  - **Skipped-candidate pre-delete recheck:** Dynamic edge protection re-validates all kept rows on disk
    excluding only remaining candidates (`planned_ids[idx:]`). Any kept row or candidate skipped/failed
    earlier in the run that becomes unreadable, corrupt, non-object, or undecodable stops deletion of all
    remaining candidates fail-closed; candidates are recorded in `failed_ids` and `skipped` with reasons
    naming the blocking row, manifest status is `"partial"`, and CLI exits 1. Supersedes D89 rule 8
    ("one failing row never aborts remaining deletions") for corrupt or unreadable records.
  - **Invalid `.git` file pointer fails closed:** A repository `.git` regular file must start with `"gitdir:"`
    and specify a non-empty pointer path; empty, garbage, colon-less, or truncated files keep the row with
    an unreadable/invalid `.git` file reason. Valid pointers with no registered worktrees remain deletable.
  - **No-swallow edge collection:** Transitive edge collection for rows transitioning to kept reuses
    already-parsed job data from classification (`cand_raw_data`) rather than re-reading from disk,
    eliminating exception-swallowing handlers and satisfying the AST structural guard.
  - **`--job` dry run reporting:** In dry run, eligible requested IDs are reported only as candidates
    (not in `failed_ids` or `skipped`); only truly ineligible requested IDs appear in `failed_ids` and
    `skipped` with reasons.
  - **NUL byte and invalid path normalization safety:** `repo_path` containing NUL bytes or raising on
    normalization never crashes the process. Rows appear under `"(unknown)"` with `eligible_count: 0` in
    `--list`, are kept with `"repo_path invalid"` in `--project`, and have repository worktree lookup safely
    skipped in `--job` while strictly maintaining job-local worktree protections (`work/.git`).
  - **Unreadable `store/jobs` directory handling:** Clean exit code 2 with a single-line stderr error and
    no traceback when `store/jobs` cannot be accessed or listed.
  - **Known limitation documentation:** Documented `--list` $O(\text{projects} \times \text{jobs})$ dry-run
    scaling in `SPEC.md` and `docs/DECISIONS.md`.

## 2026-11-01 — cleanup_jobs fail-closed pre-delete recheck, non-UTF-8 git metadata, job-mode exit code, blocker messages (D91)

- **Safe job-record cleaner fail-closed hardening and explainability (D91).**
  - **Fail-closed pre-delete edge recheck:** Reading any kept row's `job.json` immediately before
    deletion re-verifies edge protection. If any kept row is corrupt, non-object, or unreadable,
    deletion fails closed, candidate is added to `failed_ids` with a blocker reason naming the
    corrupt record and path, and deletion stops for all remaining candidates.
  - **Non-UTF-8 git metadata handling:** Undecodable or unreadable git metadata (gitdir, commondir,
    `.git` files, `worktrees` directory) is treated as "cannot determine" and fails closed with a
    clear reason naming unreadable registrations, never "not registered". Handled per entry so bad
    entries do not hide valid registrations.
  - **`--job` mode exit code and manifest preservation:** Exits 1 with manifest status `"partial"`
    whenever at least one requested ID was eligible and its deletion fails (removing exit-2 shortcut
    when nothing deleted). Exits 2 with no manifest written only when every requested ID is
    ineligible. Completed manifest preserves `planned_ids`.
  - **Explainable over-blocking:** Human and `--json` outputs name corrupt/unreadable blocker records
    by UUID and full path, state that they were left untouched, and advise manual inspection or
    removal. `--list` outputs a warning line naming the UUID, and `--list --json` provides a
    top-level `"warnings"` list containing the UUID while keeping `eligible_count = 0` and leaving
    disk bit-for-bit untouched.

## 2026-11-01 — safe job-record cleaner hardening and edge-case fixes (D90)

- **Safe job-record cleaner hardening and edge-case fixes (D90).**
  - **Fail-closed unreadable/corrupt scan:** Any kept record with corrupt JSON, non-object root,
    or unreadable permissions retains all candidates store-wide fail-closed with clear reasons.
  - **Pre-delete project and edge re-checks:** In `--project` mode, candidates are re-verified
    against target project immediately prior to removal; dynamic edge protection recomputes
    against all currently kept rows (including previously skipped candidates).
  - **Worktree detection refinements:** Relative `gitdir` files are resolved against their worktree
    entry directory; linked worktrees and submodules (`.git` as a file with `commondir`) are fully
    supported; unreadable worktree directories fail closed.
  - **`--job` mode deduplication & partial manifest tracking:** Duplicate `--job` arguments are
    deduplicated; ineligible requested jobs appear in `failed_ids` (JSON and manifest) with their
    reasons, marking the manifest `"partial"` and exiting 1.
  - **`--list` consistency & invalid `repo_path` handling:** Reuses `run_cleanup` for project
    counts so edge-protected candidates are excluded; invalid or non-string `repo_path` values
    never match `--project` and appear in `--list` under `"(unknown)"` with `eligible_count: 0`.
  - **Documented limitations:** Symlinked job folders in `--job` mode exit 2 by design; Unicode
    NFC/NFD paths are not normalized.

## 2026-11-01 — safe job-record cleaner CLI (D89)

- **Safe job-record lister and cleaner CLI (D89).** Added `src/agy_mcp/cleanup_jobs.py`
  runnable via `python -m agy_mcp.cleanup_jobs`.
  - **Three mutually exclusive modes:** `--list` (read-only project and state summary,
    bit-for-bit disk preserving), `--project PATH` (purges stale records for a specific
    project), and repeatable `--job ID` (removes explicit job IDs waiving age requirements).
  - **Strict path normalization:** Target and job `repo_path` are normalized (handling `~`,
    trailing slashes, and vanished paths) while strictly rejecting sibling, prefix, parent,
    child, and case differences.
  - **Safety and worktree preservation:** Retains rows with non-terminal states, live or
    ambiguous PIDs, symlinks, or registered git worktrees (`work/.git` or repo `.git/worktrees`),
    suggesting `git worktree prune`.
  - **Edge protection:** Protects candidates referenced as keys or substrings in any kept job.
  - **Atomic honest manifest & `before_delete` seam:** Manifest written as `"planned"` before
    deletion and replaced with `"completed"` or `"partial"` after deletion (IDs only). Re-validates
    candidates immediately prior to removal so mutated rows are preserved.

## 2026-11-01 — safe fixture cleaner fixes (D88)

- **Safe fixture cleaner hardening and honest manifest (D88).**
  - **Fail-closed unreadable scan:** Unreadable kept job directories or unreadable/unparseable
    `job.json` records cause fail-closed retention of all candidates with stated reasons, exiting 0
    without tracebacks on dry run, and deleting nothing under `--yes`.
  - **Dict key & substring edge protection:** Candidate UUIDs appearing as dictionary keys anywhere
    (including nested) or as substrings inside any string value of a kept record are protected.
    Unrelated UUIDs do not protect candidates.
  - **Pre-delete revalidation & `before_delete` hook:** `run_cleanup` supports an optional
    `before_delete` callback invoked immediately before each deletion. Each row is re-read and
    re-classified before deletion; mutated or non-matching rows are skipped on disk without stopping
    remaining deletions.
  - **Honest manifest lifecycle:** Manifest is written before deletion with status `"planned"` and
    `planned_ids`, and replaced atomically after deletion with status `"completed"` or `"partial"`,
    `removed_ids` (only actually removed rows), and `failed_ids` (failed or skipped). Manifests
    contain IDs only. Partial runs exit non-zero (exit 1) and report failed IDs.
  - **Ancestor path access refusal:** Unsearchable or unreadable ancestor directories of `--store`
    are refused with exit code 2 and a clean message without traceback.
  - **Candidate preservation in JSON output:** `--json` output under `--yes` retains the full
    `candidates` list and `candidate_count` identified before deletion, while `deleted_ids` and
    `deleted_count` record what was removed.

## 2026-10-31 — test harness state isolation and safe fixture cleanup CLI (D86, D87)

- **Test harness live state isolation (D86).** Conftest now redirects `HOME`,
  `XDG_STATE_HOME`, and `AGY_MCP_STATE_DIR` to a temporary bootstrap environment at
  import time before any test modules load. An in-process resolution guard patches
  `get_base_state_dir` across `agy_mcp` modules, raising `RuntimeError` on any attempt
  to resolve the operator's real live state store `~/.local/state/agy-mcp`. The
  `sys.addaudithook` write barrier, dir_fd wrappers and session-scoped `isolated_state`
  fixture are preserved.
- **Safe fixture cleanup tool (D87).** Added `src/agy_mcp/cleanup_fixtures.py` runnable via
  `python -m agy_mcp.cleanup_fixtures`. Provides a dry-run-first CLI with `--store PATH`
  (required), `--yes`, `--min-age-days`, and `--json`. Classifies candidates against a
  strict whitelist signature (canonical UUID folder, valid `job.json` <= 1 MiB, terminal
  state, pytest temp repository path with a per-test `test_...<n>` component, no live
  process, age > threshold, no internal symlinks, no store/repo shared pytest basetemp,
  and fail-closed follow-up-edge protection). Refuses symlinked stores, symlinked job
  directories, and stores outside `$HOME`. Writes an atomic, ids-only manifest
  `.cleanup-manifest-<timestamp>.json` before bottom-up deletion.

## 2026-10-31 — calibrated token quota estimate (D85)

- **Calibrated conservative token quota estimate (D85).** Recalibrated token-based
  quota estimate coefficients to conservative upper-envelope values (`0.011` points per
  1k tokens for the 5-hour bucket, `0.0031` for the weekly bucket; base `0.0`) fitted from
  7 measured jobs in `docs/reports/quota-calibration-data.md`. The estimate never
  under-reports an observed job.
- When no token total can be determined, `quota_estimate` is now `null` rather than a
  fabricated zero count. A missing/zero/unusable `total_tokens` falls back to
  `input_tokens + output_tokens` when both counters are present, so agy payloads that
  omit `total_tokens` still estimate from the observed counters.
- The estimate record carries `quota_estimate_basis: "calibrated-2026-10-31, n=7"`.
- Default coefficients in `Config` and `JobSupervisor` updated to `0.0031` (weekly) and
  `0.011` (5-hour). Existing stored job records are never rewritten; measured `usage_cost`
  remains authoritative.

## 2026-10-31 — additive status fields & node --test verify allowlist

- **Additive `agy_status` fields (D84).** `agy_status` exposes `conversation_id`,
  `conversation_id_source`, `runs` (projection of `Job.runs[]` with `{index, started_at,
  ended_at, wall_clock_seconds, outcome}`), and `wall_clock_seconds` (sum of run
  wall clocks; `null` when no run has finished). All changes are strictly additive.
- New run rows written by `supervisor._record_run` and `recovery` record `started_at`,
  `ended_at`, and `outcome`. Old job records and legacy run rows project `None` and `[]`
  without errors.
- **`node --test` verify allowlist (D84).** Added `"node --test"` to default
  `verify_allowlist` after `"go test"`. Supports relative paths, directories, and
  workdir globs; strictly rejects any extra token starting with `-` (flags, `--`),
  empty tokens, bare `node`, interpreter paths, glued flags, absolute paths, `..`
  components, symlink escapes, and shell metacharacters.

## 2026-10-30 — resume / commit-message review fixes

- **`usage.jsonl.duration_seconds` changed meaning (F9).** Rows written before
  D83 stored the job's *cumulative minutes*. Rows written from D83 onward store
  *this run's wrapper-measured wall clock* (the same value as
  `wall_clock_seconds`). Do not sum old and new rows together without
  accounting for the change; `cumulative_minutes` remains the cumulative field.
- Per-run telemetry (`job.json.runs[]`) records only that generation's tokens;
  resuming a job resets `tokens`/`quota_estimate` for the new generation (F3).
- Conversation-id resolution is generation-scoped: the wrapper records its
  `cli.log` anchor at every launch, ignores stale lines from an appended log,
  handles a truncated log, prefers agy's stdout JSON, and lets a real stdout
  UUID replace an early-scan-only UUID while a prior finalized UUID stays
  sticky (F1/F4/F5).
- Commit-message repair now splits lines exactly like the validator, does not
  rewrite/log a message the validator already accepts, and rejects (never
  repairs) a first line containing a bare CR or other control character (F2).
- Wrapper writes of repaired/rejected `COMMIT_MSG` evidence loop on partial
  writes and fsync the parent directory (F6).
- `job.error.first_line_repr` is wrapped as `<untrusted_text>` in the result
  envelope; the raw value stays in the job record (F7).
