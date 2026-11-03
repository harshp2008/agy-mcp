"""Build the sanitized child environment for one account.

Owns the per-account HOME and the dead D-Bus override that isolates a non-default
login from the desktop app's SecretService keyring. It must never copy the parent
environment, never pass an API key or token, and never add the D-Bus override to
the default account: that path must stay byte-identical to the pre-multi-account
environment.
"""

from pathlib import Path
from typing import Optional

from agy_mcp.accounts.registry import AccountConfig
from agy_mcp.util import env_allowlist

# A valid-looking but dead D-Bus address. agy's composite token storage tries the
# keyring, fails to connect, and falls back to the file token under HOME
# (human test 2026-11-03). Using /dev/null rather than a missing socket makes the
# failure immediate and identical on every host.
ISOLATED_DBUS_SESSION_BUS_ADDRESS = "unix:path=/dev/null"


def build_account_env(
    account: AccountConfig,
    job_tmp_dir: Path,
    path: Optional[str] = None,
    disable_hooks: bool = True,
) -> dict[str, str]:
    """Return the complete child environment for ``account``.

    The default account gets exactly :func:`agy_mcp.util.env_allowlist` output.
    Every other account additionally gets ``DBUS_SESSION_BUS_ADDRESS`` pointing at
    a dead socket, so agy cannot reach the host keyring and must use the file
    token under the account's own HOME. ``disable_hooks`` mirrors the existing
    git-commit exception and is passed through unchanged.
    """
    env = env_allowlist(
        job_tmp_dir=job_tmp_dir,
        home=account.expanded_home,
        path=path,
        disable_hooks=disable_hooks,
    )
    if not account.is_default:
        env["DBUS_SESSION_BUS_ADDRESS"] = ISOLATED_DBUS_SESSION_BUS_ADDRESS
    return env
