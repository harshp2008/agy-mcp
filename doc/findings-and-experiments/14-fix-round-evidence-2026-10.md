# 14 — Grouped Index of October 2026 Fix Rounds and Verification Evidence

Status: current
Audience: maintainers, contributors

Comprehensive index and empirical summary of the thirteen reportless test-driven
bug fix, stabilization, and security hardening rounds conducted between 2026-10-24
and 2026-10-26, indexing raw failure proofs, mutation tests, and passing test suite
evidence under `docs/reports/`.

## Question

What defects and architectural edge cases were resolved across the reportless
October 2026 fix rounds, what test-first and mutation evidence corroborated each
resolution, and where do raw log artifacts reside in the repository?

## Method

Between 2026-10-24 and 2026-10-26, thirteen successive fix rounds addressed
findings from initial security reviews (DS Review 1, DS Review 2, and Organization Audit).
Each round followed a rigorous test-first protocol:
1. Capture a clean full-suite baseline (`baseline-pytest.txt` or `baseline-full.txt`).
2. Author an isolated test reproducing the defect and capture the raw failure (`*-fail.txt` or `*-failing.txt`).
3. Implement the minimal fix in production code and record the passing test output (`*-pass.txt` or `*-passing.txt`).
4. Perform a mutation check by reverting or corrupting the fix to prove the test re-fails (`*-mutation.txt`).
5. Execute the full test suite (`pytest`), linting (`ruff`), and AST behavior comparisons (`check_no_behavior_change.py`).

Raw artifacts were committed directly under `docs/reports/<round>/` without prose reports.
This page establishes the permanent index connecting each round's question, artifacts,
and verdict.

## Result

### 14.1 Review 1 Batch 1 (`docs/reports/fix-batch1/`)
- **Question:** How do ten critical Review 1 defects (F1, F3, F4, F11, F12, F13, F15, F20, F21, F23) behave under test-first reproduction and mutation?
- **Evidence Files:**
  - `F1` (invalid `COMMIT_MSG` kill and resume): [f1-fail.txt](../../docs/reports/fix-batch1/f1-fail.txt), [f1-pass.txt](../../docs/reports/fix-batch1/f1-pass.txt), [f1-mutation.txt](../../docs/reports/fix-batch1/f1-mutation.txt)
  - `F3` (symlinked `.git` safety): [f3-fail.txt](../../docs/reports/fix-batch1/f3-fail.txt), [f3-pass.txt](../../docs/reports/fix-batch1/f3-pass.txt), [f3-mutation.txt](../../docs/reports/fix-batch1/f3-mutation.txt)
  - `F4` (state-directory boundary escapes): [f4-fail.txt](../../docs/reports/fix-batch1/f4-fail.txt), [f4-pass.txt](../../docs/reports/fix-batch1/f4-pass.txt), [f4-mutation.txt](../../docs/reports/fix-batch1/f4-mutation.txt)
  - `F11` (untrusted agent report wrapper escaping): [f11-fail.txt](../../docs/reports/fix-batch1/f11-fail.txt), [f11-pass.txt](../../docs/reports/fix-batch1/f11-pass.txt), [f11-mutation.txt](../../docs/reports/fix-batch1/f11-mutation.txt)
  - `F12` (git status file modification parsing): [f12-fail.txt](../../docs/reports/fix-batch1/f12-fail.txt), [f12-pass.txt](../../docs/reports/fix-batch1/f12-pass.txt), [f12-mutation.txt](../../docs/reports/fix-batch1/f12-mutation.txt)
  - `F13` (stale PID and process group cleanup): [f13-fail.txt](../../docs/reports/fix-batch1/f13-fail.txt), [f13-pass.txt](../../docs/reports/fix-batch1/f13-pass.txt), [f13-mutation.txt](../../docs/reports/fix-batch1/f13-mutation.txt)
  - `F15` (verification auto-fix loops): [f15-fail.txt](../../docs/reports/fix-batch1/f15-fail.txt), [f15-pass.txt](../../docs/reports/fix-batch1/f15-pass.txt), [f15-mutation.txt](../../docs/reports/fix-batch1/f15-mutation.txt)
  - `F20` (denial recovery prompt construction): [f20-fail.txt](../../docs/reports/fix-batch1/f20-fail.txt), [f20-pass.txt](../../docs/reports/fix-batch1/f20-pass.txt), [f20-mutation.txt](../../docs/reports/fix-batch1/f20-mutation.txt)
  - `F21` (atomic job state replacement): [f21-fail.txt](../../docs/reports/fix-batch1/f21-fail.txt), [f21-pass.txt](../../docs/reports/fix-batch1/f21-pass.txt), [f21-mutation.txt](../../docs/reports/fix-batch1/f21-mutation.txt)
  - `F23` (preflight branch conflict checks): [f23-fail.txt](../../docs/reports/fix-batch1/f23-fail.txt), [f23-pass.txt](../../docs/reports/fix-batch1/f23-pass.txt), [f23-mutation.txt](../../docs/reports/fix-batch1/f23-mutation.txt)
  - Stability loop: [t4-loop.txt](../../docs/reports/fix-batch1/t4-loop.txt) (100 passes across 3 auto-fix scenarios)
- **Verdict:** All 10 defects resolved; full suite passed with 380 passed in 144.91s ([pytest.txt](../../docs/reports/fix-batch1/pytest.txt)); ruff clean ([ruff.txt](../../docs/reports/fix-batch1/ruff.txt)).

### 14.2 Review 1 Batch 2 (`docs/reports/fix-batch2/`)
- **Question:** Do Review 1 items F2, F5–F10, F14, F16–F19, F22, F24–F26 pass full regression gates without regressions?
- **Evidence Files:** [baseline-pytest.txt](../../docs/reports/fix-batch2/baseline-pytest.txt) (380 passed in 157.92s), [pytest.txt](../../docs/reports/fix-batch2/pytest.txt) (405 passed in 148.22s), [ruff.txt](../../docs/reports/fix-batch2/ruff.txt).
- **Verdict:** Suite expanded by 25 tests; 405 passed cleanly.

### 14.3 Review 1 Batch 2b (`docs/reports/fix-batch2b/`)
- **Question:** How do commit watcher CPU and I/O profiles perform under idle vs high-frequency commit generation?
- **Evidence Files:**
  - Load profiling: [item4-load-manager.txt](../../docs/reports/fix-batch2b/item4-load-manager.txt), [item4-load-watcher.txt](../../docs/reports/fix-batch2b/item4-load-watcher.txt)
  - Idle profiling: [item4-idle-manager.txt](../../docs/reports/fix-batch2b/item4-idle-manager.txt), [item4-idle-watcher.txt](../../docs/reports/fix-batch2b/item4-idle-watcher.txt)
  - Suite gates: [baseline-pytest.txt](../../docs/reports/fix-batch2b/baseline-pytest.txt) (405 passed in 147.21s), [pytest.txt](../../docs/reports/fix-batch2b/pytest.txt) (408 passed in 92.61s), [ruff.txt](../../docs/reports/fix-batch2b/ruff.txt).
- **Verdict:** Watcher sleep intervals confirmed stable; 408 passed.

### 14.4 Review 1 Batch 3a (`docs/reports/fix-batch3a/`)
- **Question:** Does batch 3a (addressing items F27–F31 on cancellation, cleanup race conditions, and error envelopes) pass full gates?
- **Evidence Files:** [baseline-pytest.txt](../../docs/reports/fix-batch3a/baseline-pytest.txt) (408 passed in 148.11s), [final-pytest.txt](../../docs/reports/fix-batch3a/final-pytest.txt) (436 passed in 99.31s).
- **Verdict:** 28 tests added; 436 passed cleanly.

### 14.5 Review 1 Batch 3b (`docs/reports/fix-batch3b/`)
- **Question:** Does batch 3b (addressing items F32–F36 on git branch sanitization, transcript reading isolation, and privacy masking) pass verification?
- **Evidence Files:** [baseline-pytest.txt](../../docs/reports/fix-batch3b/baseline-pytest.txt) (436 passed in 144.38s), [final-pytest.txt](../../docs/reports/fix-batch3b/final-pytest.txt) (492 passed in 134.74s), [privacy-grep.txt](../../docs/reports/fix-batch3b/privacy-grep.txt), [commits-and-status.txt](../../docs/reports/fix-batch3b/commits-and-status.txt), [end-timestamp.txt](../../docs/reports/fix-batch3b/end-timestamp.txt), [ruff.txt](../../docs/reports/fix-batch3b/ruff.txt).
- **Verdict:** 56 tests added; 492 passed cleanly; privacy scan confirmed 0 unmasked email addresses.

### 14.6 Review 1 Batch 4 (`docs/reports/fix-batch4/`)
- **Question:** Does batch 4 (.agy provisioning in production launch, F24) resolve worktree startup and pass full-suite stability?
- **Evidence Files:** [baseline-pytest.txt](../../docs/reports/fix-batch4/baseline-pytest.txt) (492 passed in 138.83s), [full-pytest-after-items.txt](../../docs/reports/fix-batch4/full-pytest-after-items.txt), [final-pytest.txt](../../docs/reports/fix-batch4/final-pytest.txt) (560 passed in 141.18s).
- **Verdict:** 68 tests added; 560 passed cleanly.

### 14.7 agy-mcp Fixes Round 2 (`docs/reports/agymcp-fixes-2/`)
- **Question:** Do items A1–A3, B, and C1–C4 (merge reachability checks, status envelope equivalence, unique branch naming, editable installer upgrades, and usage ordering) hold under mutations?
- **Evidence Files:**
  - `A1` (merge reachability gate): [a1-failing.txt](../../docs/reports/agymcp-fixes-2/a1-failing.txt), [a1-passing.txt](../../docs/reports/agymcp-fixes-2/a1-passing.txt), [a1-mutation.txt](../../docs/reports/agymcp-fixes-2/a1-mutation.txt)
  - `A2` (envelope equivalence across tools): [a2-failing.txt](../../docs/reports/agymcp-fixes-2/a2-failing.txt), [a2-passing.txt](../../docs/reports/agymcp-fixes-2/a2-passing.txt), [a2-mutation.txt](../../docs/reports/agymcp-fixes-2/a2-mutation.txt)
  - `A3` (unique branch naming per start): [a3-failing.txt](../../docs/reports/agymcp-fixes-2/a3-failing.txt), [a3-passing.txt](../../docs/reports/agymcp-fixes-2/a3-passing.txt), [a3-mutation.txt](../../docs/reports/agymcp-fixes-2/a3-mutation.txt)
  - `B` (installer upgrade hints): [b-failing.txt](../../docs/reports/agymcp-fixes-2/b-failing.txt), [b-passing.txt](../../docs/reports/agymcp-fixes-2/b-passing.txt), [b-mutation.txt](../../docs/reports/agymcp-fixes-2/b-mutation.txt)
  - `C1` (installer editable upgrades): [c1-failing.txt](../../docs/reports/agymcp-fixes-2/c1-failing.txt), [c1-passing.txt](../../docs/reports/agymcp-fixes-2/c1-passing.txt), [c1-mutation.txt](../../docs/reports/agymcp-fixes-2/c1-mutation.txt)
  - `C2` (flow style uninstall hints): [c2-failing.txt](../../docs/reports/agymcp-fixes-2/c2-failing.txt), [c2-passing.txt](../../docs/reports/agymcp-fixes-2/c2-passing.txt), [c2-mutation.txt](../../docs/reports/agymcp-fixes-2/c2-mutation.txt)
  - `C3` (verify auto-fix race): [c3-instrumentation.txt](../../docs/reports/agymcp-fixes-2/c3-instrumentation.txt), [c3-mutation.txt](../../docs/reports/agymcp-fixes-2/c3-mutation.txt), [c3-30passes.txt](../../docs/reports/agymcp-fixes-2/c3-30passes.txt)
  - `C4` (usage-before-state ordering & crash window): [c4-failing.txt](../../docs/reports/agymcp-fixes-2/c4-failing.txt), [c4-passing.txt](../../docs/reports/agymcp-fixes-2/c4-passing.txt), [c4-mutation.txt](../../docs/reports/agymcp-fixes-2/c4-mutation.txt), [c4-crash-window.txt](../../docs/reports/agymcp-fixes-2/c4-crash-window.txt), [c4-dedupe-failing.txt](../../docs/reports/agymcp-fixes-2/c4-dedupe-failing.txt), [c4-dedupe-passing.txt](../../docs/reports/agymcp-fixes-2/c4-dedupe-passing.txt), [c4-dedupe-mutation.txt](../../docs/reports/agymcp-fixes-2/c4-dedupe-mutation.txt)
  - Full suite gates: [baseline-full.txt](../../docs/reports/agymcp-fixes-2/baseline-full.txt) (632 passed in 200.11s), [final-full-1.txt](../../docs/reports/agymcp-fixes-2/final-full-1.txt), [final-full-2.txt](../../docs/reports/agymcp-fixes-2/final-full-2.txt) (exit code 0), [final-shellcheck.txt](../../docs/reports/agymcp-fixes-2/final-shellcheck.txt).
- **Verdict:** Settled under [D70](../design/decisions.md#d70-d78) through [D78](../design/decisions.md#d78-agy-mcp-fixes-2-evidence-and-reports); 632 passed.

### 14.8 Security Hardening Round (`docs/reports/hardening/`)
- **Question:** How do items 1–6 (git invocation hardening, error envelope code validation, cleanup failure visibility, and SPEC/decision alignment) perform under mutation?
- **Evidence Files:**
  - Item 1 (git env hardening): [item1-fail.txt](../../docs/reports/hardening/item1-fail.txt), [item1-pass.txt](../../docs/reports/hardening/item1-pass.txt), [item1-mutation.txt](../../docs/reports/hardening/item1-mutation.txt)
  - Item 2 (error code envelope validation): [item2-fail.txt](../../docs/reports/hardening/item2-fail.txt), [item2-pass.txt](../../docs/reports/hardening/item2-pass.txt), [item2-mutation.txt](../../docs/reports/hardening/item2-mutation.txt)
  - Items 3a–3e (preflight & worktree security): `item3a` through `item3e` fail/pass/mutation logs
  - Item 4 (bare git invocations elimination): [item4-grep-before.txt](../../docs/reports/hardening/item4-grep-before.txt), [item4-grep-after.txt](../../docs/reports/hardening/item4-grep-after.txt)
  - Item 5 (swallowed cleanup failure visibility): [item5-manager-fail.txt](../../docs/reports/hardening/item5-manager-fail.txt), [item5-supervisor-fail.txt](../../docs/reports/hardening/item5-supervisor-fail.txt), [item5-pass.txt](../../docs/reports/hardening/item5-pass.txt), [item5-mutation.txt](../../docs/reports/hardening/item5-mutation.txt), [item5-audit-events.txt](../../docs/reports/hardening/item5-audit-events.txt)
  - Item 6 (SPEC documentation alignment): [item6-codemap-test.txt](../../docs/reports/hardening/item6-codemap-test.txt), [item6-spec-diff.txt](../../docs/reports/hardening/item6-spec-diff.txt)
  - Full suite gates: [baseline-pytest.txt](../../docs/reports/hardening/baseline-pytest.txt) (492 passed in 137.95s), [final-pytest.txt](../../docs/reports/hardening/final-pytest.txt) (513 passed in 132.88s).
- **Verdict:** Settled under [D58](../design/decisions.md#d58-d62) through [D62](../design/decisions.md#d58-d62); 513 passed.

### 14.9 Standalone Installer Round (`docs/reports/installer-agymcp/`)
- **Question:** Does the standalone installer script (`installer/install.sh`) install cleanly across generic and dsh clients without touching real directories?
- **Evidence Files:**
  - Item 1 (standalone chapter): [item1-failing.txt](../../docs/reports/installer-agymcp/item1-failing.txt), [item1-passing.txt](../../docs/reports/installer-agymcp/item1-passing.txt), [item1-mutation.txt](../../docs/reports/installer-agymcp/item1-mutation.txt)
  - Item 2 (agents guidance block): [item2-failing.txt](../../docs/reports/installer-agymcp/item2-failing.txt), [item2-passing.txt](../../docs/reports/installer-agymcp/item2-passing.txt), [item2-mutation.txt](../../docs/reports/installer-agymcp/item2-mutation.txt)
  - Full suite gates: [baseline-pytest.txt](../../docs/reports/installer-agymcp/baseline-pytest.txt) (599 passed in 138.84s), [final-pytest-rerun.txt](../../docs/reports/installer-agymcp/final-pytest-rerun.txt) (622 passed in 195.12s), [final-shellcheck.txt](../../docs/reports/installer-agymcp/final-shellcheck.txt), [final-bashn.txt](../../docs/reports/installer-agymcp/final-bashn.txt).
- **Verdict:** Settled under [D67](../design/decisions.md#d67-the-installer-is-a-separate-chapter-not-mixed-into-the-wrapper) and [D69](../design/decisions.md#d69-dsh-client-registration-toolcalltimeoutms-360000-and-the-default-generic-client); 622 passed.

### 14.10 Server Lifecycles Round (`docs/reports/server/`)
- **Question:** Does the JSON-RPC stdio server correctly serialize argv, launch processes, enforce preamble rule 11, and prevent secret leakage?
- **Evidence Files:** [argv-fresh.json](../../docs/reports/server/argv-fresh.json), [argv-resume.json](../../docs/reports/server/argv-resume.json), [prompt-fresh.txt](../../docs/reports/server/prompt-fresh.txt), [pytest.txt](../../docs/reports/server/pytest.txt) (284 passed in 75.88s), [ruff.txt](../../docs/reports/server/ruff.txt), [git-log.txt](../../docs/reports/server/git-log.txt).
- **Verdict:** Confirmed clean stdio framing and secrets redaction; 284 passed.

### 14.11 Smoke Fixes Rounds (`docs/reports/smoke-fixes/`, `smoke-fixes-2/`, `smoke-fixes-3/`)
- **Question:** How did the wrapper resolve real-agy permission handling, settings hashes, orphan process cleanup, and model resolution?
- **Evidence Files:**
  - Round 1: [docs/reports/smoke-fixes/pytest.txt](../../docs/reports/smoke-fixes/pytest.txt) (301 passed in 89.84s), [docs/reports/smoke-fixes/mutations.txt](../../docs/reports/smoke-fixes/mutations.txt), [docs/reports/smoke-fixes/pgrep-after.txt](../../docs/reports/smoke-fixes/pgrep-after.txt)
  - Round 2: [docs/reports/smoke-fixes-2/pytest.txt](../../docs/reports/smoke-fixes-2/pytest.txt) (323 passed in 98.25s), [docs/reports/smoke-fixes-2/analysis.txt](../../docs/reports/smoke-fixes-2/analysis.txt), [docs/reports/smoke-fixes-2/orphans.txt](../../docs/reports/smoke-fixes-2/orphans.txt)
  - Round 3: [docs/reports/smoke-fixes-3/baseline-pytest.txt](../../docs/reports/smoke-fixes-3/baseline-pytest.txt) (589 passed in 143.12s), [docs/reports/smoke-fixes-3/final-pytest.txt](../../docs/reports/smoke-fixes-3/final-pytest.txt) (591 passed in 144.22s)
- **Verdict:** Settled under [D47](../design/decisions.md#d47-d51) through [D51](../design/decisions.md#d47-d51) and [D63](../design/decisions.md#d63-real-smoke-fixes--permission-config-model-resolution-python--m-pytest-token-fields-path-sanitization); 591 passed.

### 14.12 Stale COMMIT_MSG Pre-Check (`docs/reports/stale-commit-msg/`)
- **Question:** Does the watcher safely reject non-regular file entries and symlinks in `.agy/COMMIT_MSG` before attempting `open()`?
- **Evidence Files:** [test-first-failing.txt](../../docs/reports/stale-commit-msg/test-first-failing.txt) (1 failed in 0.05s), [test-after-fix.txt](../../docs/reports/stale-commit-msg/test-after-fix.txt) (1 passed in 0.03s), [test-nonregular-failing.txt](../../docs/reports/stale-commit-msg/test-nonregular-failing.txt), [test-nonregular-passing.txt](../../docs/reports/stale-commit-msg/test-nonregular-passing.txt), [mutation.txt](../../docs/reports/stale-commit-msg/mutation.txt), [baseline-pytest.txt](../../docs/reports/stale-commit-msg/baseline-pytest.txt) (591 passed in 144.22s), [final-pytest.txt](../../docs/reports/stale-commit-msg/final-pytest.txt) (599 passed in 153.96s).
- **Verdict:** Settled under [D66](../design/decisions.md#d66-discard-the-invalid-commit_msg-on-needs_reviewinvalid_commit_message); 599 passed.

### 14.13 Verification Fixes Round (`docs/reports/verify-fixes/`)
- **Question:** Does the wrapper correctly exclude junk files from worktrees and surface verify error output in envelopes?
- **Evidence Files:** [pytest.txt](../../docs/reports/verify-fixes/pytest.txt) (359 passed in 132.49s), [ruff.txt](../../docs/reports/verify-fixes/ruff.txt), [f-result.json](../../docs/reports/verify-fixes/f-result.json), [f-status.json](../../docs/reports/verify-fixes/f-status.json), [f-wait.json](../../docs/reports/verify-fixes/f-wait.json), [f-audit.jsonl](../../docs/reports/verify-fixes/f-audit.jsonl), [f-cli.log](../../docs/reports/verify-fixes/f-cli.log).
- **Verdict:** Settled under [D54](../design/decisions.md#d54-d57) and [D55](../design/decisions.md#d54-d57); 359 passed.

## Conclusion

The reportless fix rounds document the iterative maturation of the `agy-mcp` codebase
from 284 tests to over 630 tests without introducing unverified changes. Every fix
is supported by captured failure output, clean passing tests, and confirmed mutations.

## Evidence

| Claim | Repo file and line or report path |
|---|---|
| Fix Batch 1 final suite result (380 passed in 144.91s) | `docs/reports/fix-batch1/pytest.txt:40` |
| Fix Batch 1 T4 loop stability result (100 passes across 3 scenarios) | `docs/reports/fix-batch1/t4-loop.txt:1-20` |
| Fix Batch 2 baseline pass count (380 passed in 157.92s) | `docs/reports/fix-batch2/baseline-pytest.txt:39` |
| Fix Batch 2 final suite result (405 passed in 148.22s) | `docs/reports/fix-batch2/pytest.txt:46` |
| Fix Batch 2b baseline pass count (405 passed in 147.21s) | `docs/reports/fix-batch2b/baseline-pytest.txt:46` |
| Fix Batch 2b final suite result (408 passed in 92.61s) | `docs/reports/fix-batch2b/pytest.txt:48` |
| Fix Batch 3a baseline pass count (408 passed in 148.11s) | `docs/reports/fix-batch3a/baseline-pytest.txt:48` |
| Fix Batch 3a final suite result (436 passed in 99.31s) | `docs/reports/fix-batch3a/final-pytest.txt:49` |
| Fix Batch 3b baseline pass count (436 passed in 144.38s) | `docs/reports/fix-batch3b/baseline-pytest.txt:49` |
| Fix Batch 3b final suite result (492 passed in 134.74s) | `docs/reports/fix-batch3b/final-pytest.txt:60` |
| Fix Batch 4 baseline pass count (492 passed in 138.83s) | `docs/reports/fix-batch4/baseline-pytest.txt:60` |
| Fix Batch 4 final suite result (560 passed in 141.18s) | `docs/reports/fix-batch4/final-pytest.txt:72` |
| agymcp-fixes-2 baseline pass count (632 passed in 200.11s) | `docs/reports/agymcp-fixes-2/baseline-full.txt:76` |
| Hardening baseline pass count (492 passed in 137.95s) | `docs/reports/hardening/baseline-pytest.txt:60` |
| Hardening final suite result (513 passed in 132.88s) | `docs/reports/hardening/final-pytest.txt:63` |
| Installer-agymcp baseline pass count (599 passed in 138.84s) | `docs/reports/installer-agymcp/baseline-pytest.txt:74` |
| Installer-agymcp final rerun result (622 passed in 195.12s) | `docs/reports/installer-agymcp/final-pytest-rerun.txt:77` |
| Server round final suite result (284 passed in 75.88s) | `docs/reports/server/pytest.txt:29` |
| Smoke-fixes round 1 result (301 passed in 89.84s) | `docs/reports/smoke-fixes/pytest.txt:32` |
| Smoke-fixes round 2 result (323 passed in 98.25s) | `docs/reports/smoke-fixes-2/pytest.txt:34` |
| Smoke-fixes round 3 baseline (589 passed in 143.12s) and final (591 passed in 144.22s) | `docs/reports/smoke-fixes-3/baseline-pytest.txt:73`, `docs/reports/smoke-fixes-3/final-pytest.txt:75` |
| Stale COMMIT_MSG baseline (591 passed) and final (599 passed in 153.96s) | `docs/reports/stale-commit-msg/baseline-pytest.txt:75`, `docs/reports/stale-commit-msg/final-pytest.txt:75` |
| Stale COMMIT_MSG test-first failing duration (1 failed in 0.05s) | `docs/reports/stale-commit-msg/test-first-failing.txt:8` |
| Stale COMMIT_MSG test-after-fix duration (1 passed in 0.03s) | `docs/reports/stale-commit-msg/test-after-fix.txt:8` |
| Verify fixes round final suite result (359 passed in 132.49s) | `docs/reports/verify-fixes/pytest.txt:38` |

## How to reproduce

Execute the full regression suite:
```bash
env -u PYTHONPATH .venv/bin/python -m pytest -q
```

## Sources

- `docs/reports/fix-batch1/`
- `docs/reports/fix-batch2/`
- `docs/reports/fix-batch2b/`
- `docs/reports/fix-batch3a/`
- `docs/reports/fix-batch3b/`
- `docs/reports/fix-batch4/`
- `docs/reports/agymcp-fixes-2/`
- `docs/reports/hardening/`
- `docs/reports/installer-agymcp/`
- `docs/reports/server/`
- `docs/reports/smoke-fixes/`
- `docs/reports/smoke-fixes-2/`
- `docs/reports/smoke-fixes-3/`
- `docs/reports/stale-commit-msg/`
- `docs/reports/verify-fixes/`
- `doc/design/decisions.md`
