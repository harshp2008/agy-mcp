# agy-mcp

**Status: pre-release.** Interfaces, tool schemas and on-disk job records may
change without a deprecation cycle.

`agy-mcp` is an MCP (Model Context Protocol) stdio server that orchestrates
autonomous coding jobs run by the Google Antigravity CLI (`agy`). For each job
it creates an isolated git worktree and branch, launches `agy` in its own
process group with a sanitized environment, supervises it (wall-clock deadline,
periodic security audit, `COMMIT_MSG` watcher), runs an optional verification
command, and returns a standard result envelope through ten MCP tools. The
wrapper never merges or pushes: landing a change is your decision.

## Requirements

- **Linux (tested), macOS (untested), Windows via WSL (untested).** The
  installer is a bash script and the server uses POSIX process control. See
  [doc/developing/platform-support.md](doc/developing/platform-support.md) for
  the per-platform audit; native Windows is not supported.
- Python 3.10 or newer. The installer refuses anything older.
- `git` on `PATH`. Every job is a git worktree and commits are git commits.
- The `agy` CLI installed and signed in, on `PATH` or at `~/.local/bin/agy`.
  `AGY_MCP_AGY_BINARY` overrides the location. The installer never runs `agy`
  and never checks your login.

## Install

Clone the repository and run the installer from the checkout:

```bash
git clone https://github.com/harshp2008/agy-mcp.git
cd agy-mcp
./installer/install.sh --dry-run   # optional: print every write, change nothing
./installer/install.sh             # default client: generic
```

Run this from a normal terminal, not from inside a dsh session: the installer
refuses to change dsh registration while dsh is running the command. The
`--help` output is the authority for flags; the current set is:

| Flag | Effect |
|---|---|
| `--client dsh\|generic` | Target client. Default: `generic` (no dsh files). |
| `--dry-run` | Print every file and command; change nothing. |
| `--upgrade` | Reinstall `agy-mcp` from `AGY_MCP_SRC` even when the venv already imports it. |
| `--doctor` | Check `run.sh`, venv, `agy`, config and client blocks (read-only). |
| `--uninstall` | Remove only this chapter's blocks and generated files. |
| `--help` | Show usage. |

Environment equivalents: `AGY_MCP_CLIENT`, `AGY_MCP_DRY_RUN=1`,
`AGY_MCP_SRC`, `AGY_MCP_EDITABLE=1`, `AGY_MCP_UPGRADE=1`, `AGY_MCP_PYTHON`,
`AGY_MCP_PIP_ARGS`, `AGY_MCP_NO_RESTART=1`, `AGY_MCP_AGY_BINARY`.

The installer creates its own virtualenv under
`${CONF_DIR:-$HOME/.config/ai-stack}/agymcp` and installs this checkout into
it. It writes:

- `${CONF_DIR:-$HOME/.config/ai-stack}/agymcp/`: `venv/`, `run.sh` (mode 755),
  `env` (mode 600), `mcp-client-config.json`, `orchestrator-guidance.md`;
- `${XDG_CONFIG_HOME:-$HOME/.config}/agy-mcp/config.toml`, only when the file
  does not exist yet, with safe first-install defaults;
- the state directory `${XDG_STATE_HOME:-$HOME/.local/state}/agy-mcp` (mode
  0700);
- with `--client dsh` only: a marker block in
  `~/.dsh/profiles/web/cordis.patch.yml` and a guidance block in
  `~/.dsh/AGENTS.md`, both after a `.bak` backup.

A rerun skips pip when the venv already imports `agy_mcp`; use `--upgrade` to
reinstall. `--uninstall` removes only the generated files and marker blocks and
keeps your config, state directory and `.bak` backups.

### Register with your MCP client

The default (generic) install writes and prints this `mcpServers` snippet:

```json
{
  "mcpServers": {
    "agy": {
      "command": "/home/user/.config/ai-stack/agymcp/run.sh",
      "args": [],
      "env": {}
    }
  }
}
```

On your machine the `command` is the absolute path the installer wrote; use
that path. Paste the object into your client's `mcpServers` map and paste
`orchestrator-guidance.md` into its instruction file. Set the client's tool-call
timeout to at least **360000 ms**: `agy_wait` may block for up to 300 s and the
client needs headroom.

## Quick start

1. Run `agy` once yourself, sign in, and finish any first-run prompt. This is
   the one deliberate human step; the wrapper cannot do it for you.
2. In your MCP client, call `agy_usage` (read-only) to see the masked account
   and remaining quota before starting a long job.
3. Start a job on a git repository with at least one commit. The repository
   must be inside your configured `allowed_workdir_roots` (the installer's
   default is `[$HOME]`), must not be `$HOME` itself, and must not track a
   `.agents/` directory unless you set `trust_agents_dir = true`.

   ```json
   {"repo_path": "~/code/widget", "title": "Add a retry helper",
    "prompt": "Add a bounded retry helper in src/widget/retry.py with tests.",
    "verify": "pytest -q"}
   ```

   `agy_start` returns as soon as the job is queued. The worktree lives under
   the state directory at `jobs/<job-id>/work`; your checkout is not switched
   out or cleaned up by the wrapper.
4. Poll with `agy_status` or block with `agy_wait` (`seconds`, max 300), then
   read `agy_result` / `agy_review` for the response, commits, diff and audit
   findings. `agy_cancel` stops a runaway job.
5. If you accept the work, merge the job branch yourself. Then call
   `agy_cleanup`; it deletes the branch only when no commit becomes
   unreachable. A squash or cherry-pick merge still needs `force=true`.

The full tool list is `agy_start`, `agy_status`, `agy_wait`, `agy_result`,
`agy_cancel`, `agy_list`, `agy_resume`, `agy_review`, `agy_cleanup`,
`agy_usage`. Exact parameters and error codes are in
[doc/reference/mcp-tools.md](doc/reference/mcp-tools.md).

## Safety notes

- **There is no OS sandbox in this pre-release.** `agy` runs directly on the
  host as your user and can read and write what you can. The wrapper provides
  branch/worktree isolation, environment secret sanitization and tamper
  detection, not containment. Do not point it at a checkout whose secrets you
  cannot afford to expose; use a throwaway clone or a dedicated branch when in
  doubt.
- **What the wrapper writes:** the state directory (`jobs/<id>/work` worktree,
  logs, `job.json`, locks), git metadata in the target repository (worktree
  registration and an `.agy/` line in `.git/info/exclude`), and its own config
  when you create it.
- **What it reads but never writes:** a fixed set of host security files
  (`~/.ssh/*`, `~/.gnupg/*`, shell rc files, `~/.gitconfig`, `~/.netrc`) hashed
  for tamper detection, and one agy session transcript under
  `~/.gemini/antigravity-cli` for denied-call detail.
- **Shutdown is not a kill switch.** On stdin EOF or `SIGTERM` the server stops
  its supervisor threads without killing the background `agy` process group;
  the next startup reconciles jobs that are still running.
- **Cleanup is dry-run-first.** List first, then delete:

  ```bash
  python -m agy_mcp.cleanup_jobs --store ~/.local/state/agy-mcp --list
  python -m agy_mcp.cleanup_jobs --store ~/.local/state/agy-mcp          # dry run
  python -m agy_mcp.cleanup_jobs --store ~/.local/state/agy-mcp --yes    # delete
  ```

  `cleanup_jobs` only removes terminal job records whose worktree is no longer
  registered (it refuses a job whose worktree still exists) and it never deletes
  a git branch. `cleanup_fixtures` removes test-fixture rows only. Prefer
  `agy_cleanup` for a single job.
- **The installer is dry-run-first and surgical.** `--dry-run` changes nothing,
  it backs up dsh files to `.bak` before editing, and it refuses to run inside
  dsh.

## Documentation

- [doc/README.md](doc/README.md) — documentation index and reading order.
- [doc/using/install.md](doc/using/install.md) — installation and client setup.
- [doc/reference/mcp-tools.md](doc/reference/mcp-tools.md) — tool contracts.
- [doc/design/spec.md](doc/design/spec.md) — normative behavior contract.
- [doc/developing/gates.md](doc/developing/gates.md) — commands a change must
  pass.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and
[SECURITY.md](SECURITY.md) for private vulnerability reporting.

## License

Apache-2.0. See [LICENSE](LICENSE).
