"""Frozen acceptance tests: ``node --test`` verify allowlist grammar.

Contract under test (owner spec, quickwins task 2):
- The default allowlist gains exactly one entry, ``node --test``; the five
  existing entries keep their behavior.
- Accepted: ``node --test`` optionally followed by relative file/dir/glob
  arguments that stay inside the job workdir after symlink resolution.
- Rejected with the existing rejection mechanism (``False`` plus a message that
  lists accepted forms): any node flag other than ``--test`` (``-e``/``--eval``,
  ``-p``/``--print``, ``-r``/``--require``, ``--import``, ``--loader``,
  ``--test-reporter``, ``--test-name-pattern``, unknown flags), absolute paths,
  ``..`` components, symlink escapes, shell metacharacters, empty argv,
  ``node`` alone, and flags smuggled after ``--``.

These tests are frozen after the freeze commit: do not edit them.
"""

from pathlib import Path

import pytest

from agy_mcp.config import Config
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.verify import accepted_verify_forms, is_verify_allowed, verify_rejection_message
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"

ORIGINAL_ALLOWLIST = ["pytest", "ruff", "npm test", "cargo test", "go test"]


@pytest.fixture()
def allowlist() -> list[str]:
    return Config().verify_allowlist


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "tests" / "test_math.mjs").write_text("// fake test\n", encoding="utf-8")
    (root / "tests" / "unit" / "helpers.js").write_text("// helper\n", encoding="utf-8")
    return root


def test_default_allowlist_adds_node_test_and_keeps_existing_entries(
    allowlist: list[str],
) -> None:
    assert allowlist == ORIGINAL_ALLOWLIST + ["node --test"]
    assert "node --test" in accepted_verify_forms(allowlist)


@pytest.mark.parametrize(
    "command",
    [
        "node --test",
        "node --test tests/test_math.mjs",
        "node --test tests/",
        "node --test tests/unit",
        "node --test tests/**/*.test.mjs",
        "node --test tests/*.test.mjs tests/unit/",
        "node --test .",
    ],
)
def test_node_test_accepted_forms(
    command: str, allowlist: list[str], worktree: Path
) -> None:
    assert is_verify_allowed(command, allowlist, worktree_path=worktree) is True


@pytest.mark.parametrize(
    "command",
    [
        "",
        "   ",
        "node",
        "node test",
        "node --testfile",
        "node --test=1",
        "/usr/bin/node --test",
        "node -e 'console.log(1)'",
        "node --eval 'console.log(1)'",
        "node -p '1'",
        "node --print '1'",
        "node -r ./preload.js",
        "node --require ./preload.js",
        "node --import ./preload.mjs",
        "node --loader ./loader.mjs",
        "node --test -e 'console.log(1)'",
        "node --test --eval 'console.log(1)'",
        "node --test --print '1'",
        "node --test --require ./preload.js",
        "node --test --import ./preload.mjs",
        "node --test --loader ./loader.mjs",
        "node --test --test-reporter=spec",
        "node --test --test-name-pattern=math",
        "node --test --watch",
        "node --test /etc/passwd",
        "node --test ../outside.js",
        "node --test tests/../../outside.js",
        "node --test tests/a.js; rm -rf /",
        "node --test 'tests/a.js && node -e 1'",
        "node --test 'tests/$(id).js'",
        "node --test 'tests/a|b.js'",
        "node --test 'tests/a>b.js'",
        "node --test 'tests/a<b.js'",
        "node --test 'tests/a`id`.js'",
        "node --test ''",
        "node --test -- --eval 'console.log(1)'",
        "node --test tests/test_math.mjs --test",
    ],
)
def test_node_test_rejections(
    command: str, allowlist: list[str], worktree: Path
) -> None:
    assert is_verify_allowed(command, allowlist, worktree_path=worktree) is False


def test_node_test_rejects_symlink_escape(
    tmp_path: Path, allowlist: list[str]
) -> None:
    worktree = tmp_path / "work"
    worktree.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escape.mjs").write_text("// outside\n", encoding="utf-8")
    (worktree / "link").symlink_to(outside, target_is_directory=True)

    assert (
        is_verify_allowed(
            "node --test link/escape.mjs", allowlist, worktree_path=worktree
        )
        is False
    )


def test_node_test_requires_the_allowlist_entry(
    allowlist: list[str], worktree: Path
) -> None:
    assert is_verify_allowed("node --test", ["pytest"], worktree_path=worktree) is False
    assert (
        is_verify_allowed(
            "node --test tests/test_math.mjs", ["pytest"], worktree_path=worktree
        )
        is False
    )
    # The configured entry is what enables the tool.
    assert (
        is_verify_allowed(
            "node --test tests/test_math.mjs",
            ["node --test"],
            worktree_path=worktree,
        )
        is True
    )


def test_existing_allowlist_entries_unchanged(
    allowlist: list[str], worktree: Path
) -> None:
    assert is_verify_allowed("pytest", allowlist, worktree_path=worktree)
    assert is_verify_allowed("pytest -v tests/test_core.py", allowlist, worktree_path=worktree)
    assert is_verify_allowed("python3 -m pytest -q", allowlist, worktree_path=worktree)
    assert not is_verify_allowed("pytest -c /tmp/evil.ini", allowlist, worktree_path=worktree)
    assert is_verify_allowed("ruff check .", allowlist, worktree_path=worktree)
    assert not is_verify_allowed(
        "ruff check --config /tmp/evil.toml", allowlist, worktree_path=worktree
    )
    assert is_verify_allowed("npm test", allowlist, worktree_path=worktree)
    assert not is_verify_allowed("npm test -- --watch", allowlist, worktree_path=worktree)
    assert is_verify_allowed("cargo test --release", allowlist, worktree_path=worktree)
    assert is_verify_allowed("go test ./...", allowlist, worktree_path=worktree)
    assert not is_verify_allowed(
        "go test -exec ./evil ./...", allowlist, worktree_path=worktree
    )


def test_rejection_message_names_the_node_test_form(allowlist: list[str]) -> None:
    message = verify_rejection_message("node -e 'console.log(1)'", allowlist)
    assert "node --test" in message


def test_manager_rejects_node_eval_verify_with_existing_error(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    make_repo(repo, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(
        config=Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )

    with pytest.raises(ManagerError) as exc_info:
        manager.start(
            prompt="Task",
            repo_path=str(repo),
            title="Node Eval Test",
            verify="node -e 'console.log(1)'",
        )

    assert exc_info.value.code == "verify_not_allowed"
    assert "node --test" in str(exc_info.value)
