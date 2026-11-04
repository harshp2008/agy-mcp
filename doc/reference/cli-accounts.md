# `agy-mcp account`: the human account CLI

Status: current
Audience: users, operators, maintainers

`agy-mcp account` manages the isolated Google accounts in
`~/.config/agy-mcp/config.toml`. It is a **human-run** command: `add` requires
an interactive terminal and hands that terminal to an isolated `agy` login. The
wrapper never authenticates, never reads a token/keyring/cookie file and never
prints a full email address.

```bash
agy-mcp account list [--probe]
agy-mcp account add <name> [--pool L] [--max-concurrent N] [--home PATH] [--yes]
agy-mcp account remove <name> [--delete-home] [--accounts-root PATH] [--yes]
agy-mcp account enable <name> [--yes]
agy-mcp account disable <name> [--yes]
```

`python -m agy_mcp account ...` is the same command when the console script is
not on `PATH`. Running `agy-mcp` (or `python -m agy_mcp`) with **no** subcommand
still starts the MCP stdio server exactly as before; only a literal first
argument `account` routes to this CLI.

The design decision is D117 in [the decisions log](../design/decisions.md); the
user-facing recipe is [Run jobs on more than one Google account](../using/accounts.md).

## Common options

| Option | Meaning |
|---|---|
| `--config PATH` | Config file. Default: `$XDG_CONFIG_HOME/agy-mcp/config.toml`, else `~/.config/agy-mcp/config.toml`. |
| `--state-dir PATH` | State store holding `jobs/`. Default: `$AGY_MCP_STATE_DIR`, `$XDG_STATE_HOME/agy-mcp`, else `~/.local/state/agy-mcp`. Used to count and inspect pinned jobs. |
| `--agy-binary PATH` | The `agy` executable. Default: `agy` on `PATH`. Required only for `add`, `list --probe` and `add`'s doctor checks. |
| `--yes` | Apply the change. Every writing command is a **dry run** without it. |
| `--dry-run` | Show the plan even when `--yes` is also present. Wins over `--yes`. |

## `list`

Offline by default. One line per registry account, the implicit `default`
first:

```text
account default: enabled=yes pool=- max_concurrent=2 home=real-home active=0 paused=0 identity=none expected_hash=no
account main: enabled=yes pool=- max_concurrent=2 home=~/.local/share/agy-accounts/main mode 0700 active=1 paused=0 identity=sha256-present expected_hash=yes
```

- `active` counts `queued` and `running` (legacy `starting` also counts).
- `paused` counts every non-terminal state that is not active:
  `needs_decision`, `needs_review` (including a quota-paused job) and `timeout`.
- `home` shows the real home for the implicit default, otherwise the configured
  path with its mode (`symlink`/`missing`/`not-a-dir` are reported instead).
- `identity` is `sha256-present`, `plaintext-field(<masked>)` for a legacy
  `expected_email`, or `none`; `expected_hash=yes/no` is the same fact spelled
  out. A full email is never printed.
- `list` never writes: it reads `config.toml` and `jobs/*/job.json` only.

`--probe` additionally runs one isolated `/usage` per account (the default
account uses its normal environment; declared accounts use their own `HOME` plus
the dead D-Bus address) and prints the masked observed email and quota:

```text
  probe: observed=pla***on@example.com quota=Gemini Models weekly=80% 5h=90%
  probe: observed=*** unavailable(auth_required)
```

Probe failures are informational; `list` exits 0. `--timeout` (default 15 s)
caps each probe.

## `add <name>`

`add` is interactive and idempotent. Without `--yes` it prints the plan and the
block text, changes nothing and exits 0. With `--yes` it:

1. validates the name (`[a-z0-9][a-z0-9-]{0,31}`, not `default`), the home
   (absolute after `~` expansion) and `max_accounts` (the total includes the
   implicit default);
2. requires stdin **and** stdout to be terminals — this command must never run
   from an agent;
3. creates the home as a real directory with mode 0700 (an existing directory is
   kept and tightened; a symlink is refused);
4. for a fresh or empty home, proves isolation by running the isolated
   `/usage` and requiring `Authentication required` with **no** quota rows. Any
   quota rows are a keyring leak (`account_cli_isolation_leak`, exit 5) and no
   login starts. A non-empty home is treated as a resume and skips this proof;
5. launches interactive `agy` in the isolated environment (`HOME=<home>`, dead
   `DBUS_SESSION_BUS_ADDRESS`) attached to the terminal, so the **human** signs
   in;
6. verifies the isolated `/usage`, asks for the expected email with hidden input
   (`getpass`), compares the SHA-256 of the typed value with the observed login's
   digest, and stores **only** `expected_email_sha256` (never the address);
7. copies the primary permission policy (`AGY_PERMISSION_CONFIG_REL`, mode 0600)
   if the primary has one, and writes a minimal
   `.gemini/antigravity-cli/settings.json` (`{"trustedWorkspaces": [...]}` from
   `allowed_workdir_roots`, mode 0600). Neither file is overwritten if it
   already exists;
8. prints the exact `# agy-mcp:account <name> begin/end` block, appends (or
   re-renders) it, and writes `config.toml` under a file lock with a timestamped
   backup;
9. runs the per-account doctor checks and prints them;
10. ends with `restart dsh: systemctl --user restart dsh.service`.

Options:

| Option | Meaning |
|---|---|
| `--pool L` | Shared quota-bucket label. |
| `--max-concurrent N` | Per-account concurrency cap (integer >= 1). |
| `--home PATH` | Home instead of `~/.local/share/agy-accounts/<name>`. |
| `--timeout SECONDS` | Isolation and verification probe timeout (default 45 s). |

If a step fails after the home exists, the home is kept and the command prints
what was done and the exact `add ... --yes` command to resume. A re-run with the
same options does not duplicate the block: an identical block is reported as
already present and nothing is rewritten. Re-running with a different home,
pool or cap is refused (`account_name_duplicate`, exit 4); remove the account
first.

## `remove <name>`

Removes **only** the `[[accounts]]` block (never the home or the login) after a
timestamped backup. The implicit `default` cannot be removed; use
`account disable default`.

Refusals:

| Situation | Result |
|---|---|
| Any job pinned to the account is `queued`/`running`/`starting` | Exit 3 with `A task is currently running on this account: <ids/titles>. Wait for it to finish or cancel it first.` |
| `needs_decision`, `needs_review` (including quota-paused) or `timeout` jobs are pinned | Prints a warning that they will become unresumable and requires the exact typed phrase `remove account <name>` before writing. |
| The account is `default` | Exit 3; message names `account disable default`. |
| No block matches the account | Exit 4 (`account_cli_block_not_found`). |
| A block is ambiguous/corrupt | Exit 3 (`account_cli_block_ambiguous`/`account_cli_block_corrupt`). |

`--delete-home` is an extra opt-in. It is allowed only when:

- the resolved home is a real directory, not a symlink;
- it is directly under `--accounts-root` (default `~/.local/share/agy-accounts`);
- no non-terminal job is pinned to the account (active jobs were already refused;
  paused jobs refuse the deletion too).

It then requires the exact typed phrase `delete account home <name>` and removes
that one path recursively (inner symlinks are unlinked, never followed). It
prints that the Google login may still be valid server-side and can be revoked
on the Google account security page.

## `enable <name> / disable <name>`

Flips `enabled` in the account's block, preserving comments and every other
line. `disable` never refuses, but warns when active jobs are pinned: they finish
and no new ones start. It also warns if no enabled account would remain.
`disable default` writes `[default_account] enabled = false` (creating the table
if needed); `enable default` flips it back.

## Writing rules

- Dry run is the default; `--yes` applies. `--dry-run` wins over `--yes`.
- Every write holds `.<config>.lock`, writes a timestamped
  `config.toml.<YYYYmmddTHHMMSS.ffffffZ>.bak`, then replaces the file with a
  temp file + `fsync` + `rename` (mode 0600).
- CLI-written blocks carry `# agy-mcp:account <name> begin` / `end`. A
  hand-written block is located by its `name = "..."` and only the span from its
  `[[accounts]]` header to the next table header (or EOF) is edited/removed, so
  comments and unrelated keys stay byte-identical.
- The whole edited file is re-parsed with `tomllib` and the real config schema
  before it is written; if the file changed since it was read, the write is
  refused.
- A running MCP server does not hot-reload `config.toml`; every writing command
  prints the restart hint.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success, including every dry run and every no-op. |
| `1` | Unexpected internal failure not covered by a stable code. |
| `2` | Usage or validation error: bad flag, invalid name, home, pool or cap, `max_accounts` reached, missing `agy`, missing/empty expected email. |
| `3` | Safety refusal: non-TTY `add`, active-job `remove`, wrong/missing typed phrase, default removal, ambiguous/corrupt block, config changed under the lock, unsafe `--delete-home`. |
| `4` | Conflict or missing target: unknown account, duplicate name, missing block. |
| `5` | Identity/isolation failure: keyring leak, isolation unproven, login failed, expected-email mismatch, health checks failed after a write. |
| `6` | Config or filesystem I/O failure: unreadable/invalid config, backup/lock/temp write failure, provisioning failure. |

## Error codes

| Code | Exit | Meaning |
|---|---|---|
| `account_cli_agy_missing` | 2 | `--agy-binary` is not executable and `agy` is not on `PATH`. |
| `account_cli_expected_email_required` | 2 | The hidden prompt was empty, interrupted, or not a valid address. |
| `account_cli_non_tty` | 3 | `add --yes` without a terminal on stdin and stdout. |
| `account_cli_jobs_active` | 3 | `remove` found an active job pinned to the account. |
| `account_cli_confirmation_required` | 3 | The paused-job or delete-home phrase did not match. |
| `account_cli_default_removal` | 3 | `remove default` was attempted. |
| `account_cli_delete_home_unsafe` | 3 | The home is a symlink, missing, not a directory, outside the root, or has a non-terminal job. |
| `account_cli_block_ambiguous` | 3 | More than one block/marker, or a marker whose payload names another account. |
| `account_cli_block_corrupt` | 3 | Unpaired/wrong markers or an unparseable block body. |
| `account_cli_config_changed` | 3 | `config.toml` changed between read and write. |
| `account_cli_isolation_leak` | 5 | A fresh home's isolated `/usage` returned quota rows. |
| `account_cli_isolation_unproven` | 5 | The isolated `/usage` did not say `Authentication required` for another reason. |
| `account_cli_login_failed` | 5 | The interactive login or the post-login `/usage` failed. |
| `account_cli_doctor_failed` | 5 | The block was written but a per-account doctor check failed. |
| `account_identity_mismatch` | 5 | The typed expected email does not match the observed login. |
| `account_identity_unknown` | 5 | agy reported no account email, so no digest can be recorded. |
| `account_cli_config_error` | 6 | The existing or edited config.toml is invalid. |
| `account_cli_write_failed` | 6 | The lock, backup, temp file or rename failed. |
| `account_cli_provision_failed` | 6 | The home, policy or settings file could not be created. |

The registry/validation codes (`account_name_invalid`, `account_home_invalid`,
`account_config_invalid`, `account_limit_exceeded`, `account_name_duplicate`,
`account_not_found`, `account_home_permissions`) keep their existing meanings
and map to exit 2/4/6 as shown above.

## Sources

- `src/agy_mcp/accounts/cli.py` — the parser, commands, output and exit mapping.
- `src/agy_mcp/accounts/config_edit.py` — the line-span edits, lock, backup and
  atomic write.
- `src/agy_mcp/accounts/provision.py` — the home, isolation probe, login seam,
  expected-email prompt and policy/settings provisioning.
- `src/agy_mcp/server.py` — `main` dispatches the literal `account` subcommand
  and otherwise starts the stdio server.
- `src/agy_mcp/accounts/doctor.py` — the per-account checks `add` prints.
- `src/agy_mcp/accounts/env.py` — the isolated child environment.
- `doc/design/decisions.md` — D117.
- `doc/using/accounts.md` — the user recipe and the manual equivalent.
