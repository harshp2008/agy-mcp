"""Unit and integration tests for worktree lifecycle, preflight, and permission inspection."""

import json
from pathlib import Path

import pytest

from agy_mcp.config import Config
from agy_mcp.gitops import run_git
from agy_mcp.models import Job
from agy_mcp.states import JobState
from agy_mcp.worktree import (
    PreflightError,
    cleanup_worktree,
    create_worktree,
    inspect_permission_rules,
    preflight,
    provision,
    validate_repo_path,
)
from tests.helpers.make_repo import make_repo


def test_preflight_valid_repo_inside_home(tmp_path: Path, fake_home: Path, safe_git_env):
    """A valid repository with commits inside HOME passes preflight checks."""
    repo = make_repo(fake_home / "my_project", env=safe_git_env)
    resolved, dirty, base_sha = preflight(str(repo), home=fake_home)
    assert resolved == repo.resolve()
    assert dirty is False
    assert len(base_sha) == 40


def test_preflight_allowed_workdir_roots(tmp_path: Path, fake_home: Path, safe_git_env):
    """allowed_workdir_roots takes precedence over HOME."""
    external_dir = tmp_path / "srv" / "repos"
    repo = make_repo(external_dir / "service_repo", env=safe_git_env)

    # Without allowed_workdir_roots, outside HOME is rejected
    with pytest.raises(PreflightError) as exc_info:
        preflight(str(repo), home=fake_home)
    assert exc_info.value.code == "path_outside_home"

    # With allowed_workdir_roots containing the root, it passes
    cfg = Config(allowed_workdir_roots=[str(external_dir)])
    resolved, dirty, base_sha = preflight(str(repo), config=cfg, home=fake_home)
    assert resolved == repo.resolve()


def test_preflight_symlink_escape_rejected(tmp_path: Path, fake_home: Path, safe_git_env):
    """A symlink inside HOME pointing to a repository outside HOME is rejected upon resolution."""
    outside_repo = make_repo(tmp_path / "outside_root" / "secret_repo", env=safe_git_env)
    symlink_repo = fake_home / "linked_secret_repo"
    symlink_repo.symlink_to(outside_repo, target_is_directory=True)

    with pytest.raises(PreflightError) as exc_info:
        preflight(str(symlink_repo), home=fake_home)
    assert exc_info.value.code == "path_outside_home"


def test_preflight_prohibited_targets(tmp_path: Path, fake_home: Path):
    """HOME directly and state dir locations are refused."""
    # HOME directly
    with pytest.raises(PreflightError) as exc1:
        validate_repo_path(str(fake_home), home=fake_home)
    assert exc1.value.code == "home_prohibited"

    # Inside state directory
    state_dir = tmp_path / "state" / "agy-mcp"
    state_dir.mkdir(parents=True)
    with pytest.raises(PreflightError) as exc2:
        validate_repo_path(str(state_dir), home=fake_home, base_state_dir=state_dir)
    assert exc2.value.code == "state_dir_prohibited"


def test_preflight_bare_and_linked_worktree_rejections(
    tmp_path: Path, fake_home: Path, safe_git_env
):
    """Bare repositories and linked worktrees are refused as repo_path."""
    # Bare repo (no .git directory at root)
    bare_repo = make_repo(fake_home / "bare_repo.git", bare=True, env=safe_git_env)
    with pytest.raises(PreflightError) as exc1:
        preflight(str(bare_repo), home=fake_home)
    assert exc1.value.code == "not_a_git_repo"

    # Linked worktree (where .git is a text file pointer)
    main_repo = make_repo(fake_home / "main_repo", env=safe_git_env)
    linked_wt = fake_home / "linked_wt"
    run_git(["worktree", "add", str(linked_wt), "HEAD"], main_repo, env=safe_git_env, check=True)
    assert (linked_wt / ".git").is_file()

    with pytest.raises(PreflightError) as exc2:
        preflight(str(linked_wt), home=fake_home)
    assert exc2.value.code == "linked_worktree_prohibited"


def test_preflight_unborn_head(tmp_path: Path, fake_home: Path, safe_git_env):
    """Repositories with unborn HEADs (no commits yet) return unborn_head code."""
    unborn_repo = make_repo(fake_home / "unborn_repo", initial_commit=False, env=safe_git_env)
    with pytest.raises(PreflightError) as exc_info:
        preflight(str(unborn_repo), home=fake_home)
    assert exc_info.value.code == "unborn_head"


def test_preflight_dirty_cleanliness_policy(tmp_path: Path, fake_home: Path, safe_git_env):
    """Dirty source repos warn by default, but refuse if require_clean_source is enabled."""
    repo = make_repo(fake_home / "dirty_repo", env=safe_git_env)
    (repo / "uncommitted.txt").write_text("scratch", encoding="utf-8")

    # Default: warns (source_dirty=True) without refusing
    _, dirty, _ = preflight(str(repo), home=fake_home)
    assert dirty is True

    # With require_clean_source=True: refuses
    cfg = Config(require_clean_source=True)
    with pytest.raises(PreflightError) as exc_info:
        preflight(str(repo), config=cfg, home=fake_home)
    assert exc_info.value.code == "source_dirty"


def test_preflight_agents_dir_check(tmp_path: Path, fake_home: Path, safe_git_env):
    """Repos tracking .agents/** refuse unless trust_agents_dir=True per SPEC section 9.8."""
    repo = make_repo(fake_home / "agents_repo", env=safe_git_env)
    agents_dir = repo / ".agents"
    agents_dir.mkdir()
    (agents_dir / "hooks.json").write_text("{}", encoding="utf-8")
    run_git(["add", ".agents/hooks.json"], repo, env=safe_git_env, check=True)
    run_git(["commit", "-m", "Track hooks"], repo, env=safe_git_env, check=True)

    # Without trust_agents_dir: refuses
    with pytest.raises(PreflightError) as exc_info:
        preflight(str(repo), home=fake_home)
    assert exc_info.value.code == "untrusted_agents_dir"

    # With trust_agents_dir=True: passes
    cfg = Config(trust_agents_dir=True)
    _, _, _ = preflight(str(repo), config=cfg, home=fake_home)


def test_create_worktree_and_provision(tmp_path: Path, fake_home: Path, safe_git_env):
    """create_worktree creates linked worktree and provision pre-seeds .agy/PLAN.md."""
    repo = make_repo(fake_home / "source_repo", env=safe_git_env)
    job_dir = tmp_path / "jobs" / "job_test_1"
    job_dir.mkdir(parents=True)

    wt_path, branch, base_sha = create_worktree(
        repo_path=repo,
        job_dir=job_dir,
        title="Add JWT authentication",
    )

    assert wt_path.is_dir()
    assert (wt_path / ".git").is_file()
    assert branch.startswith("agy/add-jwt-authentication-")
    assert len(base_sha) == 40

    # Provision .agy metadata directory
    provision(wt_path, title="Add JWT authentication")

    agy_dir = wt_path / ".agy"
    assert agy_dir.is_dir()
    assert (agy_dir.stat().st_mode & 0o777) == 0o700

    plan_file = agy_dir / "PLAN.md"
    assert plan_file.is_file()
    assert "# Plan: Add JWT authentication" in plan_file.read_text(encoding="utf-8")


def test_cleanup_worktree_safety_checks(tmp_path: Path, fake_home: Path, safe_git_env):
    """cleanup_worktree refuses when dirty or having unmerged commits unless force=True."""
    repo = make_repo(fake_home / "source_repo", env=safe_git_env)
    job_dir = tmp_path / "jobs" / "job_clean"
    job_dir.mkdir(parents=True)

    wt_path, branch, base_sha = create_worktree(
        repo_path=repo,
        job_dir=job_dir,
        title="Cleanup test",
    )

    job = Job(
        job_id="job_clean",
        title="Cleanup test",
        repo_path=repo,
        branch=branch,
        base_commit=base_sha,
        state=JobState.DONE,
        worktree_path=wt_path,
    )

    # 1. Dirty worktree refusal
    (wt_path / "scratch.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Refusing to cleanup dirty worktree"):
        cleanup_worktree(job, force=False)

    # Clean the worktree by committing
    run_git(["add", "scratch.py"], wt_path, env=safe_git_env, check=True)
    run_git(["commit", "-m", "Commit unmerged change"], wt_path, env=safe_git_env, check=True)

    # 2. Unmerged branch refusal
    with pytest.raises(RuntimeError, match="Refusing to delete branch .* unmerged commits"):
        cleanup_worktree(job, remove_worktree_flag=True, delete_branch_flag=True, force=False)

    # 3. Forced cleanup succeeds
    cleanup_resp = cleanup_worktree(
        job, remove_worktree_flag=True, delete_branch_flag=True, force=True
    )
    assert cleanup_resp.worktree_removed is True
    assert cleanup_resp.branch_deleted is True
    assert not wt_path.exists()


def test_inspect_permission_rules_dangerous_detection(tmp_path: Path):
    """inspect_permission_rules reads mock ~/.gemini files and surfaces dangerous allow-rules."""
    # 0. Missing directory handling
    missing_dir = tmp_path / "nonexistent_gemini"
    res_missing = inspect_permission_rules(missing_dir)
    assert res_missing["permission_rules_effective"] == []
    assert res_missing["dangerous_allow_rules"] == []
    assert res_missing["warnings"] == []

    gemini_dir = tmp_path / "fake_gemini"
    cli_dir = gemini_dir / "antigravity-cli"
    cli_dir.mkdir(parents=True)
    projects_dir = gemini_dir / "config" / "projects"
    projects_dir.mkdir(parents=True)

    # 1. Global settings.json with harmless ls, safe git status, and dangerous bash + git branch
    (cli_dir / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": [
                        "command(git status)",
                        "command(ls)",
                        "command(bash)",
                        "command(git branch)",
                    ]
                }
            }
        )
    )

    # 2. Project-level json with dangerous python3 -m pytest and git push
    (projects_dir / "proj_uuid.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": [
                        "command(python3 -m pytest)",
                        "command(git push)",
                    ]
                }
            }
        )
    )

    # 3. Malformed JSON file in config/config.json - must not crash, must warn
    cfg_file = gemini_dir / "config" / "config.json"
    cfg_file.write_text("{ unclosed invalid json", encoding="utf-8")

    result = inspect_permission_rules(gemini_dir)
    effective = result["permission_rules_effective"]
    dangerous = result["dangerous_allow_rules"]
    warnings = result["warnings"]

    # Harmless rules in effective but not dangerous
    assert "command(git status)" in effective
    assert "command(ls)" in effective
    assert "command(git status)" not in dangerous
    assert "command(ls)" not in dangerous

    # Dangerous rules detected
    assert "command(bash)" in dangerous
    assert "command(git branch)" in dangerous
    assert "command(python3 -m pytest)" in dangerous
    assert "command(git push)" in dangerous

    # Warnings emitted
    assert "dangerous_allow_rules" in warnings
    assert "malformed_config_json" in warnings
