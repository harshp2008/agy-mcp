"""Security snapshots and the read-only audit detector (SPEC section 12).

Owns the pre-launch baseline (worktree pointer, git config, agents/instruction/
policy files, host-sensitive paths), the append-tolerant host-path rules
(D52/D54), and the pure comparison that produces :class:`Findings`. It must
never kill processes or write state itself, and it must never treat an
unreadable baseline as a pass: callers turn `None` snapshots into fatal
findings.
"""

import hashlib
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from agy_mcp.gitops import diff_git_config_snapshots, take_git_config_snapshot
from agy_mcp.models import Job
from agy_mcp.util import sha256_file


class AuditSeverity(str, Enum):
    """Severity levels for audit findings."""

    FATAL = "fatal"
    BLOCKED = "blocked"
    WARN = "warn"


class HostPathClass(str, Enum):
    """Host security path sensitivity classification per D52 & D54."""

    STRICT = "strict"
    APPEND_TOLERANT = "append_tolerant"
    WARN_ONLY = "warn_only"


MAX_APPEND_TOLERANT_BYTES = 1024 * 1024  # 1 MB cap per D52


def classify_host_path(path: Path | str, home: Optional[Path] = None) -> HostPathClass:
    """Classify host security path as strict, append_tolerant, or warn_only per D52 & D54."""
    resolved_home = (home or Path.home()).resolve()
    p = Path(path).resolve()
    try:
        if p == (resolved_home / ".ssh" / "known_hosts.old").resolve():
            return HostPathClass.WARN_ONLY
        if p == (resolved_home / ".ssh" / "known_hosts").resolve():
            return HostPathClass.APPEND_TOLERANT
    except Exception:
        pass
    if p.name == "known_hosts.old" and p.parent.name == ".ssh":
        return HostPathClass.WARN_ONLY
    if p.name == "known_hosts" and p.parent.name == ".ssh":
        return HostPathClass.APPEND_TOLERANT
    return HostPathClass.STRICT


@dataclass
class Finding:
    """Represents an individual security finding."""

    severity: AuditSeverity
    reason: str
    path: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize severity as its string value for envelopes/audit logs."""
        return {
            "severity": self.severity.value,
            "reason": self.reason,
            "path": self.path,
            "detail": self.detail,
        }


@dataclass
class Findings:
    """Collection of findings produced by an audit pass."""

    items: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_fatal(self) -> bool:
        """True when any finding has FATAL severity (the audit gate)."""
        return any(f.severity == AuditSeverity.FATAL for f in self.items)

    @property
    def fatal_reason(self) -> Optional[str]:
        """Return the first fatal reason, or None when the audit passed."""
        for f in self.items:
            if f.severity == AuditSeverity.FATAL:
                return f.reason
        return None

    @property
    def blocked_files(self) -> list[str]:
        """Return paths whose changes were blocked but not fatal."""
        return [f.path for f in self.items if f.severity == AuditSeverity.BLOCKED]

    @property
    def passed(self) -> bool:
        """True when no FATAL finding exists (blocked/warn findings may remain)."""
        return not self.has_fatal


@dataclass
class AuditSnapshot:
    """Cryptographic baseline snapshot of host paths, worktree pointer, and git configs."""

    dot_git_content: Optional[str]
    git_config: dict[str, str]
    host_paths: dict[str, str]
    instruction_files: dict[str, Optional[str]]
    policy_files: dict[str, Optional[str]]
    agents_files: dict[str, str]


def save_snapshot(snapshot: AuditSnapshot, path: Path) -> None:
    """Persist AuditSnapshot to disk file."""
    import json

    data = {
        "dot_git_content": snapshot.dot_git_content,
        "git_config": snapshot.git_config,
        "host_paths": snapshot.host_paths,
        "instruction_files": snapshot.instruction_files,
        "policy_files": snapshot.policy_files,
        "agents_files": snapshot.agents_files,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024  # 2 MB explicit size cap per F16


def load_snapshot(path: Path) -> Optional[AuditSnapshot]:
    """Load persisted AuditSnapshot from disk file."""
    import json

    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_SNAPSHOT_BYTES:
            # Fail closed on oversized snapshot per F16. Callers convert None
            # into a fatal audit_violation; they never treat this as a pass.
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return AuditSnapshot(
            dot_git_content=data.get("dot_git_content"),
            git_config=data.get("git_config", {}),
            host_paths=data.get("host_paths", {}),
            instruction_files=data.get("instruction_files", {}),
            policy_files=data.get("policy_files", {}),
            agents_files=data.get("agents_files", {}),
        )
    except Exception:
        return None


def _hash_or_missing(path: Path) -> str:
    """Return mtime_ns:size:sha256 or 'missing' / 'unreadable' for a path."""
    if not path.is_file():
        return "missing"
    try:
        st = path.stat()
        mtime = st.st_mtime_ns
        size = st.st_size
        sha = sha256_file(path)
        return f"{mtime}:{size}:{sha}"
    except (OSError, PermissionError):
        return "unreadable"


def take_host_path_snapshot(
    home: Optional[Path] = None,
    source_repo_git_config: Optional[Path] = None,
) -> dict[str, str]:
    """Capture (mtime_ns + size + sha256) snapshot of host security paths (~/.ssh, ~/.bashrc, etc.).

    NOTE: This is a DETECTOR only, not an OS-level prevention sandbox per SPEC section 12.
    """
    resolved_home = (home or Path.home()).resolve()
    snapshot: dict[str, str] = {}

    target_files = [
        resolved_home / ".bashrc",
        resolved_home / ".profile",
        resolved_home / ".zshrc",
        resolved_home / ".gitconfig",
        resolved_home / ".netrc",
        resolved_home / ".config" / "agy-mcp" / "config.toml",
    ]
    if source_repo_git_config:
        target_files.append(Path(source_repo_git_config).resolve())

    for tf in target_files:
        snapshot[str(tf)] = _hash_or_missing(tf)

    # Scan ~/.ssh/* and ~/.gnupg/*
    for sub in (".ssh", ".gnupg"):
        sub_dir = resolved_home / sub
        if sub_dir.is_dir():
            for root, _, files in os.walk(sub_dir):
                for f in files:
                    fp = Path(root) / f
                    snapshot[str(fp)] = _hash_or_missing(fp)
        else:
            snapshot[str(sub_dir)] = "missing"

    return snapshot


@dataclass
class HostPathAuditResult:
    """Result of host path comparison including fatal paths and benign changes."""

    fatal_paths: list[str] = field(default_factory=list)
    benign_changes: list[dict[str, Any]] = field(default_factory=list)


def check_host_path_snapshot(
    baseline: dict[str, str],
    home: Optional[Path] = None,
    source_repo_git_config: Optional[Path] = None,
) -> HostPathAuditResult:
    """Compare current host path state against baseline per D52.

    strict paths: any difference in presence or content hash is fatal.
    append_tolerant paths (~/.ssh/known_hosts, ~/.ssh/known_hosts.old):
      pure appends (old_size <= new_size, first old_size bytes match) or
      creation of previously non-existent file are recorded as benign.
      Modifications to earlier bytes, truncation, deletion, >1MB size,
      or unreadable files remain fatal.
    """
    current = take_host_path_snapshot(home=home, source_repo_git_config=source_repo_git_config)
    result = HostPathAuditResult()
    all_keys = set(baseline.keys()) | set(current.keys())

    for k in sorted(all_keys):
        p = Path(k)
        base_val = baseline.get(k, "missing")
        curr_val = current.get(k, "missing")

        if base_val == curr_val:
            continue

        p_class = classify_host_path(p, home=home)
        if p_class == HostPathClass.STRICT:
            result.fatal_paths.append(k)
            continue

        if p_class == HostPathClass.WARN_ONLY:
            # Any change to known_hosts.old is benign and never fatal per D54
            result.benign_changes.append(
                {
                    "path": str(p),
                    "reason": "backup_file",
                }
            )
            continue

        # append_tolerant path logic
        # 1. Disappeared or unreadable
        if curr_val == "missing" or not p.is_file():
            result.fatal_paths.append(k)
            continue

        if curr_val == "unreadable":
            result.fatal_paths.append(k)
            continue

        try:
            st = p.stat()
            new_size = st.st_size
        except (OSError, PermissionError):
            result.fatal_paths.append(k)
            continue

        if new_size > MAX_APPEND_TOLERANT_BYTES:
            result.fatal_paths.append(k)
            continue

        try:
            with open(p, "rb") as f:
                new_bytes = f.read(MAX_APPEND_TOLERANT_BYTES + 1)
        except (OSError, PermissionError):
            result.fatal_paths.append(k)
            continue

        if len(new_bytes) > MAX_APPEND_TOLERANT_BYTES:
            result.fatal_paths.append(k)
            continue

        # Case A: file did not exist before and now exists
        # A new known_hosts entry is a normal SSH side effect, so it is recorded
        # as benign instead of killing a long-running job (D52).
        if base_val == "missing":
            added_bytes = len(new_bytes)
            new_lines_count = new_bytes.count(b"\n")
            result.benign_changes.append(
                {
                    "path": str(p),
                    "added_bytes": added_bytes,
                    "new_lines_count": new_lines_count,
                }
            )
            continue

        if base_val == "unreadable":
            result.fatal_paths.append(k)
            continue

        parts = base_val.split(":")
        if len(parts) == 3:
            try:
                old_size = int(parts[1])
                old_sha = parts[2]
            except ValueError:
                result.fatal_paths.append(k)
                continue
        elif len(parts) == 2:
            # Legacy format mtime:sha without size -> cannot verify append
            result.fatal_paths.append(k)
            continue
        else:
            result.fatal_paths.append(k)
            continue

        if old_size > MAX_APPEND_TOLERANT_BYTES:
            result.fatal_paths.append(k)
            continue

        # Truncated or shrank
        if new_size < old_size:
            result.fatal_paths.append(k)
            continue

        # Check first old_size bytes hash
        first_bytes = new_bytes[:old_size]
        first_sha = hashlib.sha256(first_bytes).hexdigest()
        if first_sha != old_sha:
            result.fatal_paths.append(k)
            continue

        # Pure append (or untouched content with changed mtime)
        added_bytes = new_size - old_size
        added = new_bytes[old_size:]
        new_lines_count = added.count(b"\n")
        result.benign_changes.append(
            {
                "path": str(p),
                "added_bytes": added_bytes,
                "new_lines_count": new_lines_count,
            }
        )

    return result


def verify_host_path_snapshot(
    baseline: dict[str, str],
    home: Optional[Path] = None,
    source_repo_git_config: Optional[Path] = None,
) -> list[str]:
    """Compare current host path state against baseline, returning list of mutated fatal paths.

    NOTE: This is a DETECTOR only.
    """
    res = check_host_path_snapshot(
        baseline=baseline,
        home=home,
        source_repo_git_config=source_repo_git_config,
    )
    return res.fatal_paths


def take_snapshot(
    job: Job,
    home: Optional[Path] = None,
    source_repo_git_config: Optional[Path] = None,
) -> AuditSnapshot:
    """Capture complete security snapshot before launch or resume."""
    wt_path = job.worktree_path.resolve() if job.worktree_path else None

    # 1. Worktree .git pointer file
    dot_git_content = None
    if wt_path and (wt_path / ".git").is_file():
        try:
            dot_git_content = (wt_path / ".git").read_text(encoding="utf-8").strip()
        except OSError:
            dot_git_content = None

    # 2. Resolved git config
    git_config = take_git_config_snapshot(wt_path) if wt_path and wt_path.exists() else {}

    # 3. Host paths
    source_config = source_repo_git_config or (job.repo_path / ".git" / "config")
    host_paths = take_host_path_snapshot(home=home, source_repo_git_config=source_config)

    # 4. Instruction files: AGENTS.md, GEMINI.md
    instruction_files: dict[str, Optional[str]] = {}
    if wt_path and wt_path.exists():
        for inf in ("AGENTS.md", "GEMINI.md"):
            p = wt_path / inf
            instruction_files[inf] = _hash_or_missing(p) if p.exists() else None

    # 5. Policy files: .gitignore, .gitattributes, .gitmodules
    policy_files: dict[str, Optional[str]] = {}
    if wt_path and wt_path.exists():
        for pf in (".gitignore", ".gitattributes", ".gitmodules"):
            p = wt_path / pf
            policy_files[pf] = _hash_or_missing(p) if p.exists() else None

    # 6. Agents directory files: .agents/**
    agents_files: dict[str, str] = {}
    if wt_path and (wt_path / ".agents").is_dir():
        for root, _, files in os.walk(wt_path / ".agents"):
            for f in files:
                fp = Path(root) / f
                rel = str(fp.relative_to(wt_path))
                agents_files[rel] = _hash_or_missing(fp)

    return AuditSnapshot(
        dot_git_content=dot_git_content,
        git_config=git_config,
        host_paths=host_paths,
        instruction_files=instruction_files,
        policy_files=policy_files,
        agents_files=agents_files,
    )


def audit(
    job: Job,
    prev_snapshot: AuditSnapshot,
    home: Optional[Path] = None,
    source_repo_git_config: Optional[Path] = None,
) -> Findings:
    """Execute complete security audit comparing current filesystem state against baseline snapshot.

    Pure function: no threads, no sleeps.
    """
    findings = Findings()
    wt_path = job.worktree_path.resolve() if job.worktree_path else None
    if not wt_path or not wt_path.exists():
        # A missing worktree has nothing left to audit; callers still fail closed
        # separately when the snapshot itself is missing or corrupt.
        return findings

    # 1. Fatal: Worktree .git pointer file
    dot_git = wt_path / ".git"
    if not dot_git.is_file():
        findings.items.append(
            Finding(
                severity=AuditSeverity.FATAL,
                reason="audit_violation",
                path=".git",
                detail="Worktree .git pointer file was modified, deleted, or replaced by directory",
            )
        )
    else:
        try:
            curr_content = dot_git.read_text(encoding="utf-8").strip()
            if curr_content != prev_snapshot.dot_git_content:
                findings.items.append(
                    Finding(
                        severity=AuditSeverity.FATAL,
                        reason="audit_violation",
                        path=".git",
                        detail=f"Worktree .git pointer content modified: {curr_content}",
                    )
                )
        except OSError as e:
            findings.items.append(
                Finding(
                    severity=AuditSeverity.FATAL,
                    reason="audit_violation",
                    path=".git",
                    detail=f"Worktree .git pointer unreadable: {e}",
                )
            )

    # 2. Fatal: Resolved git configuration diff
    curr_config = take_git_config_snapshot(wt_path)
    config_diffs = diff_git_config_snapshots(prev_snapshot.git_config, curr_config)
    if config_diffs:
        findings.items.append(
            Finding(
                severity=AuditSeverity.FATAL,
                reason="audit_violation",
                path=".git/config",
                detail=f"Resolved git config mutated: {config_diffs}",
            )
        )

    # 3. Fatal: .agents/** modifications (hooks, plugins, skills, manifests)
    agents_dir = wt_path / ".agents"
    if agents_dir.is_dir():
        current_agents: dict[str, str] = {}
        for root, _, files in os.walk(agents_dir):
            for f in files:
                fp = Path(root) / f
                rel = str(fp.relative_to(wt_path))
                current_agents[rel] = _hash_or_missing(fp)

        # Check for new or modified files
        for rel_path, h in current_agents.items():
            base_h = prev_snapshot.agents_files.get(rel_path)
            if base_h != h:
                findings.items.append(
                    Finding(
                        severity=AuditSeverity.FATAL,
                        reason="audit_violation",
                        path=rel_path,
                        detail=f"Unauthorized creation or modification under .agents: {rel_path}",
                    )
                )

    # 4. Fatal: Instruction file tampering (AGENTS.md, GEMINI.md)
    for inf in ("AGENTS.md", "GEMINI.md"):
        p = wt_path / inf
        base_h = prev_snapshot.instruction_files.get(inf)
        curr_h = _hash_or_missing(p) if p.exists() else None
        if base_h != curr_h:
            findings.items.append(
                Finding(
                    severity=AuditSeverity.FATAL,
                    reason="instruction_file_modified",
                    path=inf,
                    detail=f"Instruction file modified or created: {inf}",
                )
            )

    # 5. Blocked-not-fatal: Policy file modifications (.gitignore, .gitattributes, .gitmodules)
    for pf in (".gitignore", ".gitattributes", ".gitmodules"):
        p = wt_path / pf
        base_h = prev_snapshot.policy_files.get(pf)
        curr_h = _hash_or_missing(p) if p.exists() else None
        if base_h != curr_h:
            findings.items.append(
                Finding(
                    severity=AuditSeverity.BLOCKED,
                    reason="policy_file_modified",
                    path=pf,
                    detail=f"Policy file modified: {pf}",
                )
            )

    # 6. Host-sensitive path snapshot detector per D52 (strict vs append_tolerant)
    source_config = source_repo_git_config or (job.repo_path / ".git" / "config")
    host_audit = check_host_path_snapshot(
        baseline=prev_snapshot.host_paths,
        home=home,
        source_repo_git_config=source_config,
    )

    resolved_home = (home or Path.home()).resolve()

    def _relativize_host_path(p_str: str) -> str:
        # Never leak an absolute host path into an envelope/audit finding;
        # fall back to the basename if neither root matches (F26).
        try:
            p_obj = Path(p_str).resolve()
            if p_obj.is_relative_to(resolved_home):
                return f"~/{p_obj.relative_to(resolved_home)}"
            if job.repo_path and p_obj.is_relative_to(job.repo_path.resolve()):
                return str(p_obj.relative_to(job.repo_path.resolve()))
            return p_obj.name
        except Exception:
            return Path(p_str).name

    if host_audit.fatal_paths:
        rel_paths = [_relativize_host_path(p) for p in host_audit.fatal_paths]
        findings.items.append(
            Finding(
                severity=AuditSeverity.FATAL,
                reason="host_path_modified",
                path=_relativize_host_path(host_audit.fatal_paths[0]),
                detail=f"Host-sensitive security files modified: {rel_paths}",
            )
        )
    for benign in host_audit.benign_changes:
        rel_p = _relativize_host_path(benign["path"])
        if benign.get("reason") == "backup_file":
            findings.items.append(
                Finding(
                    severity=AuditSeverity.WARN,
                    reason="known_hosts_old_changed",
                    path=rel_p,
                    detail=f"Benign change to backup host path: {rel_p}",
                )
            )
            findings.events.append(
                {
                    "event": "host_path_changed_benign",
                    "detail": benign,
                }
            )
            if "known_hosts_old_changed" not in findings.warnings:
                findings.warnings.append("known_hosts_old_changed")
        else:
            findings.items.append(
                Finding(
                    severity=AuditSeverity.WARN,
                    reason="known_hosts_changed",
                    path=rel_p,
                    detail=f"Benign append to host path: {rel_p}",
                )
            )
            findings.events.append(
                {
                    "event": "host_path_changed_benign",
                    "detail": benign,
                }
            )
            if "known_hosts_changed" not in findings.warnings:
                findings.warnings.append("known_hosts_changed")

    return findings


def run_security_audit(
    worktree_path: Path,
    baseline_config: dict[str, str],
) -> tuple[bool, Optional[str], list[str]]:
    """Legacy helper running full worktree security audit."""
    resolved = Path(worktree_path).resolve(strict=True)
    job = Job(
        job_id="compat_job",
        title="Compat",
        repo_path=resolved,
        branch="b",
        base_commit="HEAD",
        worktree_path=resolved,
    )
    snapshot = AuditSnapshot(
        dot_git_content=(resolved / ".git").read_text(encoding="utf-8").strip()
        if (resolved / ".git").is_file()
        else None,
        git_config=baseline_config,
        host_paths={},
        instruction_files={"AGENTS.md": None, "GEMINI.md": None},
        policy_files={".gitignore": None, ".gitattributes": None, ".gitmodules": None},
        agents_files={},
    )
    res = audit(job, snapshot)
    return res.passed, res.fatal_reason, res.blocked_files
