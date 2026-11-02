# Part 2b — Experiments, findings, and the target `doc/` structure

Status: current

**Repository:** `agy-mcp` @ `main` = `a7ed98aa502b1a5ed84551aff7aa1c705156dedf`
**Mode:** read-only; this file and its siblings are the only writes.
**Companions:** `part2a-inventory.md` (documentation inventory), `part2c-coverage.md`
(coverage matrix), `part2d-migration.md` (conventions + migration), `part2-index.md`.

**Path shorthand:** a bare module name (`manager.py`) means
`src/agy_mcp/manager.py`; a bare document name (`DSH_SETUP.md`) means
`docs/DSH_SETUP.md`; `install.sh` means `installer/install.sh`.

---

## B. Experiments and findings

### B.1 What counts, and where it is today

An *experiment* answers a question with a command and a result. A *finding* is a
conclusion drawn from one or more experiments. An *audit* is a review of the
repository against a standard (it gets its own folder, §C4).

There are **three homes** for this material today, with **four naming schemes**:

| Home | Naming | Count | Written page? |
|---|---|---|---|
| `docs/AGY_FINDINGS*.md`, `docs/SMOKE_FINDINGS.md` | `Q*`, `S*`, `T*` sections | 4 files, 1,589 lines | yes, in-file |
| `docs/reports/<round>/` | `fix-batch1`, `smoke-fixes-2`, `agymcp-fixes-2`, `flaky-f1`, … | 23 dirs, 527 files | **6 of 23 dirs** |
| `~/agy-review/<agent>/` | `attack_*`, `fuzz*`, `repro_*`, `probe_*` | 16 MB, 5 agents | `FINDINGS.md` for 3 of 5 |

The numbers:

```
$ git ls-files docs/reports | wc -l
527
$ find docs/reports -name '*.md' | wc -l
51
$ find docs/reports -path '*scratchpad*' -name '*.md' | wc -l
36
$ du -sh ~/agy-review
16M
```

### B.2 Every experiment/finding artifact, with its current status

"Status" is what the repository now says about the result. **Stale** means the
report's conclusion was later overturned by a `D` number or code change and the
report was not marked.

| # | Where it lives | Date | Question | Result / verdict | Status | Target page |
|---|---|---|---|---|---|---|
| E01 | `docs/AGY_FINDINGS.md` (Q1–Q12) | 2026-10-24 | What does the real `agy` CLI allow, deny, resume, print and detach? | allow-rule prefix matching, accept-edits denials, `--conversation` resume works, stream-json, exit 0 on denial/timeout, parallel repos | current as v1.4.7 record | `01-agy-probe-1-cli-behavior.md` |
| E02 | `docs/AGY_FINDINGS_2.md` (S1–S9) | 2026-10-24 | Can compound commands, env inheritance or `/tmp` writes escape the policy? | `&&`/`;`/`|` blocked, `/tmp` writable, parent env inherited, `ask_question` skipped, timeout recovery | current as record | `02-agy-probe-2-security.md` |
| E03 | `docs/AGY_FINDINGS_3.md` (T1–T7) | 2026-10-24 | Where are the hard boundaries (reads, writes, `.git`, branch flags, test runners)? | unconfined reads via `head`/`grep`/`git diff`, `.git` writable, `git branch -D/-f`, test runners = code execution | current; it is the basis of the threat model | `03-agy-probe-3-escapes.md` |
| E04 | `docs/SMOKE_FINDINGS.md` (S1–S7) | 2026-10-25 | Does a real end-to-end job work through the MCP server? | denial halts turn, `NEEDS_DECISION` marker, resume keeps context, tokens per run (33k/90k/24k/260k), `.agy/` never committed | current as v1.4.7 record; 22 broken links | `04-smoke-real-agy-1.md` |
| E05 | `docs/ORCHESTRATION.md` §6 | 2026-10-26 | Does `mcp__agy__*` work from a real dsh chat, with which approval policy? | two runs; second ran a full job (150.9 s wait); policy `never` + `danger-full-access` | current; mixed into a how-to | `05-real-dsh-runs.md` |
| E06 | `docs/reports/agy-capability-inventory.md` | 2026-10-30 | What does `agy --help`/`--version` expose (1.4.10)? | 25 flags, 12 subcommands, no `usage`/`auth`; wrapper argv confirmed | **stale**: the "using" bug is fixed by D81 | `06-agy-capability-inventory.md` |
| E07 | `docs/reports/real-smoke-1/REPORT.md` | 2026-10-26 | Do two real jobs complete and finalize correctly? | 2 jobs, masked envelopes, git branches, `.git/info/exclude` | current | `07-real-jobs-1.md` |
| E08 | `docs/reports/quota-calibration-{report,data}.md` | 2026-10-31 | How many quota points per 1k tokens? | 7-job upper envelope: 0.011 / 0.0031 %/1k; 31.3% median error; n=7 | current (D85) | `08-quota-calibration.md` |
| E09 | `docs/reports/test-isolation-{findings,report}.md` | 2026-10-31 | How did tests touch the live store, and how is it prevented? | 332 live records, 312 fixtures, 40 ghost queued; guard + bootstrap HOME | current (D86, D87) | `09-test-store-contamination.md` |
| E10 | `docs/reports/stale-jobs-report.md` | 2026-10-31 | Why were 339 job records stuck? | 339 records, 272 failed, 40 cancelled; 40 ghost queued; backup + cleanup | current | `09-test-store-contamination.md` §2 |
| E11 | `docs/reports/needs-decision-investigation.md` | 2026-10-31 | Why do trivial jobs stop at `needs_decision`? | 27 in-scope jobs, 1 observed decision; mechanisms are guesses | current; explicitly a lower bound | `10-needs-decision-investigation.md` |
| E12 | `docs/reports/flaky-f1/REPORT.md` | 2026-10-31 | Why did the f1 resume test fail 2/30? | stale `COMMIT_MSG` + SIGKILL readiness; fix + mutation | current (D65/D66) | `11-flaky-f1-resume-race.md` |
| E13 | `tests/test_cleanup_jobs_differential.py` + `~/agy-review/differential_harness.py` | 2026-11-01 | Does the cleaner agree with real git on 390 layout × mutation cases? | 390 layout + 18 `job.json` cases; fail-open = fail, over-block = pass | **the test is in repo; the harness copy is not** | `12-cleanup-jobs-differential.md` |
| E14 | `~/agy-review/{opus,gemini2,gemini3,gemini4,ds}` | 2026-11-01/02 | Adversarial review: can the cleaner delete a live/registered job? | 16 attack scripts + fuzz + repros; several fail-open findings, fixed in D90–D100 | **no committed report; 16 MB uncommitted** | audit page `2026-11-01-cleanup-jobs-adversarial-review.md` |
| E15 | `docs/reports/installer-fixes/REPORT.md` | 2026-10-26 | Why did the F22 installer test flake? | stale `agy_wait` observation window; fixed (D76) | current | `13-installer-and-gate-fixes.md` §1 |
| E16 | `docs/reports/orchestrator-workflows/REPORT.md` | 2026-10-27 | Can a workflow be chosen from the guidance block alone? | GATED/MICRO/RESEARCH/DIRECT block + 4 tests | current | `13-installer-and-gate-fixes.md` §2 |
| E17 | `docs/reports/verify-loop/verify-flow.md` | 2026-10-26 | How does verify execute, time out and auto-fix? | 8-section review; unclear areas listed | current | `13-installer-and-gate-fixes.md` §3 |
| E18 | `docs/reports/wrapper-verify-commit/README.md` | 2026-10-29 | Does D80 hold (subjects, hooks, timeout kill)? | 5 full runs, hook accept/reject, timeout rc 124 | current | `13-installer-and-gate-fixes.md` §4 |
| E19 | `docs/reports/{fix-batch1..4,hardening,agymcp-fixes-2,smoke-fixes,smoke-fixes-2,smoke-fixes-3,stale-commit-msg,server,verify-fixes,installer-agymcp}` | 2026-10-25/26 | One question per fix round (mutation survival, fail/pass, etc.) | only raw `*-fail.txt`, `*-pass.txt`, `*-mutation.txt` | **no narrative in 17 dirs** | `14-fix-round-evidence-2026-10.md` (grouped) |
| E20 | `docs/reports/quickwins-report.md` | 2026-10-27 | Do additive `agy_status` fields and the `node --test` allowlist work? | additive fields + allowlist; tests and a fresh review; merge commands recorded | current | `13-installer-and-gate-fixes.md` §5 |

The 8 top-level `docs/reports/*.md` files are E06, E08 (two files), E09 (two
files), E10, E11 and E20; all appear in the table.

### B.3 Why the current organisation fails

1. **No index.** A reader cannot tell that `AGY_FINDINGS_2` and
   `reports/smoke-fixes-2/` are about different questions, or which of the 23
   report directories are still current.
2. **No page, only logs.** `agymcp-fixes-2` (34 files), `fix-batch1` (35),
   `hardening` (44), `smoke` (22), `smoke-fixes` (19) have no markdown; the
   question is recoverable only from `D` numbers or the commit subject.
3. **The biggest body of work has no committed evidence.** There is no
   `docs/reports/cleanup_jobs*`; the 16 MB of attack/fuzz/differential work is
   under `~/agy-review/` with absolute paths, mode-000 directories and
   permission errors (`du` reported "Permission denied" on six subtrees).
4. **Stale verdicts are not marked.** E06 still says a bug is live that D81
   fixed. E04's links are 22/22 broken. E09/E10 use a 339-record store that no
   longer exists.
5. **Numbering is by author/round, not by topic.** `fixes2`…`fixes10`, `f1`…`f28`,
   `area1`…`area19`, `attack_01`…`attack_16`.

### B.4 Proposed experiment pages

Folder: `doc/findings-and-experiments/`. Numbering `NN-slug.md` (two digits);
sub-experiments use `NN.M` in headings, never new files. Every page uses the
template in `doc/developing/writing-docs.md`:

```
# <NN> — <question as a title>
Date | Status: current | superseded by <D/commit> | not verified
## Question
## Method        (commands, build/sha, sample size)
## Result        (tables of numbers, quoted output)
## Conclusion
## Not verified / limits
## Sources       (repo files, with paths; raw evidence links)
```

| Page | Contents (question, method, result, conclusion, sources) |
|---|---|
| `README.md` | Index table: ID, date, question, verdict, status, page. One screen. |
| `findings-summary.md` | One line per settled finding, with the decision/commit that settled it; a short "still open" list (UNSURE register from ORCHESTRATION §5, auth-expiry probe, quota/auth patterns, `bwrap`/`unrestricted` modes). |
| `01-agy-probe-1-cli-behavior.md` | Q1–Q12 from `AGY_FINDINGS.md`: allow-rule prefix matching, no-rule denials, resume, stream-json, failure shapes, print-timeout, instruction files, detach, model/effort, parallel, storage, `--sandbox`/`--mode auto`. Method: exact `agy` invocations and versions. Conclusion: which wrapper choices are based on it. Sources: `AGY_FINDINGS.md`, `runner.py:22-29`. |
| `02-agy-probe-2-security.md` | S1–S9 from `AGY_FINDINGS_2.md`: compound commands, `/tmp` writes, env inheritance, safe-command heuristic, other tools, stream-json events, timeout recovery, settings scope, same-repo parallel. Token cost and recommended rule set as appendices. Sources: `AGY_FINDINGS_2.md`. |
| `03-agy-probe-3-escapes.md` | T1–T7 from `AGY_FINDINGS_3.md`: unconfined reads, `echo` redirect, `.git`/gitconfig writes, `git branch -D/-f`, test runners as code execution, project permissions, escape summary and mitigations. This page is the evidence for `doc/design/spec.md` §5 and the README table. |
| `04-smoke-real-agy-1.md` | The 7 smoke scenarios and the nine evaluation questions; per-run tokens (33,061 / 90,114 / 24,654 / 260,286); what was confirmed and what broke; `.agy/` never committed. Sources: `SMOKE_FINDINGS.md`, `docs/reports/smoke/` (22 files), `docs/reports/real-smoke-1/`. |
| `05-real-dsh-runs.md` | The two owner-run dsh sessions: what was established (transport, `run.sh`, `agy_usage`), what remains UNSURE (approval semantics, `toolCallTimeoutMs`, reload). Sources: `ORCHESTRATION.md` §5–§6. |
| `06-agy-capability-inventory.md` | 1.4.10 `--help`/`--version` inventory: 25 flags, 12 subcommands, the exact argv the wrapper builds, banned flags. Add a "superseded by D81" note for the conversation-id finding. Sources: `docs/reports/agy-capability-inventory.md`, `runner.py`, `util.py`. |
| `07-real-jobs-1.md` | Two real jobs end-to-end: per-job table, envelopes (masked), git branches, `.git/info/exclude`; the first proof that the whole pipeline finalizes. Sources: `docs/reports/real-smoke-1/`. |
| `08-quota-calibration.md` | 7-job population, observed rates, rejected fits (least squares, intercept), chosen upper envelope 0.011/0.0031, errors (median 31.3%, worst 260%), n=7 caveat, constants frozen in `tests/test_quota_calibration.py`. Sources: `quota-calibration-report.md`, `quota-calibration-data.md`, `usage.py:compute_quota_estimate`, `config.py:72-76`. |
| `09-test-store-contamination.md` | Two linked experiments: (a) the isolation audit — 332 live records, 312 pytest fixtures, 40 ghost queued, the resolution path and the guard; (b) the stale-jobs diagnosis and cleanup (339 records, backup, dry run). Sources: `test-isolation-report.md`, `test-isolation-findings.md`, `stale-jobs-report.md`, `tests/conftest.py`, `tests/test_live_state_isolation.py`. |
| `10-needs-decision-investigation.md` | Why 1 of 27 in-scope jobs stopped; sample/composition, the one observed record, mechanisms as guesses, limits (no history, retention 14 days). Sources: `needs-decision-investigation.md`. |
| `11-flaky-f1-resume-race.md` | 30 runs, 2 failures at runs 1/7; stale invalid `COMMIT_MSG` + SIGKILL readiness; the deterministic test and mutation; D65/D66 text. Sources: `flaky-f1/REPORT.md`, `tests/test_resume_and_commitmsg.py`. |
| `12-cleanup-jobs-differential.md` | The acceptance oracle: 390 layout mutations × 18 `job.json` mutations, fail-open vs over-block policy, layouts A–F, how to run it, what it does not cover. Sources: `tests/test_cleanup_jobs_differential.py`, `doc/design/spec.md` §23 (last bullet). |
| `13-installer-and-gate-fixes.md` | Four short investigations: installer F22 flake (D76), workflow-selection block (D79), verify-loop review, wrapper commit-subject/hook/timeout proof (D80). Sources: the four report dirs + `tests/test_installer_chapter.py`, `tests/test_wrapper_verify_commit_fix.py`. |
| `14-fix-round-evidence-2026-10.md` | A *grouped index* for the 12 reportless rounds: one subsection each (round, question, files, verdict), linking the raw `*-fail.txt`/`*-pass.txt`/`*-mutation.txt`. This is the minimum that makes the logs usable without inventing results. Sources: every `docs/reports/<round>/` dir. |
| `evidence/README.md` | How evidence is stored: one directory per experiment (`NN-slug/`), `git`-tracked only if it contains no secrets, no mode-000 dirs, no absolute home paths; otherwise a `README` naming where the archive lives. |

Raw evidence policy: keep the existing `docs/reports/` tree as-is until Harsh
decides (Part 1 wave 4.1). Link to it from the experiment pages; do not duplicate
it into `doc/`.

---

## C. Target structure

### C.1 Design rules

1. **Plain markdown, GitHub-renderable.** No build step, no site generator
   required. Relative links only; each folder has a `README.md` index.
2. **One concern per page.** A page is either a how-to, a reference, a design
   record, an experiment, an audit, a project note, or a release note — never
   two.
3. **Every page ends with `## Sources`** listing the repository files it was
   written from, as paths (the spawn-at convention). "Sources" is the freshness
   contract.
4. **Current vs historical is explicit.** Historical pages carry a
   `Status: historical (as of <commit/date>)` line; superseded findings say
   `Superseded by D<n>`.
5. **Experiments are numbered; audits are dated.** `NN-slug.md` for experiments,
   `YYYY-MM-DD-topic.md` for audits.
6. **`doc/` is the only prose home.** Root keeps `README.md` (packaging) and
   compatibility stubs for `doc/design/spec.md`/`CHANGELOG.md`/`CODEMAP.md`/`DECISIONS.md`
   until their moves are done in one atomic commit each.

### C.2 The tree

```
doc/
├── README.md                         # index: what agy-mcp is, how to read, folder map, current-vs-historical
├── introduction.md                   # what it is, status, supported/not supported, honest limits, how to read
│
├── using/                            # task-oriented; a user can stop here
│   ├── README.md                     # reading order for users; links to reference for lookups
│   ├── requirements.md               # Python ≥3.10, agy on PATH, git, disk, OS/permissions
│   ├── install.md                    # installer (generic/dsh), manual uv install, client registration, --doctor
│   ├── update.md                     # --upgrade, editable installs, --uninstall, what is replaced
│   ├── quick-start.md                # agy_usage → agy_start → agy_wait → agy_review → merge → agy_cleanup
│   ├── concepts.md                   # job, worktree, branch, envelope, plan/facts, verify, audit, resumability
│   ├── mcp-tools.md                  # which tool when; the normal flow; error recovery (not full schemas)
│   ├── cli.md                        # `agy-mcp` launch flags and what they change
│   ├── cleanup-jobs.md               # `--list`, `--project`, `--job`, dry-run, exit codes, known limitations
│   ├── cleanup-fixtures.md           # fixture signature, dry-run, manifest, when not to use it
│   ├── configuration.md              # config file location, grouped keys with examples, fail-closed behaviour
│   ├── environment.md                # HOME/XDG/PATH, installer env, test env; exit codes summary
│   ├── files-and-paths.md            # state/config/job paths, permissions, retention/trash, what is safe to delete
│   └── troubleshooting.md            # symptom → cause → action; log locations; FAQ; error-code pointer
│
├── reference/                        # exhaustive lookup; generated where possible
│   ├── README.md                     # what each reference page covers and which code it is generated from
│   ├── cli-agy-mcp.md                # --agy-binary/--state-dir/--config/--version, defaults, exit codes
│   ├── cli-cleanup-jobs.md           # every flag, mode exclusivity, eligibility, refusals, exit codes, JSON schema
│   ├── cli-cleanup-fixtures.md       # every flag, candidate signature, refusals, exit codes, JSON schema
│   ├── mcp-tools.md                  # 10 tools: params, defaults, returns, per-tool errors, one example each
│   ├── config-keys.md                # 27 keys: type, default, range, validator, effect, D-link
│   ├── environment.md                # every variable read by src/, installer/, tests/; scope and default
│   ├── prompt-preamble.md            # link to src/agy_mcp/preamble.md (not a copy); resume state block
│   ├── exit-codes.md                 # per entry point: 0/1/2 and what each means
│   ├── errors.md                     # 40 error codes: meaning, trigger, next action
│   ├── protocol.md                   # stdio JSON-RPC, envelope schema, caps, untrusted wrapping, timeouts
│   ├── state-layout.md               # job.json (55 fields), audit.jsonl, usage.jsonl, locks, trash, snapshot
│   ├── log-formats.md                # cli.log, server.log, audit events, usage rows, verify_tail
│   └── manifests.md                  # cleanup manifest (planned/completed), fields, atomic write, JSON output
│
├── design/                           # why and what is normative
│   ├── README.md                     # map of the design section; "SPEC is normative" statement
│   ├── spec.md                       # the moved SPEC §1–§21 (contract, state machine, caps); stable §N anchors
│   ├── architecture.md               # layers, process/persistence, corrected lock order, state machine
│   ├── threat-model.md               # §5 honest model + capability table, with confidence labels
│   ├── state-machine.md              # 8 states, 20 reason-level rows, resumability, follow-up edge
│   ├── runner.md                     # Runner interface (launch/poll/kill/cleanup) and LocalProcessRunner
│   ├── modes.md                      # protected/sandbox/unrestricted design; Status: NOT BUILT
│   └── decisions/
│       ├── README.md                 # D index: topic → ID, plus chronological; D64 gap noted
│       ├── d001-d020.md              # D1–D20 verbatim (IDs stable)
│       ├── d021-d040.md              # D21–D40
│       ├── d041-d060.md              # D41–D60
│       ├── d061-d080.md              # D61–D80
│       └── d081-d100.md              # D81–D100; round narrative trimmed to rule + link to experiment page
│
├── findings-and-experiments/         # separate from docs; numbered, indexed
│   ├── README.md                     # index table (ID, date, question, verdict, status)
│   ├── findings-summary.md           # settled findings + open questions (UNSURE register)
│   ├── 01-agy-probe-1-cli-behavior.md
│   ├── 02-agy-probe-2-security.md
│   ├── 03-agy-probe-3-escapes.md
│   ├── 04-smoke-real-agy-1.md
│   ├── 05-real-dsh-runs.md
│   ├── 06-agy-capability-inventory.md
│   ├── 07-real-jobs-1.md
│   ├── 08-quota-calibration.md
│   ├── 09-test-store-contamination.md
│   ├── 10-needs-decision-investigation.md
│   ├── 11-flaky-f1-resume-race.md
│   ├── 12-cleanup-jobs-differential.md
│   ├── 13-installer-and-gate-fixes.md
│   ├── 14-fix-round-evidence-2026-10.md
│   └── evidence/README.md            # how raw evidence is named/stored and what stays in docs/reports/
│
├── audits/                           # SEPARATE folder; dated, indexed
│   ├── README.md                     # naming convention, index, what an audit is vs an experiment
│   ├── 2026-10-24-spec-review-1.md   # from DS_REVIEW_1 (F01–F36)
│   ├── 2026-10-25-code-review-2.md   # from DS_REVIEW_2 (F1–F18)
│   ├── 2026-10-26-organization-audit.md  # from AUDIT_ORGANIZATION (A1–A14), status column added
│   ├── 2026-11-01-cleanup-jobs-adversarial-review.md  # opus/gemini2/3/4/ds findings + status
│   ├── 2026-11-02-code-structure.md  # Part 1 (a–d) consolidated or linked
│   ├── 2026-11-02-documentation.md   # this audit (Part 2)
│   └── evidence/README.md            # raw review/fuzz material, sanitised; links to ~/agy-review archive
│
├── project/                          # how the project is built and tested
│   ├── README.md                     # map of the project section
│   ├── code-layout.md                # CODEMAP: module map, job flow, "where do I look" (generated block stays)
│   ├── test-plan.md                  # strategy, fixtures, fake agy, isolation, frozen tests
│   ├── test-matrix.md                # regenerated SPEC-section → test map (never hand-edited)
│   ├── comment-style.md              # docstring/comment conventions, WHAT vs WHY, phase comments
│   ├── fake-agy.md                   # 26 scenarios and how to trigger them
│   ├── history.md                    # merged CHANGELOG_SPEC + SCAFFOLD_NOTES + QUESTIONS + roadmap-done
│   ├── roadmap.md                    # future work only
│   └── orchestrator-workflow.md      # ORCHESTRATION how-to (no run reports)
│
├── release-notes/
│   ├── README.md                     # index and the rule: one page per version/maintenance series
│   ├── unreleased.md                 # user-facing rollup of CHANGELOG entries since 0.1.0.dev0
│   ├── 0.1.0-cleanup-jobs.md         # one page for D89–D100, 5–10 bullets, links to experiments/decisions
│   └── 0.1.0.md                      # first version page; add when a tag exists
│
└── developing/
    ├── README.md                     # map of the developing section
    ├── testing.md                    # commands, layout, slow tests, no polling, evidence capture
    ├── frozen-tests.md               # policy, hash manifest decision, what may move
    ├── gates.md                      # ruff, pytest, codemap --check, behavior-change, docs check
    ├── writing-docs.md               # page template, Sources rule, current/historical, link style
    ├── decisions.md                  # how to record a D-number and when to add one
    ├── evidence-and-reports.md       # experiment/audit workflow, masking, where raw evidence goes
    ├── changelog.md                  # CHANGELOG vs release-note rules
    └── scripts.md                    # 9 scripts, flags, what writes where
```

### C.3 Mapping old content → new page

| Old path | New home | Notes |
|---|---|---|
| `README.md` | stays `README.md`; body → `doc/introduction.md` + `doc/using/install.md` + links | root must stay (packaging `readme`); keep it a one-screen pointer + safety summary |
| `doc/design/spec.md` | `doc/design/spec.md` (§1–§21) + `doc/reference/{mcp-tools,config-keys,cli-cleanup-jobs,cli-cleanup-fixtures,prompt-preamble}.md` | keep §N numbering; §8/§14/§18/§22/§23 become reference pages linked from the contract |
| `CHANGELOG.md` | stays as the raw log; `doc/release-notes/*` are the curated pages | do not duplicate entry text; release notes are written, not generated |
| `AGENTS.md` | stays (agent entry point); update every path in rule 8–11 | must be edited in the same commit as any move it names |
| `CLAUDE.md` | stays | no content |
| `doc/project/code-layout.md` | `doc/project/code-layout.md` | requires `gen_codemap_index.py:27`, `test_codemap_fresh.py:50`, `AGENTS.md:32,40,50,93,110` in the same commit |
| `doc/design/decisions.md` | `doc/design/decisions/d001-d020.md` … `d081-d100.md` + index | IDs unchanged; round narrative trimmed and linked to experiment pages |
| `docs/ARCHITECTURE.md` | `doc/design/architecture.md` | fix lock order (INV-14) |
| `docs/TEST_MATRIX.md` | `doc/project/test-matrix.md` | regenerate; do not carry the 23 stale refs |
| `docs/DSH_SETUP.md` | `doc/using/install.md` + `doc/using/troubleshooting.md` + `doc/reference/mcp-tools.md` | drop "verify against dsh docs", 9-tool list |
| `docs/ORCHESTRATION.md` §1–§4 | `doc/project/orchestrator-workflow.md` | how-to only |
| `docs/ORCHESTRATION.md` §5 | `doc/findings-and-experiments/findings-summary.md` | the UNSURE register is an open-questions list |
| `docs/ORCHESTRATION.md` §6 | `doc/findings-and-experiments/05-real-dsh-runs.md` | history/experiment, not how-to |
| `docs/ROADMAP.md` done parts | `doc/project/history.md` | future items to `doc/project/roadmap.md` |
| `docs/ROADMAP.md` future items | `doc/project/roadmap.md` | remove stale `exp/*` branch names |
| `docs/QUESTIONS.md` | `doc/project/history.md` (settled Q&A) | mark Q2 superseded by D62 |
| `docs/CHANGELOG_SPEC.md` | `doc/project/history.md` (review disposition) | mark F04 historical |
| `docs/SCAFFOLD_NOTES.md` | `doc/project/history.md` (build log) | do not keep at top level; stale |
| `docs/DESIGN_MODES.md` | `doc/design/modes.md` | keep `NOT BUILT` |
| `docs/AGY_FINDINGS*.md` | `doc/findings-and-experiments/01`, `02`, `03` | one page per probe |
| `docs/SMOKE_FINDINGS.md` | `doc/findings-and-experiments/04` | fix 22 links |
| `docs/DS_REVIEW_1.md` | `doc/audits/2026-10-24-spec-review-1.md` | audit |
| `docs/DS_REVIEW_2.md` | `doc/audits/2026-10-25-code-review-2.md` | audit |
| `docs/AUDIT_ORGANIZATION.md` | `doc/audits/2026-10-26-organization-audit.md` | add status per finding |
| `docs/orchestrator/PLAN.md`, `agy-brief-test-isolation.md` | `doc/findings-and-experiments/evidence/test-isolation-2026-10/` or delete | one-off session logs |
| `docs/reports/*.md` | source for `doc/findings-and-experiments/06`–`14` | raw evidence stays in place until Harsh decides |
| `installer/agents_block.md` | stays (runtime asset); summarised in `doc/project/orchestrator-workflow.md` | embedded by the installer; tests pin the text |
| `src/agy_mcp/preamble.md` | stays (runtime asset); `doc/reference/prompt-preamble.md` links to it | SPEC §14 must link, not copy |
| `tests/fake_agy/README.md` | `doc/project/fake-agy.md` | move the prose; keep the file next to the fake binary as a pointer |
| `~/agy-review/audit/part1*` | `doc/audits/2026-11-02-code-structure.md` (or linked) | sanitise paths, keep raw evidence outside |
| `~/agy-review/audit/part2*` | `doc/audits/2026-11-02-documentation.md` | this audit; link the split parts |
| `~/agy-review/{opus,gemini2,gemini3,gemini4,ds}` | `doc/audits/2026-11-01-cleanup-jobs-adversarial-review.md` + `doc/audits/evidence/2026-11-cleanup-jobs/` | copy `FINDINGS.md` + repro scripts; do not commit 16 MB of mode-000 fixtures |

### C.4 The audits folder

```
doc/audits/
  README.md                          # what an audit is; naming; index; status legend
  2026-10-24-spec-review-1.md        # F01–F36, disposition column
  2026-10-25-code-review-2.md        # F1–F18
  2026-10-26-organization-audit.md   # A1–A14 + status (A3 fixed, A4 resolved)
  2026-11-01-cleanup-jobs-adversarial-review.md  # five-reviewer adversarial register
  2026-11-02-code-structure.md       # Part 1
  2026-11-02-documentation.md        # Part 2 (this audit)
  evidence/README.md                 # where the sanitised artifacts live
```

The 2026-11-01 page is the register for the five-reviewer adversarial work:
opus attacks 01–16, gemini2 (commondir deleted, `worktree_path` unregistered),
gemini3 (NUL/BOM `HEAD`/`config`, concurrency), gemini4 (depth bomb, duplicate
keys, unreadable store) and ds (empty `--store`, pgid, recursion limit). Each
finding gets severity, repro command, fail-open result, and the decision that
fixed it (`D90`–`D100`) or `open`. The raw `FINDINGS.md` and repro scripts are
copied under `audits/evidence/`; the 16 MB of fixture trees stay outside git.

Rules:

- Name: `YYYY-MM-DD-topic.md`, topic in kebab-case.
- Every audit starts with `Revision: <sha>`, `Mode: read-only|fixing`, `Scope:`.
- Every finding has `ID | severity | evidence | action | risk | effort`.
- Close-out: an audit page is never edited to change findings; a later audit
  supersedes it and the index says so. Status is added in a `## Status` section.
- Evidence: small text/repro files may be committed under
  `audits/evidence/<date>-<topic>/`; multi-megabyte fixtures stay in the
  external archive and the page names the path.

### C.5 Is a site generator worth it later?

**No, not now.** Reasons, in order:

1. `doc/` will be ~70 pages of plain markdown; GitHub renders it with folder
   navigation, relative links and search-in-file. A generator adds a build
   artifact and a second source of truth for navigation.
2. There is no CI (`.github` absent, Part 1 G4). A site build would be another
   unenforced gate; the repository already fails to run `ruff`/`pytest`
   automatically.
3. Two tests and a script hard-code the docs location
   (`gen_codemap_index.py:27`, `test_codemap_fresh.py:50`); a generator would
   add a third consumer of the same paths before the first two are stable.
4. The audience is Harsh and AI agents reading the repository, not external
   readers. Search and versioned docs are not problems yet.

**Revisit when** any of these becomes true: >100 pages, an external audience,
a need for versioned docs per release, or a search feature. Then use **MkDocs
Material** (`mkdocs.yml` with `docs_dir: doc`, Python-only, already a Python
repo) rather than Starlight (needs Node, a build step, and a JS toolchain this
repository does not have). Starlight remains the right choice only if the docs
are published as a marketing/landing site with custom components.

---

## D. Findings for this part

| ID | Sev | Finding | Evidence | Proposed action | Risk | Effort |
|---|---|---|---|---|---|---|
| EXP-01 | H | Experiments/findings live in three homes with four naming schemes and no index | `docs/AGY_FINDINGS*`, `docs/reports/`, `~/agy-review/`; counts in B.1 | Create `doc/findings-and-experiments/` with the 14-page index in B.4 | Moving pages can break links; rewrite in the same commit | L |
| EXP-02 | H | 17 of 23 report directories have no narrative; the cleanup work has no committed evidence at all | per-dir markdown count; `ls docs/reports` has no cleanup dir; `du ~/agy-review` 16 MB | Write the 14 experiment pages; keep raw logs as evidence; archive the rest by decision | Writing pages takes review; do not invent numbers | L |
| EXP-03 | M | The repros and harnesses that prove D90–D100 are uncommitted, under absolute paths and mode-000 dirs | `~/agy-review/{opus,gemini2,gemini3,gemini4,ds}`; `du` shows 6 permission-denied subtrees | Commit the `FINDINGS.md` + small repro scripts, sanitised; archive the fixtures | Mode-000 dirs and personal paths must not enter git | M |
| EXP-04 | M | Experiment conclusions go stale without a marker (capability inventory's `using` bug; 339-record store) | `agy-capability-inventory.md:29` vs D81; E09/E10 samples | Add `Status: current/superseded by Dn` to every page; index shows it | Low | S |
| EXP-05 | L | Experiment file names encode author/round, not topic (`fixes2`…`fixes10`, `attack_01`) | `ls docs/reports`, `ls ~/agy-review/opus` | Renumber by topic in `doc/findings-and-experiments/`; keep old paths only in the mapping | Links; fix with the index | S |
| STR-01 | H | No index and no reading order: 20 prose files in one flat folder, no current/historical marker | `ls docs` (20 `.md`) | `doc/README.md` + per-folder README with a current-vs-historical table | Low | S |
| STR-02 | M | `docs/` mixes six concerns in one flat folder | inventory table §2.2–§2.4 | Adopt the tree in C.2 | None if done in waves | M |
| STR-03 | M | Audits have no folder, naming or status convention; three audits in three formats | `DS_REVIEW_1/2`, `AUDIT_ORGANIZATION`; no `audits/` dir | `doc/audits/` with `YYYY-MM-DD-topic.md`, index, status legend | Low | S |
| STR-04 | L | No page-level "Sources" convention, so drift is invisible | stale line refs in `ARCHITECTURE.md:62`; `CODEMAP.md:5` | Template + `## Sources` on every page; check in `developing` | Low | S |

**Part 2b counts: 9 findings — 3 high, 4 medium, 2 low.**

## Sources

- `doc/audits/2026-11-02-part2-index.md`
- `src/agy_mcp/`
- `docs/reports/`
- `tests/`
