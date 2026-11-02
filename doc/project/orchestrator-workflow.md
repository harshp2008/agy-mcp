# Orchestrator workflow: driving agy-mcp from dsh and other MCP clients

Status: current
Audience: orchestrators, contributors

This document explains how to drive `agy-mcp` from dsh or another MCP client,
what the installer writes, how to run a first supervised job, how to move to
an unattended run, and how to choose an orchestrator workflow.

**Evidence boundary.** Everything below was derived from files in this
repository (`doc/design/spec.md`, `doc/using/install.md`, `doc/design/modes.md`,
`src/agy_mcp/`, `installer/`, and the docs/reports evidence). The real
`~/.dsh` tree was **not** inspected; no dsh source or configuration was read.
Any statement about dsh UI behaviour, approval prompts or permission-preset
semantics that the repository does not define is marked **UNSURE** instead of
guessed.

---

## 1. What is established about a dsh tool call

| Fact | Where it is established |
| --- | --- |
| The server is a FastMCP **stdio** server; stdout is the JSON-RPC channel. | `doc/design/spec.md` section 8; `src/agy_mcp/server.py` module docstring. |
| Ten tools are registered: `agy_start`, `agy_status`, `agy_wait`, `agy_result`, `agy_cancel`, `agy_list`, `agy_resume`, `agy_review`, `agy_cleanup`, `agy_usage`. | `src/agy_mcp/server.py`; `tests/fixtures/tools_schema.json`. |
| The dsh registration uses `serverName: agy`, so dsh exposes the tools as `mcp__agy__<tool>`. | `installer/install.sh` (`agy_mcp_print_dsh_patch_block`); `installer/agents_block.md` names the `mcp__agy__` tools. |
| The dsh server entry is `@deepseek-ai/dsh-mcp-client`, id `mcp-agy`, transport `stdio`, command = the generated `run.sh`, `toolCallTimeoutMs: 360000`. | `installer/install.sh`; marker block format reproduced from the host installer pattern. |
| The registration lives between `# >>> ai-stack agymcp >>>` and `# <<< ai-stack agymcp <<<` in `~/.dsh/profiles/web/cordis.patch.yml`, with a `.bak` backup and replace-own-block logic. | `installer/install.sh` (`agy_mcp_register_dsh`). |
| The orchestrator guidance lives between `<!-- >>> ai-stack agymcp >>> -->` and `<!-- <<< ai-stack agymcp <<< -->` in `~/.dsh/AGENTS.md`. | `installer/install.sh`; `installer/agents_block.md`. |
| The server itself never prompts for approval. Every handler validates its input and either runs the operation or returns a standard error envelope. | `src/agy_mcp/server.py`; `src/agy_mcp/manager.py` (`ManagerError` with a stable `.code`). |
| Tool calls can fail with coded errors such as `low_quota`, `verify_not_allowed`, `cap_exceeded`, `invalid_input`, `internal_error`, and `job_finalizing`; these are server-side results, not approval prompts. | `src/agy_mcp/manager.py`, `src/agy_mcp/verify.py`, `src/agy_mcp/server.py`. |
| The installer refuses to modify dsh registration from inside a dsh session (`DSH_SESSION_ID`, `DSH_HOME` or `DSH_PROFILE` set). | `installer/install.sh` (`refuse_inside_dsh`). |
| A completed job is never merged or pushed by the wrapper; the human merges with `git merge --no-ff`. | `doc/design/spec.md` sections 1 and 19; `installer/agents_block.md`. |

The practical consequence of the two facts in the middle is important: **client
approval and server execution are separate layers.** If dsh delivers an
`mcp__agy__*` call, the server executes it (subject to its own validation). If
dsh does not deliver it, the server never sees it. The server cannot ask dsh for
permission, because the transport is a stdio JSON-RPC channel and no approval
method exists in `server.py`.

---

## 2. dsh approval policies and permission presets

The repository does **not** contain dsh's approval-policy implementation, a
permission-preset list, or a mapping from presets to MCP tool calls. The
`@deepseek-ai/dsh-mcp-client` extension is an external npm package; only its
registration block is in this repository. The following table therefore records
what can and cannot be established from repository files.

| dsh setting | What the repository establishes | Behaviour for an `mcp__agy__*` call | Status |
| --- | --- | --- | --- |
| Approval policy `"ask"` | Nothing in this repository describes the prompt or its scope. `doc/using/install.md` explicitly says "(verify against dsh docs)". | If dsh prompts and the human approves, the call reaches the server and executes. If the human denies, the server never sees it. | **UNSURE** (the prompt/approval semantics are dsh's, not agy-mcp's). |
| Approval policy `"never"` | Verified on 2026-10-26 in the second real dsh run (section 6): with the session log showing approval policy `never` and permission preset `danger-full-access`, all five tools called in that run executed with no approval prompt. | Delivery is decided entirely by dsh; agy-mcp has no approval hook. Under this policy+preset every call reached the server. | **VERIFIED** for `never` + `danger-full-access` (one session); other presets remain UNSURE. |
| Any named permission preset | No file in this repository enumerates dsh presets or maps them to MCP tools. `doc/design/modes.md` describes **agy** execution modes (`protected`, `sandbox`, `unrestricted`, status: NOT BUILT), not dsh presets. | Unknown. A preset that blocks the ten tool names will make the server unreachable; a preset that allows them behaves like the rows above. | **UNSURE** for preset names and semantics. |
| Per-tool timeout | The installer sets `toolCallTimeoutMs: 360000`; `doc/using/install.md` recommends at least 360 seconds and says the dsh cap is undocumented in the workspace. | A timeout shorter than an in-flight `agy_wait(seconds=300)` can drop the result even though the agy job keeps running. | 360000 ms is **established by the installer**; whether this dsh version honours the key is **UNSURE** (`docs/DS_REVIEW_1.md` D7 records the same uncertainty). |
| dsh restart | The installer restarts dsh through the systemd user session when one exists, unless `AGY_MCP_NO_RESTART=1`; it refuses to do so inside dsh. Whether a restart is required for the patch file to take effect is a dsh behaviour. | After a restart, dsh should load the new server and show `mcp__agy__*` tools. | Restart mechanics **established by installer**; reload semantics **UNSURE**. |

Recommended safe conclusion: **do not rely on server-side approval.** Treat
approval as a dsh-client concern. Test the chosen policy/preset by asking the
orchestrator to call the read-only `agy_usage` tool; if that call is blocked,
no amount of agy-mcp configuration will help. If `agy_usage` succeeds, the ten
tools are reachable under that policy/preset.

One repository drift worth recording: `doc/using/install.md` section 3 lists
"all 9 tools" and omits `agy_usage`. The authoritative list is
`src/agy_mcp/server.py` / `tests/fixtures/tools_schema.json` (ten tools,
including `agy_usage`). Treat the setup guide's count as stale.

---

## 3. Starting an unattended run

### 3.1 Install and restart

1. From the repository, run one command:
   - `./installer/install.sh --client dsh` for dsh, or
   - `./installer/install.sh` for the generic MCP JSON (no dsh files).
   Add `--dry-run` first to see every write, and `--doctor` afterwards.
   A rerun **skips pip** when the venv already imports `agy_mcp` and says so;
   run `./installer/install.sh --upgrade` (or `AGY_MCP_UPGRADE=1`) to reinstall
   the package from `AGY_MCP_SRC` (`pip install --upgrade --force-reinstall`,
   with dependencies re-resolved; an editable install prints `editable install
   is already live; nothing to reinstall` unless `AGY_MCP_EDITABLE=1`).
   `--uninstall` prints every dsh block it removes and only then reminds you to
   restart dsh from a normal terminal; `--uninstall --client generic` leaves
   dsh alone but warns when the block would now point at a deleted `run.sh`.
2. The dsh installer writes the patch block and the AGENTS block, backs up both
   files to `.bak`, and restarts dsh if a systemd user session exists. If it
   says the restart was skipped, restart dsh yourself.
3. Confirm the server is reachable by calling `agy_usage` from the new chat. It
   is read-only (it never starts, resumes, cancels or mutates a job).

### 3.2 The human steps that cannot be automated by this installer

- **Log in to agy once.** The installer deliberately does not run
  `agy --output-format json -p "/usage"` or any real `agy` command. It prints
  the instruction instead, because a login probe on a headless or fresh HOME
  can fail or hang. Run `agy` once in a normal terminal, complete the login,
  and exit.
- **Run the usage check yourself.** Run the host's usage check (`agy-usage` if
  your installation provides it, otherwise the read-only `agy_usage` tool or
  `agy --output-format json -p "/usage"`). Confirm the account is the expected
  one (the envelope masks the email) and that both buckets are available.
- **Confirm `agy_usage` returns `available: true`** and reason `null`. If it
  returns `available: false`, fix the login or the agy installation before
  starting a job. `usage_guard=warn` never blocks on an unavailable reading, so
  an unattended run can otherwise burn time on a broken login.
- **Choose the project folder.** `agy_start.repo_path` must be an absolute path
  to a git repository whose HEAD resolves, with no `.agents/**` trust gate
  unless `trust_agents_dir=true`, and under `allowed_workdir_roots` (the
  installer writes `[$HOME]`). With `require_clean_source=true` the source
  repository must also be clean; the default is `false`.
- **Choose the permission preset / approval policy.** No repository file
  defines dsh preset names or whether `"ask"`/`"never"` approves MCP calls; see
  section 2. Use the least-privileged setting that still lets the orchestrator
  call `agy_usage` and the other nine tools. **UNSURE** which preset that is.
- **Open a new dsh chat in that project folder.** New-chat and project-folder
  selection are dsh UI behaviour and are **UNSURE** from this repository; the
  repository only establishes that the orchestrator must pass the absolute
  repository path to `agy_start`.
- **Give the orchestrator the guidance.** The dsh install puts it in
  `~/.dsh/AGENTS.md`; the generic install writes
  `$CONF_DIR/agymcp/orchestrator-guidance.md` and prints it for pasting.

### 3.3 Expected cost

The repository records **quota cost, not money**:

- `agy_usage` returns per-group buckets with `window` `weekly` or `5h`, plus
  `remaining_fraction`, reset time and a `low` flag. Thresholds default to 10%
  weekly and 15% for the 5-hour bucket (`low_quota_weekly_pct`,
  `low_quota_five_hour_pct`; `doc/design/spec.md` section 17).
- A job records `usage_cost {five_hour_pct_points, weekly_pct_points,
  measured, concurrent_jobs, note}` at launch and finalization
  (`doc/design/spec.md` section 17; `src/agy_mcp/usage.py`). With more than one concurrent
  job the delta is explicitly approximate because quota is shared.
- Token counts are exposed in the envelope (`tokens`), and the historical probe
  logs in `docs/AGY_FINDINGS_2.md` and `docs/AGY_FINDINGS_3.md` record token
  totals for their probes (423,107 and 219,870 tokens respectively). These are
  probe measurements, not a price table.
- **UNSURE**: a dollar/euro price per job. No pricing table or currency unit
  exists in this repository, and the agy plan's quota-to-money mapping is
  outside it. Measure the expected quota cost empirically: call `agy_usage`,
  run one tiny job, call `agy_usage` again, and read `usage_cost` in the final
  envelope. Use that delta when deciding how many unattended jobs fit.

### 3.4 First real run: manual checklist

Run these in order. Do not skip the single tiny job.

- [ ] `command -v agy` succeeds, or `~/.local/bin/agy` is executable
      (the installer checks this; it never runs agy).
- [ ] The human has run `agy` once and completed the login.
- [ ] The human has run the usage check and it succeeds.
- [ ] `agy_usage` returns `available: true`, `reason: null`, and the expected
      masked account/groups; no bucket is at 0%.
- [ ] `./installer/install.sh --doctor --client dsh` reports a healthy install.
- [ ] dsh was restarted after the patch block was written and the
      `mcp__agy__agy_usage` tool is visible in a new chat.
- [ ] The project folder is a clean git repository with a resolvable HEAD,
      under `$HOME`, and `agy_start.repo_path` is its absolute path.
- [ ] Run one **tiny** job through dsh: `agy_start` with a title, a narrow
      prompt, a 3-8 item `checklist`, `verify` (for example `pytest -q`), and
      `max_minutes` around 30. Use the fake-agy test suite for rehearsal; the
      real agy run is the human's step.
- [ ] `agy_wait` until the state leaves `running` (at most 300 s per call), then
      `agy_review`; read `facts`, `audit_findings`, `verify`, `agent_report`,
      `plan` and the diff.
- [ ] Check `docs/orchestrator/NEEDS_HUMAN.md` in the project. It must exist;
      it should be empty or contain only items the human must decide. Also
      check `PLAN.md`, `DECISIONS.md` and `REPORT.md`.
- [ ] Merge the job branch into a dedicated integration branch (for example
      `integration/<topic>`) and run the project's tests there; never merge or
      push into `main`/the default branch yourself. Then `agy_cleanup`; if it
      refuses, remove the worktree only (`delete_branch=false`) and add the
      branch to `NEEDS_HUMAN`.
- [ ] Confirm no account email or secret appears in prompts, envelopes, logs or
      commits (the wrapper masks the account email, but the orchestrator must
      not put secrets in prompts).
- [ ] Only now start the unattended loop: work until the plan is done or only
      NEEDS_HUMAN items remain.

---

## 4. Non-dsh MCP clients

### What the generic install prints

`./installer/install.sh` (the default, `--client generic`) writes and prints:

1. `$CONF_DIR/agymcp/mcp-client-config.json` — a standard `mcpServers` snippet:

   ```json
   {
     "mcpServers": {
       "agy": {
         "command": "<$CONF_DIR>/agymcp/run.sh",
         "args": [],
         "env": {}
       }
     }
   }
   ```

   Only the standard `mcpServers` entry keys (`command`, `args`, `env`) are
   written (D69 item B4). The 360000 ms tool-call timeout is **printed as a
   note** and documented here, not encoded as a non-standard JSON key; the dsh
   patch block keeps its own `toolCallTimeoutMs: 360000`.

   `CONF_DIR` defaults to `$HOME/.config/ai-stack`; the `command` is always the
   absolute generated `run.sh`, which sources the mode-600 `env` file and execs
   the venv's `agy_mcp` entrypoint with the resolved agy binary, config and
   state directory.

2. `$CONF_DIR/agymcp/orchestrator-guidance.md` — the exact contents of
   `installer/agents_block.md`. Paste it into the client's instruction file.

It also prints a note that the tool-call timeout must be **at least
360000 ms**.

### Where to paste

- **JSON snippet:** into the MCP server configuration the client reads, under
  its `mcpServers` map. Keep the key `agy`; the tools then appear as
  `mcp__agy__<tool>` in clients that use that naming convention. If the client
  uses a different key, the tool prefix follows that key.
- **Guidance:** into the client's instruction/system-prompt file (for example an
  `AGENTS.md` or `CLAUDE.md` equivalent). It tells the orchestrator which jobs
  to delegate, how to wait, how to review and when to stop for a human.
- **`run.sh`:** do not edit it by hand; rerun the installer. It is the only
  supported entrypoint and it carries the agy path, config and state dir baked
  in at install time.

### Why 360000 ms

`agy_wait` blocks until the job leaves `running` or until its `seconds`
parameter expires; the parameter defaults to 30 and is capped at 300 seconds
(`doc/design/spec.md` section 8; `src/agy_mcp/server.py`). A client tool-call timeout below
the blocking call can drop the result while agy keeps working. The installer
therefore writes **360000 ms** (360 s), leaving 60 s of headroom over the
300 s maximum. If a client cannot set a timeout that high, call
`agy_wait(seconds=30)` or poll `agy_status` instead.

No file is written under `~/.dsh` by the generic client. Use
`--client dsh` only when you want the dsh patch block and AGENTS block.

---

---

## Choosing a workflow

The installer guidance tells the orchestrator to choose a workflow before any
work and to write the choice, with the reason, on the first line of
`docs/orchestrator/PLAN.md`. The order of the decision rule is:

1. **GATED** if acceptance tests can express the result now. Write the contract
   and the tests in `tests/acceptance/`, commit them, then give one big job the
   goal "make tests/acceptance pass, never modify tests/acceptance, add your own
   tests". The acceptance tests are the gate: the orchestrator checks that the
   job did not touch them, reads the diff, merges into `integration/<topic>`,
   and runs the acceptance tests and the full suite itself. The job's own claim
   of success is not evidence.
2. **RESEARCH** if no specification is possible yet. The orchestrator owns the
   design, the interfaces and the synthesis; only self-contained research
   sub-topics or modules are delegated, and every reported claim is unverified
   until checked against a source (`VERIFIED` only with a source, otherwise
   `ASSUMED`).
3. **MICRO** if the goal is clear but the code is risky or cannot be specified
   up front. Use 3-5 small chunks, one job each, and review, merge, test and
   clean up after **each** job.
4. **DIRECT** if the change is tiny (roughly one file or a few lines). The
   orchestrator does it itself; no job is started.

If the goal is ambiguous and nobody is watching, the orchestrator takes the most
reasonable reading, says which reading it took in `PLAN.md`, and continues.

### Worked examples (three clipsync runs)

These are owner-reported results from three real `clipsync` runs. They are a
single sample each, not a controlled comparison; use them to recognise the
shape of each workflow, not as a benchmark. Dollar figures are approximate and
quota points are not dollars.

| Run | Workflow used | Jobs | Orchestrator steps | Approx. cost | Weekly quota points | What happened |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | RESEARCH, then prototype chunks | 5 small jobs | 82 | ~$0.12 | 1.73 | Design was not known up front; research and prototypes were delegated in small pieces. The most expensive run in steps and quota of the three. |
| 2 | GATED | 1 build job + 1 fix job | 46 | ~$0.04 | 0.57 | Contract and acceptance tests were written first. The gate caught a `connect()` deadlock that the job's own report missed, so one fix job was needed. Cheapest of the three. |
| 3 | MICRO | 4 jobs | 49 | ~$0.05 | 1.60 | Core code was risky, so it was split into four reviewed chunks; the run also added extra tests and hardening. More quota than the gated run because every chunk was reviewed, merged and tested separately. |

The examples show why the block names a default but not a single recipe: run 2
was cheap because the acceptance tests made the result checkable, while run 3
spent more quota on repeated review and test cycles to keep risky core changes
reviewable. That is also the main caveat: one run per workflow is not enough to
claim that GATED is always cheaper. Treat the numbers as illustrations.

## Sources

- `installer/install.sh` — dsh registration and patch blocks, generic MCP client configuration, doctor checks.
- `installer/agents_block.md` — orchestrator guidance block.
- `src/agy_mcp/server.py` — stdio FastMCP tool definitions and parameter contracts.
- `src/agy_mcp/manager.py` — job management and execution lifecycle.
- `src/agy_mcp/usage.py` — quota buckets and usage guard thresholds.
- `doc/design/spec.md` — normative contracts for tools, timeouts, and quota windows.
