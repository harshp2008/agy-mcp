# agy-mcp roadmap

Status: current
Audience: maintainers, contributors

Planned work, in the order the maintainers intend to take it. Items are
verbatim as handed over; status is **DONE** (with the decision that records
it) or **NOT RUN / not started**.

## Hardening (branch `exp/hardening`) — DONE (D61)

- [x] A3: wire `checkpoint_commit_minutes` through `JobSupervisor` into
      `CommitWatcher` (default still 600 s)
- [x] A5: `agy_review` fails closed with `audit_unavailable` and human-review
      next actions
- [x] A5 subset: log plus `cleanup_error` audit events for the five agreed
      cleanup swallows (other rows deferred)
- [x] A10: delete `gitops.run_hardened_git`, `runner.read_proc_pgid` and the
      unreachable `resume` tail; keep tests-only helpers
- [x] A12: correct lock order, watcher cadence/validation, event log, budget
      naming and envelope example drift in SPEC

## Usage follow-ups — DONE (D61, item 3)

- [x] usage follow-ups (always SIGKILL group after SIGTERM, cache only
      available readings, usage_unmeasurable warning for unknown model group,
      1 MB cap on /usage output and log, full mask of short email local parts)

## Batch 4 scratchpad — DONE (D62)

- [x] Real `.agy` provisioning in the launch path (A4/F24): 0700 directory,
      local `info/exclude`, four seeded scratchpad files, fail-closed on error
- [x] Plan accounting with one automatic unticked-plan nudge (`plan_nudge`)
- [x] Wrapper `facts` vs untrusted `agent_report`, with
      `report_claims_unverified` (`claim_phrases`)
- [x] Resume state-of-the-world block and retained
      `<jobdir>/scratchpad/` copies after cleanup

## Real-agy smoke 1 fixes — DONE (D63)

- [x] Finding 1: read `<home>/.gemini/config/config.json` first, no-follow,
      size cap, fail soft, and never echo a rule body in the envelope
- [x] Finding 2: allow `gemini-3.8-flash` by default and record the CLI-resolved
      `model_resolved` in the envelope and `usage.jsonl`
- [x] Finding 3: accept `python -m pytest` / `python3 -m pytest` under the
      pytest flag policy and list accepted forms on rejection
- [x] Finding 5: carry `thinking_tokens`, `cache_read_tokens` and `num_turns`
      in the envelope `tokens` (null when absent)
- [x] Finding 9: replace the absolute worktree path with `<worktree>` and HOME
      with `~` in the response text (including `file://` URIs)
- [ ] Deferred (low): findings 6-8 — benign `COMMIT_MSG` skips, advisory
      checklist rewrite, unused `LOG.md`

## Auth-expiry follow-ups

- [ ] auth-expiry probe under an empty HOME (the real expired-login signature is
      unknown; `auth_error_patterns` must stay empty and must never match the
      healthy `You are not logged into Antigravity.` line, D63)

## Real dsh orchestration fixes 2 — DONE (D70-D78)

- [x] A1: `agy_cleanup` branch deletion uses a reachability gate (HEAD or any
      other local branch); merged into `integration`/main is deletable, squash
      and cherry-pick merges still need `force=True` (D70)
- [x] A2: one shared envelope builder; `agy_review`/`agy_result`/`agy_status`
      carry the same `plan`/`facts`/`agent_report`/`scratchpad` as `agy_wait`
      (D71)
- [x] A3: the branch name is allocated once, persisted before `agy_start`
      returns, and used verbatim by the provisioner (D72)
- [x] B: orchestrator guidance — accept on a dedicated integration branch,
      never merge/push into main, never `force=true` on cleanup, default model
      unless the human named one (D73)
- [x] C1: `--upgrade` keeps editable installs editable and re-resolves
      dependencies (D74)
- [x] C2: a refused flow-style uninstall no longer claims a restart (D75)
- [x] C3: the verify-not-found flake was the auto-fix observation window; the
      test now observes the settled state through `agy_wait` (D76)
- [x] C4: usage rows precede terminal/`needs_review` states on the watcher,
      tick-failure and recovery paths, with `run_generation` dedupe across the
      D68 crash window (D77)

## Platform / distribution

- [x] installer chapter (D67)
- [x] dsh MCP registration (D67)
- [x] orchestrator instructions (D67)
- [x] rerun upgrade path: `--upgrade` / `AGY_MCP_UPGRADE=1` reinstalls from
      `AGY_MCP_SRC` and the skip note points at it (D69 B1)
- [x] uninstall safety: a bare `--uninstall` also removes dangling dsh blocks;
      `--uninstall --client generic` warns instead of leaving a dead
      registration silently (D69 B2)
- [x] flow-style guard ignores `#` comments and the installer's own marker
      block and prints the offending lines (D69 B3)
- [x] generic `mcp-client-config.json` carries only `command`/`args`/`env`; the
      360000 ms requirement is a printed note (D69 B4)
- [x] shellcheck 0.11.0 run on `installer/install.sh` in a throwaway venv:
      four SC2016 info messages, all intentional (D69 B5)
- [x] first end-to-end run on a real dsh chat recorded in
      `docs/ORCHESTRATION.md` section 6 (masked account, no approval prompt)
- [x] second end-to-end run (approval policy `never`,
      `danger-full-access`): full job with `agy_start`/`agy_wait` (150.9 s)/
      `agy_review`/`agy_cleanup`, three wrapper bugs found and fixed (D70-D72,
      `docs/ORCHESTRATION.md` section 6)
- [ ] `agy-mcp install` subcommand (after a git remote exists)
- [ ] raw-URL `curl | bash` hosting (after a git remote exists)
- [ ] fetch-latest via a git clone/pull cache directory (after a git remote exists)
- [ ] more clients: claude-code, others
- [ ] agy permission-skip flag probe
- [ ] agy-vm and status watcher
- [ ] host tester

## Notes

- The usage follow-ups map to `usage.py` (`_kill_usage_process`,
  `UsageCache`, `group_for_model`, `read_usage_once`, `mask_email`) and the
  `agy_usage` envelope. They are implemented on `exp/hardening`; D61 supersedes
  the older D59 description of the 60 s cache and the short-local-part mask.
- The host tester is the only item with a standing language decision: it may be
  written in Rust (D60). The wrapper itself stays Python.
- The hardening, usage-follow-up, Batch 4, smoke-1 and real-dsh fixes-2 items
  above are implemented (D61, D62, D63, D70-D77); the remaining entries record
  intent only.

## Sources

- `src/agy_mcp/usage.py` — implemented usage follow-up items.
- `src/agy_mcp/manager.py` — hardening and orchestration execution paths.
- `doc/design/decisions.md` — recorded design decisions D60–D78.
