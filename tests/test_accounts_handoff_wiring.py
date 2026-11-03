"""End-to-end conversation transfer and summary handoff on resume (D111)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agy_mcp.accounts.registry import parse_accounts
from agy_mcp.accounts.transfer import SQLITE_HEADER, conversation_lock
from agy_mcp.config import Config
from agy_mcp.jobs import get_job, save_job
from agy_mcp.manager import JobManager
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.states import JobState
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"
CID = "b9c7c56e-ac18-4d63-80af-caafb9328e44"
FULL_EMAIL = "account.owner@example.com"


def _payload(weekly: float) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for group in payload["command"]["data"]["groups"]:
        if group["name"] == "Gemini Models":
            for bucket in group["buckets"]:
                bucket["remaining_fraction"] = weekly if bucket["window"] == "weekly" else 0.9
    return payload


def _write_usage(home: Path, weekly: float) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".fake_agy_usage.json").write_text(json.dumps(_payload(weekly)), encoding="utf-8")
    (home / ".fake_agy_email").write_text(FULL_EMAIL + "\n", encoding="utf-8")


def _account_home(root: Path, name: str) -> Path:
    home = root / name
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    return home


def _setup(tmp_path: Path) -> tuple[JobManager, Path, Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".fake_agy_conversation_store").write_text("1", encoding="utf-8")
    account_b = _account_home(tmp_path, "account-b")
    (account_b / ".fake_agy_conversation_store").write_text("1", encoding="utf-8")
    repo = tmp_path / "repo"
    make_repo(repo, initial_commit=True)
    config = Config(
        accounts=parse_accounts(
            [{"name": "account-b", "home": str(account_b)}], max_accounts=5
        ),
        max_accounts=5,
        model_allowlist=["gemini-3.8-flash-medium"],
        allowed_workdir_roots=[str(tmp_path)],
    )
    manager = JobManager(
        config=config,
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=tmp_path / "state",
        home=home,
        settle_window_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    return manager, repo, home, account_b


def _start_on_b(manager: JobManager, repo: Path):
    return manager.start(
        prompt="work on the task",
        repo_path=str(repo),
        title="Handoff Job",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        account="account-b",
        force=True,
    )


def _pause_with_conversation(manager: JobManager, job_id: str) -> None:
    job = get_job(job_id, manager.state_dir)
    job.state = JobState.NEEDS_REVIEW
    job.review_reason = "quota_exhausted"
    job.resume_after = (
        datetime.now(timezone.utc) + timedelta(hours=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    job.conversation_id = CID
    save_job(job, manager.state_dir)


def _write_db(home: Path, payload: bytes = b"fake conversation\n") -> Path:
    path = home / ".gemini" / "antigravity-cli" / "conversations" / f"{CID}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(SQLITE_HEADER + payload)
    path.chmod(0o600)
    return path


def _audit(manager: JobManager, job_id: str) -> str:
    return (manager.state_dir / "jobs" / job_id / "audit.jsonl").read_text(encoding="utf-8")


def test_transfer_moves_the_db_and_resumes_on_the_target(tmp_path: Path) -> None:
    manager, repo, home, account_b = _setup(tmp_path)
    _write_usage(account_b, 0.01)
    _write_usage(home, 0.90)
    envelope = _start_on_b(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    _pause_with_conversation(manager, envelope.job_id)
    source_db = _write_db(account_b)

    resumed = manager.resume(envelope.job_id, answer="continue", allow_handoff=True)
    manager.wait(resumed.job_id, seconds=20)

    job = get_job(envelope.job_id, manager.state_dir)
    assert job.account == "default"
    assert job.transferred_from == "account-b"
    assert job.transferred_to == "default"
    assert "conversation_transferred" in _audit(manager, envelope.job_id)
    target_db = home / ".gemini" / "antigravity-cli" / "conversations" / f"{CID}.db"
    assert target_db.is_file()
    assert target_db.read_bytes() == source_db.read_bytes()
    assert source_db.is_file()


def test_missing_db_falls_back_to_a_summary_handoff(tmp_path: Path) -> None:
    manager, repo, home, account_b = _setup(tmp_path)
    _write_usage(account_b, 0.01)
    _write_usage(home, 0.90)
    envelope = _start_on_b(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    _pause_with_conversation(manager, envelope.job_id)

    resumed = manager.resume(envelope.job_id, answer="continue", allow_handoff=True)
    manager.wait(resumed.job_id, seconds=20)

    job = get_job(envelope.job_id, manager.state_dir)
    assert job.account == "default"
    assert job.conversation_id != CID
    assert job.transferred_to == "default"
    audit = _audit(manager, envelope.job_id)
    assert "handoff_summary" in audit
    assert "account_transfer_source_missing" in audit


def test_corrupt_db_falls_back_to_a_summary_handoff(tmp_path: Path) -> None:
    manager, repo, home, account_b = _setup(tmp_path)
    _write_usage(account_b, 0.01)
    _write_usage(home, 0.90)
    envelope = _start_on_b(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    _pause_with_conversation(manager, envelope.job_id)
    _write_db(account_b, b"")
    path = account_b / ".gemini" / "antigravity-cli" / "conversations" / f"{CID}.db"
    path.write_bytes(b"not a sqlite file")

    resumed = manager.resume(envelope.job_id, answer="continue", allow_handoff=True)
    manager.wait(resumed.job_id, seconds=20)

    job = get_job(envelope.job_id, manager.state_dir)
    assert job.conversation_id != CID
    assert "account_transfer_corrupt" in _audit(manager, envelope.job_id)


def test_locked_conversation_falls_back_to_a_summary_handoff(tmp_path: Path) -> None:
    manager, repo, home, account_b = _setup(tmp_path)
    _write_usage(account_b, 0.01)
    _write_usage(home, 0.90)
    envelope = _start_on_b(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    _pause_with_conversation(manager, envelope.job_id)
    _write_db(account_b)

    with conversation_lock(manager.state_dir, CID):
        resumed = manager.resume(envelope.job_id, answer="continue", allow_handoff=True)
        manager.wait(resumed.job_id, seconds=20)

    assert "account_transfer_locked" in _audit(manager, envelope.job_id)


def test_no_target_keeps_the_job_on_its_account(tmp_path: Path) -> None:
    manager, repo, home, account_b = _setup(tmp_path)
    _write_usage(account_b, 0.01)
    _write_usage(home, 0.01)
    envelope = _start_on_b(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    _pause_with_conversation(manager, envelope.job_id)
    _write_db(account_b)

    resumed = manager.resume(envelope.job_id, answer="continue", allow_handoff=True)
    manager.wait(resumed.job_id, seconds=20)
    job = get_job(envelope.job_id, manager.state_dir)
    assert job.account == "account-b"
    assert job.transferred_to is None


def test_short_pause_does_not_switch_without_the_caller_request(tmp_path: Path) -> None:
    manager, repo, home, account_b = _setup(tmp_path)
    _write_usage(account_b, 0.01)
    _write_usage(home, 0.90)
    envelope = _start_on_b(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    job = get_job(envelope.job_id, manager.state_dir)
    job.state = JobState.NEEDS_REVIEW
    job.review_reason = "quota_exhausted"
    job.resume_after = (
        datetime.now(timezone.utc) + timedelta(minutes=10)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    job.conversation_id = CID
    save_job(job, manager.state_dir)
    _write_db(account_b)

    resumed = manager.resume(envelope.job_id, answer="continue")
    manager.wait(resumed.job_id, seconds=20)
    assert get_job(envelope.job_id, manager.state_dir).account == "account-b"
