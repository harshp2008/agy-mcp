# Multi-account support: investigation, design and implementation record

Status: current
Audience: maintainers, contributors, researchers

Working record for adding multi-account support to `agy-mcp`: the baseline the
work started from, the investigation that established the facts, the design and
its open questions, then the phase-by-phase implementation reports. Start with
[PROGRESS.md](PROGRESS.md) for the current phase and commit.

## Documents

| Page | Answers |
|---|---|
| [PROGRESS.md](PROGRESS.md) | Which phase is done, at which commit, with which gate results |
| [00-baseline.md](00-baseline.md) | What the repository looked like before Phase 3: HEAD, tests, gates |
| 01-investigation.md | Every single-account assumption, mapped to a file and a required change |
| 02-code-map.md | File:line table of env, HOME, state, usage, worktree, installer touchpoints |
| 03-experiments.md | What the fake agy supports and what the real-agy experiments showed |
| 04-implementation-review.md | What was built, deviations, residual risks, unverified items |

`01`, `02`, `03` and `04` are created by Phases 1, 1 and 9; this index lists them
before they exist so the intended structure is explicit.

## Evidence

Raw command output is under [evidence/](evidence/), trimmed and with account
emails masked. Files are named after the claim they support:

- [evidence/baseline-gates.txt](evidence/baseline-gates.txt) — Phase 0 gate output.
- [evidence/baseline-tests.txt](evidence/baseline-tests.txt) — Phase 0 test counts and timings.

The real-agy call log, required by the phase rules, lives at
`evidence/real-agy-calls.md` and is created by Phase 1.

## Findings and experiments

The user-facing experiment summary is
[doc/findings-and-experiments/15-multi-account-recon.md](../../findings-and-experiments/15-multi-account-recon.md),
created by Phase 1.
