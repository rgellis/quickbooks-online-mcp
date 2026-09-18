"""Inbound authentication: proving who is calling.

This establishes *who is calling*, and nothing more. It does not decide what
they may do, because this server cannot: QuickBooks issues one token per app
per company, so every call carries the same company-wide access whoever made
it. Authenticating a caller narrows who can reach the server; it does not
narrow what reaching it lets them do.

Configured entirely by environment variable, so a deployment chooses its
identity provider without changing code:

``MCP_AUTH=none``  no authentication. The default, and correct for stdio, where
                   reaching the process means already holding the machine.
``MCP_AUTH=jwt``   verify bearer tokens against a JWKS endpoint. Works with any
                   issuer that publishes one.
``MCP_AUTH=oidc``  full OAuth against an OIDC provider -- Okta, Auth0, WorkOS,
                   Keycloak, Entra, or Intuit itself. The server becomes the
                   authorization surface clients register against.

Signing in is not the same as being allowed in. An organisational provider like
Okta answers both at once: membership of the app is the permission. A consumer
provider does not -- anyone with an Intuit account can complete a Sign in with
Intuit flow, and Intuit exposes no way to ask whether they have anything to do
with the company whose books this server reads.

``MCP_OIDC_ALLOWED_SUBJECTS`` is the answer to that, and it is required in
practice with Intuit. Key it on the provider's subject identifier rather than
an email address: emails change hands and can be unverified, while a subject is
opaque, stable and issued by the provider.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Final, Iterable

from fastmcp.server.auth import AuthProvider, OIDCProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier

from src.settings import MissingConfiguration, Settings

if TYPE_CHECKING:
    from fastmcp.server.auth import AccessToken

__all__ = [
    "build_auth",
    "ScopedOIDCProxy",
    "allow_list_configured",
    "normalise_emails",
]

logger = logging.getLogger(__name__)

#: Scopes appended to the upstream authorization request but never required of
#: an issued token.
#:
#: Okta issues a refresh token only when the authorization asks for
#: ``offline_access``; without one the client's access token simply expires and
#: the connection drops. It cannot go in ``required_scopes``, because that list
#: is the floor enforced on every request -- if the provider declines to grant
#: the scope, requiring it would reject every subsequent call.
_DEFAULT_EXTRA_SCOPES: Final[tuple[str, ...]] = ()


class ScopedOIDCProxy(OIDCProxy):
    """An OIDC proxy that adds authorization scopes, and decides who may in.

    The second job exists because the identity provider will not do it. With
    Intuit in particular, signing in proves someone holds an Intuit account and
    nothing more -- not that they have any connection to the company whose
    books this server reads. Intuit's own single sign-on documentation says as
    much: the app maps Intuit identities to its own users, because there is no
    endpoint that answers "may this person see company X".

    So the allow-list is the app's user list. Without one, anyone with an Intuit
    account can sign in and read the ledger.
    """

    def __init__(
        self,
        *args: Any,
        extra_scopes: tuple[str, ...] = (),
        allowed_subjects: frozenset[str] = frozenset(),
        allowed_emails: frozenset[str] = frozenset(),
        **kwargs: Any,
    ):
        self._extra_scopes = extra_scopes
        self._allowed_subjects = allowed_subjects
        self._allowed_emails = normalise_emails(allowed_emails)
        super().__init__(*args, **kwargs)

    async def verify_token(self, token: str) -> "AccessToken | None":
        """Verify the token, then decide whether this caller is permitted.

        Returning None refuses at the authentication boundary, so an
        unpermitted caller never reaches a tool.
        """
        verified = await super().verify_token(token)
        if verified is None:
            return None
        if not (self._allowed_subjects or self._allowed_emails):
            # Nothing configured: anyone the provider authenticates gets in.
            # main() warns about this at startup rather than here, where it
            # would print on every request.
            return verified

        claims: dict[str, Any] = dict(verified.claims or {})
        subject = str(verified.subject or claims.get("sub") or "")
        if subject and subject in self._allowed_subjects:
            return verified

        email = str(claims.get("email") or "").strip().lower()
        if email and email in self._allowed_emails:
            # Intuit requires that an email be verified before it is trusted to
            # identify anyone, because an unverified one may belong to somebody
            # else. Subjects are immune to this -- they are opaque and issued by
            # the provider -- which is why they are the better key.
            verified_flag = claims.get("email_verified", claims.get("emailVerified"))
            if verified_flag is False:
                logger.warning(
                    "refused sign-in: %s is allow-listed but the provider "
                    "reports the address unverified",
                    email,
                )
                return None
            return verified

        logger.warning(
            "refused sign-in: subject %r is not in the allow-list",
            subject or "(none)",
        )
        return None

    def _build_upstream_authorize_url(
        self, txn_id: str, transaction: dict[str, Any]
    ) -> str:
        if not self._extra_scopes:
            return super()._build_upstream_authorize_url(txn_id, transaction)
        scopes: list[str] = list(
            transaction.get("scopes") or self.required_scopes or []
        )
        for scope in self._extra_scopes:
            if scope not in scopes:
                scopes.append(scope)
        return super()._build_upstream_authorize_url(
            txn_id, {**transaction, "scopes": scopes}
        )


def normalise_emails(emails: Iterable[str]) -> frozenset[str]:
    """Fold configured addresses for comparison.

    Whoever writes the list types an address the way a human writes one, and
    the provider reports it however it has it stored. Both sides are folded so
    a capital letter is not the reason someone cannot sign in.
    """
    return frozenset(e.strip().lower() for e in emails if e.strip())


def allow_list_configured() -> bool:
    """Whether anyone has said which callers are permitted."""
    return bool(_csv("MCP_OIDC_ALLOWED_SUBJECTS") or _csv("MCP_OIDC_ALLOWED_EMAILS"))


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingConfiguration(
            f"{name} is required when MCP_AUTH={os.environ.get('MCP_AUTH', '')!r}"
        )
    return value


def _csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def build_auth(settings: Settings) -> AuthProvider | None:
    """Construct the authentication provider this deployment asked for.

    Returns:
        A provider, or None when authentication is disabled.

    Raises:
        MissingConfiguration: when a provider is selected without the settings
            it needs. Failing here is deliberate: a server that silently starts
            unauthenticated because a variable was misspelled is worse than one
            that refuses to start.
    """
    kind = settings.auth
    if kind in {"", "none", "off", "disabled"}:
        return None

    if kind == "jwt":
        return JWTVerifier(
            jwks_uri=_require("MCP_JWT_JWKS_URI"),
            issuer=os.environ.get("MCP_JWT_ISSUER") or None,
            audience=os.environ.get("MCP_JWT_AUDIENCE") or None,
            required_scopes=list(_csv("MCP_JWT_REQUIRED_SCOPES")) or None,
        )

    if kind == "oidc":
        return ScopedOIDCProxy(
            config_url=_require("MCP_OIDC_CONFIG_URL"),
            client_id=_require("MCP_OIDC_CLIENT_ID"),
            client_secret=_require("MCP_OIDC_CLIENT_SECRET"),
            base_url=_require("MCP_BASE_URL"),
            required_scopes=list(
                _csv("MCP_OIDC_SCOPES", ("openid", "email", "profile"))
            ),
            extra_scopes=_csv("MCP_OIDC_EXTRA_SCOPES", _DEFAULT_EXTRA_SCOPES),
            allowed_subjects=frozenset(_csv("MCP_OIDC_ALLOWED_SUBJECTS")),
            allowed_emails=frozenset(_csv("MCP_OIDC_ALLOWED_EMAILS")),
            # Some providers' org-level authorization servers issue access
            # tokens whose audience is the org rather than this client, and
            # document that third parties must not validate them. The ID token
            # is the one whose audience is the client, so verify that instead.
            verify_id_token=os.environ.get("MCP_OIDC_VERIFY_ID_TOKEN", "true").lower()
            in {"1", "true", "yes", "on"},
            jwt_signing_key=os.environ.get("MCP_JWT_SIGNING_KEY") or None,
            allowed_client_redirect_uris=list(
                _csv(
                    "MCP_ALLOWED_CLIENT_REDIRECT_URIS",
                    ("https://claude.ai/*", "http://localhost:*"),
                )
            ),
        )

    raise MissingConfiguration(
        f"MCP_AUTH={kind!r} is not supported. Use 'none', 'jwt' or 'oidc' -- "
        "'oidc' covers any OpenID Connect provider."
    )
