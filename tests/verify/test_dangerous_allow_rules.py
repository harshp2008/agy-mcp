"""Regression tests for the dangerous allow-rule inspector (F26).

``is_dangerous_allow_rule`` decides whether an ``agy`` permission allow-rule
grants an effectively unbounded capability, and its verdict becomes the
``dangerous_allow_rules`` warning on every job start. The module used to carry a
dead ``DANGEROUS_RULE_PREFIXES`` tuple that had already drifted from this live
logic: the tuple listed ``command(uv`` while the implementation did not, so a
rule such as ``command(uv run bash -c ...)`` was reported as safe (audit C1a).

These tests pin the fix: ``uv`` (and therefore ``uvx``, which shares the prefix)
is dangerous, and every rule the deleted tuple knew about is still detected.
Over-blocking is acceptable here -- the inspector only warns, and a missed
dangerous rule is the failure that matters (D7).
"""

import pytest

from agy_mcp.worktree import is_dangerous_allow_rule

# The entries of the deleted DANGEROUS_RULE_PREFIXES tuple, expanded to complete
# rules. Every one must still be flagged; the tuple is gone, so this list is the
# durable cross-check against the live implementation.
LEGACY_DANGEROUS_RULES = [
    "command(bash -c 'echo hi')",
    "command(sh -c 'echo hi')",
    "command(zsh -c 'echo hi')",
    "command(python -c 'print(1)')",
    "command(node -e 'console.log(1)')",
    'command(uv run bash -c "rm -rf /tmp/x")',
    "command(git push origin main)",
    "command(git branch -D topic)",
    "command(rm -rf /tmp/x)",
    "command(curl https://example.com)",
    "command(wget https://example.com)",
    "command(pytest -q)",
    "command(npm test)",
    "command(cargo test)",
    "command(go test ./...)",
]

UV_RULES = [
    'command(uv run bash -c "rm -rf /tmp/pwned")',
    "command(uv pip install requests)",
    "command(uvx --from requests python -c 'print(1)')",
    "tool(uv run python -c 'print(1)')",
    "command(/usr/local/bin/uv run rm -rf /tmp/pwned)",
    "command(uv)",
]

SAFE_RULES = [
    "command(ls -la)",
    "command(git status)",
    "command(git diff HEAD~1)",
    "read_file(src/main.py)",
]


@pytest.mark.parametrize("rule", LEGACY_DANGEROUS_RULES)
def test_every_legacy_dangerous_rule_is_flagged(rule: str) -> None:
    """The live logic must still cover the drifted constant's entries."""
    assert is_dangerous_allow_rule(rule) is True


@pytest.mark.parametrize("rule", UV_RULES)
def test_uv_allow_rules_are_dangerous(rule: str) -> None:
    """uv/uvx can run an arbitrary command, so allowing them is dangerous."""
    assert is_dangerous_allow_rule(rule) is True


@pytest.mark.parametrize("rule", SAFE_RULES)
def test_ordinary_allow_rules_stay_safe(rule: str) -> None:
    """The fix must not turn benign read-only rules into warnings."""
    assert is_dangerous_allow_rule(rule) is False
