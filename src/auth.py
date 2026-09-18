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
                   Keycloak, Entra. The server becomes the authorization
                   surface clients register against.
"""

from __future__ import annotations

import os
from typing import Any, Final

from fastmcp.server.auth import AuthProvider, OIDCProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier

from src.settings import MissingConfiguration, Settings

__all__ = ["build_auth", "ScopedOIDCProxy"]

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
    """An OIDC proxy that adds scopes to the authorization request only."""

    def __init__(self, *args: Any, extra_scopes: tuple[str, ...] = (), **kwargs: Any):
        self._extra_scopes = extra_scopes
        super().__init__(*args, **kwargs)

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
