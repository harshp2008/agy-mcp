# Installing and configuring agy-mcp

Status: current
Audience: users, orchestrators

This guide details configuring and running `agy-mcp` inside the DeepSeek host (`dsh`) or any compatible MCP client orchestrator.

## 1. MCP Stdio Server Configuration

> [!NOTE]
> *(verify against dsh docs)*
> DSH uses the standard JSON format for Model Context Protocol stdio servers. If DSH uses a specific settings file format (e.g. inside `~/.config/dsh/mcp.json` or through a GUI settings panel), map these fields accordingly.

### Generic MCP stdio JSON Entry

```json
{
  "mcpServers": {
    "agy": {
      "command": "agy-mcp",
      "args": [
        "--agy-binary", "/absolute/path/to/agy",
        "--state-dir", "/home/user/.local/state/agy-mcp",
        "--config", "/home/user/.config/agy-mcp/config.toml"
      ],
      "env": {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/home/user/.local/bin",
        "HOME": "/home/user"
      }
    }
  }
}
```

Or using `python -m agy_mcp` with an explicit virtual environment:

```json
{
  "mcpServers": {
    "agy": {
      "command": "/path/to/venv/bin/python",
      "args": [
        "-m", "agy_mcp",
        "--agy-binary", "/absolute/path/to/agy",
        "--state-dir", "/home/user/.local/state/agy-mcp",
        "--config", "/home/user/.config/agy-mcp/config.toml"
      ],
      "env": {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/home/user/.local/bin",
        "HOME": "/home/user"
      }
    }
  }
}
```

### Parameter Notes
- `--agy-binary`: Required absolute path if `agy` is not present on standard system `PATH`. Launch will fail with exit code 2 and a clear stderr message if the binary is missing or non-executable.
- `--state-dir`: Optional base directory for job metadata, streams, and locks. Defaults to `~/.local/state/agy-mcp`. Always created with permissions mode `0700`.
- `--config`: Optional path to `config.toml`. Defaults to `~/.config/agy-mcp/config.toml`.

## 2. Tool-Call Timeout Requirements

> [!IMPORTANT]
> **DSH Tool-Call Timeout vs `agy_wait` Duration:**
> The `agy_wait` tool blocks until an active job leaves state `running` or until its `seconds` parameter expires (default 30 seconds, maximum 300 seconds).
> - If DSH enforces a per-tool-call client timeout (e.g. 60s or 120s), calling `agy_wait(seconds=300)` will cause DSH to terminate the HTTP/stdio RPC channel before the server replies, leaving the orchestrator confused even though `agy` continues running.
> - **Recommendation:** Set the DSH per-tool-call timeout to at least **360 seconds**.
> - If DSH's timeout cannot be changed or is unknown: always invoke `agy_wait` with `seconds <= 60` (e.g., `agy_wait(job_id=..., seconds=30)` or poll with `agy_status`).

## 3. Smoke Checklist

Verify your setup by running through the following smoke checklist:

1. **Binary Check:**
   Verify `agy` exists and is executable:
   ```bash
   which agy && agy --version
   ```
2. **Server Launch Check:**
   Verify `agy-mcp` starts up cleanly and prints version without crashing:
   ```bash
   agy-mcp --version
   ```
3. **Missing Binary Refusal Check:**
   Verify launch fails with exit code 2 if pointed at a non-existent binary:
   ```bash
   agy-mcp --agy-binary /nonexistent/path/agy || echo "Exited with code: $?"
   ```
   *(Expected exit code: 2)*
4. **Tool Discovery Check:**
   In DSH or your MCP client, verify that all 10 tools appear in the available tools list (see [MCP tools reference](../reference/mcp-tools.md)).
5. **Dry-Run Job Check:**
   From DSH, run `agy_list()`. It should return a valid JSON object with `jobs: []`, `total_active_jobs: 0`, and `disk_usage_bytes: ...`.
6. **Clean Shutdown & Recovery Check:**
   If DSH or the MCP client process is restarted while a job is running, `agy-mcp` cleanly shuts down its supervisors without terminating the background `agy` process group. Upon restart, `agy-mcp` reconciles running jobs and resumes supervision.

## 4. Multi-account setups

Adding a second Google account is a manual, human-login recipe: create a private
home, prove D-Bus isolation, log in yourself, copy the permission policy, then
add a `[[accounts]]` table to `config.toml`. The full recipe is
[Run jobs on more than one Google account](accounts.md). The installer never
logs in or out and never writes an account home. `--doctor` runs the read-only
account checks described on that page.

## Sources

- `installer/install.sh` — automated registration, patch blocks, and installer CLI flags.
- `src/agy_mcp/server.py` — stdio server entry point and CLI argument parsing.
- `src/agy_mcp/config.py` — configuration defaults and paths.
- `doc/reference/mcp-tools.md` — 10 MCP tools reference.
