"""Smoke-fix item 3: accept the canonical ``python -m pytest`` verify form.

Finding 3 in the masked smoke report: ``verify.is_verify_allowed`` rejected
``python -m pytest -q`` while accepting ``pytest -q``. The interpreter form must
be accepted for the same pytest tool and under the exact same flag policy, with
no other interpreter shape (absolute paths, .venv paths, -c, -mpytest) allowed.
"""

from agy_mcp.verify import is_verify_allowed, verify_rejection_message

DEFAULT_ALLOWLIST = ["pytest", "ruff", "npm test", "cargo test", "go test"]


def test_accepts_python_m_pytest_under_pytest_policy() -> None:
    """Both bare interpreter forms are accepted when pytest is allowlisted."""
    assert is_verify_allowed("python -m pytest", DEFAULT_ALLOWLIST)
    assert is_verify_allowed("python3 -m pytest", DEFAULT_ALLOWLIST)
    assert is_verify_allowed("python -m pytest -q", DEFAULT_ALLOWLIST)
    assert is_verify_allowed("python3 -m pytest tests/test_core.py -v", DEFAULT_ALLOWLIST)
    assert is_verify_allowed("python -m pytest --maxfail=1", DEFAULT_ALLOWLIST)


def test_rejects_other_interpreter_forms() -> None:
    """No other -m module, -c, pip, absolute path, .venv path or glued -mpytest."""
    assert not is_verify_allowed("python -m http.server", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("python -c 'import os; os.system(\"ls\")'", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("python -m pip install requests", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("/usr/bin/python -m pytest", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("/home/user/.venv/bin/python -m pytest", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed(".venv/bin/python -m pytest", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("python -mpytest", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("python3 -m pytestx", DEFAULT_ALLOWLIST)
    # Only the exact token pair python/python3 + -m + pytest is special-cased.
    assert not is_verify_allowed("python2 -m pytest", DEFAULT_ALLOWLIST)


def test_pytest_flag_policy_still_applies_to_interpreter_form() -> None:
    """Dangerous pytest flags and escaping paths are still rejected."""
    assert not is_verify_allowed("python -m pytest -c pytest.ini", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("python -m pytest -p no:cacheprovider", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("python -m pytest --rootdir=/tmp", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("python -m pytest /etc/passwd", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("python3 -m pytest ../outside", DEFAULT_ALLOWLIST)


def test_interpreter_form_requires_pytest_in_allowlist() -> None:
    """Without a pytest entry the interpreter form is not implicitly allowed."""
    assert not is_verify_allowed("python -m pytest", ["ruff", "npm test"])
    assert is_verify_allowed("python -m pytest", ["python -m pytest"])


def test_rejection_message_lists_accepted_forms() -> None:
    """A rejection explains the accepted forms, including the interpreter form."""
    message = verify_rejection_message("python -m http.server", DEFAULT_ALLOWLIST)
    assert "Accepted forms" in message
    assert "pytest" in message
    assert "python -m pytest" in message
    assert "python3 -m pytest" in message
    assert "python -m http.server" in message
