# agy CLI capability inventory and agy-mcp usage

**Date:** 2026-10-30
**Workspace:** `~/Documents/GITHUB PROJECTS/agy-mcp` (read-only; this report is the only file created)
**Binary inspected:** `~/.local/bin/agy` — ELF 64-bit, stripped, version `1.4.10`
**Method:** help/version commands only. No `agy` model turn was started. No network lookup was made.
**Scope note:** `agy` is not on this shell's `PATH`; it was invoked by absolute path. The repo's fallback path is the same (`docs/reports/real-smoke-1/REPORT.md:10`).

## Labels used in this report

- **VERIFIED** — I saw it in `agy --help`/`--version` output, or I read it in this repo's code. File:line is given for code.
- **DOCS** — a repo document states it; the document is cited. Most runtime behavior docs come from probes of **agy v1.4.7**, not the installed **1.4.10**; this gap is called out where it matters.
- **UNKNOWN** — neither `agy --help` nor a repo document establishes it, and I did not test it (testing would require a model turn or reading `~/.gemini`, both out of scope).

A statement that is "in the help" is not automatically a statement about runtime behavior; the help is unusually thin about env vars, config paths, exit codes, JSON schemas, conversation storage, hooks and auth.

---

## 0. Short version

- **VERIFIED (help):** `agy --help` lists 25 top-level flags and 12 subcommands; the full help is reproduced in §2.
- **VERIFIED (help):** the core non-interactive form is `agy [flags] -p <prompt>`; output formats are `text`, `json`, `stream-json`; conversation resume is `--conversation <id>`; `-c/--continue` resumes the most recent conversation.
- **VERIFIED (help):** there is no `usage`, `auth`, `login`, `logout`, `conversation list`, `hook` or `mcp use` subcommand. Usage/quota is not a subcommand; agy-mcp obtains it with a print-mode `/usage` prompt.
- **VERIFIED (code):** agy-mcp launches exactly:
  `agy --mode accept-edits --output-format json --print-timeout <N>s --log-file <abs jobdir>/cli.log --model <model> [--conversation <id>] -p <prompt>`
  (`src/agy_mcp/runner.py:144-166`).
- **VERIFIED (code):** agy-mcp bans `--sandbox`, `--effort`, `-c`, `--dangerously-skip-permissions`; it permits only `accept-edits` and `json` (`src/agy_mcp/runner.py:22-29`).
- **VERIFIED (code):** agy-mcp passes only `HOME`, `PATH`, `LANG`, `TMPDIR` and `GIT_CONFIG_*` to the child, with no API keys (`src/agy_mcp/util.py:80-89`).
- **VERIFIED (code + a real repo log):** the conversation-ID fallback regex can persist the literal token `using`; it is not validated before being passed back as `--conversation` (`src/agy_mcp/supervisor.py:244`, `src/agy_mcp/runner.py:162-163`).
- **VERIFIED (code):** a hard timeout that kills the process before it writes its final JSON can lose the conversation ID; if no ID was already persisted, `agy_resume` silently starts a *new* conversation instead of resuming (`src/agy_mcp/parse_result.py:145-155`, `src/agy_mcp/runner.py:162-163`).
- **DOCS:** most runtime assumptions (JSON fields, `denied_actions`, timeout exit 0, storage paths, `/usage` schema, permission-config path) come from v1.4.7 probes; they were not re-verified on 1.4.10.

---

## 1. Method, incident, and limits

### 1.1 Commands actually run

All of these are non-model commands.

- `agy --version` → `1.4.10`, exit 0. **VERIFIED**
- `agy --help` → top-level help, exit 0. **VERIFIED**
- `agy -h` → exit 0 (output not retained separately). **VERIFIED**
- `agy --badflag` → `flags provided but not defined: -badflag`, full usage, exit 2. **VERIFIED**
- `agy help` → same top-level help, exit 0. **VERIFIED**
- `agy help mcp` → MCP help, exit 0. **VERIFIED**
- `agy <sub> --help` for `agent`, `agents`, `changelog`, `install`, `mcp`, `mic-serve`, `models`, `plugin`, `plugins`, `remote-control`, `update`. **VERIFIED**
- `agy mcp add|remove|list|enable|disable --help`. **VERIFIED**
- `agy remote-control start|status|stop --help`. **VERIFIED**
- `agy plugin help` → plugin command list, exit 0. **VERIFIED**
- `agy help --help` → `Error: unknown subcommand: --help`, exit 1. **VERIFIED**
- `agy plugin install --help` → `Error: install target must be a directory: --help`, exit 1; no install happened. **VERIFIED**
- `agy plugin list --help` → `Error: unknown list option "--help": only --json is supported`, exit 1. **VERIFIED**
- `agy plugin enable --help` / `disable --help` → `Error: plugin "--help" not found or invalid`, exit 1. **VERIFIED**
- `agy update --help` → `Usage of update:` (no flags printed), exit 2. **VERIFIED**
- A Python one-liner replayed `src/agy_mcp/supervisor.py`'s conversation regex against the real `docs/reports/smoke/s1-cli.log`; first match was `Conversation using` → `using`. **VERIFIED** (read-only; snippet in §6.1).

### 1.2 Incident: one plugin subcommand executed with `--help`

While enumerating subcommand help, I ran `agy plugin uninstall --help`. Unlike the other plugin subcommands, `uninstall` does **not** parse `--help`; it executed and printed:

```text
Uninstalled plugin "--help"
```

Exit code was `0`. This was the only command I ran that may have changed state. It did **not** start a model turn. I did **not** run `agy plugin list` to check whether a plugin literally named `--help` existed, because that is outside the allowed `--help`-only scope. Residual risk, **UNKNOWN**: if such a plugin existed, it is now uninstalled. No secrets were printed.

### 1.3 Deliberately not run

I did not run any of the following because they may call the model, fetch the network, start a daemon, or read/change user state:

- any `agy -p <real prompt>`, `--print`, `--prompt`, `--prompt-interactive`, or `--input-format stream-json` with real input;
- `agy models`, `agy agent`/`agents` (may query the backend);
- `agy changelog` (may fetch);
- `agy mcp list` (reads user MCP configuration; only `--help` was run);
- `agy plugin list`, `import`, `validate`, `link`, `install`, `enable`, `disable` (only the parent `help` and the accidental `uninstall --help` above);
- `agy install`, `agy update`;
- `agy remote-control start|status|stop`, `agy mic-serve`;
- `agy-usage` (a separate binary, not part of `agy`);
- no network lookups beyond the help text.

### 1.4 Boundaries respected

- I did not read or write `~/.local/state/agy-mcp`, `~/.config/ai-stack`, `~/.gemini`, or `~/.ssh`.
- I did not switch branches, commit, or modify any tracked file. `git status` before writing showed the pre-existing untracked `docs/reports/stale-jobs-report.md`; I did not touch it.
- The only file created is this report.

---

## 2. `agy` CLI surface (VERIFIED from help)

### 2.1 Top-level `agy --help` (verbatim)

```text
Usage of agy:
  --add-dir                       Add a directory to the workspace (repeatable) (default [])
  --agent                         Agent for the current CLI session
  -c                              Short alias for --continue
  --continue                      Continue the most recent conversation
  --conversation                  Resume a previous conversation by ID
  --dangerously-skip-permissions  Auto-approve all tool permission requests without prompting
  --disable-slash-commands        Disable slash command and skill expansion in print mode
  --effort                        Reasoning effort for the current CLI session (low|medium|high|xhigh|max)
  -i                              Short alias for --prompt-interactive
  --input-format                  Input format for print mode (text, stream-json). stream-json reads one NDJSON message per line from stdin and runs a turn for each; it requires --output-format stream-json (default text)
  --json-schema                   Optional JSON schema string or path to a schema file to enforce structured output (for stream-json, only applicable to the final result)
  --log-file                      Override CLI log file path
  --mode                          Set the agent execution mode for this session (accept-edits, auto, plan)
  --model                         Model for the current CLI session
  --new-project                   Create a new project for this session
  --output-format                 Output format for print mode (text, json, stream-json) (default text)
  -p                              Short alias for --print
  --print                         Run a single prompt non-interactively and print the response
  --print-timeout                 Optional time limit for print mode; 0 waits until the turn completes (default 0s)
  --project                       Project ID or project name for the current CLI session
  --prompt                        Alias for --print
  --prompt-interactive            Run an initial prompt interactively and continue the session
  --remote-control                Create a remote connection for the CLI session on start up
  --sandbox                       Run in a sandbox with terminal restrictions enabled
  --settings                      Settings for this session only, never saved: a JSON object of settings.json keys, inline or as a file path

Available subcommands:
  agent           List available agents
  agents          List available agents
  changelog       Show changelog and release notes
  help            Show help for subcommands
  install         Configure environment paths and shell settings
  mcp             Manage MCP servers (add, remove, list, enable, disable)
  mic-serve       Serve this machine's microphone to a CLI on another host
  models          List available models
  plugin          Manage plugins (install, uninstall, list, enable, disable)
  plugins         Alias for plugin
  remote-control  Manage the remote-control background daemon (start, status, stop)
  update          Update CLI
```

Notes on the help itself:

- **VERIFIED:** `--version` is **not** listed, but `agy --version` works and prints `1.4.10`.
- **VERIFIED:** `-h`/`--help` are not listed either, but both work at top level.
- **VERIFIED:** the help text has no section for environment variables, config files, exit codes, auth, hooks, or JSON field schemas.
- **UNKNOWN:** whether undisclosed/hidden flags exist. The help prints the flags it knows about; I did not inspect the stripped binary for hidden flags.

### 2.2 Subcommand help (verbatim)

`agy agent --help` and `agy agents --help` are identical:

```text
Usage: agy agent [flags]

List available agents

Flags:
  -h      Show help
  --help  Show help
```

`agy changelog --help`:

```text
Usage: agy changelog [flags]

Show changelog and release notes

Flags:
  -h      Show help
  --help  Show help
```

`agy install --help`:

```text
Usage: agy install [flags]

Configure environment paths and shell settings

Flags:
  --dir           Custom directory target to configure PATH for
  -h              Show help
  --help          Show help
  --skip-aliases  Bypasses shell profile alias purging
  --skip-path     Bypasses shell profile PATH appending
```

`agy mcp --help`:

```text
Usage: agy mcp <subcommand> [flags] [args]

Available subcommands:
  add      Add or update an MCP server configuration
  remove   Remove an MCP server configuration
  list     List all configured MCP servers
  enable   Enable an MCP server
  disable  Disable an MCP server

Run "agy mcp <subcommand> --help" for flags, argument rules, and examples.
```

`agy mcp add --help`:

```text
Usage: agy mcp add [flags] <name> <commandOrUrl> [args...]

Add or update an MCP server configuration

Flags:
  -H        Short alias for --header (default [])
  -e        Short alias for --env (default [])
  --env     Environment variable KEY=value (repeatable) (default [])
  -h        Show help
  --header  HTTP header Key: Value (repeatable) (default [])
  --help    Show help
  -t        Short alias for --type (default stdio)
  --type    Server type: 'stdio' or 'http' (default stdio)

Arguments:
  <name>          Unique name for the server.
  <commandOrUrl>  Executable for a stdio server, or the URL for an http server.
  [args...]       Arguments passed to the stdio command.

Notes:
  Flags must come before <name>; a flag placed after it is rejected.
  http/https URLs are detected automatically, so --type is usually unnecessary.
  Use -- before the command to pass a command or args that begin with '-'.

Examples:
  agy mcp add fs npx -y @modelcontextprotocol/server-filesystem /work
  agy mcp add --header "Authorization: Bearer TOKEN" api https://example.com/mcp
  agy mcp add --env GITHUB_TOKEN=xxx gh -- docker run -i ghcr.io/github/mcp
```

`agy mcp remove|list|enable|disable --help`:

```text
Usage: agy mcp remove <name> [flags]     Remove an MCP server configuration
Usage: agy mcp list [flags]              List all configured MCP servers
Usage: agy mcp enable <name> [flags]     Enable an MCP server
Usage: agy mcp disable <name> [flags]    Disable an MCP server

Flags (all four):
  -h      Show help
  --help  Show help
```

`agy mic-serve --help`:

```text
Usage: agy mic-serve [flags]

Serve this machine's microphone to a CLI on another host

Flags:
  --addr  Address to listen on; keep it on loopback (default 127.0.0.1:4713)
  -h      Show help
  --help  Show help
```

`agy models --help`:

```text
Usage: agy models [flags]

List available models

Flags:
  -h      Show help
  --help  Show help
```

`agy plugin --help` and `agy plugins --help` are identical:

```text
Usage: agy plugin <command> [arguments]

Commands:
  list [--json]          List installed plugins
  import [source]        Import plugins from gemini or claude
  install <target>       Install a plugin (supports plugin[@marketplace])
  uninstall <name>       Uninstall a plugin
  enable <name>          Enable a plugin
  disable <name>         Disable a plugin
  validate [path] [--strict] [--json]
                         Check a plugin's manifest and files
  link <mp> <target>     Generate link to a marketplace
  help                   Show this help
```

Plugin subcommands do **not** implement `--help` consistently: `install`, `list`, `enable`, `disable` reject it; `uninstall` executes it (see §1.2). **VERIFIED.** I did not run `import`, `validate`, or `link`.

`agy remote-control --help`:

```text
Usage: agy remote-control <subcommand>

Available subcommands:
  start    Register and start the remote-control daemon
  status   Show the daemon's status
  stop     Stop the daemon and unregister it

Run "agy remote-control <subcommand> --help" for flags.
```

`agy remote-control start --help`:

```text
Usage: agy remote-control start [flags]

Register and start the remote-control daemon

Flags:
  -h         Show help
  --help     Show help
  --name     Set this machine's instance name shown in Remote Control
  --session  Register the daemon login-scoped (stops at logout) instead of starting at boot
```

`agy remote-control status|stop --help`:

```text
Usage: agy remote-control status [flags]   Show the daemon's status
Usage: agy remote-control stop [flags]     Stop the daemon and unregister it

Flags (both):
  -h      Show help
  --help  Show help
```

`agy update --help`:

```text
Usage of update:
```

Exit code `2`. **VERIFIED.** The update subcommand's flag parser does not implement `--help` the way the others do; I did not run `agy update` itself.

`agy help --help`:

```text
Error: unknown subcommand: --help
```

Exit code `1`. **VERIFIED.** `agy help` with no argument prints the top-level help; `agy help mcp` prints the MCP help.

### 2.3 Flags at a glance

**VERIFIED (help):**

- **Input/prompt:** `-p`/`--print`/`--prompt` (single non-interactive prompt), `-i`/`--prompt-interactive` (initial prompt then continue interactively), `--input-format` (`text` or `stream-json`; stream-json requires `--output-format stream-json`), `--json-schema` (schema string or file; for stream-json only the final result).
- **Conversation:** `-c`/`--continue` (most recent conversation), `--conversation` (resume by ID).
- **Model/agent:** `--model`, `--effort` (`low|medium|high|xhigh|max`), `--agent`.
- **Mode/permissions:** `--mode` (`accept-edits`, `auto`, `plan`), `--sandbox`, `--dangerously-skip-permissions`, `--settings` (session-only settings JSON object or file).
- **Output/logging:** `--output-format` (`text`, `json`, `stream-json`; default `text`), `--log-file` (override CLI log path), `--print-timeout` (duration; `0` waits until the turn completes; default `0s`).
- **Workspace/project:** `--add-dir` (repeatable), `--project`, `--new-project`, `--remote-control`.
- **Misc:** `--disable-slash-commands` (print mode; disables slash command and skill expansion).

**VERIFIED (help):** the no-value switches are `-c`/`--continue`, `--dangerously-skip-permissions`, `--disable-slash-commands`, `--new-project`, `--remote-control`, and `--sandbox`; the rest take a value or an argument as shown in the help.

### 2.4 Exit codes

The help does **not** document exit codes. What I observed and what the repo docs record:

- **VERIFIED:** `0` — `--version`, `--help`, `-h`, `agy help`, most subcommand `--help`, and `agy plugin uninstall --help`.
- **VERIFIED:** `1` — `agy help --help` (unknown subcommand), `agy plugin install --help`, `agy plugin list --help`, `agy plugin enable|disable --help` (invalid usage/argument).
- **VERIFIED:** `2` — `agy --badflag` (flag-parser error), `agy update --help`.
- **VERIFIED (environment):** `127` — shell `agy: command not found` when the binary is not on `PATH`. This is not an agy exit code.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:184-204`):** permission denial in headless print mode exits `0`, with `status: "SUCCESS"`, empty `response`, and `denied_actions`.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:262-274`):** `--print-timeout` expiry exits `0`, emits valid JSON with the conversation ID, and writes `[agy] print timeout after <N>s ...` to stderr.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:206-236`):** bad `--model` and empty prompt exit `1`; a missing flag value exits `2`.
- **UNKNOWN:** exit codes for internal crashes, network loss, auth expiry, SIGTERM, and SIGINT on 1.4.10. The repo's `parse_result` only treats negative codes as signals (`src/agy_mcp/parse_result.py:157-165`).

The practical consequence (**VERIFIED code**): agy-mcp treats return codes `1` and `2` as `cli_error` *before* parsing stdout (`src/agy_mcp/parse_result.py:167-176`). If agy ever exits `1` after producing a valid JSON payload, that payload is discarded.

### 2.5 Environment variables

- **VERIFIED (help):** the help documents **no** environment variables at all. There is no `AGY_*`, `GEMINI_*`, `GOOGLE_*`, proxy, or auth variable mentioned.
- **VERIFIED (code):** agy-mcp builds the child environment from scratch and passes only `HOME`, `PATH`, `LANG=C.UTF-8`, `TMPDIR=<jobdir>/tmp`, and the three `GIT_CONFIG_*` overrides (`src/agy_mcp/util.py:78-90`). It deliberately does not inherit `os.environ`.
- **VERIFIED (code):** `XDG_CONFIG_HOME` is consulted only for agy-mcp's own config path (`src/agy_mcp/config.py:295-297`); it is not passed to agy.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS_2.md:97-135`):** agy inherits exported parent variables when run from a shell; the wrapper prevents that with its allowlist. That probe did not establish which variables agy *requires*.
- **UNKNOWN:** whether agy needs `USER`, `LOGNAME`, `XDG_*`, `DBUS_SESSION_BUS_ADDRESS`, keyring variables, proxy variables, `SSL_CERT_FILE`, or a locale other than `C.UTF-8`. The wrapper works in the recorded smoke runs, but the help does not state requirements.

### 2.6 Config files

- **VERIFIED (help):** `--settings` accepts "a JSON object of settings.json keys, inline or as a file path", and is session-only, never saved. The help does not name a default settings file.
- **VERIFIED (help):** `--log-file` overrides the CLI log path. The help does not name the default log path.
- **VERIFIED (help):** `--project`/`--new-project` imply agy project state, but the help does not give the on-disk layout.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:376-384`):** under `~/.gemini/antigravity-cli/`: `conversations/<conversation_id>.db`, `brain/<conversation_id>/.system_generated/logs/transcript.jsonl` (and `transcript_full.jsonl`), `conversation_summaries.db`, `jetbox_summaries_proto.pb`, `annotations/<id>.pbtxt`, `log/cli-YYYYMMDD_HHMMSS.log`, `presence/<id>.lock`, `settings.json`.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS_2.md:269-280`):** settings are read from (claimed priority) `~/.gemini/config/projects/<project-id>.json`, then `~/.gemini/antigravity-cli/settings.json`, then `~/.gemini/config/config.json`, plus workspace `.agents/hooks.json`, `GEMINI.md`, `AGENTS.md`.
- **DOCS (D63, `docs/DECISIONS.md:355`):** the real CLI "loads its shared permission config from `<home>/.gemini/config/config.json`" (masked smoke evidence), which is why agy-mcp probes that path **first**.
- **CONTRADICTION / UNKNOWN:** D63 says `~/.gemini/config/config.json` is the loaded path; the S8 probe says project JSON and `~/.gemini/antigravity-cli/settings.json` outrank it. Both are repo docs, and neither is `agy --help`. agy-mcp probes all candidates (`src/agy_mcp/worktree.py:628-642`), so it is robust to the ambiguity, but its reported "effective rules" may not match what agy actually loaded.
- **VERIFIED (code):** agy-mcp's own config is `~/.config/agy-mcp/config.toml` (or `$XDG_CONFIG_HOME/agy-mcp/config.toml`) and is unrelated to agy's config (`src/agy_mcp/config.py:289-297`).

### 2.7 Output formats

- **VERIFIED (help):** `--output-format` is `text` (default), `json`, or `stream-json`.
- **VERIFIED (help):** `--input-format` is `text` (default) or `stream-json`; stream-json reads one NDJSON message per line and runs a turn for each; it requires `--output-format stream-json`.
- **VERIFIED (help):** `--json-schema` enforces structured output; for stream-json it applies only to the final result.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:130`):** JSON result shape:
  `{"conversation_id", "status", "response", "duration_seconds", "num_turns", "usage": {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"}}`, plus optional `denied_actions` and `warnings` (`docs/AGY_FINDINGS.md:198`, `docs/SMOKE_FINDINGS.md:201`).
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:164-172`):** stream-json emits `init`, `step_update` and `result` events; `init` carries `model`, `cwd`, `tools` and `permission_mode`; `step_update` carries `conversation_id`, `step_index`, `state`, `step_type`, `text_delta`, optional `tool_name`/`tool_info`.
- **DOCS (SPEC, `SPEC.md:692`):** in `--output-format json`, agy emits no stdout until completion. This is why agy-mcp's `stdout.txt` is empty for the whole run and status comes from `cli.log`/git.
- **UNKNOWN:** whether 1.4.10 changed any field names or added fields; the 1.4.7 probe is the only schema evidence.
- **VERIFIED (code):** agy-mcp only uses `json`; it parses the last object-shaped stdout line (`src/agy_mcp/parse_result.py:71-97`).

---

## 3. Capability deep dive

### 3.1 Non-interactive / print mode

- **VERIFIED (help):** `-p`, `--print`, and `--prompt` are aliases. All run a single prompt non-interactively and print the response. This is the mode agy-mcp uses.
- **VERIFIED (help):** `-i`/`--prompt-interactive` runs an initial prompt and continues the session interactively. Not usable from an MCP stdio server.
- **VERIFIED (help):** `--print-timeout` is "optional time limit for print mode; 0 waits until the turn completes (default 0s)". The help does not say whether the limit covers model time, tool time, or both.
- **VERIFIED (help):** `--input-format stream-json` is the non-interactive multi-turn interface: one NDJSON message per line, a turn per message, requires stream-json output.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:259-261`):** `--print-timeout` accepts duration strings such as `120s` and `2m`; a bare `120` fails with `time: missing unit`.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:262-274`):** on expiry, agy exits `0`, writes `[agy] print timeout after <N>s with turn in progress; returning partial output` to stderr, and emits JSON with `status: "SUCCESS"` and the conversation ID.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:228-236`):** empty prompt exits `1`; a flag with an omitted value exits `2`.
- **UNKNOWN:** whether 1.4.10 still exits `0` on print-timeout, and whether the stderr wording is stable. Help does not document either.

### 3.2 Conversation handling

- **VERIFIED (help):** a fresh run with no conversation flag starts a new conversation.
- **VERIFIED (help):** `-c`/`--continue` continues the **most recent** conversation.
- **VERIFIED (help):** `--conversation <id>` resumes a previous conversation by ID. The help does not state the ID format, whether it must be a UUID, or where conversations are stored.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:116-146`):** resume by `--conversation <uuid>` works, preserves context, does not re-print prior turns, and increments `num_turns`; `-c` also works but resumes the latest conversation.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:376-384`):** conversations are stored under `~/.gemini/antigravity-cli/`: a per-conversation SQLite DB `conversations/<id>.db`, a JSONL transcript `brain/<id>/.system_generated/logs/transcript.jsonl`, a central `conversation_summaries.db`, annotations, and a presence lock. **UNKNOWN:** whether 1.4.10 uses the same layout; I did not read `~/.gemini`.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS_2.md:245-265`):** after a print timeout, the conversation is preserved and can be resumed with `--conversation`; no orphan processes remain.
- **DOCS (S4, `docs/SMOKE_FINDINGS.md:128`, `docs/SMOKE_FINDINGS.md:194`):** real `--conversation` resume preserved context and operated in the dedicated worktree.
- **CONTRADICTION:** `SPEC.md:872` marks `--conversation` cwd preservation "Settled (Verified Safe)" from S4, while `docs/DS_REVIEW_2.md:575` lists `--conversation` semantics with real agy as unverified. The help is silent. I treat the smoke result as DOCS, not VERIFIED.
- **VERIFIED (help):** there is no `conversations` list/delete subcommand, no `--delete-conversation`, and no documented retention/cleanup. **UNKNOWN:** whether any hidden/undocumented management exists.
- **VERIFIED (code):** agy-mcp never passes `-c`/`--continue`; it passes `--conversation` only when `job.conversation_id` is truthy (`src/agy_mcp/runner.py:162-163`, `src/agy_mcp/manager.py:1404`). It validates the *job* ID as a UUID (`src/agy_mcp/manager.py:89`, `:183-184`) but does not validate `job.conversation_id`.
- **VERIFIED (code):** the primary ID source is the last JSON object on stdout (`src/agy_mcp/parse_result.py:204`, `src/agy_mcp/supervisor.py:232-234`); the fallback is a regex on `cli.log` (`src/agy_mcp/supervisor.py:240-248`).
- **VERIFIED (code + log):** that fallback regex is unsafe; see §6.1 for the real-log demonstration.

### 3.3 Model and agent selection

- **VERIFIED (help):** `--model` selects the model for the session.
- **VERIFIED (help):** `--effort` selects reasoning effort (`low|medium|high|xhigh|max`).
- **VERIFIED (help):** `--agent` selects the agent for the session; `agy agent`/`agy agents` list available agents; `agy models` lists available models.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:323-345`):** model IDs with a level in the name (`gemini-3.8-flash-medium`) are accepted; a **conflicting** `--effort` is rejected with exit 1; a matching effort is accepted. Token usage differs by level.
- **DOCS (D63, `docs/DECISIONS.md:357`):** the alias `gemini-3.8-flash` is accepted and resolves to `gemini-3.8-flash-high`; the wrapper records the resolved model by parsing `model alias "X" resolved to "Y"` from `cli.log` (`src/agy_mcp/cli_log.py:81-88`).
- **VERIFIED (code):** agy-mcp passes `--model` on every job (`src/agy_mcp/runner.py:154-155`), allows an alias/level allowlist (`src/agy_mcp/config.py:39-48`), and never passes `--effort` or `--agent`.
- **VERIFIED (code):** agy-mcp never runs `agy models` or `agy agent`; model/agent validity is discovered from an agy error or from `cli.log`.
- **UNKNOWN:** the output format of `agy models`/`agy agent` on 1.4.10; I did not run them (may query the backend).
- **UNKNOWN:** whether `--agent` values are stable names, IDs, or both; the help does not say.

### 3.4 Permission, sandbox and approval modes

- **VERIFIED (help):** `--mode` accepts `accept-edits`, `auto`, `plan`.
- **VERIFIED (help):** `--sandbox` runs "in a sandbox with terminal restrictions enabled".
- **VERIFIED (help):** `--dangerously-skip-permissions` auto-approves all tool permission requests without prompting.
- **VERIFIED (help):** `--settings` sets session-only settings keys, but the help does not enumerate the permission keys.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:74-112`):** `--mode accept-edits` permits file reads, listings, creates and edits, and some "safe" read-only shell commands; it auto-denies modifying shell execution unless `permissions.allow` rules exist.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:184-204`):** headless print mode cannot prompt for permission; a denied tool confirmation exits `0` with empty response and `denied_actions`; under JSON, the JSON still says `SUCCESS`.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:391-414`):** `--mode auto` warns and falls back when the terminal sandbox is unavailable; `--sandbox` produced a normal response with empty stderr on a host where `bwrap` failed (unprivileged user namespaces restricted). The docs describe this as a silent/soft fallback, not a hard failure.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS_2.md:269-290`):** permission grants are machine-local under `~/.gemini/config/projects/<id>.json` and the global settings/config files; in-tree `.agents/settings.json` did not grant permissions.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS_2.md:205-243`):** stream-json cannot be used as an interactive permission broker; unpermitted tools are hard-denied and `ask_question` auto-skips.
- **VERIFIED (code):** agy-mcp only permits `accept-edits` and `json` (`src/agy_mcp/runner.py:28-29`), and rejects `--sandbox`, `--effort`, `-c`, `--dangerously-skip-permissions` (`src/agy_mcp/runner.py:22-29`). It never passes `--mode auto`/`plan`.
- **VERIFIED (code):** agy-mcp reads permission allow-rules from several candidate files and flags dangerous rules (`src/agy_mcp/worktree.py:607-684`); it never modifies the files (`docs/DECISIONS.md:18`).
- **DOCS (SPEC, `SPEC.md:873`):** `GIT_CONFIG_COUNT` enforcement by agy is "Assumed Safe", not verified. This matters because the wrapper uses `GIT_CONFIG_*` to disable repo hooks for non-commit git calls (`src/agy_mcp/util.py:73-89`).
- **UNKNOWN:** whether 1.4.10's `--sandbox` still silently falls back; whether `--settings` can pin per-job permissions; whether `--mode plan` has any use in headless print mode.

### 3.5 Tools, MCP, hooks, plugins and skills

- **VERIFIED (help):** `agy mcp add|remove|list|enable|disable` manages MCP servers. `add` supports stdio or http, repeatable `--env KEY=value`, repeatable `--header Key: Value`, and `--type stdio|http`.
- **VERIFIED (help):** `agy plugin list|import|install|uninstall|enable|disable|validate|link` manages plugins; `list` supports `--json`, `validate` supports `--strict`/`--json`.
- **VERIFIED (help):** `--disable-slash-commands` disables slash command and **skill** expansion in print mode. It does **not** mention hooks or plugins.
- **VERIFIED (help):** there is no `--mcp-config`, `--mcp-server`, `--no-mcp`, `--hook`, `--no-hooks`, `--plugin`, `--no-plugins`, `--tool`, or `--allowed-tools` flag.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:164`):** the `init` event lists the tool set, including `run_command`, file tools, browser tools, `call_mcp_tool`, subagents, `search_web`, `read_url_content`, `generate_image`, etc.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS_2.md:177-201`):** under `accept-edits`, `search_web` works without rules; `read_url_content`, browser tools and MCP tools are blocked without explicit rules; `call_mcp_tool` was **UNTESTED**.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS_3.md:177-202`):** workspace `.agents/hooks.json` lifecycle hooks execute automatically on their event; `.agents/skills/<name>/SKILL.md` skills are triggered by description matching and run helper scripts via `run_command`; plugin manifests can bundle `mcp_config.json`, `hooks.json`, rules and skills. The docs call hooks "ASSUMED EXPLOITABLE" and record that active hook firing was not tested (`docs/AGY_FINDINGS_3.md:262`, `:320`).
- **VERIFIED (code):** agy-mcp does not configure MCP servers, plugins, skills or hooks. It relies on whatever the user's global agy config provides. Its mitigations are prompt rules (file tools only, no subagents), `.agents/**` preflight/refusal, and audit (`src/agy_mcp/preamble.md:3-15`, `src/agy_mcp/worktree.py:145-152`, `src/agy_mcp/audit.py:502-521`).
- **VERIFIED (code):** agy-mcp currently does **not** pass `--disable-slash-commands`; a grep of `src/` finds zero occurrences. A historical smoke resume argv did include it (`docs/reports/smoke/s4-argv-resume.json:7`).
- **DOCS (SPEC, `SPEC.md:792`):** network and MCP tools are "Gated by Policy" and blocked without explicit allow-rules.
- **UNKNOWN:** whether `--settings` can disable hooks/plugins/MCP per session; whether `--disable-slash-commands` blocks plugin-provided skills; whether global plugins/hooks can execute in a job even when `.agents/` is clean.

### 3.6 Output and log formats

- **VERIFIED (help):** `--output-format json` and `stream-json` are print-mode formats; default is `text`.
- **VERIFIED (help):** `--log-file` overrides the CLI log path; no format is documented.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:385-387`):** `--log-file` redirects the internal session debug log (gRPC transport and language-server lifecycle); a 39 KB log was produced in the probe.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:382`):** the default log path is `~/.gemini/antigravity-cli/log/cli-YYYYMMDD_HHMMSS.log`.
- **DOCS (v1.4.7, `docs/reports/smoke/s1-cli.log:179`):** real log lines include `Print mode: soft-denying tool confirmation "RunCommand" at step 8`, `model alias "..." resolved to "..."`, `Created conversation <uuid>`, `Conversation using project ID: ...`, and `Print mode: starting (... conversationID="")`.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:198`):** JSON `denied_actions` contains only coarse entries such as `{"action":"command","display_name":"RunCommand"}`; command text is not there. agy-mcp compensates by parsing `cli.log` and, for denied-call detail, the transcript (`src/agy_mcp/cli_log.py:91-114`, `src/agy_mcp/transcript.py:88-109`).
- **VERIFIED (code):** agy-mcp's parser prefers the last object-shaped stdout line and tolerates human/log lines after the JSON (`src/agy_mcp/parse_result.py:71-97`).
- **UNKNOWN:** whether the JSON schema, denial lines, model-alias line, or log format changed in 1.4.10.

### 3.7 Timeouts and interrupts

- **VERIFIED (help):** `--print-timeout` is the only CLI timeout; default `0` means wait until the turn completes.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:248-274`):** duration units are required (`120s`, `2m`); expiry is a clean exit 0 with partial JSON and a stderr warning; the conversation is preserved.
- **VERIFIED (code):** agy-mcp passes `--print-timeout <N>s` where N is `min(max_minutes, max_minutes_cap) * 60` on a fresh run and `max(60, remaining_budget)` on resume (`src/agy_mcp/manager.py:463`, `:469`, `:1396`, `:1402`).
- **VERIFIED (code):** the supervisor adds a wall-clock deadline of `budget * 60 + 120` seconds, then sends SIGTERM to the process group, waits up to 3 s, and escalates to SIGKILL (`src/agy_mcp/supervisor.py:139-143`, `src/agy_mcp/runner.py:300-336`).
- **VERIFIED (help):** there is no `agy cancel`, `agy interrupt`, or `agy abort` subcommand. Cancellation is external process signaling (agy-mcp's `agy_cancel`).
- **UNKNOWN:** how agy handles SIGTERM/SIGINT in 1.4.10. The probes tested print timeout, not external signals; `docs/AGY_FINDINGS_2.md:245-265` says the print-timeout path leaves no orphans, but that is a different path.
- **VERIFIED (code):** if the supervisor kills the process before it writes JSON and no ID was already persisted, `parse_result` cannot recover a conversation ID from stdout (`src/agy_mcp/parse_result.py:145-155`, `:178-188`); the job can end in `timeout` with no ID, and a later resume omits `--conversation`. This is a real 3–6 hour run risk.

### 3.8 Usage and quota reporting

- **VERIFIED (help):** there is **no** `usage`, `quota`, `limits`, or `credits` subcommand. The subcommands are the 12 listed in §2.1.
- **DOCS (D59, `docs/DECISIONS.md:316-317`):** agy-mcp obtains usage by running `agy --log-file <tmp>/usage.log --output-format json -p "/usage"` in an empty temp cwd; it parses `command.data.groups[].buckets[]` and cross-checks the tab-separated `response` lines; it reads `applyAuthResult: email=..., authMethod=...` from the temp log; it masks the email, deletes the temp dir, and caches only successful readings for 60 s.
- **VERIFIED (code):** the exact argv is in `src/agy_mcp/usage.py:607-615`; the timeout is 30 s, the stream/log caps are 1 MB, and the cache TTL is 60 s (`src/agy_mcp/usage.py:33-36`, `:582`, `:683-724`).
- **DOCS (v1.4.7 fixture, `tests/fixtures/real_usage.json`):** the `/usage` payload is a JSON envelope whose `response`/`command.data.groups` drive the parser; `SPEC.md:719` and `SPEC.md:877` record that the schema is verified only against the committed fixture and live drift is possible.
- **UNKNOWN:** whether `/usage` is a client-side slash command, a model prompt, or both; whether it consumes quota; whether 1.4.10 returns the same shape. The help does not list slash commands at all.
- **DOCS (D63, `docs/DECISIONS.md:361`):** a healthy job logs `You are not logged into Antigravity.` before keyring auth succeeds; therefore `auth_error_patterns` must not match that string. The real expired-login text is still unknown; defaults are empty (`src/agy_mcp/config.py:72`).
- **VERIFIED (code):** agy-mcp runs usage readings at launch and finalization, plus explicit `agy_usage`; it can warn or block on low quota (`src/agy_mcp/usage.py`, `src/agy_mcp/manager.py`). This is wrapper policy, not an agy CLI feature.

### 3.9 Working directory and git behavior

- **VERIFIED (help):** there is no `--cwd`/`--chdir` flag. The process working directory is the workspace.
- **VERIFIED (help):** `--add-dir` adds a directory to the workspace (repeatable). Not used by agy-mcp.
- **VERIFIED (help):** `--project`/`--new-project` select/create an agy project; not git projects. Not used by agy-mcp.
- **VERIFIED (code):** agy-mcp spawns agy with `cwd=<jobdir>/work`, the dedicated git worktree (`src/agy_mcp/manager.py:482`, `:1420`; `src/agy_mcp/runner.py:239`).
- **VERIFIED (code):** agy-mcp creates the branch/worktree with `git worktree add -b agy/<slug>-<shortid> ... HEAD` (`SPEC.md:372`, `src/agy_mcp/worktree.py`), and commits via the `.agy/COMMIT_MSG` watcher (`src/agy_mcp/watcher.py:225`, `:489`). The wrapper never pushes or merges.
- **VERIFIED (code):** the preamble tells agy to stay inside the cwd, not run git or tests, and to write `.agy/COMMIT_MSG` instead (`src/agy_mcp/preamble.md:3-15`). The wrapper, not agy, owns commits.
- **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:112`):** under `accept-edits`, mutating shell commands including `git add`/`git commit` are auto-denied without allow rules. So the default path cannot have agy commit.
- **VERIFIED (code):** the dangerous-rule classifier flags `git push` and `git branch`, but **not** `git add`/`git commit` (`src/agy_mcp/worktree.py:540-542`). If a user's allow rules include `command(git add)` or `command(git commit)`, agy could commit inside the worktree; the wrapper's "wrapper-owned commit" assumption is prompt/policy-enforced, not CLI-enforced.
- **DOCS (SPEC, `SPEC.md:873`):** agy's enforcement of `GIT_CONFIG_COUNT`/`core.hooksPath` overrides is "Assumed Safe". The wrapper relies on those overrides to bypass repo hooks for non-commit git calls (`src/agy_mcp/util.py:73-89`).
- **UNKNOWN:** whether `--conversation` resume restores the original cwd or uses the caller's cwd. Help is silent; S4 smoke says it operated in the dedicated worktree; a later review calls it unverified (`docs/DS_REVIEW_2.md:575`).

### 3.10 Authentication

- **VERIFIED (help):** there is no `auth`, `login`, `logout`, `account`, or `whoami` subcommand in 1.4.10.
- **VERIFIED (help):** `agy install` configures PATH/aliases; it is not an auth command.
- **DOCS (D63, `docs/DECISIONS.md:361`):** auth is pre-existing and keyring-based; a healthy job logs `You are not logged into Antigravity.` before keyring auth succeeds, then `keyringAuth: loaded token...`, `ChainedAuth: authenticated via keyring`, `Print mode: silent auth succeeded`. The expired-login signature is unknown; `auth_error_patterns` defaults to empty.
- **DOCS (D59, `docs/DECISIONS.md:318-322`):** agy-mcp detects auth expiry from the `/usage` reading (`reason: "auth_required"`) and from configured patterns; it cannot log in for the user. On `auth_required`, it stops and asks the human.
- **VERIFIED (code):** agy-mcp passes `HOME` (the real home) so agy can find its existing credentials, but passes no API keys and no `XDG_*`/DBus variables (`src/agy_mcp/util.py:80-89`). `agy_home` defaults to `~/.gemini/antigravity-cli` (`src/agy_mcp/config.py:77`).
- **UNKNOWN:** whether a fresh HOME or a stripped environment can authenticate; whether keyring access needs a session bus; whether 1.4.10 added an auth subcommand not shown in `--help`.

### 3.11 Other subcommands

- **VERIFIED (help):** `agy install` configures PATH and shell aliases (`--dir`, `--skip-aliases`, `--skip-path`). agy-mcp never runs it.
- **VERIFIED (help):** `agy update` updates the CLI. Its `--help` is broken (`Usage of update:`, exit 2); agy-mcp never runs it.
- **VERIFIED (help):** `agy changelog` shows release notes. Not run (may fetch); agy-mcp never uses it.
- **VERIFIED (help):** `agy remote-control start|status|stop` manages a background daemon; `--remote-control` creates a remote connection at session start. Not used; would be inappropriate for unattended local jobs.
- **VERIFIED (help):** `agy mic-serve` serves this machine's microphone to another host (`--addr`, default `127.0.0.1:4713`). Not used.
- **VERIFIED (help):** `agy agent`/`agents` and `agy models` list agents/models. Not used by agy-mcp.

---

## 4. How agy-mcp uses agy today

### 4.1 Exact argv

**VERIFIED (code):** `build_argv` produces, in this order (`src/agy_mcp/runner.py:144-166`):

```text
<agy_abs_path>
  --mode accept-edits
  --output-format json
  --print-timeout <N>s
  --log-file <abs jobdir>/cli.log
  --model <model>
  [--conversation <job.conversation_id>]   # only if truthy
  -p <prompt>
```

The prompt is always last. `build_argv` rejects `--sandbox`, `--effort`, `-c`, `--dangerously-skip-permissions`, any mode other than `accept-edits`, any output format other than `json`, and a timeout `< 1` (`src/agy_mcp/runner.py:22-29`, `:112-132`, `:158-160`).

- **Fresh launch:** no `--conversation` (`src/agy_mcp/manager.py:465-471`).
- **Resume:** `--conversation job.conversation_id`, with a fresh `--print-timeout` based on remaining budget (`src/agy_mcp/manager.py:1398-1405`).
- **Usage:** a separate argv, `agy --log-file <tmp>/usage.log --output-format json -p "/usage"`, with no `--mode`/`--model`/`--print-timeout` (`src/agy_mcp/usage.py:607-615`).

### 4.2 Environment

**VERIFIED (code):** the child gets exactly:

```text
HOME=<real home>
PATH=<server PATH or default>
LANG=C.UTF-8
TMPDIR=<jobdir>/tmp
GIT_CONFIG_COUNT=3
GIT_CONFIG_KEY_0=core.fsmonitor   GIT_CONFIG_VALUE_0=false
GIT_CONFIG_KEY_1=core.hooksPath   GIT_CONFIG_VALUE_1=/dev/null
GIT_CONFIG_KEY_2=core.pager       GIT_CONFIG_VALUE_2=cat
```

(`src/agy_mcp/util.py:70-90`.) No `os.environ` copy, no API keys, no `XDG_*`, no `USER`, no proxy variables. The wrapper's own commit path intentionally uses only two overrides (no `hooksPath`) so repo hooks run (`src/agy_mcp/util.py:58-76`, `SPEC.md:458-462`).

### 4.3 Process supervision

**VERIFIED (code):**

- `subprocess.Popen(..., start_new_session=True)`, stdin `DEVNULL`, stdout/stderr to job files (`src/agy_mcp/runner.py:230-244`).
- Supervisor deadline: `min(max_minutes, max_minutes_cap) * 60 + 120` (`src/agy_mcp/supervisor.py:139-143`).
- On deadline/cancel/audit: SIGTERM to the process group, 3 s grace, SIGKILL (`src/agy_mcp/runner.py:300-336`).
- Poll every ~1 s; audit every ~5 s; checkpoint commit every 10 min by default (`src/agy_mcp/supervisor.py:102-106`, `src/agy_mcp/config.py:60`).
- No agy-side cancel; cancellation is external (`src/agy_mcp/manager.py`, `server.py` `agy_cancel`).

### 4.4 Conversation handling

**VERIFIED (code):**

- Fresh run: no conversation flag; a new conversation is created by agy.
- Resume: `--conversation` only if `job.conversation_id` is set (`src/agy_mcp/runner.py:162-163`).
- ID extraction, primary: last JSON object on stdout, key `conversation_id` (`src/agy_mcp/parse_result.py:204`, `src/agy_mcp/supervisor.py:232-234`).
- ID extraction, fallback: regex `conversation(?:_id)?[:\s]+([a-zA-Z0-9_-]+)` over the 2 MB `cli.log` tail (`src/agy_mcp/supervisor.py:240-248`, `src/agy_mcp/cli_log.py:34-44`).
- ID persistence: `_finalize_non_running_process` saves it (`src/agy_mcp/supervisor.py:399-403`); `_finalize_terminated_process` only uses the parsed JSON ID (`src/agy_mcp/supervisor.py:951-952`).
- Validation: the *job* ID is UUID-validated (`src/agy_mcp/manager.py:89`, `:183-184`); `job.conversation_id` is not.
- No `-c`/`--continue`; no conversation list/delete/cleanup.

### 4.5 Output parsing

**VERIFIED (code):**

- Reads the last 10 MB of stdout/stderr (`src/agy_mcp/supervisor.py:597-601`).
- Extracts denials from `cli.log` before parsing (`src/agy_mcp/supervisor.py:603-607`); extracts resolved model from `cli.log` (`src/agy_mcp/supervisor.py:608-612`).
- `parse_agy_output` precedence: cancelled, audit violation, negative signal, rc 1/2 CLI error, timeout, `NEEDS_DECISION`, permission block, denied-without-progress, `SUCCESS`, unhandled (`src/agy_mcp/parse_result.py:100-282`).
- Timeout is detected by `supervisor_timed_out` or stderr substring `[agy] print timeout after` (`src/agy_mcp/parse_result.py:179`).
- JSON fields used: `status`, `response`, `conversation_id`, `tokens` or `usage`, `num_turns`, `denied_actions`, `warnings` (`src/agy_mcp/parse_result.py:202-207`, `:54-68`).
- `SUCCESS` + zero commits + zero modified files + any denial becomes `needs_review(denied_without_progress)` (`src/agy_mcp/supervisor.py:1019-1028`; `docs/DECISIONS.md:233`).

### 4.6 Usage

**VERIFIED (code):**

- `USAGE_PROMPT = "/usage"`; 30 s timeout; 1 MB caps; 60 s cache (`src/agy_mcp/usage.py:33-36`).
- Fresh reading at launch, fresh reading at finalization; explicit `agy_usage` returns the cached/read value (`src/agy_mcp/manager.py:473-477`, `src/agy_mcp/supervisor.py:277-288`).
- Parses `command.data.groups[].buckets[]` and the tabular `response`; fails soft to `available: false` (`src/agy_mcp/usage.py:110`, `:283`; `SPEC.md:719`).
- Parses `applyAuthResult: email=..., authMethod=...`, masks the email, never persists it (`src/agy_mcp/usage.py:39`, `:65-76`).

### 4.7 Transcript and permission config

**VERIFIED (code):**

- Transcript path: `<agy_home>/brain/<conversation_id>/.system_generated/logs/transcript.jsonl`, with UUID validation, no `..`, no symlinks, 5 MB cap/tail 2 MB (`src/agy_mcp/transcript.py:88-109`; `docs/DECISIONS.md:236-237`).
- `agy_home` default: `~/.gemini/antigravity-cli` (`src/agy_mcp/config.py:77`); resolved at job creation (`src/agy_mcp/manager.py:519`).
- Permission config candidates, in order: `~/.gemini/config/config.json`, `~/.gemini/antigravity-cli/settings.json`, `~/.gemini/settings.json`, `~/.gemini/config/config.json`, and `~/.gemini/config/projects/*.json` (`src/agy_mcp/worktree.py:628-642`).
- Dangerous-rule classifier: shells, interpreters, test runners, `git push`/`git branch`, `rm`, `curl`/`wget`, `ssh`/`scp`/`sftp`, and any rule containing `mcp` (`src/agy_mcp/worktree.py:486-552`).

### 4.8 Git and commits

**VERIFIED (code):**

- The worktree is created by the wrapper (`git worktree add`), not agy (`SPEC.md:372`).
- agy is told not to run git or tests and to write `.agy/COMMIT_MSG` instead (`src/agy_mcp/preamble.md:3-15`).
- The watcher validates/stages/commits `.agy/COMMIT_MSG` and runs a 10-minute checkpoint/safety-net commit (`src/agy_mcp/watcher.py:225-301`, `:489`; `src/agy_mcp/gitops.py:569`).
- The wrapper's commit path honors repo hooks; other git calls bypass hooks via `core.hooksPath=/dev/null` (`src/agy_mcp/util.py:58-89`; `docs/DECISIONS.md:491-499`).
- No push, no merge into main; cleanup is explicit (`SPEC.md:4`, `docs/DECISIONS.md:18`).

### 4.9 MCP tools to CLI actions

**VERIFIED (code):**

- `agy_start` → worktree + fresh `agy -p` (`src/agy_mcp/manager.py:405-534`).
- `agy_resume` → audit gate + `agy --conversation <id> -p` (`src/agy_mcp/manager.py:1309-1440`).
- `agy_cancel` → SIGTERM/SIGKILL process group; no agy CLI command (`src/agy_mcp/manager.py`, `src/agy_mcp/runner.py:300-336`).
- `agy_usage` → `agy -p "/usage"` (cached) (`src/agy_mcp/usage.py:683-724`).
- `agy_status`, `agy_wait`, `agy_result`, `agy_review`, `agy_list`, `agy_cleanup` → no new agy process; they read state, files, git, transcripts (`src/agy_mcp/server.py`, `src/agy_mcp/manager.py`).

---

## 5. Capability table

One table, as requested. `yes` means agy-mcp passes the flag or directly relies on the behavior; `no` means it neither passes it nor relies on it; `yes (indirect)` means it relies on agy's use of a config/behavior without passing a flag.

| Capability | Flag / command | Used by agy-mcp? | Where | Notes |
|---|---|---|---|---|
| Non-interactive single prompt | `-p`, `--print`, `--prompt` | yes | `src/agy_mcp/runner.py:165` | VERIFIED (help): three aliases; only `-p` is emitted, always last. |
| Interactive initial prompt | `-i`, `--prompt-interactive` | no | — | VERIFIED (help): continues interactively; unsuitable for MCP stdio. |
| New conversation | default, no flag | yes | `src/agy_mcp/manager.py:465-471` | VERIFIED (help/code): fresh runs omit `--conversation`. |
| Continue most recent | `-c`, `--continue` | no | `src/agy_mcp/runner.py:22-29` | VERIFIED (help): `-c` is forbidden; `--continue` is never emitted. Rationale DOCS: it may resume another job's conversation. |
| Resume by ID | `--conversation <id>` | yes | `src/agy_mcp/runner.py:162-163`; `src/agy_mcp/manager.py:1404` | VERIFIED (help/code): passed only when `job.conversation_id` is truthy; no UUID validation. |
| Conversation storage | no flag; reads transcript | yes (indirect) | `src/agy_mcp/transcript.py:95-109`; `src/agy_mcp/config.py:77` | DOCS (v1.4.7): `~/.gemini/antigravity-cli/brain/<id>/...`; help is silent; 1.4.10 unverified. |
| Model selection | `--model` | yes | `src/agy_mcp/runner.py:154-155` | VERIFIED (help/code): always passed. |
| Reasoning effort | `--effort` | no | `src/agy_mcp/runner.py:22-29` | VERIFIED (help): `low`, `medium`, `high`, `xhigh`, `max`; banned because the model ID encodes the level. |
| Agent selection | `--agent` | no | — | VERIFIED (help): exists; unused. |
| List agents | `agy agent`, `agy agents` | no | — | VERIFIED (help): lists available agents; not run by me. |
| List models | `agy models` | no | — | VERIFIED (help): lists available models; not run by me. |
| Execution mode | `--mode` | yes | `src/agy_mcp/runner.py:146-147` | VERIFIED (help/code): only `accept-edits`; `auto` and `plan` unused. |
| Sandbox | `--sandbox` | no | `src/agy_mcp/runner.py:23` | VERIFIED (help): forbidden. DOCS (v1.4.7): silently/softly falls back when bwrap/user namespaces fail. |
| Skip permissions | `--dangerously-skip-permissions` | no | `src/agy_mcp/runner.py:26` | VERIFIED (help): auto-approves all; forbidden by policy. |
| Session settings | `--settings` | no | — | VERIFIED (help): JSON object/file, session-only; unused. |
| Output format | `--output-format` | yes | `src/agy_mcp/runner.py:148-149` | VERIFIED (help/code): only `json`; `text`/`stream-json` unused. |
| Input format | `--input-format` | no | — | VERIFIED (help): `text` or `stream-json`; unused. |
| JSON schema | `--json-schema` | no | — | VERIFIED (help): schema string/file; unused. |
| Print timeout | `--print-timeout` | yes | `src/agy_mcp/runner.py:150-151`; `src/agy_mcp/manager.py:463`, `:1396` | VERIFIED (help/code): `Ns`; default 0. DOCS (v1.4.7): requires a duration unit. |
| Log file override | `--log-file` | yes | `src/agy_mcp/runner.py:152-153` | VERIFIED (help/code): absolute `<jobdir>/cli.log`. DOCS: redirects the internal debug log. |
| Add directory | `--add-dir` | no | — | VERIFIED (help): repeatable; unused. |
| Project selection | `--project` | no | — | VERIFIED (help): ID/name; unused. |
| New project | `--new-project` | no | — | VERIFIED (help): unused. |
| Remote control at start | `--remote-control` | no | — | VERIFIED (help): unused; inappropriate for local unattended jobs. |
| Disable slash/skill expansion | `--disable-slash-commands` | no | — | VERIFIED (help): print-mode flag; **currently unused**; a historical smoke argv used it (`docs/reports/smoke/s4-argv-resume.json:7`). |
| CLI version | `--version` | no | — | VERIFIED: prints `1.4.10`; agy-mcp never queries it. |
| Help | `-h`, `--help` | no | — | VERIFIED: top-level and most subcommands. |
| MCP server management | `agy mcp add/remove/list/enable/disable` | no | — | VERIFIED (help): CRUD, stdio/http, `--env`, `--header`; unused. |
| Plugin management | `agy plugin list/import/install/uninstall/enable/disable/validate/link` | no | — | VERIFIED (help): unused. Note: plugin subcommands do not handle `--help` consistently. |
| Remote-control daemon | `agy remote-control start/status/stop` | no | — | VERIFIED (help): unused. |
| Mic server | `agy mic-serve` | no | — | VERIFIED (help): unused. |
| Install helper | `agy install` | no | — | VERIFIED (help): PATH/alias configuration; unused. |
| Self-update | `agy update` | no | — | VERIFIED (help): `--help` exits 2; unused. |
| Changelog | `agy changelog` | no | — | VERIFIED (help): unused. |
| Usage/quota reading | `-p "/usage"` | yes | `src/agy_mcp/usage.py:607-615` | VERIFIED (code): separate 30 s call; DOCS (D59): JSON `command.data.groups[].buckets[]`; help has no usage command. |
| Permission allow-rules | no flag; reads config | yes (indirect) | `src/agy_mcp/worktree.py:607-684` | DOCS (D63/S8): reads `~/.gemini/config/config.json` first plus fallbacks; help only documents `--settings`. |
| Auth / login | no subcommand | yes (indirect) | `src/agy_mcp/config.py:72`; `src/agy_mcp/usage.py:39` | VERIFIED (help): no auth command. DOCS (D63): pre-existing keyring auth; expired-login text UNVERIFIED; patterns default empty. |
| JSON result envelope | `--output-format json` | yes | `src/agy_mcp/parse_result.py:202-207` | DOCS (v1.4.7): `status`, `response`, `conversation_id`, `usage`, `num_turns`, `denied_actions`, `warnings`; help does not document the schema. |
| Timeout stderr marker | no flag | yes | `src/agy_mcp/parse_result.py:179` | DOCS (v1.4.7): exact string `[agy] print timeout after`; help is silent. |
| Exit-code classification | process return code | yes | `src/agy_mcp/parse_result.py:157-176` | VERIFIED observed: 0/1/2; DOCS: 0 on denial/timeout, 1 on model errors, 2 on flag errors; `1`/`2` short-circuit JSON parsing. |
| Denial extraction | `denied_actions` + `cli.log` | yes | `src/agy_mcp/parse_result.py:206`; `src/agy_mcp/cli_log.py:91-114` | DOCS (v1.4.7): JSON carries coarse actions; `cli.log` carries `Print mode: soft-denying ... at step N`. |
| Resolved model | `cli.log` line | yes | `src/agy_mcp/cli_log.py:81-88` | DOCS (D63): `model alias "X" resolved to "Y"`. |
| Transcript detail | no flag; reads file | yes (indirect) | `src/agy_mcp/transcript.py:95-109` | DOCS (D50): `<agy_home>/brain/<uuid>/.../transcript.jsonl`; UUID-validated; 5 MB cap. |
| Process-group interrupts | SIGTERM/SIGKILL | yes | `src/agy_mcp/runner.py:300-336` | VERIFIED (code): no agy cancel command; wrapper signals the group. |
| Working directory | process cwd, no flag | yes | `src/agy_mcp/manager.py:482`, `:1420` | VERIFIED (code/help): `<jobdir>/work`; help has no cwd flag. |
| Git worktree/branch | git CLI, not agy | yes | `SPEC.md:372`; `src/agy_mcp/worktree.py` | VERIFIED (code): wrapper-owned. |
| Wrapper-owned commits | `.agy/COMMIT_MSG` | yes | `src/agy_mcp/watcher.py:225`, `:489` | VERIFIED (code): watcher commits; DOCS: agy is told not to run git. |
| Hooks | no flag | no (audited) | `src/agy_mcp/audit.py:502-521`; `src/agy_mcp/worktree.py:145-152` | DOCS (v1.4.7): `.agents/hooks.json` auto-executes; help is silent; wrapper refuses tracked `.agents` and kills on modification, but cannot disable hooks via CLI. |
| MCP tool calls | no session flag | yes (indirect) | — | DOCS (v1.4.7): `call_mcp_tool` is gated by `mcp(...)` rules; UNTESTED. Wrapper inherits global MCP config. |
| Plugins/skills | `agy plugin ...`; `--disable-slash-commands` | no | — | VERIFIED (help): no per-job control; `--disable-slash-commands` covers slash/skill expansion, not plugin loading. |
| Sandbox fallback | `--sandbox`, `--mode auto` | no | `SPEC.md:444` | DOCS (v1.4.7): soft fallback; help does not mention fallback. |
| Print timeout 0 | `--print-timeout 0` | no | — | VERIFIED (help): waits until the turn completes; wrapper always passes >0. |
| Conversation validation | no flag | yes | `src/agy_mcp/runner.py:162-163` | VERIFIED (code): only truthiness; no UUID check before `--conversation`. |
| Resume with missing ID | no flag | yes | `src/agy_mcp/runner.py:162-163` | VERIFIED (code): silently omits `--conversation`, starting a new conversation. |
| Budget consistency | no flag | yes | `src/agy_mcp/manager.py:1396`; `src/agy_mcp/supervisor.py:143` | VERIFIED (code): print timeout uses remaining budget; supervisor deadline uses full per-run budget. |
| Env allowlist | wrapper env | yes | `src/agy_mcp/util.py:80-89` | VERIFIED (code): only HOME/PATH/LANG/TMPDIR/GIT_CONFIG. |
| `XDG_CONFIG_HOME` | wrapper config only | yes (indirect) | `src/agy_mcp/config.py:295-297` | VERIFIED (code): not passed to agy. |
| Model allowlist | wrapper config | yes | `src/agy_mcp/config.py:39-48` | VERIFIED (code): alias + low/medium/high variants. |
| Conversation list/delete | no documented command | no | — | UNKNOWN: help has no conversation management command. |

---

## 6. Three lists

### 6.1 Features we use but may be using wrongly

1. **Conversation-ID fallback can persist a non-ID token.** `_extract_conversation_id` scans the `cli.log` tail with `conversation(?:_id)?[:\s]+([a-zA-Z0-9_-]+)` (`src/agy_mcp/supervisor.py:240-248`). On the real `docs/reports/smoke/s1-cli.log`, the first match is the line `Conversation using project ID: default-cli-project`, so the regex captures `using`. **VERIFIED** by replaying the regex read-only:

   ```text
   first match: Conversation using -> using
   first 10 captured (UUIDs masked): ['using', '<uuid>', '<uuid>', ...]
   ```

   The primary stdout-JSON path normally wins. But `_finalize_non_running_process` runs on watcher-driven transitions (invalid `COMMIT_MSG`, partial commit, audit) and saves the fallback ID (`src/agy_mcp/supervisor.py:399-403`, `:556-566`). If a fresh run is killed by the watcher before stdout JSON exists, `job.conversation_id` becomes `using`. `resume` then passes `--conversation using` (`src/agy_mcp/runner.py:162-163`), and nothing validates it first. The transcript reader would reject it as a non-UUID (`src/agy_mcp/transcript.py:100-104`), but resume already sent it to agy. **Fix direction:** accept only a UUID from the stdout JSON or an anchored `Created conversation <uuid>`/`Print mode: conversation=<uuid>` log pattern; never persist a generic word; refuse resume with a clear `conversation_id_missing` error rather than sending garbage.

2. **A hard timeout can silently lose the conversation.** If the supervisor deadline kills the process before it writes the final JSON **and no ID was persisted earlier** (typically a fresh run), `parse_result` returns `timeout` with `conversation_id=None` (`src/agy_mcp/parse_result.py:145-155`, `:178-188`). `build_argv` omits `--conversation` when the ID is falsy (`src/agy_mcp/runner.py:162-163`), so `agy_resume` starts a **new** conversation with the preamble/state block, not a continuation. There is no warning or state error. This is a likely 3–6 hour failure mode: long runs hit the wrapper deadline, not agy's own `--print-timeout`, and lose context. **Fix direction:** persist the ID from `cli.log`/JSON eagerly, require it for resume, or recover it from the agy conversation DB/transcript; if absent, return a resumable error that names the problem.

3. **Timeout detection is an English stderr substring.** `parse_result` treats `"[agy] print timeout after"` in stderr as timeout (`src/agy_mcp/parse_result.py:179`). The help does not document this marker. If 1.4.10 changes the wording or localizes it, the same payload (`status: "SUCCESS"`, empty `response`, zero commits) becomes `done` with a `no_commits` warning instead of `timeout`. **Fix direction:** treat elapsed time near `--print-timeout` plus empty response as timeout, and keep the marker as one signal, not the only one.

4. **Exit codes 1/2 short-circuit JSON parsing.** `parse_result` classifies rc `1`/`2` as `cli_error` before looking at stdout (`src/agy_mcp/parse_result.py:167-176`). agy uses 1 for bad model and 2 for flag errors (DOCS), but if an internal error exits 1 after emitting a valid final JSON, the wrapper discards the payload and its `conversation_id`. **Fix direction:** parse a present JSON object first; use rc as a secondary signal.

5. **Budget enforcement is inconsistent across resumes.** Resume's `--print-timeout` uses the remaining budget (`max(60, remaining*60)`, `src/agy_mcp/manager.py:1396`), but the supervisor deadline is `min(max_minutes, cap)*60 + 120` from the *current* `launch_wall_clock` (`src/agy_mcp/supervisor.py:139-143`), i.e. a fresh full per-run budget. If agy hangs before honoring its print timeout, a resumed run can exceed the cumulative cap the manager checked at resume time. **Fix direction:** compute the supervisor deadline from `effective_limit - cumulative_minutes` as well, or clamp it to the remaining budget.

6. **Usage relies on slash-command expansion.** The usage reader runs `-p "/usage"` (`src/agy_mcp/usage.py:607-615`), but `--disable-slash-commands` exists specifically to disable slash command/skill expansion. If that hardening flag is later applied globally (or to the usage call by mistake), `/usage` may stop working. **Fix direction:** keep the usage call separate (it already is) and document that it must not receive `--disable-slash-commands`; add a test that asserts the usage argv does not contain it.

### 6.2 Unused features that would help reliability, resume, or 3–6 hour unattended runs

1. **`--input-format stream-json` + `--output-format stream-json`.** **VERIFIED (help):** stream-json reads one NDJSON message per line and runs a turn for each; it requires stream-json output. **DOCS (v1.4.7, `docs/AGY_FINDINGS.md:150-176`, `docs/AGY_FINDINGS_2.md:205-243`):** it emits `init`/`step_update`/`result` events, supports multiple turns over one process, and exposes live progress. For 3–6 hour jobs this removes the respawn/`--conversation` handoff, gives real liveness, and allows nudges without a new process. Caveat: headless permissions still auto-deny, and the supervisor/parser would need a redesign; this is the largest potential reliability win.

2. **`--disable-slash-commands`.** **VERIFIED (help):** disables slash command and skill expansion in print mode. For untrusted task text/repositories, this closes an expansion path; a historical smoke resume argv already used it (`docs/reports/smoke/s4-argv-resume.json:7`), but current `src/` does not. Add it to the job argv (not to the `/usage` call) and test both.

3. **`--settings`.** **VERIFIED (help):** session-only settings JSON/file, never saved. This is the only documented way to pin per-job settings without touching global `~/.gemini` config. If it can carry `permissions.allow` (UNKNOWN), agy-mcp could make each job's permission surface explicit and reproducible instead of inheriting the user's mutable global rules. Investigate the accepted keys before relying on it.

4. **`--json-schema`.** **VERIFIED (help):** schema string/file to enforce structured output; for stream-json, applies to the final result. Could require `status`, `conversation_id`, `response` and fail closed on schema violations, reducing the "last object-shaped line" parser's fragility. Low effort if the schema is accepted in `json` mode.

5. **`agy --version` as a preflight gate.** **VERIFIED:** the installed binary is `1.4.10`; the probes are `1.4.7` (`docs/SMOKE_FINDINGS.md:4`, `docs/SCAFFOLD_NOTES.md:289`). agy-mcp never checks. A startup check could warn on untested versions and prevent a silent flag/schema drift from corrupting state. This is a small, high-value reliability fix.

6. **`agy models` / `agy agent` as a preflight (cached).** **VERIFIED (help):** both list available models/agents. Running them before `git worktree add` could fail fast on an unallowlisted model/agent, instead of creating a worktree and then failing with a model error. Caveat: they may hit the backend; cache and make it optional.

7. **`agy mcp list` as an operator/audit preflight.** **VERIFIED (help):** lists configured MCP servers. The wrapper currently inherits all global MCP configuration and has no per-job MCP flag. A read-only preflight could record which MCP servers a job could call and warn on unexpected ones. Caveat: it reads user config and may be sensitive; not run here.

8. **`--agent` pinning.** **VERIFIED (help):** selects the agent for the session. If the user's default agent changes, job behavior changes silently. Pinning an explicit agent (once `agy agent` names are understood) would improve reproducibility. Semantics/output are UNKNOWN; investigate first.

9. **`--project`/`--new-project`.** **VERIFIED (help):** selects/creates an agy project. A per-job project could isolate conversation summaries and project-level permissions, which matters for long unattended runs. But project permissions are machine-local (`docs/AGY_FINDINGS_2.md:269-290`) and a new project might inherit defaults; UNKNOWN and potentially risky. Investigate, don't adopt blindly.

10. **`--print-timeout 0`.** **VERIFIED (help):** waits until the turn completes. This is *not* recommended as-is: it removes agy's clean timeout JSON and leaves only the supervisor's SIGKILL, which is exactly the path that loses the conversation ID. Keep the current `budget` + 120 s grace.

11. **`--add-dir`.** **VERIFIED (help):** adds a directory to the workspace. Not recommended for the current isolation model; it widens the writable/readable workspace and contradicts the preamble's "work strictly within the current working directory."

12. **`--dangerously-skip-permissions`.** **VERIFIED (help):** auto-approves all tools. It would remove permission denials for headless jobs, but it destroys the permission/audit boundary and is explicitly prohibited by `SPEC.md:18`. Do not use; listed here only to close the loop.

### 6.3 Things we assume about agy that nothing in the help or docs backs up

1. **JSON schema stability.** The wrapper hard-depends on `status`, `response`, `conversation_id`, `tokens`/`usage`, `num_turns`, `denied_actions`, `warnings` (`src/agy_mcp/parse_result.py:202-207`). `agy --help` documents only "json"; the schema is from v1.4.7 probes (`docs/AGY_FINDINGS.md:130`, `docs/SMOKE_FINDINGS.md:201`). Nothing says 1.4.10 is identical. **Label: DOCS (v1.4.7) / UNKNOWN for 1.4.10.**

2. **Conversation storage path.** `<agy_home>/brain/<uuid>/.system_generated/logs/transcript.jsonl` is from a v1.4.7 probe (`docs/AGY_FINDINGS.md:376-384`), not help. agy-mcp reads it for denied-call detail (`src/agy_mcp/transcript.py:95-109`). If 1.4.10 moved the brain directory, the wrapper silently returns "no denied-call detail." **Label: DOCS (v1.4.7) / UNKNOWN for 1.4.10.**

3. **`/usage` is a valid print prompt and its output schema.** There is no `usage` subcommand in help; the wrapper's `-p "/usage"` and `command.data.groups[].buckets[]` parser are backed only by D59 and a committed fixture (`docs/DECISIONS.md:316-317`, `SPEC.md:719`, `SPEC.md:877`). The help does not list slash commands. **Label: DOCS (fixture) / UNKNOWN for live 1.4.10.**

4. **Exit 0 on permission denial and print timeout.** The state machine's `denied_without_progress` and `timeout` branches depend on this (`src/agy_mcp/parse_result.py:179`, `:226-250`). It is a v1.4.7 probe result (`docs/AGY_FINDINGS.md:184-204`, `:262-274`), not help. **Label: DOCS (v1.4.7) / UNKNOWN for 1.4.10.**

5. **The permission-config path and priority.** D63 says `~/.gemini/config/config.json` is what the real CLI loads (`docs/DECISIONS.md:355`); S8 says project JSON and `~/.gemini/antigravity-cli/settings.json` outrank it (`docs/AGY_FINDINGS_2.md:269-280`). The help only documents `--settings`. agy-mcp probes all candidates (`src/agy_mcp/worktree.py:628-642`), but its "effective rules" are a best-effort union, not a verified copy of agy's view. **Label: DOCS contradiction / UNKNOWN.**

6. **`GIT_CONFIG_*` enforcement by agy's embedded git.** The wrapper relies on `GIT_CONFIG_COUNT`/`core.hooksPath` to bypass repo hooks for non-commit git calls (`src/agy_mcp/util.py:73-89`). `SPEC.md:873` explicitly lists "`GIT_CONFIG_COUNT` enforcement" as **Assumed Safe**, not verified. **Label: DOCS (assumed) / UNKNOWN.**

7. **Auth error text.** D63 says the expired-login signature is still unknown and `auth_error_patterns` must stay empty (`docs/DECISIONS.md:361`); the help has no auth command. The wrapper's reliable signal is the `/usage` reading, which is itself schema-dependent. If `/usage` fails for a non-auth reason, an expired login may be misdiagnosed or missed. **Label: DOCS (unverified) / UNKNOWN.**

8. **Hooks and MCP execution.** `.agents/hooks.json` auto-execution is a v1.4.7 probe that explicitly did not actively fire a hook (`docs/AGY_FINDINGS_3.md:262`, `:320`); `call_mcp_tool` was UNTESTED (`docs/AGY_FINDINGS_2.md:195`). The help says nothing about hooks or per-session MCP. The wrapper audits `.agents/**` but cannot disable global hooks/plugins/MCP via a documented flag. **Label: DOCS (assumed/untested) / UNKNOWN.**

9. **`--conversation` cwd/context behavior.** `SPEC.md:872` says it is "Settled (Verified Safe)" from S4, but `docs/DS_REVIEW_2.md:575` lists real-agy `--conversation` semantics as unverified. Help says only "Resume a previous conversation by ID." The wrapper assumes resuming with a new `cwd` still works in the worktree. **Label: DOCS (contradictory) / UNKNOWN.**

10. **No version compatibility contract.** The installed binary is 1.4.10; all probes/smokes are 1.4.7 (`docs/SMOKE_FINDINGS.md:4`, `docs/SCAFFOLD_NOTES.md:289`). agy-mcp never checks `agy --version`. Nothing in help or repo docs says the flags, JSON fields, log lines, exit codes, or storage paths are stable across versions. **Label: UNKNOWN.**

11. **`--log-file` append/truncate semantics across resume generations.** agy-mcp reuses the same `<jobdir>/cli.log` for every run generation and reads its tail (`src/agy_mcp/manager.py:1391`, `src/agy_mcp/cli_log.py:34-44`). The help only says "Override CLI log file path"; neither help nor repo docs say whether agy appends or truncates on a new run. The conversation-ID fallback is especially sensitive to stale/old lines. **Label: UNKNOWN.**

12. **`--settings` merge semantics and accepted keys.** The help says "a JSON object of settings.json keys" but does not say which keys, whether they merge with global/project settings, or whether permissions/hooks/MCP can be pinned. Any plan to use `--settings` for per-job isolation is currently **UNKNOWN**.

13. **`agy`'s need for env vars beyond the allowlist.** The wrapper passes only HOME/PATH/LANG/TMPDIR/GIT_CONFIG (`src/agy_mcp/util.py:80-89`). Help documents no env requirements; the probes exercise a stripped environment, but auth/keyring/proxy/locale requirements are not documented. This is **UNKNOWN**, and a failure would look like an auth or network error rather than an env error.

14. **`agy` never commits.** The wrapper's design says commits are wrapper-owned (`docs/DECISIONS.md:491-499`), but the enforcement is the preamble and default permission denials, not a CLI guarantee. The dangerous-rule classifier flags `git push`/`git branch` but not `git add`/`git commit` (`src/agy_mcp/worktree.py:540-542`). If the user's allow rules include `command(git add)`/`command(git commit)`, agy can commit. Nothing in help says agy will not. **Label: VERIFIED (code gap) / UNKNOWN (runtime).**

---

## 7. What this means for agy-mcp (short)

- **VERIFIED (code):** the wrapper uses a deliberately small, stable subset: `--mode accept-edits`, `--output-format json`, `--print-timeout`, `--log-file`, `--model`, `--conversation`, `-p`. That is a sensible baseline.
- **VERIFIED (code):** the weakest points are conversation-ID handling, hard-timeout recovery, and the assumption that v1.4.7's JSON/log/exit behavior still holds on 1.4.10.
- **DOCS/UNKNOWN:** the wrapper's security model depends on agy behaviors that are not in `agy --help` (permission config path, `.agents/hooks.json`, MCP gating, `GIT_CONFIG_*`, auth text). Those are probe-level assumptions, not contracts.
- **VERIFIED (help):** the unused features most worth investigating are `--input-format stream-json`/`--output-format stream-json` (live progress, multi-turn), `--disable-slash-commands` (hardening), `--settings` (per-job pinning), `--json-schema` (structured output), and a `--version` preflight.

---

## 8. Evidence index

### 8.1 Raw help captures

All raw help text is in §2.1 and §2.2. Exit codes from those runs are in §2.4.

### 8.2 Real-log regex replay

Read-only command used for §6.1 item 1:

```python
import re
p = 'docs/reports/smoke/s1-cli.log'
t = open(p, encoding='utf-8', errors='replace').read()
m = re.search(r"conversation(?:_id)?[:\s]+([a-zA-Z0-9_-]+)", t, re.IGNORECASE)
print('first match:', m.group(0)[:80] if m else None, '->', m.group(1) if m else None)
```

Output:

```text
first match: Conversation using -> using
```

The source line is `docs/reports/smoke/s1-cli.log:179` (`Conversation using project ID: default-cli-project`). The same regex is at `src/agy_mcp/supervisor.py:244`.

### 8.3 Repo files read (for claims)

- Code: `src/agy_mcp/runner.py`, `manager.py`, `supervisor.py`, `usage.py`, `parse_result.py`, `cli_log.py`, `transcript.py`, `worktree.py`, `audit.py`, `server.py`, `config.py`, `util.py`, `watcher.py`, `gitops.py`, `models.py`, `preamble.md`.
- Contracts: `SPEC.md` (especially §10, §15, §17, §21), `docs/DECISIONS.md` (D50, D59, D63, D80), `docs/QUESTIONS.md`.
- Probes: `docs/AGY_FINDINGS.md`, `docs/AGY_FINDINGS_2.md`, `docs/AGY_FINDINGS_3.md`, `docs/SMOKE_FINDINGS.md`, `docs/SCAFFOLD_NOTES.md`.
- Reviews/reports: `docs/DS_REVIEW_1.md`, `docs/DS_REVIEW_2.md`, `docs/reports/real-smoke-1/REPORT.md`, `docs/reports/smoke/s1-cli.log`, `docs/reports/smoke/s4-argv-resume.json`.
- Tests (contract evidence only): `tests/fake_agy/agy`, `tests/fake_agy/README.md`, `tests/test_runner.py`, `tests/test_parse_result.py`.

### 8.4 Not verified

- Any 1.4.10 runtime behavior that requires a model turn: JSON schema, denial shape, timeout wording/exit code, `/usage` payload, auth behavior, cwd preservation on resume, SIGTERM handling.
- Any path under `~/.gemini` (not read): actual conversation layout, settings priority, auth tokens, MCP/plugin configuration.
- `agy models`, `agy agent`, `agy changelog`, `agy mcp list`, `agy plugin list`, install/update/remote-control/mic-serve output.
- Hidden flags or env vars not printed by `--help`.

---

## 9. Recommended follow-ups (for the owner, not implemented here)

1. **Fix conversation-ID handling first.** Validate UUIDs, anchor the `cli.log` fallback to `Created conversation <uuid>`/`Print mode: conversation=<uuid>`, and refuse resume without a verified ID. This is a correctness bug, not a feature request.
2. **Make hard-timeout recovery explicit.** If the supervisor kills agy before JSON, persist the last known ID from `cli.log` (validated) and mark the job `timeout` with a clear `conversation_id_missing` warning if none exists.
3. **Add a version preflight.** Run `agy --version` at startup, record it in the envelope/state, and warn when it differs from the tested version.
4. **Trial `--disable-slash-commands`** on job runs (not on the `/usage` call) and add a test that both argv builders keep the distinction.
5. **Prototype stream-json** in a branch: one process, NDJSON turns, `result` event as the final parse, `step_update` as liveness. Keep the current print-mode path as fallback.
6. **Re-probe on 1.4.10** with a throwaway repo and a designated real-run step: JSON fields, denial shape, timeout wording/exit code, `/usage` payload, `--conversation` cwd, and `.agents/hooks.json` behavior. Do not treat v1.4.7 probes as contracts.
7. **Document the version gap** in `SPEC.md`/`docs/DECISIONS.md`: which behaviors are verified on which agy version.
