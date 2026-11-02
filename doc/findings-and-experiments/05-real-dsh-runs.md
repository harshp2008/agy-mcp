# 05 — Real DSH Client Verification Runs

Status: current
Audience: maintainers, contributors

Owner-verified end-to-end runs of `agy-mcp` from real `dsh` chat sessions, establishing
stdio transport compatibility, approval policy interactions, quota readings, and
resolving three edge-case bugs in the orchestration workflow.

## Question

Does the `agy-mcp` stdio server operate end-to-end when invoked by an actual `dsh`
chat client, how do approval policies and permissions behave, and what bugs were discovered?

## Method

Two live client validation sessions were executed by the repository owner on 2026-10-26
(from a standard terminal and `dsh` chat; no autonomous agent session inspected `~/.dsh`
or invoked the real `agy`):
- **Run 1 (Read-only tool):** Installed editable client registration via `AGY_MCP_EDITABLE=1 ./installer/install.sh --client dsh`, verified with `--doctor`, and invoked `mcp__agy__agy_usage` from a fresh `dsh` chat.
- **Run 2 (Full lifecycle):** Executed a complete job against a throwaway repository under approval policy `never` with permission preset `danger-full-access`, invoking `agy_usage`, `agy_start`, `agy_wait`, `agy_review`, and `agy_cleanup`.

## Result

### Run 1: Read-Only Tool Execution (2026-10-26)

- **Installation:** `./installer/install.sh --client dsh --doctor` reported a completely healthy install.
- **Tool call:** `mcp__agy__agy_usage` executed from a new `dsh` chat returned live quota data for the masked account (`<user-email>`):
  - Weekly remaining: **24.87%**
  - 5-hour remaining: **100%**
- **Approval prompts:** Zero prompts appeared.
- **Settled:** Confirmed that the client configuration patch block, generated `run.sh` script, virtual environment, and stdio JSON-RPC transport function seamlessly together from a live `dsh` chat.

### Run 2: Full Job Workflow (2026-10-26)

- **Configuration:** Approval policy `never`, permission preset `danger-full-access`.
- **Tools called:** `agy_usage` -> `agy_start` -> `agy_wait` -> `agy_review` -> `agy_cleanup`.
- **Timing & Cost:**
  - `agy_wait` returned after **150.9s**.
  - Final envelope token count: **262,774 tokens**.
  - Final envelope quota points spent: **0.21 weekly points** / **0.74 5-hour points**.
- **Prompts:** Zero approval prompts appeared for any tool call.
- **Bugs identified and fixed:** The run revealed three operational defects, resolved on branch `exp/agymcp-fixes-2`:
  1. **A1 (Branch cleanup refusal):** `agy_cleanup(delete_branch=true)` refused to delete a branch whose commits were already merged into `integration` and `main` (fixed in commit `d248710`, [D70](../design/decisions.md#d70-cleanup-branch-reachability-includes-upstream-and-main-a1)).
  2. **A2 (Review envelope inconsistency):** `agy_review` returned empty `plan`, `facts`, `agent_report`, and `scratchpad` payloads for a finished job while `agy_wait` returned them (fixed in commit `91c4b60`, [D71](../design/decisions.md#d71-agy_review-returns-finished-job-planfactsreports-a2)).
  3. **A3 (Branch suffix mismatch):** `agy_start` reported a different branch suffix in its return envelope than the branch actually created on disk (fixed in commit `8e7946e`, [D72](../design/decisions.md#d72-agy_start-returns-the-branch-name-actually-created-a3)).
- **Orchestration rules tightened ([D73](../design/decisions.md#d73-tighten-orchestrator-guidance-integration-branch-force-model-flags)):**
  - Never merge directly into `main`; merge into an integration branch.
  - Never pass `force=true` to `agy_cleanup`.
  - Do not pass `model` unless explicitly requested by the human user.

### Open Questions (UNSURE Register)

The runs confirmed basic transport but left specific client behaviors unverified:
1. What `dsh`'s `"ask"` policy prompts for, and whether a denied MCP call reaches the server at all (**UNSURE**).
2. Whether `dsh`'s `"never"` policy auto-approves or auto-denies MCP calls under presets other than `danger-full-access` (**UNSURE**).
3. The names and semantics of `dsh` permission presets, and which preset allows the ten `mcp__agy__*` tools (**UNSURE**).
4. Whether `toolCallTimeoutMs` is the exact key honored by the installed `dsh` version, and its default value (**UNSURE**).
5. Whether a `dsh` restart is required, or a new chat is enough, for the patch file to load (**UNSURE**).
6. New-chat and project-folder UI behavior (**UNSURE**).
7. Dollar cost per job (**UNSURE**; only quota percentage points and token counts are recorded).
8. Whether the host has a separate `agy-usage` executable (**UNSURE**).

## Conclusion

Live `dsh` verification confirmed that `agy-mcp` integrates cleanly into real agent chat
sessions without interactive approval prompts under policy `never` and preset `danger-full-access`.
Edge-case defects discovered during the run led directly to bug fixes [D70](../design/decisions.md#d70-cleanup-branch-reachability-includes-upstream-and-main-a1),
[D71](../design/decisions.md#d71-agy_review-returns-finished-job-planfactsreports-a2), [D72](../design/decisions.md#d72-agy_start-returns-the-branch-name-actually-created-a3),
and tightened orchestrator rules [D73](../design/decisions.md#d73-tighten-orchestrator-guidance-integration-branch-force-model-flags).

## Evidence

| Claim | Repo file and line or report path |
|---|---|
| Run 1: install command and doctor check | `docs/ORCHESTRATION.md:46-55` |
| Run 1: `agy_usage` remaining quota (weekly 24.87%, 5h 100%) | `docs/ORCHESTRATION.md:56-58` |
| Run 1: zero prompts and transport validation | `docs/ORCHESTRATION.md:59-68` |
| Run 2: policy `never`, preset `danger-full-access`, no prompts | `docs/ORCHESTRATION.md:70-75` |
| Run 2: `agy_wait` elapsed time (150.9s) and job tokens (262,774) | `docs/ORCHESTRATION.md:77-83` |
| Run 2: quota points spent (0.21 weekly / 0.74 5h) | `docs/ORCHESTRATION.md:80-81` |
| Bug A1: branch cleanup refusal fix (commit `d248710`, D70) | `docs/ORCHESTRATION.md:86-88` |
| Bug A2: `agy_review` empty fields fix (commit `91c4b60`, D71) | `docs/ORCHESTRATION.md:89-91` |
| Bug A3: `agy_start` branch suffix mismatch fix (commit `8e7946e`, D72) | `docs/ORCHESTRATION.md:92-93` |
| Guidance tightening on integration branches, force, model (D73) | `docs/ORCHESTRATION.md:94-96` |
| Approval policy in force for Run 1 | not recorded in repo (UNSURE, to be filled in by owner per `docs/ORCHESTRATION.md:60`) |
| Dollar cost per job | not recorded (only quota percentage points and token counts recorded per `docs/ORCHESTRATION.md:32-33`) |
| Default `toolCallTimeoutMs` value | not recorded in repo (UNSURE per `docs/ORCHESTRATION.md:25-27`) |

## How to reproduce

Installation and client registration are reproduced via:
```bash
AGY_MCP_EDITABLE=1 ./installer/install.sh --client dsh
./installer/install.sh --client dsh --doctor
```
Calling MCP tools requires an authorized `dsh` client configured with the server registration block.

## Sources

- `docs/ORCHESTRATION.md`
- `doc/design/decisions.md`
