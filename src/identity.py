"""Who is calling, for the record.

This does not restrict anything, and deliberately so.

QuickBooks issues one OAuth token per app per company. Only an admin can
authorize it, and a second admin connecting disconnects the first. There is no
per-user token and no per-user enforcement: every call this server makes
carries the same credential with the same company-wide access, whoever asked.

A gate in this server could refuse some callers some tools, but it would not be
QuickBooks permissions and should not be mistaken for them. Anyone who can
reach this server has whatever the token has. If two people need different
access, they need different deployments -- separate Intuit apps, separate
tokens, separate ``QBO_READ_ONLY`` and ``--groups``. That is how the API is
built to work.

What identity is good for is the record: knowing who asked for a change is
worth having even when everyone who can ask could have asked for anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from fastmcp.server.dependencies import get_access_token

__all__ = ["Identity", "current_identity", "LOCAL_OPERATOR"]

#: Claim names carrying an email address, in the order they are tried.
#: Providers disagree: some issue "email", others only "preferred_username".
_EMAIL_CLAIMS: tuple[str, ...] = ("email", "preferred_username", "upn", "sub")


@dataclass(frozen=True)
class Identity:
    """The caller, as the verified token describes them."""

    subject: str
    email: str = ""
    authenticated: bool = True

    @property
    def label(self) -> str:
        """A short description for logs."""
        return self.email or self.subject or "unidentified caller"


#: The caller when no authentication is configured.
LOCAL_OPERATOR = Identity(subject="local", email="", authenticated=False)


def _first_claim(claims: dict[str, Any], names: Sequence[str]) -> str:
    for name in names:
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def current_identity() -> Identity:
    """The caller for the request in flight.

    Returns:
        The verified identity, or :data:`LOCAL_OPERATOR` when the server runs
        without authentication.
    """
    token = get_access_token()
    if token is None:
        return LOCAL_OPERATOR
    claims: dict[str, Any] = dict(token.claims or {})
    return Identity(
        subject=token.subject or token.client_id or "unknown",
        email=_first_claim(claims, _EMAIL_CLAIMS),
        authenticated=True,
    )
