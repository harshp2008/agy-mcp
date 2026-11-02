"""Helper to construct isolated, reproducible git repositories for testing."""

import os
import subprocess
from pathlib import Path
from typing import Optional


def get_safe_git_env(home_dir: Optional[Path] = None) -> dict[str, str]:
    """Return an isolated environment for git subprocesses preventing host leakage."""
    resolved_home = str(home_dir or Path("/tmp"))
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": resolved_home,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "author@example.com",
        "GIT_COMMITTER_NAME": "Test Committer",
        "GIT_COMMITTER_EMAIL": "committer@example.com",
        "LANG": "C.UTF-8",
    }


def make_repo(
    repo_path: Path,
    bare: bool = False,
    initial_commit: bool = True,
    default_branch: str = "main",
    env: Optional[dict[str, str]] = None,
) -> Path:
    """Initialize a git repository in repo_path with safe environment settings."""
    repo_path.mkdir(parents=True, exist_ok=True)
    git_env = env or get_safe_git_env(repo_path.parent)

    init_cmd = ["git", "init", f"-b{default_branch}"]
    if bare:
        init_cmd.append("--bare")
    init_cmd.append(str(repo_path))

    subprocess.run(init_cmd, env=git_env, check=True, capture_output=True)

    if not bare:
        # Configure user name/email in local config as well
        subprocess.run(
            ["git", "-C", str(repo_path), "config", "user.name", "Test Author"],
            env=git_env,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "config", "user.email", "author@example.com"],
            env=git_env,
            check=True,
            capture_output=True,
        )

        if initial_commit:
            readme = repo_path / "README.md"
            readme.write_text("# Test Repository\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo_path), "add", "README.md"],
                env=git_env,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_path), "commit", "-m", "Initial commit"],
                env=git_env,
                check=True,
                capture_output=True,
            )

    return repo_path
