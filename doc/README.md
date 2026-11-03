# agy-mcp documentation

Status: current
Audience: users, orchestrators, contributors

`agy-mcp` is an MCP stdio server that orchestrates autonomous Google
Antigravity (`agy`) CLI coding jobs on the local host. For each job it creates
an isolated git worktree, launches `agy` in its own process group, supervises
it, runs optional verification, and returns a standard envelope through ten MCP
tools. The wrapper never merges or pushes branches.

This folder is the organised documentation home. It is being built in phases.
Phase 1 added the index, writing conventions, development gates and
operator reference pages. Phase 2 moved core design and project prose
into `doc/design/`, `doc/project/` and `doc/using/`.

## Reading order

1. Open [the root README](../README.md) for install, the MCP tool list and the
   safety model.
2. Read [the installation guide](using/install.md) for setup and MCP registration.
3. Read [the reference index](reference/README.md) when you need an exact
   answer: an error code, an environment variable, an exit status, a config key,
   a tool parameter, a state file, a log format or a manifest.
4. Read [the normative specification](design/spec.md), the [multi-account design](design/multi-account.md), and
   [the architecture overview](design/architecture.md) for design contracts.
5. Read [the decisions log](design/decisions.md) for architectural choices (D1–D114).
6. Read [the code layout](project/code-layout.md) for the module map and job flow.
7. Read [the writing conventions](developing/writing-docs.md) and
   [the gates](developing/gates.md) before changing code or documentation.
8. Read [the Part 2 audit](audits/2026-11-02-part2-index.md) for the evidence
   behind this structure and the migration order.

## Folder map

Folders marked **exists** have live documentation pages. Folders marked
**planned** are the target layout from the Part 2 audit and are populated in later
phases.

| Folder | State | Holds |
|---|---|---|
| [`using/`](using/install.md) | exists | Task-oriented user guides: [install](using/install.md), configuration, troubleshooting. |
| [`reference/`](reference/README.md) | exists | Exhaustive lookup generated or checked from the code: tools, errors, environment, config, state, logs, manifests, CLIs. |
| [`design/`](design/architecture.md) | exists | Normative design pages and the decision archive: [spec](design/spec.md), [architecture](design/architecture.md), [modes](design/modes.md), [decisions](design/decisions.md), [multi-account design](design/multi-account.md). |
| [`findings-and-experiments/`](findings-and-experiments/README.md) | exists | Numbered experiment pages (01–15), [findings summary](findings-and-experiments/findings-summary.md), and [evidence index](findings-and-experiments/evidence/README.md). |
| [`audits/`](audits/README.md) | exists | Dated audits of the repository, with evidence. See [audits/README.md](audits/README.md). |
| [`project/`](project/code-layout.md) | exists | Code layout ([code-layout.md](project/code-layout.md)), orchestrator workflow ([orchestrator-workflow.md](project/orchestrator-workflow.md)), fake-agy guide ([fake-agy.md](project/fake-agy.md)), history ([history.md](project/history.md)), roadmap ([roadmap.md](project/roadmap.md)). |
| `release-notes/` | planned | One user-facing page per version or maintenance series. `CHANGELOG.md` stays the raw log at repo root. |
| [`developing/`](developing/writing-docs.md) | exists | How to write a page, where a new finding or audit goes, and every gate with its exact command. |

```text
doc/
├── README.md                  # this index
├── using/                     # exists: install and user guides
│   └── install.md
├── reference/                 # exists: exact answers, generated where possible
│   ├── README.md
│   ├── cli-cleanup-fixtures.md
│   ├── cli-cleanup-jobs.md
│   ├── cli-scripts.md
│   ├── config-keys.md
│   ├── environment.md
│   ├── errors.md
│   ├── exit-codes.md
│   ├── log-formats.md
│   ├── manifests.md
│   ├── mcp-tools.md
│   └── state-layout.md
├── design/                    # exists: normative contracts and design
│   ├── architecture.md
│   ├── decisions.md
│   ├── modes.md
│   └── spec.md
├── findings-and-experiments/  # exists: experiment records, findings summary, evidence
│   ├── README.md
│   ├── findings-summary.md
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
│   └── evidence/README.md
├── project/                   # exists: codebase map, testing, history, roadmap
│   ├── code-layout.md
│   ├── fake-agy.md
│   ├── history.md
│   ├── orchestrator-workflow.md
│   └── roadmap.md
├── developing/                # exists: conventions and gates
│   ├── gates.md
│   └── writing-docs.md
└── audits/                    # exists: dated audits and evidence
    ├── README.md
    ├── 2026-11-02-part1-index.md
    ├── 2026-11-02-part2-index.md
    ├── ... (part1a-d, part2a-d)
    └── evidence/              # 2026-11-02-part1, 2026-11-cleanup-jobs
```

## What is deliberately not moved

These items remain in place until later phases:

- Release notes (`CHANGELOG.md` remains the raw log at repository root).
- The `docs/reports/` evidence archive (deferred to later archive phase).
- `docs/orchestrator/PLAN.md` (a **consumer-project** path named by
  `tests/test_installer_chapter.py` and `installer/agents_block.md`; keeping it
  preserves the consumer guidance contract).
- Experiment probe logs (`docs/AGY_FINDINGS*.md`, `docs/SMOKE_FINDINGS.md`) and
  earlier audits (`docs/DS_REVIEW_*.md`, `docs/AUDIT_ORGANIZATION.md`), scheduled
  for phases 3 and 4.

## Keeping this index honest

A page is linked only when it exists. `scripts/check_docs.py` enforces relative
links, a `## Sources` footer on every non-index page, and freshness of the
generated reference tables; [the gates page](developing/gates.md) lists its exact
command. A planned folder appears here as text, never as a broken link.

## Sources

- `README.md` — the product description and the tool list this order points to.
- `AGENTS.md` — the repository commands and the rule that new findings and
  audits get a page.
- `doc/design/spec.md` — the normative contract referenced by every design pointer.
- `doc/audits/2026-11-02-part2-index.md` — the verdict, the reading-order goal
  and the "what to do first" sequence.
- `doc/audits/2026-11-02-part2b-target-structure.md` — the target tree and the
  folder responsibilities.
- `doc/audits/2026-11-02-part2d-migration.md` — the "do not move yet" list and
  the consumer-project path warning.
- `scripts/check_docs.py` — the link, Sources and generated-table checks.
