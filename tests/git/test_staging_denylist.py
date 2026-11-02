"""Tests for F7: staging deny-list covers AGENTS.md, .agents/, GEMINI.md,

and removes .github blanket exemption.
"""

from pathlib import Path

from agy_mcp.gitops import is_sensitive_path, run_git, stage_changes
from tests.helpers.make_repo import make_repo


def test_is_sensitive_path_covers_instruction_and_agents_files() -> None:
    """AGENTS.md, GEMINI.md, and .agents/** must be recognized as sensitive paths."""
    assert is_sensitive_path("AGENTS.md") is True
    assert is_sensitive_path("subdir/AGENTS.md") is True
    assert is_sensitive_path("GEMINI.md") is True
    assert is_sensitive_path("subdir/GEMINI.md") is True
    assert is_sensitive_path(".agents/hooks.json") is True
    assert is_sensitive_path("nested/.agents/config") is True


def test_github_blanket_exemption_removed() -> None:
    """.github must NOT blanket-exempt secrets like .env or keys, while allowing normal files."""
    assert is_sensitive_path(".github/.env") is True
    assert is_sensitive_path(".github/id_rsa") is True
    assert is_sensitive_path(".github/secret.pem") is True
    # Non-sensitive files in .github must still be allowed
    assert is_sensitive_path(".github/workflows/ci.yml") is False


def test_stage_changes_blocks_agents_and_gemini_files(tmp_path: Path) -> None:
    """stage_changes must unstage AGENTS.md, .agents/, and GEMINI.md and skip them."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    # Create files
    (repo_dir / "feature.py").write_text("print('hello')\n")
    (repo_dir / "AGENTS.md").write_text("# Malicious agent instructions\n")
    (repo_dir / "GEMINI.md").write_text("# Malicious gemini instructions\n")
    agents_dir = repo_dir / ".agents"
    agents_dir.mkdir()
    (agents_dir / "hooks.json").write_text("{}\n")

    staging = stage_changes(repo_dir)

    # feature.py should be staged
    assert "feature.py" in staging.staged_files

    # Instruction files must NOT be staged
    assert "AGENTS.md" not in staging.staged_files
    assert "GEMINI.md" not in staging.staged_files
    assert ".agents/hooks.json" not in staging.staged_files

    # Check git index directly
    diff_res = run_git(["diff", "--cached", "--name-only"], repo_dir)
    assert "AGENTS.md" not in diff_res.stdout
    assert "GEMINI.md" not in diff_res.stdout
    assert ".agents/hooks.json" not in diff_res.stdout
