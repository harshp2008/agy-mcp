"""Stable account error codes and the account-specific exception.

Owns :class:`AccountError`, the failure type raised by the registry, identity
verification and later account scheduling/transfer code. Every instance carries a
stable ``code`` attribute. It must never place an unmasked email, token or
credential path in its message: the message is user-visible and may reach an MCP
envelope or a log.
"""


class AccountError(ValueError):
    """A multi-account failure with a stable machine-readable ``code``.

    ``message`` must already be safe to show: masked email only, no token or
    credential material. ``code`` is the stable value callers and tests depend
    on; adding a code is a documented change, renaming one is not allowed.
    """

    def __init__(self, message: str, code: str = "account_error") -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
