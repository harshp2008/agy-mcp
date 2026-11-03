"""Usage/quota parsing, reading, caching, cost and the usage log (D59).

Owns the fail-soft `/usage` parser, the temp-directory `/usage` invocation,
email masking, the 60 s in-memory cache (with an optional per-account key),
low-quota/threshold helpers, per-job cost deltas, auth/quota interruption
detection and the append-only ``usage.jsonl`` record (with optional account
attribution). It must never persist an unmasked email, never let a
usage failure block a job (unavailable only warns), and never raise into callers
from parsing or reading.
"""

import copy
import fcntl
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agy_mcp.jobs import append_audit_event as _jobs_append_audit_event
from agy_mcp.jobs import check_and_increment_daily_cap
from agy_mcp.models import Job, JobState

GROUP_GEMINI = "Gemini Models"
GROUP_CLAUDE_GPT = "Claude and GPT models"

USAGE_PROMPT = "/usage"
USAGE_TIMEOUT_SECONDS = 30.0
USAGE_CACHE_TTL_SECONDS = 60.0
MAX_USAGE_STREAM_BYTES = 1024 * 1024

_APPLY_AUTH_RE = re.compile(
    r"applyAuthResult:\s*email=([^,\s]+)\s*,\s*authMethod=([A-Za-z0-9_.\-]+)"
)
_WINDOW_WEEKLY = "weekly"
_WINDOW_FIVE_HOUR = "5h"


# ---------------------------------------------------------------------------
# Parser (Item 1)
# ---------------------------------------------------------------------------


def mask_email(email: str) -> str:
    """Mask an account email, fully masking a local part of two chars or fewer."""
    # Two visible characters are enough to recognise a normal account in logs,
    # but a one- or two-character local part would expose most of it, so short
    # local parts are masked completely.
    if not isinstance(email, str) or "@" not in email:
        return "***"
    local, _, domain = email.rpartition("@")
    if not local or not domain:
        return "***"
    if len(local) <= 2:
        return f"***@{domain}"
    return f"{local[:2]}***@{domain}"


def parse_auth_line(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract (email, auth_method) from an `applyAuthResult:` log line."""
    if not isinstance(text, str) or not text:
        return None, None
    match = _APPLY_AUTH_RE.search(text)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _parse_iso_utc(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reset_seconds(reset_dt: Optional[datetime], now_dt: datetime) -> Optional[int]:
    if reset_dt is None:
        return None
    return max(0, int((reset_dt - now_dt).total_seconds()))


def _bucket(window: str, fraction: float, reset_dt: Optional[datetime], now_dt: datetime) -> dict:
    return {
        "window": window,
        "remaining_fraction": float(fraction),
        "reset_time_utc": _format_utc(reset_dt) if reset_dt is not None else None,
        "resets_in_seconds": _reset_seconds(reset_dt, now_dt),
    }


def _parse_structured_groups(
    groups_raw: Any, now_dt: datetime
) -> Optional[list[dict[str, Any]]]:
    """Parse command.data.groups[] as the primary source of truth."""
    if not isinstance(groups_raw, list) or not groups_raw:
        return None

    result: list[dict[str, Any]] = []
    for group_raw in groups_raw:
        if not isinstance(group_raw, dict):
            return None
        name = group_raw.get("name")
        buckets_raw = group_raw.get("buckets")
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(buckets_raw, list) or not buckets_raw:
            return None

        buckets: list[dict[str, Any]] = []
        for bucket_raw in buckets_raw:
            if not isinstance(bucket_raw, dict):
                return None
            window = bucket_raw.get("window")
            fraction = bucket_raw.get("remaining_fraction")
            if not isinstance(window, str) or not window.strip():
                return None
            if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
                return None
            fraction_val = float(fraction)
            if fraction_val < 0.0 or fraction_val > 1.0:
                return None
            reset_dt = _parse_iso_utc(bucket_raw.get("reset_time"))
            buckets.append(_bucket(window.strip(), fraction_val, reset_dt, now_dt))

        result.append({"name": name.strip(), "buckets": buckets})

    return result


def _normalize_label(label: str) -> Optional[str]:
    lowered = label.strip().lower()
    if "weekly" in lowered:
        return _WINDOW_WEEKLY
    if "five hour" in lowered or "5-hour" in lowered or "5 hour" in lowered:
        return _WINDOW_FIVE_HOUR
    return None


def _parse_text_groups(response: Any, now_dt: datetime) -> Optional[list[dict[str, Any]]]:
    """Parse the tab-separated `response` text: group, label, percent, reset UTC."""
    if not isinstance(response, str) or not response.strip():
        return None

    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for raw_line in response.splitlines():
        line = raw_line.strip("\r")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            return None
        group_name, label, pct_text, reset_text = (p.strip() for p in parts)
        window = _normalize_label(label)
        if not group_name or window is None:
            return None
        pct_clean = pct_text.rstrip("%").strip()
        try:
            pct = float(pct_clean)
        except ValueError:
            return None
        if pct < 0.0 or pct > 100.0:
            return None
        reset_dt = _parse_iso_utc(reset_text)
        if reset_dt is None:
            return None
        if group_name not in grouped:
            grouped[group_name] = []
            order.append(group_name)
        grouped[group_name].append(_bucket(window, pct / 100.0, reset_dt, now_dt))

    if not order:
        return None
    return [{"name": name, "buckets": grouped[name]} for name in order]


def _cross_check(
    structured: list[dict[str, Any]], text_groups: Optional[list[dict[str, Any]]]
) -> str:
    """Compare structured (primary) values against the text lines."""
    if not text_groups:
        return "missing"
    text_index: dict[tuple[str, str], dict[str, Any]] = {}
    for group in text_groups:
        for bucket in group["buckets"]:
            text_index[(group["name"], bucket["window"])] = bucket

    matched = 0
    for group in structured:
        for bucket in group["buckets"]:
            other = text_index.get((group["name"], bucket["window"]))
            if other is None:
                return "mismatch"
            matched += 1
            if abs(other["remaining_fraction"] - bucket["remaining_fraction"]) > 0.01:
                return "mismatch"
            if (
                other["reset_time_utc"] is not None
                and bucket["reset_time_utc"] is not None
                and other["reset_time_utc"] != bucket["reset_time_utc"]
            ):
                return "mismatch"
    if matched != len(text_index):
        return "mismatch"
    return "ok"


def parse_usage_payload(payload: Any, now: Optional[datetime] = None) -> dict[str, Any]:
    """Parse a decoded `/usage` JSON payload. Never raises."""
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_dt = now_dt.astimezone(timezone.utc)

    try:
        command = payload.get("command") if isinstance(payload, dict) else None
        data = command.get("data") if isinstance(command, dict) else None
        groups_raw = data.get("groups") if isinstance(data, dict) else None
        response = payload.get("response") if isinstance(payload, dict) else None
    except Exception:
        return {"available": False, "reason": "unrecognized_format"}

    try:
        structured = _parse_structured_groups(groups_raw, now_dt)
        text_groups = _parse_text_groups(response, now_dt)
    except Exception:
        return {"available": False, "reason": "unrecognized_format"}

    if structured:
        return {
            "available": True,
            "groups": structured,
            "cross_check": _cross_check(structured, text_groups),
        }
    if text_groups:
        return {
            "available": True,
            "groups": text_groups,
            "cross_check": "text_only",
        }
    return {"available": False, "reason": "unrecognized_format"}


def _load_json_output(raw: str) -> Optional[Any]:
    """Decode the last JSON object in agy stdout, tolerating surrounding log lines."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            return loaded
    except (TypeError, ValueError):
        pass

    for line in reversed([ln.strip() for ln in raw.splitlines() if ln.strip()]):
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            loaded = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def parse_usage_output(raw: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """Parse raw `/usage` stdout JSON. Fail-soft: never raises."""
    payload = _load_json_output(raw)
    if payload is None:
        return {"available": False, "reason": "unrecognized_format"}
    return parse_usage_payload(payload, now=now)


# ---------------------------------------------------------------------------
# Helpers shared by guard, cost, and detection (Items 3-7)
# ---------------------------------------------------------------------------


def group_for_model(model: Optional[str]) -> Optional[str]:
    """Map an agy model identifier to its usage group name."""
    name = (model or "").strip().lower()
    if name.startswith("gemini"):
        return GROUP_GEMINI
    if name.startswith("claude") or name.startswith("gpt"):
        return GROUP_CLAUDE_GPT
    return None


def find_group(reading: dict[str, Any], model: Optional[str]) -> Optional[dict[str, Any]]:
    """Return the usage group relevant to a model, or None when unknown/ambiguous."""
    groups = reading.get("groups") if isinstance(reading, dict) else None
    if not isinstance(groups, list) or not groups:
        return None
    target = group_for_model(model)
    if target is not None:
        for group in groups:
            if isinstance(group, dict) and group.get("name") == target:
                return group
        return None
    if len(groups) == 1 and isinstance(groups[0], dict):
        return groups[0]
    return None


def low_buckets(
    reading: dict[str, Any], model: Optional[str], five_hour_pct: float, weekly_pct: float
) -> list[dict[str, Any]]:
    """Return the relevant group's buckets whose remaining percent is below thresholds."""
    group = find_group(reading, model)
    if group is None:
        return []
    low: list[dict[str, Any]] = []
    for bucket in group.get("buckets", []):
        if not isinstance(bucket, dict):
            continue
        window = bucket.get("window")
        threshold = weekly_pct if window == _WINDOW_WEEKLY else five_hour_pct
        try:
            remaining_pct = float(bucket.get("remaining_fraction")) * 100.0
        except (TypeError, ValueError):
            continue
        if remaining_pct < float(threshold):
            low.append(bucket)
    return low


def exhausted_bucket_reset(
    reading: dict[str, Any], model: Optional[str]
) -> tuple[bool, Optional[str]]:
    """Return (exhausted, latest reset UTC) when a relevant bucket has 0 remaining."""
    if not isinstance(reading, dict) or not reading.get("available"):
        return False, None
    group = find_group(reading, model)
    if group is None:
        return False, None
    exhausted: list[dict[str, Any]] = []
    for bucket in group.get("buckets", []):
        if not isinstance(bucket, dict):
            continue
        try:
            if float(bucket.get("remaining_fraction")) == 0.0:
                exhausted.append(bucket)
        except (TypeError, ValueError):
            continue
    if not exhausted:
        return False, None
    resets = [str(b["reset_time_utc"]) for b in exhausted if b.get("reset_time_utc")]
    return True, (max(resets) if resets else None)


def matches_patterns(text: Any, patterns: Optional[list[str]]) -> bool:
    """Return True when any configured regex/text pattern matches text."""
    if not isinstance(text, str) or not text or not patterns:
        return False
    for pattern in patterns:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def annotate_low_flags(reading: dict[str, Any], config: Any) -> dict[str, Any]:
    """Return a copy of a reading with per-bucket `low` flags applied."""
    result = {k: v for k, v in reading.items() if k != "groups"}
    groups: list[dict[str, Any]] = []
    for group in reading.get("groups", []) or []:
        if not isinstance(group, dict):
            continue
        group_copy = dict(group)
        buckets: list[dict[str, Any]] = []
        for bucket in group.get("buckets", []) or []:
            if not isinstance(bucket, dict):
                continue
            bucket_copy = dict(bucket)
            window = bucket_copy.get("window")
            threshold = (
                config.low_quota_weekly_pct
                if window == _WINDOW_WEEKLY
                else config.low_quota_five_hour_pct
            )
            try:
                remaining_pct = float(bucket_copy.get("remaining_fraction")) * 100.0
            except (TypeError, ValueError):
                remaining_pct = 0.0
            bucket_copy["low"] = remaining_pct < float(threshold)
            buckets.append(bucket_copy)
        group_copy["buckets"] = buckets
        groups.append(group_copy)
    result["groups"] = groups
    return result


def _fraction_for(group: Optional[dict[str, Any]], window: str) -> Optional[float]:
    if not isinstance(group, dict):
        return None
    for bucket in group.get("buckets", []) or []:
        if isinstance(bucket, dict) and bucket.get("window") == window:
            try:
                return float(bucket.get("remaining_fraction"))
            except (TypeError, ValueError):
                return None
    return None


def compute_usage_cost(
    model: Optional[str],
    launch_reading: Optional[dict[str, Any]],
    final_reading: Optional[dict[str, Any]],
    concurrent_jobs: int = 1,
) -> dict[str, Any]:
    """Compute per-job quota consumption from launcher/finalization readings (Item 5).

    Never raises. With more than one concurrent job the figures are approximate because
    quota is shared across jobs.
    """
    count = max(1, int(concurrent_jobs or 1))
    cost: dict[str, Any] = {
        "five_hour_pct_points": 0.0,
        "weekly_pct_points": 0.0,
        "measured": False,
        "concurrent_jobs": count,
        "note": "",
    }

    launch_ok = isinstance(launch_reading, dict) and bool(launch_reading.get("available"))
    final_ok = isinstance(final_reading, dict) and bool(final_reading.get("available"))
    if not launch_ok or not final_ok:
        cost["note"] = "usage unavailable at launch or finalization; cost not measured"
        return cost

    launch_group = find_group(launch_reading, model)
    final_group = find_group(final_reading, model)
    if launch_group is None or final_group is None:
        cost["note"] = "model group not found in usage reading; cost not measured"
        return cost

    launch_weekly = _fraction_for(launch_group, _WINDOW_WEEKLY)
    final_weekly = _fraction_for(final_group, _WINDOW_WEEKLY)
    launch_five = _fraction_for(launch_group, _WINDOW_FIVE_HOUR)
    final_five = _fraction_for(final_group, _WINDOW_FIVE_HOUR)
    if None in (launch_weekly, final_weekly, launch_five, final_five):
        cost["note"] = "usage windows missing at launch or finalization; cost not measured"
        return cost

    raw_weekly = (float(launch_weekly) - float(final_weekly)) * 100.0
    raw_five = (float(launch_five) - float(final_five)) * 100.0

    notes: list[str] = []
    if count > 1:
        notes.append(
            f"approximate: {count} jobs ran concurrently; quota points are shared "
            "across jobs"
        )
    else:
        notes.append("single active job; quota points attributed to this job")

    if raw_weekly < 0.0 or raw_five < 0.0:
        notes.append("a quota window reset during the job; negative deltas clamped to 0")
        raw_weekly = max(0.0, raw_weekly)
        raw_five = max(0.0, raw_five)

    cost["weekly_pct_points"] = round(raw_weekly, 2)
    cost["five_hour_pct_points"] = round(raw_five, 2)
    cost["measured"] = True
    cost["note"] = "; ".join(notes)
    return cost


QUOTA_ESTIMATE_BASIS = "calibrated-2026-10-31, n=7"


def _is_non_negative_number(val: Any) -> bool:
    """Return True if val is a non-negative int or float (excluding bool)."""
    if isinstance(val, bool):
        return False
    if not isinstance(val, (int, float)):
        return False
    if math.isnan(val) or math.isinf(val):
        return False
    return val >= 0


def compute_quota_estimate(
    tokens: Optional[dict[str, Any]],
    weekly_per_1k_tokens: float = 0.0031,
    five_hour_per_1k_tokens: float = 0.011,
) -> Optional[dict[str, Any]]:
    """Estimate quota points from agy's JSON token counts (tokens x coefficient).

    Calibrated conservative coefficients (upper envelope across 7 measured jobs;
    see docs/reports/quota-calibration-data.md and D85). Unknown token counts
    return None instead of fabricating an estimate. Measured usage_cost delta
    remains authoritative. Never raises.
    """
    try:
        if not isinstance(tokens, dict):
            return None

        total: Optional[int] = None
        raw_total = tokens.get("total_tokens")
        if _is_non_negative_number(raw_total):
            total = int(raw_total)
        if total is None or total == 0:
            # ``TokenUsage.from_dict`` materializes an absent ``total_tokens`` as 0,
            # so a zero total is only trustworthy when the component counters agree.
            # Prefer the observed counters over a fabricated zero.
            raw_in = tokens.get("input_tokens")
            raw_out = tokens.get("output_tokens")
            if _is_non_negative_number(raw_in) and _is_non_negative_number(raw_out):
                total = int(raw_in) + int(raw_out)

        if total is None:
            return None

        weekly = max(0.0, float(weekly_per_1k_tokens))
        five_hour = max(0.0, float(five_hour_per_1k_tokens))

        if (
            math.isnan(weekly)
            or math.isinf(weekly)
            or math.isnan(five_hour)
            or math.isinf(five_hour)
        ):
            return None
        base = 0.0

        return {
            "tokens": total,
            "weekly_pct_points": round(base + total / 1000.0 * weekly, 4),
            "five_hour_pct_points": round(base + total / 1000.0 * five_hour, 4),
            "weekly_per_1k_tokens": weekly,
            "five_hour_per_1k_tokens": five_hour,
            "estimated": total > 0,
            "quota_estimate_basis": QUOTA_ESTIMATE_BASIS,
            "note": (
                "estimate from agy JSON tokens (calibrated 2026-10-31); "
                "measured usage_cost is authoritative"
            ),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Reading `/usage` (Item 2)
# ---------------------------------------------------------------------------


class UsageCache:
    """Thread-safe in-memory cache for usage readings (never persisted)."""

    def __init__(self, ttl_seconds: float = USAGE_CACHE_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, now_monotonic: float) -> Optional[dict[str, Any]]:
        """Return a deep copy of a live cache entry, or None when absent/expired."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, reading = entry
            if now_monotonic >= expires_at:
                self._entries.pop(key, None)
                return None
            # Copy on read so a caller annotating `low` flags cannot mutate the
            # cached raw reading.
            return copy.deepcopy(reading)

    def put(self, key: str, reading: dict[str, Any], now_monotonic: float) -> None:
        """Store a deep copy of a reading with a TTL relative to now_monotonic."""
        with self._lock:
            self._entries[key] = (
                now_monotonic + self.ttl_seconds,
                copy.deepcopy(reading),
            )

    def clear(self) -> None:
        """Drop every cached reading."""
        with self._lock:
            self._entries.clear()


_DEFAULT_USAGE_CACHE = UsageCache()


def _account_dict(email: Optional[str], method: Optional[str]) -> dict[str, Any]:
    return {
        "masked_email": mask_email(email) if email else None,
        "auth_method": method or None,
    }


def _unavailable_reading(
    reason: str,
    account: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "groups": [],
        "account": account or {"masked_email": None, "auth_method": None},
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _kill_usage_process(proc: "subprocess.Popen[str]") -> None:
    """Terminate the whole /usage process group (SIGTERM, wait, then always SIGKILL)."""
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=2.0)
    except Exception:
        pass
    # Always escalate to SIGKILL: a child that ignores SIGTERM must not outlive
    # this call just because the group leader exited first.
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=2.0)
    except Exception:
        pass


def read_usage_once(
    agy_binary: str | Path,
    *,
    home: Optional[Path] = None,
    timeout_seconds: float = USAGE_TIMEOUT_SECONDS,
    env_overrides: Optional[dict[str, str]] = None,
    auth_error_patterns: Optional[list[str]] = None,
    expected_email: Optional[str] = None,
    expected_email_sha256: Optional[str] = None,
) -> dict[str, Any]:
    """Invoke `agy --log-file <tmp>/usage.log --output-format json -p "/usage"`.

    Runs in a fresh, empty temporary directory with the same sanitized environment and
    process-group handling used for job launches. The account email is parsed from the
    temporary CLI log and masked in memory; it is never written to a persistent file.
    """
    from agy_mcp.util import env_allowlist

    tmp_dir = Path(tempfile.mkdtemp(prefix="agy-usage-"))
    # Run in an empty throwaway cwd and parse the auth line from a throwaway log:
    # the raw email must never touch a persistent path (D59 item 2).
    try:
        try:
            os.chmod(tmp_dir, 0o700)
        except OSError:
            pass
        log_file = tmp_dir / "usage.log"
        env = env_allowlist(job_tmp_dir=tmp_dir, home=home)
        if env_overrides:
            env.update(env_overrides)

        argv = [
            str(agy_binary),
            "--log-file",
            str(log_file),
            "--output-format",
            "json",
            "-p",
            USAGE_PROMPT,
        ]
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(tmp_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except (OSError, ValueError):
            return _unavailable_reading("unavailable")

        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_usage_process(proc)
            try:
                proc.communicate(timeout=2.0)
            except Exception:
                pass
            return _unavailable_reading("timeout")

        # Cap every stream we inspect. A huge stdout/stderr or temp log is not
        # a usage reading; reject it without parsing (or, for the log, reading).
        if any(
            len((stream or "").encode("utf-8", errors="replace")) > MAX_USAGE_STREAM_BYTES
            for stream in (stdout, stderr)
        ):
            return _unavailable_reading("output_too_large")

        log_text = ""
        if log_file.is_file():
            try:
                log_size = log_file.stat().st_size
            except OSError:
                log_size = 0
            if log_size > MAX_USAGE_STREAM_BYTES:
                return _unavailable_reading("output_too_large")
            try:
                log_text = log_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                log_text = ""

        email, auth_method = parse_auth_line(log_text)
        account = _account_dict(email, auth_method)

        # Multi-account identity hook (D103): when a registry expectation is
        # supplied, compare the FULL parsed address (never the masked value) and
        # turn a mismatch into an unavailable reading. Without an expectation the
        # reading shape is unchanged, so single-account callers are unaffected.
        if expected_email is not None or expected_email_sha256 is not None:
            from agy_mcp.accounts.errors import AccountError
            from agy_mcp.accounts.identity import mask_email as _new_mask
            from agy_mcp.accounts.identity import verify_identity

            try:
                check = verify_identity(
                    email,
                    expected_email=expected_email,
                    expected_email_sha256=expected_email_sha256,
                )
            except AccountError as exc:
                # The multi-account helper reveals at most first3+last2 (never
                # more than half); existing no-expectation readings keep the
                # historical first2 mask pinned by tests.
                account["masked_email"] = _new_mask(email)
                account["identity"] = exc.code
                return _unavailable_reading(exc.code, account)
            account["identity"] = "matched" if check.verified else check.reason
            account["masked_email"] = check.masked_email or account["masked_email"]

        combined = "\n".join([stdout or "", stderr or "", log_text])
        if matches_patterns(combined, auth_error_patterns):
            return _unavailable_reading("auth_required", account)

        if proc.returncode != 0:
            return _unavailable_reading("command_failed", account)

        reading = parse_usage_output(stdout or "")
        if not reading.get("available"):
            return _unavailable_reading(
                str(reading.get("reason") or "unrecognized_format"), account
            )
        reading["account"] = account
        reading["fetched_at"] = datetime.now(timezone.utc).isoformat()
        return reading
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def usage_cache_key(
    agy_binary: str | Path,
    home: Optional[Path] = None,
    account_name: Optional[str] = None,
) -> str:
    """Return the cache key for one account's `/usage` reading.

    With no account the key is exactly the pre-multi-account binary path, so the
    single-account cache is unchanged. With an account the name and home are
    prefixed, so account A's reading can never be served for account B (D107).
    """
    try:
        base = str(Path(agy_binary).resolve())
    except (OSError, ValueError):
        base = str(agy_binary)
    if account_name is None:
        return base
    return f"{account_name}|{home if home is not None else ''}|{base}"


def get_usage(
    agy_binary: str | Path,
    *,
    home: Optional[Path] = None,
    timeout_seconds: float = USAGE_TIMEOUT_SECONDS,
    cache: Optional[UsageCache] = None,
    use_cache: bool = True,
    now_monotonic: Optional[float] = None,
    env_overrides: Optional[dict[str, str]] = None,
    auth_error_patterns: Optional[list[str]] = None,
    account_name: Optional[str] = None,
    expected_email: Optional[str] = None,
    expected_email_sha256: Optional[str] = None,
) -> dict[str, Any]:
    """Return a usage reading, served from the in-memory 60 s cache when possible.

    Never raises for any agy failure; unavailable readings carry `available: false`
    plus a machine-readable `reason`.
    """
    active_cache = cache if cache is not None else _DEFAULT_USAGE_CACHE
    cache_key = usage_cache_key(agy_binary, home=home, account_name=account_name)
    now_val = time.monotonic() if now_monotonic is None else now_monotonic

    if use_cache:
        cached = active_cache.get(cache_key, now_val)
        if cached is not None:
            cached["cached"] = True
            return cached

    reading = read_usage_once(
        agy_binary,
        home=home,
        timeout_seconds=timeout_seconds,
        env_overrides=env_overrides,
        auth_error_patterns=auth_error_patterns,
        expected_email=expected_email,
        expected_email_sha256=expected_email_sha256,
    )
    reading["cached"] = False
    # Cache only successful readings: caching an unavailable result would keep
    # returning it for the full TTL after the transient failure has cleared.
    if reading.get("available") is True:
        active_cache.put(cache_key, reading, now_val)
    return copy.deepcopy(reading)


def append_audit_event(
    job_dir: Path,
    event: str,
    detail: dict[str, Any],
    ts: Optional[str] = None,
) -> None:
    """Atomically append a security audit event to <jobdir>/audit.jsonl with fsync."""
    _jobs_append_audit_event(job_dir, event, detail, ts)


def record_usage(
    usage_path: Path,
    job: Job,
    duration_seconds: float,
    tokens: dict[str, int],
    usage_cost: Optional[dict[str, Any]] = None,
    state: Optional[JobState] = None,
    account: Optional[str] = None,
    masked_email: Optional[str] = None,
) -> None:
    """Append job completion summary to usage.jsonl under an exclusive file lock.

    Per SPEC section 17 and F25:
    Strictly excludes all prompt text, titles, or repository code snippets to prevent
    leakage of sensitive user information.

    ``state`` overrides the recorded state. The supervisor passes the state it is
    about to persist (D68), so the record and job.json always agree even though
    the record is appended first.
    """
    parent = usage_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    os.chmod(parent, 0o700)

    recorded_state = job.state if state is None else state
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "job_id": job.job_id,
        "state": (
            recorded_state.value
            if hasattr(recorded_state, "value")
            else str(recorded_state)
        ),
        "model": job.model,
        "model_resolved": job.model_resolved,
        "duration_seconds": round(duration_seconds, 2),
        "cumulative_minutes": round(job.cumulative_minutes, 2),
        "commits_count": job.commits_count,
        "tokens": tokens,
        "usage_cost": usage_cost if usage_cost is not None else job.usage_cost,
        "quota_estimate": getattr(job, "quota_estimate", None),
        # Identifies the run inside a job (resume increments it). Recovery uses
        # it to avoid appending a second row for a run whose finalizer already
        # wrote one before crashing (D68/C4). Older rows default to 1.
        "run_generation": int(getattr(job, "run_generation", 1) or 1),
        "wall_clock_seconds": round(duration_seconds, 2),
    }
    # Multi-account attribution is additive: without a registry these keys are
    # absent and the row is byte-identical to the single-account record (D107).
    if account is not None:
        record["account"] = account
    if masked_email is not None:
        record["masked_email"] = masked_email

    line = json.dumps(record) + "\n"

    # Open with O_WRONLY | O_CREAT | O_APPEND
    fd = os.open(str(usage_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.chmod(usage_path, 0o600)
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def has_usage_record_for_generation(
    usage_path: Path, job_id: str, run_generation: int, max_bytes: int = 2 * 1024 * 1024
) -> bool:
    """True when ``usage.jsonl`` already has a row for this job run (D68/C4).

    Reads at most ``max_bytes`` from the end of the append-only log. Rows that
    predate the ``run_generation`` field are treated as generation 1, which is
    the default generation of a fresh job.
    """
    if not usage_path.is_file():
        return False
    try:
        with open(usage_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return False
    for line in data.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(record, dict) or record.get("job_id") != job_id:
            continue
        try:
            generation = int(record.get("run_generation", 1) or 1)
        except (TypeError, ValueError):
            generation = 1
        if generation == int(run_generation or 1):
            return True
    return False


def check_daily_cap(state_dir: Path, daily_cap: int) -> bool:
    """Check if the count of job starts today has reached daily_jobs_cap under lock."""
    return check_and_increment_daily_cap(state_dir, daily_cap=daily_cap)
