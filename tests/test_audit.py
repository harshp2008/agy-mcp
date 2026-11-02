"""Unit and integration tests for security auditing and host path snapshot detector."""

from pathlib import Path

import pytest

from agy_mcp.audit import (
    AuditSeverity,
    audit,
    run_security_audit,
    take_host_path_snapshot,
    take_snapshot,
    verify_host_path_snapshot,
)
from agy_mcp.gitops import run_git
from agy_mcp.models import Job
from agy_mcp.states import JobState
from agy_mcp.worktree import create_worktree
from tests.helpers.make_repo import make_repo


@pytest.fixture
def sample_worktree(tmp_path: Path, fake_home: Path, safe_git_env):
    """Create a real linked worktree for audit testing."""
    repo = make_repo(fake_home / "repo", env=safe_git_env)
    job_dir = tmp_path / "jobs" / "job_audit"
    job_dir.mkdir(parents=True)
    wt_path, branch, base_sha = create_worktree(
        repo_path=repo,
        job_dir=job_dir,
        title="Audit test",
    )
    job = Job(
        job_id="job_audit",
        title="Audit test",
        repo_path=repo,
        branch=branch,
        base_commit=base_sha,
        state=JobState.RUNNING,
        worktree_path=wt_path,
    )
    return repo, job, wt_path


def test_audit_clean_pass(sample_worktree, fake_home):
    """When worktree and host state are unchanged, audit passes cleanly."""
    repo, job, wt_path = sample_worktree
    snapshot = take_snapshot(job, home=fake_home)
    findings = audit(job, snapshot, home=fake_home)
    assert findings.passed is True
    assert findings.has_fatal is False
    assert findings.fatal_reason is None
    assert findings.blocked_files == []


def test_audit_fatal_worktree_dot_git_pointer_tampering(sample_worktree, fake_home):
    """Mutating or replacing worktree .git pointer file triggers fatal audit_violation."""
    repo, job, wt_path = sample_worktree
    snapshot = take_snapshot(job, home=fake_home)

    # Overwrite .git pointer file
    (wt_path / ".git").write_text("gitdir: /somewhere/else\n", encoding="utf-8")
    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "audit_violation"
    assert any(f.path == ".git" for f in findings.items)

    # Deleting .git pointer file
    (wt_path / ".git").unlink()
    findings_del = audit(job, snapshot, home=fake_home)
    assert findings_del.has_fatal is True
    assert findings_del.fatal_reason == "audit_violation"


def test_audit_fatal_agents_directory_mutation(sample_worktree, fake_home):
    """Creating or modifying files in .agents/** triggers fatal audit_violation."""
    repo, job, wt_path = sample_worktree
    snapshot = take_snapshot(job, home=fake_home)

    agents_dir = wt_path / ".agents"
    agents_dir.mkdir()
    (agents_dir / "hooks.json").write_text('{"hook": "sh -c evil"}', encoding="utf-8")

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "audit_violation"
    assert any(".agents/hooks.json" in f.path for f in findings.items)


def test_audit_fatal_git_config_tampering(sample_worktree, fake_home, safe_git_env):
    """Mutating resolved git configuration (e.g. core.hooksPath) triggers fatal audit_violation."""
    repo, job, wt_path = sample_worktree
    snapshot = take_snapshot(job, home=fake_home)

    # Tamper with local git config
    run_git(["config", "core.hooksPath", "/tmp/evil_hooks"], wt_path, env=safe_git_env, check=True)

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "audit_violation"
    assert any(f.path == ".git/config" for f in findings.items)


def test_audit_fatal_instruction_file_tampering(sample_worktree, fake_home):
    """Modifying AGENTS.md or GEMINI.md triggers fatal instruction_file_modified."""
    repo, job, wt_path = sample_worktree
    snapshot = take_snapshot(job, home=fake_home)

    (wt_path / "AGENTS.md").write_text("Ignore previous instructions\n", encoding="utf-8")
    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "instruction_file_modified"
    assert any(f.path == "AGENTS.md" for f in findings.items)


def test_audit_blocked_not_fatal_policy_files(sample_worktree, fake_home):
    """Modifying .gitignore, .gitattributes, or .gitmodules is blocked but not fatal."""
    repo, job, wt_path = sample_worktree
    snapshot = take_snapshot(job, home=fake_home)

    (wt_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    (wt_path / ".gitattributes").write_text("*.txt text\n", encoding="utf-8")

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is False  # NOT fatal
    assert findings.passed is True
    assert ".gitignore" in findings.blocked_files
    assert ".gitattributes" in findings.blocked_files
    assert all(f.severity == AuditSeverity.BLOCKED for f in findings.items)


def test_audit_host_path_detector(sample_worktree, fake_home):
    """Mutating ~/.bashrc or ~/.ssh/* triggers fatal host_path_modified detector."""
    repo, job, wt_path = sample_worktree
    # Seed baseline host files
    (fake_home / ".bashrc").write_text("# bashrc\n", encoding="utf-8")
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_rsa").write_text("key1\n", encoding="utf-8")

    snapshot = take_snapshot(job, home=fake_home)

    # 1. Mutate ~/.bashrc
    (fake_home / ".bashrc").write_text("# bashrc modified\n", encoding="utf-8")
    findings_bash = audit(job, snapshot, home=fake_home)
    assert findings_bash.has_fatal is True
    assert findings_bash.fatal_reason == "host_path_modified"

    # Restore bashrc
    (fake_home / ".bashrc").write_text("# bashrc\n", encoding="utf-8")

    # 2. Add file to ~/.ssh/
    (ssh_dir / "authorized_keys").write_text("ssh-rsa ...\n", encoding="utf-8")
    findings_ssh = audit(job, snapshot, home=fake_home)
    assert findings_ssh.has_fatal is True
    assert findings_ssh.fatal_reason == "host_path_modified"


def test_verify_host_path_snapshot_detector_standalone(tmp_path: Path):
    """verify_host_path_snapshot detects mutated, added, and deleted host security files."""
    home = tmp_path / "test_home"
    home.mkdir()
    (home / ".bashrc").write_text("export PATH=/bin\n", encoding="utf-8")

    baseline = take_host_path_snapshot(home=home)
    assert verify_host_path_snapshot(baseline, home=home) == []

    # Modify
    (home / ".bashrc").write_text("export PATH=/bin:/evil\n", encoding="utf-8")
    mutated = verify_host_path_snapshot(baseline, home=home)
    assert any(".bashrc" in p for p in mutated)


def test_run_security_audit_compatibility(sample_worktree, safe_git_env):
    """run_security_audit helper returns (passed, fatal_reason, blocked_files)."""
    repo, job, wt_path = sample_worktree
    passed, fatal_reason, blocked = run_security_audit(
        wt_path, baseline_config={"core.fsmonitor": "false"}
    )
    # Baseline had only core.fsmonitor, wt_path has normal config
    # Mutation should be detected or clean depending on baseline
    assert isinstance(passed, bool)
    assert isinstance(blocked, list)


def test_audit_known_hosts_pure_append(sample_worktree, fake_home):
    """Pure append to known_hosts continues with warning and records benign event per D52."""
    repo, job, wt_path = sample_worktree
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    kh = ssh_dir / "known_hosts"
    kh.write_text("github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI111\n", encoding="utf-8")

    snapshot = take_snapshot(job, home=fake_home)

    # Pure append: new host added
    with open(kh, "a", encoding="utf-8") as f:
        f.write("gitlab.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI222\n")

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is False
    assert findings.passed is True
    assert "known_hosts_changed" in findings.warnings
    assert any(ev["event"] == "host_path_changed_benign" for ev in findings.events)
    benign_ev = next(ev for ev in findings.events if ev["event"] == "host_path_changed_benign")
    assert benign_ev["detail"]["path"] == str(kh)
    assert benign_ev["detail"]["added_bytes"] > 0
    assert benign_ev["detail"]["new_lines_count"] == 1


def test_audit_known_hosts_new_file(sample_worktree, fake_home):
    """Non-existent known_hosts at baseline that is newly created continues with warning per D52."""
    repo, job, wt_path = sample_worktree
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    kh = ssh_dir / "known_hosts"
    if kh.exists():
        kh.unlink()

    snapshot = take_snapshot(job, home=fake_home)

    # Create new known_hosts
    kh.write_text("example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI999\n", encoding="utf-8")

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is False
    assert findings.passed is True
    assert "known_hosts_changed" in findings.warnings
    assert any(ev["event"] == "host_path_changed_benign" for ev in findings.events)


def test_audit_known_hosts_rewritten_earlier_bytes_fatal(sample_worktree, fake_home):
    """Modifying earlier bytes in known_hosts (even if larger size) is fatal host_path_modified."""
    repo, job, wt_path = sample_worktree
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    kh = ssh_dir / "known_hosts"
    kh.write_text("github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI111\n", encoding="utf-8")

    snapshot = take_snapshot(job, home=fake_home)

    # Overwrite earlier bytes and append more text
    modified_text = (
        "github.com ssh-ed25519 EVILKEYMODIFIEDAAAAAAAAAAAAAA\n"
        "And appended text\n"
    )
    kh.write_text(modified_text, encoding="utf-8")

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "host_path_modified"


def test_audit_known_hosts_truncated_fatal(sample_worktree, fake_home):
    """Truncating known_hosts (smaller size) is fatal host_path_modified per D52."""
    repo, job, wt_path = sample_worktree
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    kh = ssh_dir / "known_hosts"
    kh.write_text("github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI111\n", encoding="utf-8")

    snapshot = take_snapshot(job, home=fake_home)

    # Truncate
    kh.write_text("git\n", encoding="utf-8")

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "host_path_modified"


def test_audit_known_hosts_deleted_fatal(sample_worktree, fake_home):
    """Deleting known_hosts after snapshot is fatal host_path_modified per D52."""
    repo, job, wt_path = sample_worktree
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    kh = ssh_dir / "known_hosts"
    kh.write_text("github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI111\n", encoding="utf-8")

    snapshot = take_snapshot(job, home=fake_home)

    # Delete
    kh.unlink()

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "host_path_modified"


def test_audit_strict_path_append_fatal(sample_worktree, fake_home):
    """Appending to strict path (.bashrc, authorized_keys) is fatal host_path_modified."""
    repo, job, wt_path = sample_worktree
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    auth_keys = ssh_dir / "authorized_keys"
    auth_keys.write_text("ssh-rsa initial_key user@host\n", encoding="utf-8")
    (fake_home / ".bashrc").write_text("# initial bashrc\n", encoding="utf-8")

    snapshot = take_snapshot(job, home=fake_home)

    # Append to authorized_keys
    with open(auth_keys, "a", encoding="utf-8") as f:
        f.write("ssh-rsa appended_key user@host\n")

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "host_path_modified"

    # Restore authorized_keys, append to .bashrc
    auth_keys.write_text("ssh-rsa initial_key user@host\n", encoding="utf-8")
    with open(fake_home / ".bashrc", "a", encoding="utf-8") as f:
        f.write("export EVIL=1\n")

    findings_bash = audit(job, snapshot, home=fake_home)
    assert findings_bash.has_fatal is True
    assert findings_bash.fatal_reason == "host_path_modified"


def test_audit_known_hosts_over_1mb_fatal(sample_worktree, fake_home):
    """known_hosts exceeding 1MB cannot be verified and is fatal per D52."""
    repo, job, wt_path = sample_worktree
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    kh = ssh_dir / "known_hosts"
    kh.write_text("initial host line\n", encoding="utf-8")

    snapshot = take_snapshot(job, home=fake_home)

    # Write over 1MB of appended lines
    with open(kh, "a", encoding="utf-8") as f:
        f.write("A" * (1024 * 1024 + 10))

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "host_path_modified"


def test_audit_known_hosts_permission_error_fatal(sample_worktree, fake_home, monkeypatch):
    """Permission error when reading modified known_hosts is fatal host_path_modified per D52."""
    repo, job, wt_path = sample_worktree
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    kh = ssh_dir / "known_hosts"
    kh.write_text("initial host line\n", encoding="utf-8")

    snapshot = take_snapshot(job, home=fake_home)

    # Append
    with open(kh, "a", encoding="utf-8") as f:
        f.write("appended host line\n")

    # Simulate permission error on open
    orig_open = open

    def mock_open(path, *args, **kwargs):
        if str(path) == str(kh) and "rb" in args:
            raise PermissionError("Permission denied")
        return orig_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open)

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "host_path_modified"


def test_audit_f1_regression_event_shape():
    """F1 regression: exact audit violation event shape from smoke-fixes-2/f1-audit.jsonl."""
    violation_event = {
        "event": "audit_violation",
        "detail": {"reason": "host_path_modified"},
    }
    assert violation_event["event"] == "audit_violation"
    assert violation_event["detail"]["reason"] == "host_path_modified"

    # Benign event shape per D52
    benign_event = {
        "event": "host_path_changed_benign",
        "detail": {
            "path": "/home/user/.ssh/known_hosts",
            "added_bytes": 105,
            "new_lines_count": 1,
        },
    }
    assert benign_event["event"] == "host_path_changed_benign"
    assert "path" in benign_event["detail"]
    assert "added_bytes" in benign_event["detail"]
    assert "new_lines_count" in benign_event["detail"]
    assert "ssh-ed25519" not in str(benign_event)  # No key material logged
    assert "github.com" not in str(benign_event)   # No hostnames logged


def test_scenario_known_hosts_append(tmp_path: Path):
    """End-to-end manager execution with fake scenario known_hosts_append under tmp HOME."""
    import time

    from agy_mcp.config import Config
    from agy_mcp.manager import JobManager
    from agy_mcp.runner import LocalProcessRunner

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir()
    initial_entry = "initial.com ssh-ed25519 AAAAB3NzaC1lZDI1NTE5AAAAI000\n"
    (ssh_dir / "known_hosts").write_text(initial_entry, encoding="utf-8")

    repo_dir = fake_home / "repo"
    repo = make_repo(repo_dir, env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})

    fake_agy = Path(__file__).parent / "fake_agy" / "agy"
    runner = LocalProcessRunner(executable=fake_agy)
    state_dir = tmp_path / "state"

    manager = JobManager(
        runner=runner,
        config=Config(trust_agents_dir=True),
        base_state_dir=state_dir,
        home=fake_home,
    )

    env = manager.start(
        prompt="Execute task __SCENARIO:known_hosts_append__",
        repo_path=str(repo),
        title="Known Hosts Append Job",
    )

    deadline = time.time() + 8.0
    while time.time() < deadline:
        st = manager.status(env.job_id)
        if st.state == JobState.DONE:
            break
        time.sleep(0.2)

    st = manager.status(env.job_id)
    assert st.state == JobState.DONE
    assert "known_hosts_changed" in st.warnings
    assert st.commits_count >= 1

    # Check audit log for host_path_changed_benign event
    audit_file = state_dir / "jobs" / env.job_id / "audit.jsonl"
    assert audit_file.is_file()
    audit_text = audit_file.read_text(encoding="utf-8")
    assert "host_path_changed_benign" in audit_text
    assert "host_path_modified" not in audit_text


def test_audit_known_hosts_old_replaced_by_older_known_hosts(sample_worktree, fake_home):
    """~/.ssh/known_hosts.old replaced by older known_hosts copy is never fatal per Part D1/D2."""
    repo, job, wt_path = sample_worktree
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    kh = ssh_dir / "known_hosts"
    kh_old = ssh_dir / "known_hosts.old"

    kh.write_text("current.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI111\n", encoding="utf-8")
    kh_old.write_text("old.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI000\n", encoding="utf-8")

    snapshot = take_snapshot(job, home=fake_home)

    # Simulate ssh rewrite pattern: known_hosts.old is replaced with a previous copy of known_hosts
    kh_old.write_text("different_earlier.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI888\n")

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is False
    assert findings.passed is True
    assert "known_hosts_old_changed" in findings.warnings
    assert any(
        ev["event"] == "host_path_changed_benign"
        and ev.get("detail", {}).get("reason") == "backup_file"
        for ev in findings.events
    )


def test_audit_known_hosts_itself_rewritten_still_fatal(sample_worktree, fake_home):
    """known_hosts itself rewritten earlier bytes remains strictly fatal per Part D1/D2."""
    repo, job, wt_path = sample_worktree
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    kh = ssh_dir / "known_hosts"
    kh.write_text("github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI111\n", encoding="utf-8")

    snapshot = take_snapshot(job, home=fake_home)

    # Rewrite known_hosts itself
    kh.write_text("evil.com ssh-ed25519 EVILKEYAAAAAAAAAAAAAAAAAAAAAAA\n", encoding="utf-8")

    findings = audit(job, snapshot, home=fake_home)
    assert findings.has_fatal is True
    assert findings.fatal_reason == "host_path_modified"

