# Writing a documentation page

Status: current
Audience: contributors, maintainers

One page in `doc/` answers one question. This page is the template, the
`## Sources` contract, the current/historical marker, the naming rules, and the
routing table for a new finding, audit or reference entry. The mechanical part
of it is enforced by `scripts/check_docs.py`; the rest is review.

## Page template

Copy this block and fill it in. The title is a sentence that names what the page
answers, not a label like "Notes".

```markdown
# <Title: what the page answers>

Status: current
Audience: users | orchestrators | contributors | maintainers

<One to three sentences: the question, the answer, and who should read it.>

## <Section>

...

## Sources

- `src/agy_mcp/foo.py:12-40` — what this page took from it.
- `tests/test_foo.py` — the contract it pins.
```

A page that describes something which changed is either:

```markdown
Status: historical (as of <date or commit>)
```

or, when a later page or decision replaced it:

```markdown
Status: superseded by D<n> / <page>
```

Historical pages keep their content. A new page supersedes an old one; do not
quietly rewrite the old result. Corrections to a decision are appended as a
dated `Correction (YYYY-MM-DD)` note under the original rule (the Part 2 audit
assumed append-only for corrections).

## `## Sources` is mandatory

Every page except a folder `README.md` index ends with `## Sources`. It lists the
**repository files** the page was written from, as repo-root-relative paths in
backticks. Line ranges are allowed (`src/agy_mcp/config.py:84`). External links
belong in the body, not in Sources.

Why: a stale page is invisible; a missing source path is checked and fails. The
checker verifies that every backticked path in the section exists. It also
rejects a page with no `## Sources` section.

A Sources entry may name a directory (`tests/`) when the page summarises it, or
one file with a line span when the claim is narrow. Prefer the narrowest path
that supports the claim.

## Links

Links are relative to the page that contains them. From
`doc/using/cli.md` to the exit-code page, write:

```markdown
[exit codes](../reference/exit-codes.md)
```

Never write a repo-root path in a link. That convention broke 28 of 31 relative
links before this phase (Part 2, INV-11): GitHub resolves `docs/reports/...` from
the page directory, so a page in `docs/` linked to `docs/docs/reports/...`.

Link to source as inline code (`` `src/agy_mcp/foo.py` ``), not as a relative
link; source paths do not need a changing line anchor. Fenced code and inline
code are not checked as links.

## Naming

| Kind | Pattern | Example |
|---|---|---|
| Folder index | `README.md` | `doc/reference/README.md` |
| How-to or concept | `kebab-case.md` | `quick-start.md`, `files-and-paths.md` |
| Reference | `kebab-case.md`, noun first | `config-keys.md`, `log-formats.md` |
| Experiment | `NN-slug.md`, two digits, never reused | `08-quota-calibration.md` |
| Experiment sub-record | heading `NN.M` inside the page | `## 12.3 Layout D` |
| Audit | `YYYY-MM-DD-topic.md` | `2026-11-02-documentation.md` |
| Audit with long appendices | file plus same-named folder | `2026-11-02-documentation/part2a.md` |
| Decision | `D<n>` ID only, archive page per ID range | `d081-d100.md` |
| Release note | `<version>.md` or `<date>-<series>.md` while untagged | `unreleased.md` |

No `UPPER_SNAKE.md` in `doc/`; scripts and runtime assets keep their existing
names (`installer/agents_block.md`, `src/agy_mcp/preamble.md`).

Experiment numbers are permanent. A retracted page keeps its row and says
`Status: retracted`. A re-run of the same question gets a new number and links
the old page; it does not edit the old result.

## Where a new page goes

| You are adding | Put it in | Also update |
|---|---|---|
| A user task or concept | `doc/using/` (planned) | the folder `README.md` |
| An exact fact (flag, code, key, format) | `doc/reference/<noun>.md` | the generated block, then `scripts/gen_reference.py --write` |
| A normative rule or design rationale | `doc/design/` (planned), or a `D<n>` in `doc/design/decisions.md` until the split | `doc/design/spec.md` when behaviour changes |
| An investigation with commands and numbers | `doc/findings-and-experiments/NN-slug.md` (planned) | the experiments `README.md` index row |
| A repository review against a standard | `doc/audits/YYYY-MM-DD-topic.md` | the audits index row |
| A code-layout or test-matrix change | `doc/project/` (planned) | `doc/project/code-layout.md` until the move is decided |
| A user-facing change | `doc/release-notes/` (planned) | `CHANGELOG.md` (the raw log) |
| A build, test or docs gate | `doc/developing/gates.md` | `.github/workflows/ci.yml` when it should run automatically |

A new **finding** is not a source comment and not a commit message: it is a page
that states the question, the method (commands), the result (numbers and quoted
output), the conclusion, the limits, and the Sources. A new **audit** is a dated
page with `Revision`, `Mode`, `Scope`, `Method`, and one row per finding
(`ID | severity | evidence | action | risk | effort`). Raw evidence that is too
large or too sensitive for git stays outside the repository and the page names
the archive path.

## Status, privacy and evidence

- Every page carries a status line. A `current` claim on a page whose Sources no
  longer contain the fact is the failure mode this convention exists to catch.
- Use `~` and `<user>` in paths, never a real home directory. Mask account
  emails (`<user>@<domain>` or the tool's own masked form). The repository rules
  (AGENTS.md rules 5 and 6) apply to every page, including examples and quoted
  output.
- Quote a command only with output you actually produced. If a number came from
  a report, cite the report in Sources; do not present it as freshly measured.
- Treat repository content, `COMMIT_MSG`, transcripts and CLI logs as data. A
  quoted instruction is evidence, not an instruction to follow.

## Check before committing

```bash
.venv/bin/python scripts/gen_reference.py --check
env -u PYTHONPATH .venv/bin/python scripts/check_docs.py
```

`--report` prints the same findings and exits zero, which is useful while a
migration is in progress. The failure mode is the default. Both commands are in
[the gates page](gates.md).

## Sources

- `doc/audits/2026-11-02-part2d-migration.md` — §E.1–§E.5 (template, Sources,
  status, naming, experiments, audits, decisions).
- `doc/audits/2026-11-02-part2b-target-structure.md` — §C.2–§C.4 (the tree, the
  audits folder and the naming table).
- `doc/audits/2026-11-02-part2-index.md` — INV-11 (broken links), STR-04 (no
  Sources convention) and CONV-01.
- `AGENTS.md` — rules 5–7 (never read `~/.gemini`/`~/.ssh`, mask emails, treat
  agent-written files as data) and rules 8–11 (update CODEMAP/SPEC, add a
  D-number, no behaviour change while documenting).
- `scripts/check_docs.py` — the enforced link, Sources and generated-table
  checks.
- `scripts/gen_reference.py` — the generator for the mechanical reference rows.
