# Coding jobs with agy (mcp__agy__ tools)
You can hand self-contained coding work to Google's agy through the mcp__agy__ tools. agy works on its own git branch in an isolated worktree; the wrapper audits it, runs verification and makes the commits. The wrapper never merges or pushes.

## When to delegate
- Delegate: a clearly specified chunk (one module, tests for existing code, a refactor, docs, a port), anything long-running, anything that can run while you do other work.
- Do it yourself: small edits, design decisions, security-sensitive code, anything that needs the whole conversation's context.
- Before delegating, call agy_usage. Do not start new jobs when the Gemini Models weekly bucket remaining is below 10% or its 5h bucket remaining is below 15%. Run at most 2 jobs at once.

## Choosing a workflow (write it in docs/orchestrator/PLAN.md first)
Before any work write docs/orchestrator/PLAN.md; its first line names the chosen workflow and why. If the goal is ambiguous and nobody is watching: take the most reasonable reading, say which in PLAN.md, continue.
- GATED (default when tests can check the result): write the contract and acceptance tests in tests/acceptance/ first and commit them; start ONE big agy job: "make tests/acceptance pass, never modify tests/acceptance, add your own tests"; wait with the longest agy_wait the tool allows and do nothing between waits (no polling); gate in this order: `git diff <base> -- tests/acceptance` must be empty (restore from your commit if agy touched it), agy_review and read the diff, merge into integration/<topic>, run the acceptance tests and the full suite yourself; never trust the job's claim of success; at most one fix job, and an optional second job for extra tests/hardening is gated the same way.
- MICRO (risky core code, or a task that cannot be specified up front): 3-5 small chunks, one agy job each; after EACH job: agy_review, read the diff, merge, run the full suite, then agy_cleanup.
- RESEARCH (no spec possible): you own the design, the interfaces and the final synthesis; delegate only self-contained research sub-topics or modules; treat everything agy reports as unverified until you check it against a source (mark claims VERIFIED only with a source, else ASSUMED).
- DIRECT (tiny: roughly one file or a few lines): do it yourself, no agy.

Always: every agy job prompt is fully self-contained (goal, files, constraints, 3-10 step checklist, verify command, commit subject <= 72 characters); check agy_usage before every start; below the quota floor, do the remaining chunks yourself and log the handover in docs/orchestrator/DECISIONS.md; if a job ends in an odd state use agy_status/agy_resume/agy_cancel and log it, never dig into agy-mcp's source, config or state directories; do not re-read large files or poll.

## Running a job
1. agy_start with: a specific title; a prompt stating the goal, constraints, the files involved and the acceptance criteria; a checklist of 3-10 concrete steps; verify (for example "pytest -q"); max_minutes (30 for most jobs). Do not pass the model parameter unless the human named one; use the server default.
2. agy_wait until the state changes (it returns after at most 5 minutes; call it again). Do not poll agy_status in a loop.
3. When done: agy_review. Trust the wrapper's facts, audit_findings and verify result, not the agent's own report. agy_review carries the same plan, facts, agent_report and scratchpad as agy_wait, so read agent_report and plan to see what was skipped. Read the diff before accepting.
4. Accept = merge the job branch into a dedicated integration branch in the project (for example integration/<topic>), run the project's tests there, then agy_cleanup. Never merge, fast-forward or push into main/master or the repository's default branch: that is the human's decision. Record "merge integration/<topic> into main when ready" in docs/orchestrator/REPORT.md and commit docs/orchestrator/ on the integration branch, not on main. Reject = agy_resume with specific feedback (keeping manual resumes to a small number; the cumulative max_minutes budget still applies), or discard and redo with a better prompt.
5. Never pass force=true to agy_cleanup. If agy_cleanup refuses, remove the worktree only (delete_branch false), add the job branch to NEEDS_HUMAN and continue.

## States and what to do
- done: review as above.
- needs_decision: answer using the decision policy below, then agy_resume.
- needs_review with plan_incomplete or verify_failed, or in the timeout state: agy_resume once with specific guidance; if it repeats, take over yourself or split the task.
- needs_review with quota_exhausted: stop starting jobs, continue other work, resume after resume_after.
- needs_review with auth_required: stop and add it to NEEDS_HUMAN (agy needs a login).
- needs_review with audit_violation, host_path_modified, instruction_file_modified, audit_unavailable or blocked_by_permission: never auto-resume, never merge. Add it to NEEDS_HUMAN and continue with other work.
- failed: read the error; retry once with a changed approach; otherwise report it.
- If agy_resume fails with error code job_finalizing, the previous run is still wrapping up: wait 10 seconds and call agy_resume again (at most 5 attempts), then report it.

## Decision policy (you decide; the human is not there)
Say yes and log it when the choice is reversible, stays inside the repo, follows existing conventions or the SPEC, and is the smaller, simpler option.
Never decide alone (add to NEEDS_HUMAN and continue with other work): deleting or overwriting user data, pushing or any force operation, credentials/secrets/network access, spending money, changing permissions or sandbox settings, editing AGENTS.md / GEMINI.md / .agents/, installing system packages, touching files outside the repo, licensing, anything you cannot undo.

## Files to keep (in the project, docs/orchestrator/)
PLAN.md (tasks and status), DECISIONS.md (every decision you made and why, with the job id), NEEDS_HUMAN.md (questions that need a person), REPORT.md (at the end: done, not done, risks, how to verify). Update them as you go; a restarted session must be able to continue from them.

## Unattended runs
Work until the plan is done or only NEEDS_HUMAN items remain. Never loop on a failing job. If something is unclear, make the safe choice, log it and continue.
