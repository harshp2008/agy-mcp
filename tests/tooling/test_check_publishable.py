"""Tests for the publish-hygiene gate and its allowlist."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_publishable.py"


def _repo(tmp_path: Path, files: dict[str, str | bytes], allowlist: str = "# test\n") -> Path:
    """Create a temporary git repository with the given files, staged."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    allow_path = root / "scripts" / "publishable-allowlist.txt"
    allow_path.parent.mkdir(parents=True, exist_ok=True)
    allow_path.write_text(allowlist, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _bad_email() -> str:
    # Built at run time so this test file never contains a publishable email.
    return "mallory" + "@" + "evil.example"


def _bad_home() -> str:
    # Built at run time so this test file never contains a bad home path.
    return "/" + "home" + "/" + "mallory" + "/project"


def test_real_tree_passes() -> None:
    """The checked-in tree is clean."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "publish hygiene: OK" in result.stdout


def test_injected_email_and_home_path_are_reported(tmp_path: Path) -> None:
    """One bad email and one bad home path each fail the gate with a line."""
    root = _repo(
        tmp_path,
        {"notes.txt": f"contact {_bad_email()}\nhome {_bad_home()}\n"},
    )

    result = _run(root)

    assert result.returncode == 1
    assert "notes.txt:1: email:" in result.stdout
    assert "evil.example" in result.stdout
    assert "notes.txt:2: home-path:" in result.stdout
    assert "/" + "home" + "/" + "mallory" in result.stdout
    assert "publish hygiene: FAIL" in result.stdout


def test_tailscale_hostname_fails(tmp_path: Path) -> None:
    """A .ts.net hostname is always a finding."""
    host = "builder" + ".ts" + ".net"
    root = _repo(tmp_path, {"hosts.txt": f"ssh {host}\n"})

    result = _run(root)

    assert result.returncode == 1
    assert "tailscale-hostname" in result.stdout
    assert host in result.stdout


def test_credential_shapes_fail(tmp_path: Path) -> None:
    """Each supported credential shape is reported and the value is not printed."""
    fake_google = "AIza" + "a" * 35
    fake_openai = "sk-" + "b" * 24
    fake_github = "ghp_" + "c" * 36
    fake_aws = "AKIA" + "D" * 16
    fake_pem = (
        "-----BEGIN RSA "
        + "PRIVATE KEY-----\n"
        + "MIIB\n"
        + "-----END RSA "
        + "PRIVATE KEY-----\n"
    )
    root = _repo(
        tmp_path,
        {
            "keys.txt": "\n".join([fake_google, fake_openai, fake_github, fake_aws, fake_pem]),
        },
    )

    result = _run(root)

    assert result.returncode == 1
    assert result.stdout.count("credential:") == 5
    assert "google-api-key" in result.stdout
    assert "openai-style-key" in result.stdout
    assert "github-token" in result.stdout
    assert "aws-access-key-id" in result.stdout
    assert "private-key-block" in result.stdout
    assert fake_google not in result.stdout
    assert fake_openai not in result.stdout


def test_credential_allowlist_exempts_a_named_fixture(tmp_path: Path) -> None:
    """A justified credential entry exempts that file only."""
    fake_google = "AIza" + "a" * 35
    allowlist = (
        "# test allowlist\n"
        "credential:fixtures/fake_redaction.py  # fake token for the redaction test\n"
    )
    root = _repo(tmp_path, {"fixtures/fake_redaction.py": fake_google + "\n"}, allowlist)

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr


def test_allowlisted_placeholders_pass(tmp_path: Path) -> None:
    """The documented placeholder domains, names and email domains pass."""
    text = "\n".join(
        [
            "user@example.com",
            "author@example.org",
            "a@b.com",
            "x@test.local",
            "y@local.invalid",
            "z@users.noreply.github.com",
            "/" + "home" + "/user/repo",
            "/" + "home" + "/repo",
            "/" + "home" + "/other",
            "/" + "home" + "/smoke-user",
            "/" + "home" + "/harmful",
        ]
    )
    root = _repo(tmp_path, {"ok.txt": text + "\n"})

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr


def test_email_domain_allowlist_accepts_subdomains(tmp_path: Path) -> None:
    """An email-domain entry matches the domain and its subdomains."""
    allowlist = "# test allowlist\nemail-domain:corp.example  # internal test domain\n"
    domain = "corp.example"
    mail = "x@" + domain + "\ny@" + "sub." + domain + "\n"
    root = _repo(tmp_path, {"mail.txt": mail}, allowlist)

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr


def test_unlisted_email_domain_still_fails(tmp_path: Path) -> None:
    """An allowlist entry does not widen unrelated domains."""
    allowlist = "# test allowlist\nemail-domain:corp.example  # internal test domain\n"
    root = _repo(tmp_path, {"mail.txt": "x@" + "other.example" + "\n"}, allowlist)

    result = _run(root)

    assert result.returncode == 1
    assert "other.example" in result.stdout


def test_allowlist_entry_without_justification_is_an_error(tmp_path: Path) -> None:
    """A silent bypass is rejected: every entry needs a reason."""
    root = _repo(tmp_path, {"ok.txt": "nothing\n"}, "credential:fixtures/fake.py\n")

    result = _run(root)

    assert result.returncode == 2
    assert "justification" in result.stderr


def test_unknown_allowlist_kind_is_an_error(tmp_path: Path) -> None:
    """A typo in the allowlist fails closed."""
    root = _repo(tmp_path, {"ok.txt": "nothing\n"}, "nonsense:value  # reason\n")

    result = _run(root)

    assert result.returncode == 2
    assert "unknown allowlist kind" in result.stderr


def test_binary_files_are_skipped(tmp_path: Path) -> None:
    """NUL-containing files are not scanned as text."""
    blob = (_bad_email().encode("utf-8") + b"\0binary")
    root = _repo(tmp_path, {"blob.bin": blob})

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr
