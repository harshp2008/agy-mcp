"""mask_email and identity verification (D103)."""

import hashlib

import pytest

from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.identity import email_sha256, mask_email, verify_identity

FULL_EMAIL = "harold.example.08@example.com"


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("", "***"),
        ("no-at-sign", "***"),
        ("@example.com", "***"),
        ("a@example.com", "***@example.com"),
        ("ab@example.com", "***@example.com"),
        ("abc@example.com", "a***@example.com"),
        ("abcd@example.com", "ab***@example.com"),
        ("abcde@example.com", "ab***@example.com"),
        ("abcdef@example.com", "abc***@example.com"),
        ("abcdefg@example.com", "abc***@example.com"),
        ("abcdefgh@example.com", "abch***@example.com"),
        ("abcdefghi@example.com", "abci***@example.com"),
        ("harold.example.08@example.com", "har08***@example.com"),
    ],
)
def test_mask_email_table(email: str, expected: str) -> None:
    assert mask_email(email) == expected


def test_mask_never_reveals_more_than_half() -> None:
    for length in range(1, 40):
        local = "a" * length
        masked = mask_email(f"{local}@example.com")
        visible_local = masked.split("***", 1)[0]
        assert len(visible_local) <= length // 2


def test_mask_accepts_custom_head_and_tail() -> None:
    assert mask_email("abcdefghij@example.com", head=1, tail=1) == "aj***@example.com"
    assert mask_email("abcdefghij@example.com", head=0, tail=0) == "***@example.com"


def test_email_sha256_is_normalized() -> None:
    expected = hashlib.sha256(FULL_EMAIL.casefold().encode("utf-8")).hexdigest()
    assert email_sha256(FULL_EMAIL) == expected
    assert email_sha256("  " + FULL_EMAIL.upper() + " ") == expected


def test_no_expectation_is_not_verified_but_not_an_error() -> None:
    check = verify_identity(FULL_EMAIL)
    assert check.verified is False
    assert check.reason == "no_expectation"
    assert check.masked_email == mask_email(FULL_EMAIL)


def test_expected_email_matches_case_insensitively() -> None:
    check = verify_identity(FULL_EMAIL, expected_email=FULL_EMAIL.upper())
    assert check.verified is True
    assert check.reason == "matched"


def test_expected_hash_matches() -> None:
    check = verify_identity(FULL_EMAIL, expected_email_sha256=email_sha256(FULL_EMAIL))
    assert check.verified is True


def test_mismatch_error_carries_only_the_masked_value() -> None:
    with pytest.raises(AccountError) as exc:
        verify_identity(FULL_EMAIL, expected_email="someone.else@example.com")
    assert exc.value.code == "account_identity_mismatch"
    assert FULL_EMAIL not in exc.value.message
    assert mask_email(FULL_EMAIL) in exc.value.message


def test_hash_mismatch_error_carries_only_the_masked_value() -> None:
    with pytest.raises(AccountError) as exc:
        verify_identity(FULL_EMAIL, expected_email_sha256="0" * 64)
    assert exc.value.code == "account_identity_mismatch"
    assert FULL_EMAIL not in exc.value.message


def test_missing_observation_with_expectation_is_unknown() -> None:
    with pytest.raises(AccountError) as exc:
        verify_identity(None, expected_email=FULL_EMAIL)
    assert exc.value.code == "account_identity_unknown"


def test_missing_observation_without_expectation_is_fine() -> None:
    check = verify_identity(None)
    assert check.verified is False
    assert check.masked_email is None


def test_two_expectations_are_rejected() -> None:
    with pytest.raises(AccountError) as exc:
        verify_identity(
            FULL_EMAIL,
            expected_email=FULL_EMAIL,
            expected_email_sha256=email_sha256(FULL_EMAIL),
        )
    assert exc.value.code == "account_config_invalid"
