"""mask_email and identity verification (D103)."""

import hashlib

import pytest

from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.identity import email_sha256, mask_email, verify_identity

FULL_EMAIL = "harold.example.08@example.com"
PLANTED_EMAIL = "planted.person@example.com"
# Invented address used for the reported masking-shape case. Never put a real
# address in tests, docs or fixtures.
REPORTED_LOCAL = "jordan.lee81"
REPORTED_DOMAIN = "example.com"
REPORTED_EMAIL = f"{REPORTED_LOCAL}@{REPORTED_DOMAIN}"
REPORTED_MASKED = f"jor***81@{REPORTED_DOMAIN}"


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
        ("abcdefgh@example.com", "abc***h@example.com"),
        ("abcdefghi@example.com", "abc***i@example.com"),
        ("harold.example.08@example.com", "har***08@example.com"),
        (REPORTED_EMAIL, REPORTED_MASKED),
        ("JORDAN.LEE81@EXAMPLE.COM", "JOR***81@EXAMPLE.COM"),
        ("jordan81+tag@example.com", "jor***ag@example.com"),
        (PLANTED_EMAIL, "pla***on@example.com"),
    ],
)
def test_mask_email_table(email: str, expected: str) -> None:
    assert mask_email(email) == expected


def test_mask_keeps_the_shape_isolated_head_and_tail() -> None:
    for length in range(1, 40):
        local = "a" * length
        masked = mask_email(f"{local}@example.com")
        head, separator, rest = masked.partition("***")
        tail, at_sign, domain = rest.partition("@")
        assert separator == "***"
        assert at_sign == "@"
        assert domain == "example.com"
        assert head == local[: len(head)]
        assert not tail or tail == local[-len(tail) :]
        assert len(head) + len(tail) <= length // 2


def test_planted_address_is_never_returned_in_full() -> None:
    masked = mask_email(PLANTED_EMAIL)
    assert PLANTED_EMAIL not in masked
    assert masked.endswith("@example.com")


def test_mask_accepts_custom_head_and_tail() -> None:
    assert mask_email("abcdefghij@example.com", head=1, tail=1) == "a***j@example.com"
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
