"""Tests for F10: verify artifacts are not committed by the safety net."""

from pathlib import Path

from agy_mcp.gitops import run_git, safety_net_commit
from tests.helpers.make_repo import make_repo


def test_verify_artifacts_not_committed_by_safety_net(tmp_path: Path) -> None:
    """Verify artifacts (.coverage, htmlcov/, junit.xml, target/) not in safety net."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    # Agent modified a legitimate file
    (repo_dir / "src.py").write_text("print('legitimate feature')\n")

    # Verify run created test artifacts: .coverage, htmlcov/, junit.xml, target/
    (repo_dir / ".coverage").write_text("coverage-data\n")
    (repo_dir / "junit.xml").write_text("<testsuite></testsuite>\n")
    htmlcov_dir = repo_dir / "htmlcov"
    htmlcov_dir.mkdir()
    (htmlcov_dir / "index.html").write_text("<h1>Coverage Report</h1>\n")
    target_dir = repo_dir / "target"
    target_dir.mkdir()
    (target_dir / "debug_binary").write_text("binary\n")

    # Run safety-net commit
    sha = safety_net_commit(
        worktree_path=repo_dir,
        title="Safety Net Test",
        audit_passed=True,
    )
    assert sha is not None, "Safety-net commit did not create a commit for legitimate changes"

    # Inspect the committed tree in the new commit
    diff_tree = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", sha], repo_dir)
    committed_files = diff_tree.stdout.splitlines()

    # Legitimate changes MUST be in the commit
    assert "src.py" in committed_files

    # Verify artifacts MUST NOT be in the commit
    assert ".coverage" not in committed_files, ".coverage was committed by safety net"
    assert "junit.xml" not in committed_files, "junit.xml was committed by safety net"
    assert not any(f.startswith("htmlcov") for f in committed_files), (
        "htmlcov was committed by safety net"
    )
    assert not any(f.startswith("target") for f in committed_files), (
        "target/ was committed by safety net"
    )
