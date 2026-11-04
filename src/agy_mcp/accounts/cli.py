"""Human-run account CLI: ``agy-mcp account list|add|remove|enable|disable`` (D117).

Owns the command-line surface, the fail-closed safety gates and every
human-facing line the account command prints. It never reads, copies or prints a
token/keyring/cookie, never logs in by itself (``add`` hands the terminal to an
isolated ``agy`` for the human), and never prints a full email address.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from agy_mcp.accounts import config_edit
from agy_mcp.accounts.doctor import _check_account, render_report
from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.identity import email_sha256, mask_email
from agy_mcp.accounts.provision import (
    ADD_PROBE_TIMEOUT_SECONDS,
    AUTH_REQUIRED_PATTERN,
    LoginRunner,
    _default_isatty,
    _default_prompt_email,
    accounts_root,
    default_home_value,
    ensure_account_home,
    home_is_empty,
    probe_isolation,
    provision_files,
    read_expected_email,
    require_interactive_terminal,
    run_interactive_login,
    verify_login,
)
from agy_mcp.accounts.registry import (
    DEFAULT_ACCOUNT_NAME,
    AccountConfig,
    build_registry,
    parse_account_entry,
    validate_account_home_value,
    validate_account_name,
    validate_account_pool,
    validate_max_concurrent_jobs,
)
from agy_mcp.config import Config, ConfigError, load_config
from agy_mcp.jobs import get_base_state_dir, job_belongs_to_account, list_jobs
from agy_mcp.models import Job
from agy_mcp.states import TERMINAL_STATES
from agy_mcp.usage import read_usage_once

RESTART_HINT = "restart dsh: systemctl --user restart dsh.service"

_ACTIVE_STATES = frozenset({"queued", "running", "starting"})
_TERMINAL_STATES = frozenset(state.value for state in TERMINAL_STATES)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

_EXIT_USAGE = 2
_EXIT_REFUSED = 3
_EXIT_CONFLICT = 4
_EXIT_IDENTITY = 5
_EXIT_IO = 6

_EXIT_BY_CODE = {
    "account_name_invalid": _EXIT_USAGE,
    "account_home_invalid": _EXIT_USAGE,
    "account_config_invalid": _EXIT_USAGE,
    "account_limit_exceeded": _EXIT_USAGE,
    "account_cli_expected_email_required": _EXIT_USAGE,
    "account_cli_agy_missing": _EXIT_USAGE,
    "account_name_duplicate": _EXIT_CONFLICT,
    "account_not_found": _EXIT_CONFLICT,
    "account_cli_block_not_found": _EXIT_CONFLICT,
    "account_cli_non_tty": _EXIT_REFUSED,
    "account_cli_jobs_active": _EXIT_REFUSED,
    "account_cli_confirmation_required": _EXIT_REFUSED,
    "account_cli_default_removal": _EXIT_REFUSED,
    "account_cli_delete_home_unsafe": _EXIT_REFUSED,
    "account_cli_block_ambiguous": _EXIT_REFUSED,
    "account_cli_block_corrupt": _EXIT_REFUSED,
    "account_cli_config_changed": _EXIT_REFUSED,
    "account_cli_doctor_failed": _EXIT_IDENTITY,
    "account_cli_isolation_leak": _EXIT_IDENTITY,
    "account_cli_isolation_unproven": _EXIT_IDENTITY,
    "account_cli_login_failed": _EXIT_IDENTITY,
    "account_identity_mismatch": _EXIT_IDENTITY,
    "account_identity_unknown": _EXIT_IDENTITY,
    "account_home_permissions": _EXIT_IO,
    "account_cli_config_error": _EXIT_IO,
    "account_cli_provision_failed": _EXIT_IO,
    "account_cli_write_failed": _EXIT_IO,
}


def _exit_code_for(code: str) -> int:
    return _EXIT_BY_CODE.get(code, 1)


def _safe(text: str) -> str:
    """Mask every email-shaped substring before it reaches a terminal."""
    return _EMAIL_RE.sub(lambda match: mask_email(match.group(0)), text)


def _out(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def _err(line: str = "") -> None:
    sys.stderr.write(line + "\n")


@dataclass
class CliSeams:
    """Injectable terminal/probe seams so the command is testable without a pty."""

    login_runner: LoginRunner = run_interactive_login
    isatty: Callable[[], bool] = _default_isatty
    prompt_email: Callable[[], str] = _default_prompt_email
    input_func: Callable[[str], str] = input


def _state_value(job: Job) -> str:
    state = job.state
    return state.value if hasattr(state, "value") else str(state)


def _pinned_jobs(state_dir: Optional[str], name: str) -> list[Job]:
    base = Path(state_dir) if state_dir else None
    return [job for job in list_jobs(get_base_state_dir(base)) if job_belongs_to_account(job, name)]


def _split_jobs(jobs: list[Job]) -> tuple[list[Job], list[Job]]:
    active = [job for job in jobs if _state_value(job) in _ACTIVE_STATES]
    paused = [
        job
        for job in jobs
        if _state_value(job) not in _ACTIVE_STATES
        and _state_value(job) not in _TERMINAL_STATES
    ]
    return active, paused


def _job_label(job: Job) -> str:
    title = " ".join(_safe(str(job.title)).split())[:80]
    return f"{job.job_id} ({title})" if title else job.job_id


def _load(args: argparse.Namespace) -> tuple[Config, Path, str]:
    path = config_edit.resolve_config_path(args.config)
    try:
        config = load_config(path=path)
    except ConfigError as exc:
        raise AccountError(
            f"config.toml is invalid: {exc}",
            code="account_cli_config_error",
        ) from exc
    return config, path, config_edit.read_config_text(path)


def _registry(config: Config):
    return build_registry(config, Path.home())


def _resolve_agy(args: argparse.Namespace) -> str:
    if getattr(args, "agy_binary", None):
        candidate = Path(args.agy_binary)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        raise AccountError(
            "the --agy-binary path is not an executable file",
            code="account_cli_agy_missing",
        )
    found = shutil.which("agy")
    if not found:
        raise AccountError(
            "'agy' was not found on PATH; pass --agy-binary PATH",
            code="account_cli_agy_missing",
        )
    return found


def _mode_text(account: AccountConfig) -> str:
    if account.is_default:
        return "real-home"
    home = account.expanded_home
    try:
        info = os.lstat(str(home))
    except OSError:
        return f"{_safe(str(home))} missing"
    import stat

    if stat.S_ISLNK(info.st_mode):
        return f"{_safe(str(home))} symlink"
    if not stat.S_ISDIR(info.st_mode):
        return f"{_safe(str(home))} not-a-dir"
    return f"{_safe(str(home))} mode {stat.S_IMODE(info.st_mode):04o}"


def _identity_text(account: AccountConfig) -> str:
    if account.expected_email_sha256 is not None:
        return "sha256-present expected_hash=yes"
    if account.expected_email is not None:
        return (
            "plaintext-field("
            f"{mask_email(account.expected_email)}) expected_hash=no"
        )
    return "none expected_hash=no"


def _quota_text(reading: dict) -> str:
    groups = reading.get("groups")
    if not isinstance(groups, list):
        return "none"
    parts: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        buckets = {
            str(bucket.get("window")): bucket
            for bucket in group.get("buckets", [])
            if isinstance(bucket, dict)
        }
        pieces: list[str] = []
        for window in ("weekly", "5h"):
            bucket = buckets.get(window)
            if not isinstance(bucket, dict):
                continue
            try:
                pct = float(bucket.get("remaining_fraction")) * 100.0
            except (TypeError, ValueError):
                continue
            pieces.append(f"{window}={pct:.0f}%")
        if pieces:
            parts.append(f"{group.get('name')} " + " ".join(pieces))
    return "; ".join(parts) if parts else "none"


def cmd_list(args: argparse.Namespace, seams: CliSeams) -> int:
    """Print the registry and per-account job counts; probe only with --probe."""
    del seams
    config, _, _ = _load(args)
    registry = _registry(config)
    jobs = list_jobs(get_base_state_dir(Path(args.state_dir) if args.state_dir else None))
    binary = _resolve_agy(args) if args.probe else None
    for account in registry.all():
        pinned = [job for job in jobs if job_belongs_to_account(job, account.name)]
        active, paused = _split_jobs(pinned)
        max_jobs = account.max_concurrent_jobs or config.max_concurrent_jobs
        _out(
            f"account {account.name}: enabled={'yes' if account.enabled else 'no'} "
            f"pool={account.pool or '-'} max_concurrent={max_jobs} "
            f"home={_mode_text(account)} active={len(active)} paused={len(paused)} "
            f"identity={_identity_text(account)}"
        )
        if binary is not None:
            reading = read_usage_once(
                binary,
                account=account,
                timeout_seconds=args.timeout,
                auth_error_patterns=[AUTH_REQUIRED_PATTERN],
            )
            info = reading.get("account")
            masked = info.get("masked_email") if isinstance(info, dict) else None
            observed = masked if isinstance(masked, str) else "***"
            if reading.get("available") is True:
                _out(f"  probe: observed={observed} quota={_quota_text(reading)}")
            else:
                _out(
                    f"  probe: observed={observed} "
                    f"unavailable({reading.get('reason') or 'unknown'})"
                )
    return 0


def _add_plan(
    name: str,
    home_value: str,
    pool: Optional[str],
    max_concurrent: Optional[int],
    expected_hash: Optional[str],
    enabled: bool,
) -> str:
    return config_edit.render_account_block(
        name=name,
        home=home_value,
        pool=pool,
        max_concurrent_jobs=max_concurrent,
        expected_email_sha256=expected_hash,
        enabled=enabled,
    )


def _print_resume(name: str, home_value: str, done: list[str]) -> None:
    _err("what was done:")
    for item in done:
        _err(f"  - {_safe(item)}")
    _err(f"the home was kept at {_safe(home_value)}")
    _err(
        "to resume: agy-mcp account add "
        f"{name} --yes"
        + (f" --home {home_value}" if home_value != default_home_value(name) else "")
    )


def cmd_add(args: argparse.Namespace, seams: CliSeams) -> int:
    """Provision one isolated account, handing the login to the human."""
    name = validate_account_name(args.name)
    home_value = args.home or default_home_value(name)
    home_path = validate_account_home_value(home_value)
    pool_value = validate_account_pool(args.pool) if args.pool is not None else None
    max_value = (
        validate_max_concurrent_jobs(args.max_concurrent)
        if args.max_concurrent is not None
        else None
    )

    config, config_path, old_text = _load(args)
    registry = _registry(config)
    try:
        existing = registry.by_name(name)
    except AccountError:
        existing = None
    block = config_edit.find_account_block(old_text, name)

    enabled = True
    if existing is not None:
        if block is None or not block.marked:
            raise AccountError(
                f"account '{name}' already exists as a hand-written [[accounts]] block; "
                f"run `agy-mcp account remove {name}` first",
                code="account_name_duplicate",
            )
        if existing.expanded_home != home_path and args.home is not None:
            raise AccountError(
                f"account '{name}' already uses a different home; remove it first",
                code="account_name_duplicate",
            )
        home_value = existing.home
        home_path = existing.expanded_home
        if args.pool is not None and pool_value != existing.pool:
            raise AccountError(
                f"account '{name}' already uses pool '{existing.pool}'; remove it first",
                code="account_name_duplicate",
            )
        pool_value = existing.pool
        if args.max_concurrent is not None and max_value != existing.max_concurrent_jobs:
            raise AccountError(
                f"account '{name}' already uses a different max_concurrent_jobs; "
                "remove it first",
                code="account_name_duplicate",
            )
        max_value = existing.max_concurrent_jobs
        enabled = existing.enabled
    else:
        if 1 + len(config.accounts) + 1 > config.max_accounts:
            raise AccountError(
                f"max_accounts ({config.max_accounts}) is reached; the total includes "
                "the implicit 'default' account",
                code="account_limit_exceeded",
            )

    # Validate the final entry with the loader's own rules before any write.
    entry: dict[str, object] = {
        "name": name,
        "home": home_value,
        "enabled": enabled,
    }
    if pool_value is not None:
        entry["pool"] = pool_value
    if max_value is not None:
        entry["max_concurrent_jobs"] = max_value
    parse_account_entry(entry)

    plan_block = _add_plan(
        name,
        home_value,
        pool_value,
        max_value,
        existing.expected_email_sha256 if existing is not None else None,
        enabled,
    )
    if not (args.yes and not args.dry_run):
        _out("dry run (no changes):")
        _out(f"  config: {_safe(str(config_path))}")
        _out(f"  home:   {_safe(home_value)}")
        _out("  steps: prove isolation, launch the isolated agy login, verify the")
        _out("         login, record only the SHA-256 of the expected email, copy the")
        _out("         permission policy and write settings.json, then append:")
        _out(plan_block)
        _out(f"re-run with --yes to apply. {RESTART_HINT}")
        return 0

    require_interactive_terminal(seams.isatty)
    binary = _resolve_agy(args)
    done: list[str] = []
    try:
        created = ensure_account_home(home_path)
        done.append(f"{'created' if created else 'kept'} the account home (mode 0700)")

        account = AccountConfig(
            name=name,
            home=home_value,
            pool=pool_value,
            enabled=enabled,
            max_concurrent_jobs=max_value,
        )
        if created or home_is_empty(home_path):
            _out("proving the home is isolated (expecting 'Authentication required')...")
            probe_isolation(account, binary, timeout_seconds=args.timeout)
            done.append("proved the fresh home cannot see the host keyring")

        _out(f"launching the isolated agy login for {name}; sign in as the human...")
        returncode = seams.login_runner(account, home_path, binary)
        if returncode != 0:
            raise AccountError(
                f"the interactive agy login exited with status {returncode}",
                code="account_cli_login_failed",
            )
        done.append("the human login command exited successfully")

        _out("verifying the isolated /usage after login...")
        reading = verify_login(account, binary, timeout_seconds=args.timeout)
        observed_hash = str(reading["account"]["identity_sha256"])
        observed_masked = str(reading["account"].get("masked_email") or "***")
        done.append("verified the isolated /usage and its account identity")

        expected_email = read_expected_email(seams.prompt_email)
        expected_hash = email_sha256(expected_email)
        if expected_hash != observed_hash:
            raise AccountError(
                "the expected email does not match the observed login "
                f"{observed_masked}; nothing was written to config.toml",
                code="account_identity_mismatch",
            )
        done.append("checked the typed expected email against the observed login digest")

        paths = provision_files(home_path, Path.home(), config.allowed_workdir_roots)
        if paths.policy is not None:
            done.append(f"placed the permission policy at {paths.policy}")
        if paths.settings is not None:
            done.append(f"placed settings.json at {paths.settings}")

        entry["expected_email_sha256"] = expected_hash
        parsed = parse_account_entry(entry)
        final_block = _add_plan(
            name,
            home_value,
            pool_value,
            max_value,
            expected_hash,
            enabled,
        )
        if block is not None:
            new_text = config_edit.replace_account_block(old_text, final_block, block)
        else:
            new_text = config_edit.append_account_block(old_text, final_block)

        _out("account block to write:")
        _out(final_block)
        if new_text == old_text:
            _out("config.toml already has this exact block; no change written")
        else:
            result = config_edit.write_config_text(
                config_path,
                new_text,
                expected_text=old_text,
                dry_run=False,
            )
            done.append(f"wrote config.toml (backup: {result.backup_path})")
    except AccountError:
        _print_resume(name, home_value, done)
        raise

    _out("doctor checks for the new account:")
    findings, _, _ = _check_account(parsed, Path.home(), binary, args.timeout)
    _out(render_report({"findings": findings}))
    _out(RESTART_HINT)
    if any(not finding.ok and not finding.warn for finding in findings):
        raise AccountError(
            "the account was added, but the doctor checks reported a problem; "
            "fix it and re-run `agy-mcp account add`",
            code="account_cli_doctor_failed",
        )
    return 0


def _delete_home_path(
    account: AccountConfig, root_arg: Optional[str]
) -> Path:
    """Resolve and safety-check the home --delete-home may remove."""
    root = Path(root_arg).expanduser() if root_arg else accounts_root()
    if root.is_symlink():
        raise AccountError(
            f"the agy-accounts root '{_safe(str(root))}' is a symlink; refusing",
            code="account_cli_delete_home_unsafe",
        )
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise AccountError(
            f"the agy-accounts root does not exist: {_safe(str(root))}",
            code="account_cli_delete_home_unsafe",
        ) from exc
    home = account.expanded_home
    if home.is_symlink():
        raise AccountError(
            f"account home '{_safe(str(home))}' is a symlink; refusing to delete it",
            code="account_cli_delete_home_unsafe",
        )
    try:
        resolved = home.resolve(strict=True)
    except OSError as exc:
        raise AccountError(
            f"account home '{_safe(str(home))}' does not exist; nothing to delete",
            code="account_cli_delete_home_unsafe",
        ) from exc
    if not resolved.is_dir():
        raise AccountError(
            f"account home '{_safe(str(resolved))}' is not a directory",
            code="account_cli_delete_home_unsafe",
        )
    if resolved.parent != root_resolved:
        raise AccountError(
            f"account home '{_safe(str(resolved))}' is not directly under the "
            f"agy-accounts root '{_safe(str(root_resolved))}'",
            code="account_cli_delete_home_unsafe",
        )
    return resolved


def cmd_remove(args: argparse.Namespace, seams: CliSeams) -> int:
    """Remove one account's config block, refusing while work depends on it."""
    if args.name == DEFAULT_ACCOUNT_NAME:
        raise AccountError(
            "the implicit default account cannot be removed; use `account disable default`",
            code="account_cli_default_removal",
        )
    name = validate_account_name(args.name)
    config_path = config_edit.resolve_config_path(args.config)
    old_text = config_edit.read_config_text(config_path)
    # Locate the block before the loader so two same-named hand-written blocks
    # are reported as ambiguous (3) rather than as a generic invalid config. A
    # syntactically broken file still reports the loader's error (6).
    try:
        block = config_edit.find_account_block(old_text, name)
    except AccountError as block_error:
        if block_error.code == "account_cli_block_ambiguous":
            raise
        try:
            load_config(path=config_path)
        except ConfigError as exc:
            raise AccountError(
                f"config.toml is invalid: {exc}",
                code="account_cli_config_error",
            ) from exc
        raise
    try:
        config = load_config(path=config_path)
    except ConfigError as exc:
        raise AccountError(
            f"config.toml is invalid: {exc}",
            code="account_cli_config_error",
        ) from exc
    registry = _registry(config)
    try:
        account = registry.by_name(name)
    except AccountError as exc:
        raise AccountError(f"unknown account '{name}'", code="account_not_found") from exc
    if block is None:
        raise AccountError(
            f"account '{name}' has no [[accounts]] block in config.toml",
            code="account_cli_block_not_found",
        )

    pinned = _pinned_jobs(args.state_dir, name)
    active, paused = _split_jobs(pinned)
    if active:
        labels = ", ".join(_job_label(job) for job in active)
        raise AccountError(
            f"A task is currently running on this account: {labels}. "
            "Wait for it to finish or cancel it first.",
            code="account_cli_jobs_active",
        )
    delete_home = bool(args.delete_home)
    delete_path: Optional[Path] = None
    if delete_home:
        if paused:
            raise AccountError(
                "cannot delete the home while non-terminal jobs are pinned to this "
                "account; remove the config block only, then delete it later",
                code="account_cli_delete_home_unsafe",
            )
        delete_path = _delete_home_path(account, args.accounts_root)

    new_text, span = config_edit.remove_account_block(old_text, name)
    _out(f"block to remove from {_safe(str(config_path))}:")
    _out(_safe(span.text(old_text)))
    if paused:
        _out(
            "warning: these paused jobs will become unresumable because the account "
            "is no longer in the registry:"
        )
        for job in paused:
            _out(f"  - {_job_label(job)} state={_state_value(job)}")
        _out("they are refused on resume with account_not_found.")
    if delete_path is not None:
        _out(f"--delete-home would remove {_safe(str(delete_path))}")
    if not (args.yes and not args.dry_run):
        _out(f"dry run (no changes). {RESTART_HINT} after applying.")
        return 0

    if paused:
        phrase = f"remove account {name}"
        answer = seams.input_func(f"type '{phrase}' to continue: ")
        if (answer or "").strip() != phrase:
            raise AccountError(
                "the confirmation phrase did not match; nothing was removed",
                code="account_cli_confirmation_required",
            )
    if delete_path is not None:
        phrase = f"delete account home {name}"
        answer = seams.input_func(f"type '{phrase}' to delete the home: ")
        if (answer or "").strip() != phrase:
            raise AccountError(
                "the delete-home phrase did not match; the config block was not removed",
                code="account_cli_confirmation_required",
            )

    result = config_edit.write_config_text(
        config_path,
        new_text,
        expected_text=old_text,
        dry_run=False,
    )
    _out(f"removed account '{name}'; backup: {_safe(str(result.backup_path))}")
    if delete_path is not None:
        root = Path(args.accounts_root).expanduser() if args.accounts_root else accounts_root()
        try:
            root_resolved = root.resolve(strict=True)
            if delete_path.parent != root_resolved or delete_path.is_symlink():
                raise AccountError(
                    "the account home moved while deleting; refusing",
                    code="account_cli_delete_home_unsafe",
                )
            shutil.rmtree(delete_path)
        except AccountError:
            _out(
                "config removed, but the home deletion was refused or failed; "
                f"the home is still at {_safe(str(delete_path))}"
            )
            _out(RESTART_HINT)
            return _EXIT_REFUSED
        except OSError as exc:
            _out(
                f"config removed, but deleting the home failed: {_safe(str(exc))}"
            )
            _out(RESTART_HINT)
            return _EXIT_IO
        _out(f"deleted the account home {_safe(str(delete_path))}")
        _out(
            "the Google login may still be valid server-side; revoke it on the "
            "Google account security page if you no longer want it"
        )
    _out(RESTART_HINT)
    return 0


def _write_toggle(
    args: argparse.Namespace,
    new_text: str,
    old_text: str,
    config_path: Path,
    message: str,
) -> int:
    if not (args.yes and not args.dry_run):
        _out("dry run (no changes):")
        _out(_safe(new_text))
        _out(f"re-run with --yes to apply. {RESTART_HINT}")
        return 0
    if new_text == old_text:
        _out("no change needed")
        return 0
    result = config_edit.write_config_text(
        config_path,
        new_text,
        expected_text=old_text,
        dry_run=False,
    )
    _out(f"{message}; backup: {_safe(str(result.backup_path))}")
    _out(RESTART_HINT)
    return 0


def cmd_toggle(args: argparse.Namespace, seams: CliSeams) -> int:
    """Enable or disable one declared account (or the implicit default)."""
    del seams
    enabled = args.command == "enable"
    config, config_path, old_text = _load(args)
    if args.name == DEFAULT_ACCOUNT_NAME:
        current_default = config.default_account
        current = True if current_default is None else current_default.enabled
        if current == enabled:
            _out(f"the implicit default account is already {'enabled' if enabled else 'disabled'}")
            return 0
        new_text = config_edit.set_default_enabled(old_text, enabled)
        return _write_toggle(
            args,
            new_text,
            old_text,
            config_path,
            f"the implicit default account is now {'enabled' if enabled else 'disabled'}",
        )

    args.name = validate_account_name(args.name)
    registry = _registry(config)
    try:
        account = registry.by_name(args.name)
    except AccountError as exc:
        raise AccountError(f"unknown account '{args.name}'", code="account_not_found") from exc
    block = config_edit.find_account_block(old_text, args.name)
    if block is None:
        raise AccountError(
            f"account '{args.name}' has no [[accounts]] block in config.toml",
            code="account_cli_block_not_found",
        )
    if account.enabled == enabled:
        _out(f"account '{args.name}' is already {'enabled' if enabled else 'disabled'}")
        return 0
    if not enabled:
        active, _ = _split_jobs(_pinned_jobs(args.state_dir, args.name))
        if active:
            _out(
                f"warning: {len(active)} active job(s) will finish on this account; "
                "no new ones will start"
            )
        remaining = [other for other in registry.enabled() if other.name != account.name]
        if not remaining:
            _out(
                "warning: this leaves no enabled account; the registry will report "
                "account_none_enabled"
            )
    new_text = config_edit.set_account_enabled(old_text, args.name, enabled)
    label = "enabled" if enabled else "disabled"
    return _write_toggle(
        args,
        new_text,
        old_text,
        config_path,
        f"account '{args.name}' is now {label}",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the ``agy-mcp account`` parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=None, help="Path to config.toml")
    common.add_argument(
        "--state-dir", default=None, help="State directory holding jobs/"
    )
    common.add_argument(
        "--agy-binary", default=None, help="Path to the agy executable"
    )
    common.add_argument("--yes", action="store_true", help="Apply the change")
    common.add_argument(
        "--dry-run", action="store_true", help="Show the plan even with --yes"
    )

    parser = argparse.ArgumentParser(
        prog="agy-mcp account",
        description="Manage isolated agy accounts (never reads credentials; never logs in)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list", parents=[common], help="List accounts and job counts (offline by default)"
    )
    list_parser.add_argument(
        "--probe", action="store_true", help="Run one isolated /usage per account"
    )
    list_parser.add_argument(
        "--timeout", type=float, default=15.0, help="Per-probe timeout in seconds"
    )
    list_parser.set_defaults(func=cmd_list)

    add_parser = subparsers.add_parser(
        "add", parents=[common], help="Provision an isolated account (human login)"
    )
    add_parser.add_argument("name")
    add_parser.add_argument("--pool", default=None)
    add_parser.add_argument("--max-concurrent", dest="max_concurrent", type=int, default=None)
    add_parser.add_argument("--home", default=None)
    add_parser.add_argument(
        "--timeout", type=float, default=ADD_PROBE_TIMEOUT_SECONDS,
        help="Isolation/probe timeout in seconds",
    )
    add_parser.set_defaults(func=cmd_add)

    remove_parser = subparsers.add_parser(
        "remove", parents=[common], help="Remove an account's config block"
    )
    remove_parser.add_argument("name")
    remove_parser.add_argument(
        "--delete-home",
        dest="delete_home",
        action="store_true",
        help="Also delete the home (extra typed phrase; never follows symlinks)",
    )
    remove_parser.add_argument(
        "--accounts-root",
        dest="accounts_root",
        default=None,
        help="Expected parent of a deletable home (default ~/.local/share/agy-accounts)",
    )
    remove_parser.set_defaults(func=cmd_remove)

    for command, help_text in (
        ("enable", "Enable an account"),
        ("disable", "Disable an account (active jobs finish)"),
    ):
        toggle = subparsers.add_parser(command, parents=[common], help=help_text)
        toggle.add_argument("name")
        toggle.set_defaults(func=cmd_toggle)

    return parser


def main(
    argv: Optional[list[str]] = None,
    *,
    seams: Optional[CliSeams] = None,
    login_runner: Optional[LoginRunner] = None,
    isatty: Optional[Callable[[], bool]] = None,
    prompt_email: Optional[Callable[[], str]] = None,
    input_func: Optional[Callable[[str], str]] = None,
) -> int:
    """Run one account command and return its documented exit status.

    ``seams`` is the primary injection point; the four keyword arguments are
    convenience overrides so a caller can replace one behavior without building
    a whole :class:`CliSeams`.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    active_seams = seams or CliSeams()
    if login_runner is not None:
        active_seams.login_runner = login_runner
    if isatty is not None:
        active_seams.isatty = isatty
    if prompt_email is not None:
        active_seams.prompt_email = prompt_email
    if input_func is not None:
        active_seams.input_func = input_func
    try:
        return int(args.func(args, active_seams))
    except AccountError as exc:
        _err(f"error: [{exc.code}] {_safe(exc.message)}")
        return _exit_code_for(exc.code)
    except ConfigError as exc:
        _err(f"error: [account_cli_config_error] {_safe(str(exc))}")
        return _EXIT_IO
    except OSError as exc:
        _err(f"error: [account_cli_write_failed] {_safe(str(exc))}")
        return _EXIT_IO
