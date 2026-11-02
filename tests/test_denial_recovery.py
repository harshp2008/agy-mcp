"""Unit tests for stop-and-ask preamble rule and auto denial recovery (SPEC 14, 15, 18)."""

import json
from pathlib import Path

from agy_mcp.config import Config
from agy_mcp.jobs import get_job, get_job_dir
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.util import format_fresh_prompt, load_preamble
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _setup_manager_and_repo(
    tmp_path: Path, config: Config | None = None
) -> tuple[JobManager, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    runner = LocalProcessRunner(executable=FAKE_AGY)
    cfg = config or Config(max_concurrent_jobs=2, daily_jobs_cap=5, auto_resume_on_denial=1)
    manager = JobManager(
        config=cfg,
        runner=runner,
        base_state_dir=state_dir,
        home=tmp_path,
    )
    return manager, repo_dir


def test_preamble_rule_10_file_tools_and_size_caps() -> None:
    """Verify preamble rule 10 contents and ensure prompt stays strictly under 100,000 bytes."""
    preamble = load_preamble()

    # Rule 10 requirements from C1:
    assert (
        "Use only your built-in file tools (view, create, edit, list, search) "
        "to read and change files."
    ) in preamble
    assert (
        "Do NOT run shell commands: they are not available here and will end your run." in preamble
    )
    assert "If you believe a shell command is truly required, do not try it." in preamble
    assert (
        "NEEDS_DECISION: need command `<the exact command>` | "
        "allow it, or do it another way | <your recommendation>"
    ) in preamble

    # Prompt byte cap assertion
    full_prompt = format_fresh_prompt("Add basic calculator module in mathutil.py")
    assert len(full_prompt.encode("utf-8")) < 100000


def test_preamble_rule_11_and_rule_6() -> None:
    """Verify rule 11 presence and rule 6 wording in preamble, prompt < 100000 bytes."""
    preamble = load_preamble()
    assert (
        "Do not start subagents, research agents, browsers or any other delegated tool. "
        "They are unavailable here and will end your run. "
        "Do the work yourself with your file tools, "
        "and only read files inside the current directory."
    ) in preamble
    assert "The wrapper runs verification tests and handles git commits" in preamble
    assert "You cannot run test commands or git commands that change state" not in preamble

    full_prompt = format_fresh_prompt("Execute task with file tools")
    assert len(full_prompt.encode("utf-8")) < 100000


def test_subagent_denial_halt_denied_without_progress(tmp_path: Path) -> None:
    """H2-shaped run ends in needs_review(denied_without_progress) when auto-resume disabled."""
    fake_home = tmp_path / "home"
    manager, repo_dir = _setup_manager_and_repo(
        tmp_path,
        config=Config(auto_resume_on_denial=0, agy_home=str(fake_home / "antigravity-cli")),
    )
    env = manager.start(
        prompt="Execute task __SCENARIO:subagent_denial_halt__",
        repo_path=str(repo_dir),
        title="Subagent Denial Halt",
        model="gemini-3.8-flash-medium",
    )

    res = manager.wait(job_id=env.job_id, seconds=10)
    assert res.state == JobState.NEEDS_REVIEW
    assert res.commits_count == 0
    assert res.files_modified == []

    job = get_job(env.job_id, manager.state_dir)
    assert job.review_reason == "denied_without_progress"
    assert len(job.denied_tools) > 0
    assert job.denied_tools[0]["kind"] == "subagent"
    assert job.denied_tools[0]["id"] == "55c62dde-8307-484a-bc8a-4e954192ba01"


def test_subagent_denial_auto_resume_succeeds(tmp_path: Path) -> None:
    """Subagent denial auto-resumes with guidance and succeeds with 1 commit."""
    fake_home = tmp_path / "home"
    manager, repo_dir = _setup_manager_and_repo(
        tmp_path,
        config=Config(auto_resume_on_denial=1, agy_home=str(fake_home / "antigravity-cli")),
    )
    env = manager.start(
        prompt="Execute task __SCENARIO:subagent_denial_then_ok__",
        repo_path=str(repo_dir),
        title="Subagent Denial Auto Resume",
        model="gemini-3.8-flash-medium",
    )

    res = manager.wait(job_id=env.job_id, seconds=10)
    assert res.state == JobState.DONE
    assert res.commits_count == 1

    job_dir = get_job_dir(env.job_id, manager.state_dir)
    audit_events = [
        json.loads(line)
        for line in (job_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    auto_resume_events = [e for e in audit_events if e.get("event") == "denial_auto_resume"]
    assert len(auto_resume_events) == 1
    assert auto_resume_events[0]["detail"]["attempt"] == 1


def test_subagent_denial_exhausted_attempts_stays_needs_review(tmp_path: Path) -> None:
    """When auto-resume attempts are exhausted, job stays in
    needs_review(denied_without_progress)."""
    fake_home = tmp_path / "home"
    manager, repo_dir = _setup_manager_and_repo(
        tmp_path,
        config=Config(auto_resume_on_denial=1, agy_home=str(fake_home / "antigravity-cli")),
    )
    env = manager.start(
        prompt="Execute task __SCENARIO:subagent_denial_halt__",
        repo_path=str(repo_dir),
        title="Subagent Denial Exhausted",
        model="gemini-3.8-flash-medium",
    )

    res = manager.wait(job_id=env.job_id, seconds=10)
    assert res.state == JobState.NEEDS_REVIEW
    assert res.commits_count == 0

    job = get_job(env.job_id, manager.state_dir)
    assert job.review_reason == "denied_without_progress"
    assert len(job.denied_tools) > 0


def test_zero_commit_no_denial_done_with_warning() -> None:
    """Zero commits and zero denials results in done with warning 'no_commits'."""
    from agy_mcp.parse_result import parse_agy_output

    stdout = json.dumps({"status": "SUCCESS", "response": "Task finished", "denied_actions": []})
    parsed = parse_agy_output(
        stdout_content=stdout,
        stderr_content="",
        return_code=0,
        commits_count=0,
        files_modified_count=0,
        has_cli_denials=False,
    )
    assert parsed.state == JobState.DONE
    assert "no_commits" in parsed.warnings


def test_denied_without_progress_unit() -> None:
    """denied_without_progress returns needs_review."""
    from agy_mcp.parse_result import parse_agy_output

    stdout = json.dumps(
        {
            "status": "SUCCESS",
            "response": "Task finished",
            "denied_actions": [{"action": "read_file"}],
        }
    )
    parsed = parse_agy_output(
        stdout_content=stdout,
        stderr_content="",
        return_code=0,
        commits_count=0,
        files_modified_count=0,
        has_cli_denials=False,
    )
    assert parsed.state == JobState.NEEDS_REVIEW
    assert parsed.review_reason == "denied_without_progress"


def test_denial_with_progress_done_with_warnings() -> None:
    """Denial with progress results in done with warnings."""
    from agy_mcp.parse_result import parse_agy_output

    stdout = json.dumps(
        {
            "status": "SUCCESS",
            "response": "Task finished",
            "denied_actions": [{"action": "read_file"}],
        }
    )
    parsed = parse_agy_output(
        stdout_content=stdout,
        stderr_content="",
        return_code=0,
        commits_count=1,
        files_modified_count=1,
        has_cli_denials=False,
    )
    assert parsed.state == JobState.DONE
    assert "denied_actions" in parsed.warnings


def test_auto_resume_on_denial_succeeds_with_one_commit(tmp_path: Path) -> None:
    """Fake scenario denial_then_ok auto-resumes once and finishes in done with 1 commit."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Implement feature __SCENARIO:denial_then_ok__",
        repo_path=str(repo_dir),
        title="Auto Resume Success",
        model="gemini-3.8-flash-medium",
    )

    # Wait for execution and auto-resume to complete
    res = manager.wait(job_id=env.job_id, seconds=10)
    assert res.state == JobState.DONE
    assert res.commits_count == 1

    # Verify audit event denial_auto_resume was recorded
    job_dir = get_job_dir(env.job_id, manager.state_dir)
    audit_events = [
        json.loads(line)
        for line in (job_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    auto_resume_events = [e for e in audit_events if e.get("event") == "denial_auto_resume"]
    assert len(auto_resume_events) == 1
    assert auto_resume_events[0]["detail"]["attempt"] == 1


def test_auto_resume_on_denial_exhausted_ends_needs_review(tmp_path: Path) -> None:
    """When fake agy always halts on denial, attempts exhaust and job ends in needs_review."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=Config(auto_resume_on_denial=1))
    env = manager.start(
        prompt="Execute command __SCENARIO:real_denial_halt__",
        repo_path=str(repo_dir),
        title="Auto Resume Exhausted",
        model="gemini-3.8-flash-medium",
    )

    res = manager.wait(job_id=env.job_id, seconds=10)
    assert res.state == JobState.NEEDS_REVIEW
    assert res.commits_count == 0

    status = manager.status(env.job_id)
    assert status.state == JobState.NEEDS_REVIEW
    assert len(status.denied_commands) > 0
    assert status.denied_commands[0]["step"] == 8
    assert "step 8" in status.next_actions[0]

    # Verify audit event recorded attempt 1 and did not exceed cap
    job_dir = get_job_dir(env.job_id, manager.state_dir)
    audit_events = [
        json.loads(line)
        for line in (job_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    auto_resume_events = [e for e in audit_events if e.get("event") == "denial_auto_resume"]
    assert len(auto_resume_events) == 1


def test_auto_resume_on_denial_disabled_when_zero(tmp_path: Path) -> None:
    """When auto_resume_on_denial=0, no auto-resumes are triggered on denial."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=Config(auto_resume_on_denial=0))
    env = manager.start(
        prompt="Execute command __SCENARIO:real_denial_halt__",
        repo_path=str(repo_dir),
        title="Auto Resume Disabled",
        model="gemini-3.8-flash-medium",
    )

    res = manager.wait(job_id=env.job_id, seconds=10)
    assert res.state == JobState.NEEDS_REVIEW

    job_dir = get_job_dir(env.job_id, manager.state_dir)
    audit_events = [
        json.loads(line)
        for line in (job_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    auto_resume_events = [e for e in audit_events if e.get("event") == "denial_auto_resume"]
    assert len(auto_resume_events) == 0
