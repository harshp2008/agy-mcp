"""A planted full email must never reach wrapper records, logs or envelopes (D103).

agy's own ``cli.log`` is written by the CLI and is not rewritten by the wrapper;
this test scopes the guarantee to wrapper-generated state, events and envelopes.
"""

import json
from pathlib import Path

from agy_mcp.accounts.doctor import doctor_report, render_report
from agy_mcp.accounts.registry import parse_accounts
from agy_mcp.config import Config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"
PLANTED_EMAIL = "planted.person@example.com"


def _payload() -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for group in payload["command"]["data"]["groups"]:
        for bucket in group["buckets"]:
            bucket["remaining_fraction"] = 0.9
    return payload


def test_wrapper_outputs_never_contain_the_full_account_email(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    account_b = tmp_path / "account-b"
    account_b.mkdir(mode=0o700)
    account_b.chmod(0o700)
    for account_home in (home, account_b):
        (account_home / ".fake_agy_usage.json").write_text(
            json.dumps(_payload()), encoding="utf-8"
        )
        (account_home / ".fake_agy_email").write_text(
            PLANTED_EMAIL + "\n", encoding="utf-8"
        )
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
    envelope = manager.start(
        prompt="do work",
        repo_path=str(repo),
        title="Email Hygiene Job",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        account="account-b",
    )
    manager.wait(envelope.job_id, seconds=20)
    job = get_job(envelope.job_id, manager.state_dir)
    job_dir = manager.state_dir / "jobs" / envelope.job_id

    wrapper_files = [job_dir / "job.json", job_dir / "audit.jsonl"]
    usage_path = manager.state_dir / "usage.jsonl"
    if usage_path.is_file():
        wrapper_files.append(usage_path)
    server_log = manager.state_dir / "server.log"
    if server_log.is_file():
        wrapper_files.append(server_log)

    envelopes = [
        manager.wait(envelope.job_id, seconds=1).to_dict(),
        manager.status(envelope.job_id).to_dict(),
        manager.result(envelope.job_id).to_dict(),
        envelope.to_dict(),
        job.to_dict(),
    ]

    blob = "\n".join(
        [path.read_text(encoding="utf-8", errors="replace") for path in wrapper_files]
        + [json.dumps(item, default=str) for item in envelopes]
    )
    assert PLANTED_EMAIL not in blob
    # A masked form is present and recognisable; the full address is not.
    assert "***@" in blob

    report = doctor_report(config, home, str(FAKE_AGY))
    rendered = render_report(report)
    assert PLANTED_EMAIL not in rendered
    assert "***@" in rendered

