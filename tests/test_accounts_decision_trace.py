"""Decision trace on ``account_selected``: content, masking and compatibility (D115).

Hermetic and deterministic: the fake agy supplies fixed ``/usage`` payloads, the
selector's clock is injected, and no test sleeps. The trace is produced by the
pure scheduler, enriched with masked emails by the selector, and appended to the
job's audit case by ``create_job``.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agy_mcp.accounts.identity import mask_email
from agy_mcp.accounts.pools import (
    POOL_CLAUDE_GPT,
    POOL_GEMINI,
    AccountUsage,
    PoolReading,
    WindowReading,
)
from agy_mcp.accounts.registry import build_registry, parse_accounts
from agy_mcp.accounts.scheduler import (
    MAX_TRACE_ACCOUNTS,
    AccountState,
    DecisionTrace,
    JobRequest,
    TraceAccount,
    schedule_account,
)
from agy_mcp.accounts.selection import (
    AccountSelector,
    decision_trace_detail,
)
from agy_mcp.config import Config
from agy_mcp.jobs import create_job
from agy_mcp.usage import mask_email as reading_mask_email

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

DEFAULT_EMAIL = "jordan.lee81@example.com"
ACCOUNT_EMAILS = {
    "account-b": "avery.chen42@example.com",
    "account-c": "riley.park57@example.com",
    "account-d": "sam.rivera28@example.com",
    "account-e": "drew.nakamura63@example.com",
    "family-a": "casey.morgan19@example.com",
    "family-b": "taylor.reed84@example.com",
}


def _payload(
    weekly: float = 0.9,
    weekly_reset: str = "2026-11-08T12:00:00Z",
    five: float = 0.9,
    five_reset: str = "2026-11-04T16:00:00Z",
) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for group in payload["command"]["data"]["groups"]:
        for bucket in group["buckets"]:
            if group["name"] == POOL_GEMINI:
                if bucket["window"] == "weekly":
                    bucket["remaining_fraction"] = weekly
                    bucket["reset_time"] = weekly_reset
                elif bucket["window"] == "5h":
                    bucket["remaining_fraction"] = five
                    bucket["reset_time"] = five_reset
            elif group["name"] == POOL_CLAUDE_GPT:
                bucket["remaining_fraction"] = 0.9
    return payload


def _write_home(
    home: Path,
    email: str,
    *,
    weekly: float = 0.9,
    weekly_reset: str = "2026-11-08T12:00:00Z",
    five: float = 0.9,
    five_reset: str = "2026-11-04T16:00:00Z",
) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    (home / ".fake_agy_usage.json").write_text(
        json.dumps(
            _payload(
                weekly=weekly,
                weekly_reset=weekly_reset,
                five=five,
                five_reset=five_reset,
            )
        ),
        encoding="utf-8",
    )
    (home / ".fake_agy_email").write_text(email + "\n", encoding="utf-8")
    return home


def _selector(
    tmp_path: Path,
    accounts: list[dict],
    default_usage: dict | None = None,
    account_usage: dict[str, dict] | None = None,
) -> AccountSelector:
    home = _write_home(tmp_path / "home", DEFAULT_EMAIL, **(default_usage or {}))
    for spec in accounts:
        usage = (account_usage or {}).get(spec["name"], {})
        _write_home(Path(spec["home"]), ACCOUNT_EMAILS[spec["name"]], **usage)
    parsed = parse_accounts(accounts, max_accounts=5)
    config = Config(
        accounts=parsed,
        max_accounts=5,
        model_allowlist=["gemini-3.8-flash-medium"],
        allowed_workdir_roots=[str(tmp_path)],
    )
    registry = build_registry(config, home)
    return AccountSelector(
        config,
        registry,
        home,
        str(FAKE_AGY),
        state_dir=tmp_path / "state",
        now_func=lambda: NOW,
    )


def _account(tmp_path: Path, name: str, **usage) -> dict:
    home = tmp_path / name
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    return {"name": name, "home": str(home), **usage}


def _rows(detail: dict) -> dict[str, dict]:
    return {row["account"]: row for row in detail["accounts"]}


# ---------------------------------------------------------------------------
# Trace content
# ---------------------------------------------------------------------------


def test_trace_for_one_account_is_only_candidate(tmp_path: Path) -> None:
    selector = _selector(tmp_path, [], default_usage={"weekly": 0.9})
    selection = selector.select("gemini-3.8-flash-medium", 10)
    assert selection.account is not None
    detail = decision_trace_detail(selection.trace)
    assert detail is not None
    assert detail["winner"] == "default"
    assert detail["rule"] == "only_candidate"
    assert len(detail["accounts"]) == 1
    row = detail["accounts"][0]
    assert row["account"] == "default"
    assert row["eligible"] is True
    assert row["rank"] == 1
    assert row["pool"] == "default"
    assert row["weekly_remaining_pct"] == pytest.approx(90.0)
    assert row["five_hour_remaining_pct"] == pytest.approx(90.0)
    assert row["hours_to_binding_reset"] == pytest.approx(96.0)
    assert row["urgency_score"] == pytest.approx(90.0 / 96.0, abs=1e-4)
    # The trace repeats the usage reading's masked form (legacy shape).
    assert row["account_masked_email"] == reading_mask_email(DEFAULT_EMAIL)


def test_trace_two_accounts_records_the_human_test_decision(tmp_path: Path) -> None:
    """The 2026-11-04 human test: 58% in 40 min beats 88% in four days."""
    account_b = _account(tmp_path, "account-b")
    selector = _selector(
        tmp_path,
        [account_b],
        default_usage={"weekly": 0.58, "weekly_reset": "2026-11-04T12:40:00Z"},
        account_usage={"account-b": {"weekly": 0.88}},
    )
    selection = selector.select("gemini-3.8-flash-medium", 10)
    assert selection.account is not None
    assert selection.account.name == "default"
    detail = decision_trace_detail(selection.trace)
    assert detail is not None
    assert detail["winner"] == "default"
    assert detail["rule"] == "urgency"
    rows = _rows(detail)
    assert rows["default"]["rank"] == 1
    assert rows["account-b"]["rank"] == 2
    assert rows["default"]["weekly_remaining_pct"] == pytest.approx(58.0)
    assert rows["default"]["hours_to_binding_reset"] == pytest.approx(0.6667, abs=1e-4)
    assert rows["default"]["urgency_score"] == pytest.approx(87.0, abs=1e-3)
    assert rows["account-b"]["weekly_remaining_pct"] == pytest.approx(88.0)
    assert rows["account-b"]["hours_to_binding_reset"] == pytest.approx(96.0)
    assert rows["account-b"]["account_masked_email"] == reading_mask_email(
        ACCOUNT_EMAILS["account-b"]
    )
    blob = json.dumps(detail)
    assert DEFAULT_EMAIL not in blob
    assert ACCOUNT_EMAILS["account-b"] not in blob
    assert "***@" in blob


def test_trace_five_accounts_records_a_rejection(tmp_path: Path) -> None:
    declared = [
        _account(tmp_path, "account-b"),
        _account(tmp_path, "account-c"),
        _account(tmp_path, "account-d"),
        _account(tmp_path, "account-e", enabled=False),
    ]
    selector = _selector(
        tmp_path,
        declared,
        default_usage={"weekly": 0.80, "weekly_reset": "2026-11-06T12:00:00Z"},
        account_usage={
            "account-b": {"weekly": 0.70, "weekly_reset": "2026-11-04T16:00:00Z"},
            "account-c": {"weekly": 0.60, "weekly_reset": "2026-11-04T18:00:00Z"},
            "account-d": {"weekly": 0.50, "weekly_reset": "2026-11-04T20:00:00Z"},
        },
    )
    selection = selector.select("gemini-3.8-flash-medium", 10)
    assert selection.account is not None
    detail = decision_trace_detail(selection.trace)
    assert detail is not None
    assert len(detail["accounts"]) == 5
    rows = _rows(detail)
    assert rows["account-e"]["eligible"] is False
    assert rows["account-e"]["rejection"] == "disabled"
    eligible = [row for row in rows.values() if row["eligible"]]
    assert sorted(row["rank"] for row in eligible) == [1, 2, 3, 4]
    # account-b is the most urgent strict account (70% in four hours).
    assert detail["winner"] == "account-b"
    assert rows["account-b"]["rank"] == 1
    assert detail["rule"] == "urgency"


def test_trace_shared_pool_uses_group_quota_and_name_tie_break(tmp_path: Path) -> None:
    family_a = _account(tmp_path, "family-a", pool="family")
    family_b = _account(tmp_path, "family-b", pool="family")
    selector = _selector(
        tmp_path,
        [family_a, family_b],
        # The default cannot fit, so only the shared pool is eligible.
        default_usage={"weekly": 0.01},
        account_usage={
            "family-a": {"weekly": 0.50, "weekly_reset": "2026-11-08T12:00:00Z"},
            "family-b": {"weekly": 0.60, "weekly_reset": "2026-11-09T12:00:00Z"},
        },
    )
    selection = selector.select("gemini-3.8-flash-medium", 10)
    assert selection.account is not None
    assert selection.account.name == "family-a"
    detail = decision_trace_detail(selection.trace)
    assert detail is not None
    rows = _rows(detail)
    assert rows["default"]["eligible"] is False
    assert rows["default"]["rejection"] == "quota_exhausted"
    # Both members are one bucket: the group minimum (50%) and its reset.
    assert rows["family-a"]["pool"] == "family"
    assert rows["family-b"]["pool"] == "family"
    assert rows["family-a"]["weekly_remaining_pct"] == pytest.approx(50.0)
    assert rows["family-b"]["weekly_remaining_pct"] == pytest.approx(50.0)
    assert rows["family-a"]["hours_to_binding_reset"] == pytest.approx(96.0)
    assert rows["family-b"]["hours_to_binding_reset"] == pytest.approx(96.0)
    assert rows["family-a"]["rank"] == 1
    assert rows["family-b"]["rank"] == 2
    assert detail["rule"] == "lru"


def test_trace_background_names_burn_and_the_real_binding_reset(tmp_path: Path) -> None:
    account_b = _account(tmp_path, "account-b")
    account_c = _account(tmp_path, "account-c")
    selector = _selector(
        tmp_path,
        [account_b, account_c],
        default_usage={
            "weekly": 0.01,
            "weekly_reset": "2026-11-04T13:00:00Z",
            # A full five-hour window with a rolling five-hour placeholder reset.
            "five": 1.0,
            "five_reset": "2026-11-04T17:00:00Z",
        },
        account_usage={
            "account-b": {"weekly": 0.90},
            "account-c": {"weekly": 1.0, "weekly_reset": "2026-11-04T12:05:00Z"},
        },
    )
    selection = selector.select("gemini-3.8-flash-medium", 10, priority="background")
    assert selection.account is not None
    assert selection.account.name == "default"
    assert selection.result.reason == "burn"
    detail = decision_trace_detail(selection.trace)
    assert detail is not None
    assert detail["winner"] == "default"
    assert detail["rule"] == "urgency"
    rows = _rows(detail)
    assert rows["default"]["rank"] == 1
    assert rows["default"]["hours_to_binding_reset"] == pytest.approx(1.0)
    assert rows["default"]["binding_window"] == "weekly"
    # The untouched five-hour window is full: no urgency and no binding reset.
    assert rows["default"]["five_hour_remaining_pct"] == pytest.approx(100.0)
    assert rows["account-c"]["urgency_score"] == pytest.approx(0.0)


def test_trace_is_bounded(tmp_path: Path) -> None:
    states = tuple(
        AccountState(
            name=f"a{index:02d}",
            pool_key=f"a{index:02d}",
            usage=AccountUsage(
                account=f"a{index:02d}",
                available=True,
                pools=(
                    PoolReading(
                        POOL_GEMINI,
                        (
                            WindowReading("weekly", 0.8, "2026-11-08T12:00:00Z"),
                            WindowReading("5h", 0.8, "2026-11-04T16:00:00Z"),
                        ),
                    ),
                ),
            ),
        )
        for index in range(MAX_TRACE_ACCOUNTS + 8)
    )
    result = schedule_account(states, JobRequest("gemini-3.8-flash-medium", 10), NOW)
    assert len(result.trace.accounts) == MAX_TRACE_ACCOUNTS
    assert result.trace.truncated is True


def test_trace_detail_truncates_a_prebuilt_trace(tmp_path: Path) -> None:
    trace = DecisionTrace(
        accounts=tuple(
            TraceAccount(name=f"a{index:02d}", eligible=True, rank=index + 1)
            for index in range(MAX_TRACE_ACCOUNTS + 5)
        ),
        winner="a00",
        rule="urgency",
        truncated=True,
    )
    detail = decision_trace_detail(trace)
    assert detail is not None
    assert len(detail["accounts"]) == MAX_TRACE_ACCOUNTS
    assert detail["truncated"] is True


# ---------------------------------------------------------------------------
# Audit compatibility
# ---------------------------------------------------------------------------


def _audit_events(state_dir: Path, job_id: str) -> list[dict]:
    path = state_dir / "jobs" / job_id / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _new_job(tmp_path: Path, **kwargs):
    return create_job(
        repo_path=tmp_path / "repo",
        title="Decision Trace Job",
        prompt="do work",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        base_state_dir=tmp_path / "state",
        **kwargs,
    )


def test_account_selected_carries_the_decision_trace(tmp_path: Path) -> None:
    account_b = _account(tmp_path, "account-b")
    selector = _selector(
        tmp_path,
        [account_b],
        default_usage={"weekly": 0.58, "weekly_reset": "2026-11-04T12:40:00Z"},
        account_usage={"account-b": {"weekly": 0.88}},
    )
    selection = selector.select("gemini-3.8-flash-medium", 10)
    assert selection.account is not None
    detail = decision_trace_detail(selection.trace)
    job = _new_job(
        tmp_path,
        account=selection.account.name,
        account_masked_email=selection.masked_email,
        decision_trace=detail,
    )
    events = _audit_events(tmp_path / "state", job.job_id)
    selected = [event for event in events if event["event"] == "account_selected"]
    assert len(selected) == 1
    recorded = selected[0]["detail"]
    assert recorded["account"] == "default"
    assert recorded["decision_trace"] == detail
    assert recorded["decision_trace"]["winner"] == "default"
    assert recorded["decision_trace"]["rule"] == "urgency"
    audit_text = (tmp_path / "state" / "jobs" / job.job_id / "audit.jsonl").read_text(
        encoding="utf-8"
    )
    assert DEFAULT_EMAIL not in audit_text
    assert ACCOUNT_EMAILS["account-b"] not in audit_text


def test_account_selected_without_a_trace_is_unchanged(tmp_path: Path) -> None:
    """No registry selection means no new key: the old detail is byte-identical."""
    job = _new_job(tmp_path, account="solo")
    events = _audit_events(tmp_path / "state", job.job_id)
    selected = [event for event in events if event["event"] == "account_selected"]
    assert len(selected) == 1
    assert selected[0]["detail"] == {"account": "solo"}

    masked_job = _new_job(
        tmp_path,
        job_id="11111111-1111-1111-1111-111111111111",
        account="solo",
        account_masked_email=mask_email(DEFAULT_EMAIL),
    )
    events = _audit_events(tmp_path / "state", masked_job.job_id)
    selected = [event for event in events if event["event"] == "account_selected"]
    assert selected[0]["detail"] == {
        "account": "solo",
        "account_masked_email": mask_email(DEFAULT_EMAIL),
    }


def test_no_registry_start_writes_no_account_selected_event(tmp_path: Path) -> None:
    """A single-account job never had this event, and still does not."""
    job = _new_job(tmp_path)
    events = _audit_events(tmp_path / "state", job.job_id)
    assert not [event for event in events if event["event"] == "account_selected"]


def test_explicit_account_trace_names_the_override(tmp_path: Path) -> None:
    account_b = _account(tmp_path, "account-b")
    selector = _selector(
        tmp_path,
        [account_b],
        default_usage={"weekly": 0.01},
        account_usage={"account-b": {"weekly": 0.90}},
    )
    selection = selector.select("gemini-3.8-flash-medium", 10, account="account-b")
    assert selection.account is not None
    detail = decision_trace_detail(selection.trace)
    assert detail is not None
    assert detail["winner"] == "account-b"
    assert detail["rule"] == "explicit_account"
    rows = _rows(detail)
    assert rows["default"]["eligible"] is False
    assert rows["account-b"]["eligible"] is True


def test_forced_account_trace_names_force(tmp_path: Path) -> None:
    account_b = _account(tmp_path, "account-b")
    selector = _selector(
        tmp_path,
        [account_b],
        default_usage={"weekly": 0.90},
        account_usage={"account-b": {"weekly": 0.01}},
    )
    selection = selector.select(
        "gemini-3.8-flash-medium", 10, account="account-b", force=True
    )
    assert selection.account is not None
    detail = decision_trace_detail(selection.trace)
    assert detail is not None
    assert detail["winner"] == "account-b"
    assert detail["rule"] == "force"
    rows = _rows(detail)
    assert rows["account-b"]["eligible"] is False
    assert rows["account-b"]["rejection"] == "quota_exhausted"
