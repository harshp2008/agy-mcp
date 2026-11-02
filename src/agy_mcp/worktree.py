"""Repository preflight and worktree lifecycle (SPEC sections 8, 9, 18).

Owns path/containment/cleanliness checks, `.agents/**` trust gating, branch
allocation, `git worktree add`, metadata provisioning, safe removal and the
read-only dangerous-allow-rule inspector. It must never run a job in HOME or
the state directory, never accept a linked worktree/submodule as `repo_path`,
and never remove a dirty or unmerged worktree without `force=True`.
"""

import json
import os
import secrets
import shutil
import stat
from pathlib import Path
from typing import Any, Callable, Optional

from agy_mcp.config import AGY_PERMISSION_CONFIG_REL, Config
from agy_mcp.gitops import is_worktree_dirty, run_git
from agy_mcp.jobs import get_base_state_dir, get_job_disk_usage
from agy_mcp.models import Job, JobCleanupResponse
from agy_mcp.scratchpad import seed_scratchpad, write_metadata_file
from agy_mcp.util import derive_branch_slug


class PreflightError(ValueError):
    """Raised when repository preflight validation fails."""

    def __init__(self, message: str, code: str = "invalid_repo_state"):
        super().__init__(message)
        self.code = code


def validate_repo_path(
    repo_path_str: str,
    allowed_roots: Optional[list[str]] = None,
    require_clean: bool = False,
    home: Optional[Path] = None,
    base_state_dir: Optional[Path] = None,
    trust_agents_dir: bool = False,
) -> tuple[Path, bool, str]:
    """Validate repository path, containment, resolvable HEAD, and cleanliness.

    Returns:
        tuple[Path, bool, str]: (resolved_path, source_dirty, base_commit_sha).
    """
    try:
        resolved = Path(repo_path_str).resolve(strict=True)
    except Exception as e:
        raise PreflightError(
            f"Repository path does not exist: {repo_path_str}", code="repo_not_found"
        ) from e

    if not resolved.is_dir():
        raise PreflightError(
            f"Repository path is not a directory: {resolved}", code="not_a_directory"
        )

    resolved_home = (home or Path.home()).resolve()
    resolved_state_dir = get_base_state_dir(base_state_dir).resolve()

    # 1. Prohibited target checks: cannot be HOME itself
    if resolved == resolved_home:
        raise PreflightError("Cannot execute on HOME directory directly", code="home_prohibited")

    # 2. Cannot reside within the state directory
    if resolved == resolved_state_dir or resolved.is_relative_to(resolved_state_dir):
        raise PreflightError(
            f"Cannot execute on state directory: {resolved}", code="state_dir_prohibited"
        )

    # 3. Containment check: allowed_workdir_roots take precedence if non-empty
    # An explicit allowlist overrides the HOME default, so a user can opt into
    # repositories outside HOME without widening the default for everyone.
    if allowed_roots:
        resolved_roots = [Path(r).resolve() for r in allowed_roots if r.strip()]
        is_contained = any(resolved == r or resolved.is_relative_to(r) for r in resolved_roots)
        if not is_contained:
            raise PreflightError(
                f"Path {resolved} is not inside allowed_workdir_roots {allowed_roots}",
                code="path_outside_allowed_roots",
            )
    else:
        # Default containment: must reside inside HOME
        if not (resolved == resolved_home or resolved.is_relative_to(resolved_home)):
            raise PreflightError(
                f"Path {resolved} is not inside HOME ({resolved_home})",
                code="path_outside_home",
            )

    # 4. Must contain a .git DIRECTORY (reject linked worktrees, bare repos, submodule files)
    # A pointer-file .git means we would be operating inside another worktree,
    # which breaks the isolation model and audit assumptions.
    dot_git = resolved / ".git"
    if not dot_git.exists():
        raise PreflightError(
            f"Path does not contain a .git entry: {resolved}", code="not_a_git_repo"
        )

    if dot_git.is_file():
        raise PreflightError(
            f"Linked worktrees or submodules are not permitted as repo_path: {resolved}",
            code="linked_worktree_prohibited",
        )

    if not dot_git.is_dir():
        raise PreflightError(f".git entry is not a directory: {dot_git}", code="invalid_git_dir")

    # 5. Resolvable HEAD commit
    head_res = run_git(["rev-parse", "--verify", "HEAD"], resolved)
    if head_res.returncode != 0:
        raise PreflightError(
            f"Repository has an unborn HEAD or no commits: {resolved}", code="unborn_head"
        )
    base_commit_sha = head_res.stdout.strip()

    # 6. Cleanliness check
    source_dirty = is_worktree_dirty(resolved)
    if source_dirty and require_clean:
        raise PreflightError(
            f"Source repository is dirty and require_clean_source is enabled: {resolved}",
            code="source_dirty",
        )

    # 7. Agents directory preflight (SPEC section 9.8 & F29)
    # .agents/** may contain hooks/plugins that execute with the agent, so a
    # repository that tracks it is refused unless the user opts in.
    if not trust_agents_dir:
        agents_res = run_git(["ls-files", ".agents"], resolved)
        if agents_res.stdout.strip():
            raise PreflightError(
                "Repository tracks .agents/** which is prohibited unless trust_agents_dir=true",
                code="untrusted_agents_dir",
            )

    return resolved, source_dirty, base_commit_sha


def preflight(
    repo_path: str,
    config: Optional[Config] = None,
    home: Optional[Path] = None,
    base_state_dir: Optional[Path] = None,
) -> tuple[Path, bool, str]:
    """Execute full preflight validation per SPEC section 9."""
    cfg = config or Config()
    return validate_repo_path(
        repo_path_str=repo_path,
        allowed_roots=cfg.allowed_workdir_roots,
        require_clean=cfg.require_clean_source,
        home=home,
        base_state_dir=base_state_dir,
        trust_agents_dir=cfg.trust_agents_dir,
    )


def allocate_branch_name(
    repo_path: Path,
    title: str,
    id_generator: Optional[Callable[[], str]] = None,
) -> str:
    """Allocate a non-colliding ``agy/<slug>-<shortid>`` branch name without creating it.

    Kept separate from :func:`create_worktree` so the manager can persist the
    exact name in ``job.json`` *before* returning the agy_start envelope (A3).
    The name used to be guessed at job creation from the job id and then
    silently replaced by a ``secrets.token_hex(4)`` name in the provisioning
    thread, so the start envelope and git disagreed.
    """
    resolved_repo = Path(repo_path).resolve(strict=True)
    slug = derive_branch_slug(title)
    id_gen = id_generator or (lambda: secrets.token_hex(4))
    for _ in range(10):
        candidate = f"agy/{slug}-{id_gen()}"

        # Validate git branch ref format
        ref_check = run_git(["check-ref-format", "--branch", candidate], resolved_repo)
        if ref_check.returncode != 0:
            continue

        # Check for branch collision
        exists_check = run_git(["rev-parse", "--verify", f"refs/heads/{candidate}"], resolved_repo)
        if exists_check.returncode != 0:
            return candidate

    raise RuntimeError("Failed to allocate a non-colliding branch name after 10 attempts")


def create_worktree(
    repo_path: Path,
    job_dir: Path,
    title: str,
    config: Optional[Config] = None,
    timeout_seconds: Optional[int] = None,
    base_state_dir: Optional[Path] = None,
    id_generator: Optional[Callable[[], str]] = None,
    branch: Optional[str] = None,
) -> tuple[Path, str, str]:
    """Create a linked git worktree for the job with collision-resistant branch naming.

    When ``branch`` is given it is used verbatim (the manager already allocated
    and persisted it, A3). A collision at this point fails the job instead of
    silently renaming the branch out from under the start envelope.
    """
    cfg = config or Config()
    resolved_repo = Path(repo_path).resolve(strict=True)

    # 1. Disk check: filesystem hosting state dir must have min_free_gb free
    state_root = get_base_state_dir(base_state_dir).resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    try:
        usage = shutil.disk_usage(state_root)
        free_gb = usage.free / (1024**3)
        if free_gb < cfg.min_free_gb:
            raise PreflightError(
                f"Insufficient disk space on state directory: {free_gb:.2f} GB free, "
                f"{cfg.min_free_gb:.2f} GB required",
                code="insufficient_disk_space",
            )
    except OSError:
        pass

    # 2. Use the pre-allocated name when given, otherwise allocate one now.
    if branch is not None:
        ref_check = run_git(["check-ref-format", "--branch", branch], resolved_repo)
        if ref_check.returncode != 0:
            raise RuntimeError(f"Pre-allocated branch name is not a valid git ref: {branch!r}")
        exists_check = run_git(["rev-parse", "--verify", f"refs/heads/{branch}"], resolved_repo)
        if exists_check.returncode == 0:
            raise RuntimeError(f"Pre-allocated branch '{branch}' already exists")
    else:
        branch = allocate_branch_name(resolved_repo, title, id_generator)

    # 3. Provision worktree
    worktree_path = job_dir / "work"
    timeout = timeout_seconds or cfg.worktree_setup_seconds

    add_res = run_git(
        ["worktree", "add", str(worktree_path), "-b", branch, "HEAD"],
        resolved_repo,
        timeout=float(timeout),
    )

    if add_res.returncode != 0:
        # Failure cleanup: prune worktree and delete the newly created branch
        run_git(["worktree", "prune"], resolved_repo)
        run_git(["branch", "-D", branch], resolved_repo)
        raise RuntimeError(f"git worktree add failed: {add_res.stderr}")

    base_sha_res = run_git(["rev-parse", "HEAD"], worktree_path, check=True)
    base_commit_sha = base_sha_res.stdout.strip()
    return worktree_path, branch, base_commit_sha


def ensure_local_exclude(worktree_path: Path, pattern: str = ".agy/") -> Path:
    """Add ``pattern`` to the repository-local ``info/exclude`` (never a tracked file).

    ``git rev-parse --git-path info/exclude`` resolves to the shared local
    exclude file (``$GIT_COMMON_DIR/info/exclude``), which git reads but never
    commits. This keeps the wrapper's scratchpad out of every commit without
    editing a tracked ``.gitignore`` (SPEC section 9, D62).
    """
    res = run_git(["rev-parse", "--git-path", "info/exclude"], worktree_path)
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError(
            f"Unable to resolve repository-local exclude: {res.stderr.strip() or 'no path'}"
        )
    exclude_path = Path(res.stdout.strip())
    if not exclude_path.is_absolute():
        exclude_path = Path(worktree_path) / exclude_path

    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    try:
        with open(exclude_path, encoding="utf-8", errors="replace") as handle:
            existing = handle.read()
    except FileNotFoundError:
        existing = ""

    if any(line.strip() == pattern for line in existing.splitlines()):
        return exclude_path

    with open(exclude_path, "a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(f"{pattern}\n")
    return exclude_path


def provision_metadata_dir(
    worktree_path: Path,
    title: str,
    checklist: Optional[list[str]] = None,
) -> None:
    """Pre-provision <worktree>/.agy/ with mode 0700 and seed the scratchpad (SPEC §9).

    Seeds PLAN.md (a numbered checkbox list), NOTES.md, LOG.md and REPORT.md and
    adds the directory to the repository-local exclude. Any failure is raised
    so the launch path can fail closed before agy starts.
    """
    resolved_worktree = Path(worktree_path).resolve(strict=True)
    agy_dir = resolved_worktree / ".agy"
    # Refuse to provision through a symlink: an agent-controlled .agy -> ~/.ssh
    # would otherwise let metadata writes land outside the worktree.
    if agy_dir.is_symlink():
        raise RuntimeError("Refusing to provision .agy: it is a symlink")
    agy_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(agy_dir, 0o700)

    seed_scratchpad(agy_dir, title, checklist)

    # Keep the wrapper scratchpad out of every commit using the local exclude.
    ensure_local_exclude(resolved_worktree)

    # Sentinel write verification
    # Writing+deleting a file catches a read-only/mis-mounted .agy before agy is
    # launched, instead of failing later inside the agent turn.
    sentinel_path = agy_dir / f".sentinel_{os.getpid()}_{secrets.token_hex(4)}"
    write_metadata_file(sentinel_path, "verified")
    if not sentinel_path.exists():
        raise RuntimeError("Sentinel write verification failed in .agy directory")
    sentinel_path.unlink()


def provision(
    worktree_path: Path,
    title: str,
    checklist: Optional[list[str]] = None,
) -> None:
    """Alias for provision_metadata_dir."""
    provision_metadata_dir(worktree_path, title, checklist)


def remove_worktree(
    repo_path: Path,
    worktree_path: Path,
    branch: Optional[str] = None,
    force: bool = False,
) -> int:
    """Safely remove a linked worktree, prune metadata, and optionally delete the branch."""
    resolved_repo = Path(repo_path).resolve(strict=True)
    freed_bytes = 0

    if worktree_path.exists():
        freed_bytes = get_job_disk_usage(worktree_path)
        cmd = ["worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(str(worktree_path))
        res = run_git(cmd, resolved_repo)
        if res.returncode != 0 and not force:
            raise RuntimeError(f"Failed to remove worktree: {res.stderr}")

    run_git(["worktree", "prune"], resolved_repo)

    if branch:
        # -d refuses to delete an unmerged branch; only force cleanup uses -D.
        flag = "-D" if force else "-d"
        run_git(["branch", flag, branch], resolved_repo)

    return freed_bytes


def count_unmerged_commits(repo_path: Path, branch: str) -> int:
    """Count commits reachable from ``branch`` but from neither HEAD nor any other local branch.

    Reachability is the correct test, not ``base_commit..<branch>``: a job
    branch is unmerged only when deleting it would make a commit unreachable.
    Reachability is ancestor-closed, so a branch whose tip is reachable from
    HEAD or from another local branch has *all* of its commits reachable and
    is safe to delete. ``git branch -d`` only compares against HEAD (and the
    upstream), which is why it is not used as the gate below.

    Squash and cherry-pick merges create a new commit and leave the original
    commits unreachable; such branches therefore still count as unmerged and
    are refused without ``force=True``.

    An already-missing branch returns 0 (nothing to lose). A failing git query
    raises ``RuntimeError`` so cleanup fails closed instead of deleting blind.
    """
    heads = run_git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], repo_path)
    if heads.returncode != 0:
        raise RuntimeError(
            f"Unable to list local branches in '{repo_path}': {heads.stderr.strip()}"
        )
    local_branches = [line.strip() for line in heads.stdout.splitlines() if line.strip()]
    if branch not in local_branches:
        return 0

    # Every other local branch (minus the job branch itself) is a negative
    # revision: a commit counted by rev-list is one that no surviving ref can
    # reach after the deletion.
    others = [name for name in local_branches if name != branch]
    res = run_git(["rev-list", "--count", branch, "--not", "HEAD", *others], repo_path)
    if res.returncode != 0:
        raise RuntimeError(
            f"Unable to count unmerged commits on branch '{branch}': {res.stderr.strip()}"
        )
    try:
        return int(res.stdout.strip() or 0)
    except ValueError:
        raise RuntimeError(
            f"Unable to count unmerged commits on branch '{branch}': unexpected git output"
        ) from None


def cleanup_worktree(
    job: Job,
    remove_worktree_flag: bool = True,
    delete_branch_flag: bool = False,
    force: bool = False,
    **kwargs: Any,
) -> JobCleanupResponse:
    """Safely cleanup worktree and branch according to tool contracts in SPEC section 8 & 9."""
    if "remove_worktree" in kwargs:
        remove_worktree_flag = bool(kwargs["remove_worktree"])
    if "delete_branch" in kwargs:
        delete_branch_flag = bool(kwargs["delete_branch"])
    if not force:
        # Check dirty worktree
        if job.worktree_path and job.worktree_path.exists():
            if is_worktree_dirty(job.worktree_path):
                raise RuntimeError(
                    f"Refusing to cleanup dirty worktree for job '{job.job_id}' without force=True"
                )

        # Check unmerged commits on branch. "Unmerged" means some commit
        # reachable from the branch is not reachable from HEAD or from any
        # other local branch: a branch merged into an integration branch (or
        # fast-forwarded into the default branch from a detached HEAD) is safe
        # to delete because no commit becomes unreachable.
        if delete_branch_flag and job.repo_path.exists():
            count = count_unmerged_commits(job.repo_path, job.branch)
            if count > 0:
                raise RuntimeError(
                    f"Refusing to delete branch '{job.branch}' with {count} unmerged commits "
                    f"without force=True"
                )

    freed = 0
    worktree_removed = False
    branch_deleted = False

    if remove_worktree_flag and job.worktree_path and job.worktree_path.exists():
        freed = remove_worktree(
            repo_path=job.repo_path,
            worktree_path=job.worktree_path,
            force=force,
        )
        worktree_removed = True

    if delete_branch_flag and job.repo_path.exists():
        # -D is deliberate: the reachability gate above already proved that no
        # commit is lost, and `git branch -d` would still refuse a branch that
        # is merged into another local branch but not into HEAD.
        del_res = run_git(["branch", "-D", job.branch], job.repo_path)
        branch_deleted = del_res.returncode == 0

    return JobCleanupResponse(
        job_id=job.job_id,
        worktree_removed=worktree_removed,
        branch_deleted=branch_deleted,
        freed_bytes=freed,
    )


def is_dangerous_allow_rule(rule: str) -> bool:
    """Check if an allow-rule grants permissions to dangerous tools per F26.

    Dangerous categories:
    - Shells: bash, sh, zsh, ksh, csh, fish, dash
    - Interpreters and tool runners: python, node, ruby, perl, php, bun, deno,
      uv/uvx
    - Test runners: pytest, npm, cargo, go
    - Git modifying/branching commands: git push, git branch
    - File deletion: rm
    - Network retrieval: curl, wget, ssh, scp, sftp
    - Any MCP tool grant
    """
    cleaned = rule.strip().lower()
    if not cleaned:
        return False

    # Any MCP tool
    if "mcp" in cleaned:
        return True

    # Extract inner command if wrapped in command(...) or tool(...)
    cmd = cleaned
    if cleaned.startswith("command(") and cleaned.endswith(")"):
        cmd = cleaned[8:-1].strip()
    elif cleaned.startswith("tool(") and cleaned.endswith(")"):
        cmd = cleaned[5:-1].strip()

    tokens = cmd.split()
    if not tokens:
        return False
    bin_name = Path(tokens[0]).name

    # Shells
    if bin_name in {"bash", "sh", "zsh", "ksh", "csh", "tcsh", "fish", "dash"}:
        return True

    # Interpreters and tool runners: each can execute arbitrary code, so the
    # rule is not a bounded permission. `uv` runs any command (`uv run ...`) and
    # `uvx` any published tool; the tuple prefix also covers uvx. It was listed
    # in the deleted DANGEROUS_RULE_PREFIXES constant but never here, so such a
    # rule used to be reported as safe (audit C1a). Over-blocking only raises a
    # warning (D7), so the safe direction is to flag it.
    if bin_name.startswith(
        ("python", "node", "ruby", "perl", "php", "bun", "deno", "uv")
    ):
        return True

    # Test runners
    if bin_name == "pytest" or bin_name.startswith("pytest"):
        return True
    if bin_name == "npm" and (
        len(tokens) == 1 or any(t in ("test", "run", "exec") for t in tokens[1:])
    ):
        return True
    if bin_name == "cargo" and (len(tokens) == 1 or any(t in ("test", "run") for t in tokens[1:])):
        return True
    if bin_name == "go" and (len(tokens) == 1 or any(t in ("test", "run") for t in tokens[1:])):
        return True

    # Git push, git branch
    if bin_name == "git" and len(tokens) > 1 and tokens[1] in ("push", "branch"):
        return True

    # rm
    if bin_name == "rm":
        return True

    # curl / wget
    if bin_name in {"curl", "wget"}:
        return True

    # ssh / scp / sftp
    if bin_name in {"ssh", "scp", "sftp"}:
        return True

    return False


# Real agy config files are tiny (the observed one has 31 allow rules); a 1 MB
# cap keeps an attacker-controlled or corrupted file from expanding a start.
MAX_PERMISSION_CONFIG_BYTES = 1024 * 1024


def _read_permission_config_file(
    cfile: Path,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Read one permission config no-follow, regular-file-only and size-capped.

    Returns ``(data, warning)``. An absent file is ``(None, None)`` so the next
    candidate can be tried; every unreadable, non-regular, oversize or
    malformed file fails soft to a warning code instead of raising.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(cfile), flags)
    except (FileNotFoundError, NotADirectoryError):
        return None, None
    except OSError:
        # ELOOP (symlink), EACCES and friends must never break job start.
        return None, "permission_rules_unreadable"

    try:
        try:
            file_stat = os.fstat(fd)
        except OSError:
            return None, "permission_rules_unreadable"
        if not stat.S_ISREG(file_stat.st_mode):
            return None, "permission_rules_unreadable"
        if file_stat.st_size > MAX_PERMISSION_CONFIG_BYTES:
            return None, "permission_rules_unreadable"
        try:
            raw = os.read(fd, MAX_PERMISSION_CONFIG_BYTES + 1)
        except OSError:
            return None, "permission_rules_unreadable"
    finally:
        os.close(fd)

    if len(raw) > MAX_PERMISSION_CONFIG_BYTES:
        return None, "permission_rules_unreadable"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "malformed_config_json"
    if not isinstance(data, dict):
        return None, "malformed_config_json"
    return data, None


def inspect_permission_rules(gemini_dir: Optional[Path] = None) -> dict[str, list[str]]:
    """READ-ONLY inspector of configuration for dangerous allow-rules per F26.

    The real agy CLI loads its shared permission config from
    ``config.AGY_PERMISSION_CONFIG_REL`` under HOME (masked smoke evidence), so
    that path is probed first; the older candidates remain as fallbacks. Reads
    are no-follow, regular-file-only and capped, and every failure is a warning
    code, never an exception and never a blocked start.
    """
    target_dir = gemini_dir
    effective_rules: list[str] = []
    dangerous_rules: list[str] = []
    warnings: list[str] = []

    if not target_dir or not target_dir.exists():
        return {
            "permission_rules_effective": [],
            "dangerous_allow_rules": [],
            "warnings": [],
        }

    candidate_files = [
        # The path the real CLI actually loads (finding 1). The literal path
        # lives in config.py so the transcript-reading scope guard stays tight.
        target_dir / AGY_PERMISSION_CONFIG_REL,
        target_dir / "antigravity-cli" / "settings.json",
        target_dir / "settings.json",
        target_dir / "config" / "config.json",
    ]

    # Include project config files: config / projects / *.json
    projects_dir = target_dir / "config" / "projects"
    if projects_dir.is_dir():
        for pfile in projects_dir.glob("*.json"):
            candidate_files.append(pfile)

    for cfile in candidate_files:
        data, read_warning = _read_permission_config_file(cfile)
        if read_warning == "malformed_config_json":
            warnings.append("malformed_config_json")
            warnings.append("permission_rules_unreadable")
            continue
        if read_warning is not None:
            warnings.append(read_warning)
            continue
        if data is None:
            continue

        # Extract rules from permissions.allow or permissions.allowed_commands
        perms = data.get("permissions", {})
        allow_list: list[str] = []
        if isinstance(perms, dict):
            allow_val = perms.get("allow") or perms.get("allowed_commands")
            if isinstance(allow_val, list):
                allow_list.extend(str(item) for item in allow_val)

        # Also check top-level "allow"
        if isinstance(data.get("allow"), list):
            allow_list.extend(str(item) for item in data["allow"])

        for rule in allow_list:
            cleaned_rule = rule.strip()
            if cleaned_rule and cleaned_rule not in effective_rules:
                effective_rules.append(cleaned_rule)

            # Check if rule matches dangerous patterns
            if is_dangerous_allow_rule(cleaned_rule):
                if cleaned_rule not in dangerous_rules:
                    dangerous_rules.append(cleaned_rule)

    if dangerous_rules:
        warnings.append("dangerous_allow_rules")

    return {
        "permission_rules_effective": sorted(effective_rules),
        "dangerous_allow_rules": sorted(dangerous_rules),
        "warnings": sorted(list(set(warnings))),
    }


def find_drifted_worktrees(base_state_dir: Optional[Path] = None) -> list[str]:
    """Identify jobs whose source repository HEAD has drifted from base_commit."""
    from agy_mcp.jobs import list_jobs

    drifted: list[str] = []
    for job in list_jobs(base_state_dir):
        if not job.worktree_path or not job.worktree_path.is_dir():
            continue
        if not job.repo_path.is_dir():
            continue
        try:
            res = run_git(["rev-parse", "HEAD"], job.repo_path, check=False)
            if res.returncode == 0:
                current_head = res.stdout.strip()
                if current_head != job.base_commit:
                    drifted.append(job.job_id)
        except Exception:
            pass
    return drifted
