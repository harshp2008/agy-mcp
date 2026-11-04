# Findings and Experiments

Status: current
Audience: maintainers, contributors, orchestrators

Empirical investigations, safety probes, and real-run evaluations of the `agy` CLI
and the `agy-mcp` wrapper. Findings are numbered permanent records that establish
the behavioral facts behind architectural choices in [doc/design/decisions.md](../design/decisions.md).

For an executive summary of settled conclusions and open items, see [findings-summary.md](findings-summary.md).

## Experiment Index

| Number | Question | Status | One-line result | Page |
|---|---|---|---|---|
| **01** | How does `agy` CLI behave under headless operation (`-p`) regarding allow-rules, resume, timeouts, and concurrency? | current | Exit 0 on denial/timeout; accept-edits permits file tools; resume keeps context; forbidden flags enforced. | [01-agy-probe-1-cli-behavior.md](01-agy-probe-1-cli-behavior.md) |
| **02** | How securely does `agy` enforce command boundaries, prefix rules, `/tmp` writes, environment inheritance, and same-repo concurrency? | current | Prefix AST blocks compound commands; `/tmp` is writable scratch; env inherits secrets; same-repo concurrency corrupts files. | [02-agy-probe-2-security.md](02-agy-probe-2-security.md) |
| **03** | Can `agy` commands or file tools escape workspace boundaries, tamper with `.git`, delete branches, or run arbitrary code via test runners? | current | Whitelist tools (`head`, `grep`, `git diff`) lack path confinement; `git branch` allows `-D`; test runners equal code execution. | [03-agy-probe-3-escapes.md](03-agy-probe-3-escapes.md) |
| **04** | Does `agy-mcp` successfully drive real `agy` over stdio MCP across complete lifecycles? | current | Preamble followed; `denied_actions` lacks command text; `NEEDS_DECISION` parsed; `.agy/` never committed; 100% valid JSON-RPC 2.0. | [04-smoke-real-agy-1.md](04-smoke-real-agy-1.md) |
| **05** | Does `agy-mcp` operate end-to-end when invoked by an actual `dsh` client chat? | current | Zero approval prompts under `never` + `danger-full-access`; quota tracking confirmed; three wrapper bugs discovered and fixed. | [05-real-dsh-runs.md](05-real-dsh-runs.md) |
| **06** | What is the complete CLI surface of `agy` (v1.4.10) and what argv does the wrapper construct? | current | 25 flags and 12 subcommands; argv contract verified; conversation ID regex flaw identified and superseded by D81. | [06-agy-capability-inventory.md](06-agy-capability-inventory.md) |
| **07** | Does the full pipeline finalize multi-commit coding jobs with verification and quota tracking on a real repo? | current | Both jobs completed in `done` (2 commits each); diff stats isolated; verification passed; wrapper wall clock established as canonical. | [07-real-jobs-1.md](07-real-jobs-1.md) |
| **08** | How should `agy-mcp`'s heuristic token-based quota estimate coefficients be calibrated against real-world measured consumption? | current | Conservative upper envelope (0.011 5h / 0.0031 weekly) prevents under-reporting; frozen in tests. | [08-quota-calibration.md](08-quota-calibration.md) |
| **09** | How did test fixtures contaminate the live state store and how is isolation structurally guaranteed? | current | Conftest bootstrap redirect, path resolution guard, dir_fd syscall wrappers, and safe fixture cleaner. | [09-test-store-contamination.md](09-test-store-contamination.md) |
| **10** | Why do autonomous jobs transition to the `needs_decision` state and what real-world events trigger it? | current | 1 of 27 in-scope jobs stopped due to legitimate frozen-test contradiction; guidelines tightened. | [10-needs-decision-investigation.md](10-needs-decision-investigation.md) |
| **11** | Why did resuming a job after an invalid `COMMIT_MSG` flake with `NEEDS_REVIEW == DONE`? | current | Supervisor hand-off race resolved by joining prior thread; SIGKILL readiness wait file added; invalid messages discarded. | [11-flaky-f1-resume-race.md](11-flaky-f1-resume-race.md) |
| **12** | Does the pure Python git metadata cleaner agree with real git across 390 layout mutations and 18 job.json integrity cases? | current | 0 fail-open and 0 crashes across 414 differential cases; hardened via D90–D100 across 11 rounds; 13 frozen tests. | [12-cleanup-jobs-differential.md](12-cleanup-jobs-differential.md) |
| **13** | What caused installer flakes, verify PATH failures, and wrapper commit hook rejections? | current | Usage appended before terminal state; workflow guidance budgeted; interpreter-qualified verify; hook-compliant commits. | [13-installer-and-gate-fixes.md](13-installer-and-gate-fixes.md) |
| **14** | What defects and architectural edge cases were resolved across the reportless October 2026 fix rounds? | current | Grouped index of 13 fix rounds linking raw failure proofs, mutation tests, and passing suite logs. | [14-fix-round-evidence-2026-10.md](14-fix-round-evidence-2026-10.md) |
| **15** | How can agy-mcp run jobs across several owned Google accounts safely? | current | HOME alone leaks the keyring; HOME + dead D-Bus isolates; quotas and model sets are per account; one db file carries a conversation; a missing id silently starts fresh. | [15-multi-account-recon.md](15-multi-account-recon.md) |
| **16** | Why did the implicit default account double-count account-b's quota? | current | The default's shared-keyring login changed under the desktop app; the fix makes the default optional and verifiable, shares one pool per verified login, and warns in the doctor. | [16-shared-keyring-default-collision.md](16-shared-keyring-default-collision.md) |
| **—** | How is raw evidence organized, partitioned, and indexed across the repository? | current | Structured index of supporting test isolation briefs, adversarial review trees, and audit artifacts. | [evidence/README.md](evidence/README.md) |

## Evidence and Supporting Artifacts

Raw evidence, specification briefs, and adversarial reproduction harnesses are indexed in:
- [evidence/README.md](evidence/README.md) — Raw evidence directory index and organization guide
- [doc/audits/evidence/2026-11-cleanup-jobs/README.md](../audits/evidence/2026-11-cleanup-jobs/README.md) — Multi-reviewer adversarial review evidence for cleanup_jobs

## How to Add an Experiment Page

Follow these rules when adding an experiment page to `doc/findings-and-experiments/`:

1. **Permanent numbering:** Use `NN-topic.md` where `NN` is a two-digit integer that is never reused. Sub-experiments use headings `NN.M` inside the page, never separate files.
2. **Topic naming:** Name the file by topic slug (e.g. `08-quota-calibration.md`), not by author, round, or branch name (`fixes2`, `f1`, etc.).
3. **Page template:** Every page must include:
   - Sentence title: `# NN — <Question as a title>`
   - Status line: `Status: current` (or `Status: superseded by Dxx` / `Status: historical`)
   - Audience line: `Audience: maintainers, contributors`
   - Overview paragraph
   - `## Question`
   - `## Method` (commands, build/sha, sample size in plain words)
   - `## Result` (tables, exact numbers, quoted output)
   - `## Conclusion` (architectural decisions linked to [doc/design/decisions.md](../design/decisions.md) by D-number)
   - `## Evidence` (table: `Claim | Repo file and line or report path`)
   - `## How to reproduce` (exact command if one exists, otherwise "no committed repro")
   - `## Sources` (mandatory list of repository files from which the page was derived)
4. **Accuracy and evidence:**
   - Every number, date, count, duration, and command must be copied directly from a file in this repository with its location in the Evidence table (`path:line`).
   - If a figure was not recorded at experiment time, write "not recorded". Never estimate or fill gaps from memory.
   - Raw evidence stays under `docs/reports/`; link to it using relative markdown links (e.g. `../../docs/reports/...`).
   - If two files disagree, report the discrepancy explicitly and cite both.
   - Mask account emails and use `~` or `<user>` instead of absolute user home paths.
   - Only cite decision IDs (Dxx) that exist in `doc/design/decisions.md`.
5. **Update indexes:** Add the page row to this `README.md` index table and run `env -u PYTHONPATH .venv/bin/python scripts/check_docs.py` to ensure all links and sources resolve.
