"""Server construction, group selection, and the auth wiring."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastmcp.server.auth import OIDCProxy

import main
from main import TOOL_GROUPS, build_server
from src.auth import ScopedOIDCProxy, build_auth
from src.settings import MissingConfiguration
from tests.conftest import make_settings


def names(groups: list[str] | None = None) -> set[str]:
    return {t.name for t in asyncio.run(build_server(groups).list_tools())}


class TestGroups:
    def test_default_registers_everything(self) -> None:
        assert names() == names(["all"])

    def test_a_single_group(self) -> None:
        assert names(["core"]) < names(["all"])

    def test_several_groups_combine(self) -> None:
        combined = names(["core", "reports"])
        assert names(["core"]) < combined
        assert "get_report" in combined

    def test_an_unknown_group_is_refused_by_name(self) -> None:
        with pytest.raises(SystemExit, match="payroll"):
            build_server(["payroll"])

    def test_the_refusal_lists_what_is_available(self) -> None:
        with pytest.raises(SystemExit) as exc:
            build_server(["nope"])
        for group in TOOL_GROUPS:
            assert group in str(exc.value)

    def test_all_alongside_a_name_still_means_all(self) -> None:
        assert names(["all", "core"]) == names(["all"])


class TestCli:
    def test_list_tools_prints_and_exits(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main.main(["--list-tools"]) == 0
        printed = capsys.readouterr().out
        assert "query_quickbooks" in printed
        assert "create_journal_entry" in printed

    def test_list_tools_honours_group_selection(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main.main(["--groups", "core", "--list-tools"])
        printed = capsys.readouterr().out
        assert "query_quickbooks" in printed
        assert "create_journal_entry" not in printed

    def test_listing_needs_no_credentials(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """So a misconfigured deployment can still be inspected."""
        for name in ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REALM_ID"):
            monkeypatch.delenv(name, raising=False)
        assert main.main(["--list-tools"]) == 0


class TestAuthConstruction:
    def test_none_means_no_provider(self) -> None:
        assert build_auth(make_settings(auth="none")) is None

    def test_an_unknown_provider_is_refused(self) -> None:
        with pytest.raises(MissingConfiguration, match="not supported"):
            build_auth(make_settings(auth="carrier-pigeon"))

    def test_jwt_requires_a_jwks_uri(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_JWT_JWKS_URI", raising=False)
        with pytest.raises(MissingConfiguration, match="MCP_JWT_JWKS_URI"):
            build_auth(make_settings(auth="jwt"))

    def test_jwt_provider_is_built(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "MCP_JWT_JWKS_URI", "https://issuer.example/.well-known/jwks"
        )
        monkeypatch.setenv("MCP_JWT_ISSUER", "https://issuer.example")
        provider = build_auth(make_settings(auth="jwt"))
        assert provider is not None

    def test_oidc_requires_its_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in (
            "MCP_OIDC_CONFIG_URL",
            "MCP_OIDC_CLIENT_ID",
            "MCP_OIDC_CLIENT_SECRET",
            "MCP_BASE_URL",
        ):
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(MissingConfiguration, match="MCP_OIDC_CONFIG_URL"):
            build_auth(make_settings(auth="oidc"))


class TestScopedProxy:
    """Scope injection, isolated from the provider it wraps.

    The parent's method is patched rather than the subclass's, so the super()
    call resolves to the stub and what is asserted is precisely the transform
    this class applies.
    """

    @staticmethod
    def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
        captured: dict[str, object] = {}

        def fake(self: object, txn_id: str, transaction: dict[str, object]) -> str:
            captured.update(transaction)
            return "https://upstream/authorize"

        monkeypatch.setattr(OIDCProxy, "_build_upstream_authorize_url", fake)
        return captured

    @staticmethod
    def _proxy(extra: tuple[str, ...]) -> ScopedOIDCProxy:
        proxy = ScopedOIDCProxy.__new__(ScopedOIDCProxy)
        proxy._extra_scopes = extra  # pyright: ignore[reportPrivateUsage]
        proxy.required_scopes = ["openid"]
        return proxy

    def test_extra_scopes_are_added_to_the_authorization_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Okta issues a refresh token only when offline_access is requested,
        and it cannot go in required_scopes: that list is enforced on every
        request, so requiring a scope the provider declines to grant would
        reject every call."""
        captured = self._capture(monkeypatch)
        proxy = self._proxy(("offline_access",))
        ScopedOIDCProxy._build_upstream_authorize_url(  # pyright: ignore[reportPrivateUsage]
            proxy, "txn", {"scopes": ["openid", "email"]}
        )
        assert captured["scopes"] == ["openid", "email", "offline_access"]

    def test_an_already_present_scope_is_not_duplicated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = self._capture(monkeypatch)
        proxy = self._proxy(("offline_access",))
        ScopedOIDCProxy._build_upstream_authorize_url(  # pyright: ignore[reportPrivateUsage]
            proxy, "txn", {"scopes": ["openid", "offline_access"]}
        )
        assert captured["scopes"] == ["openid", "offline_access"]

    def test_no_extra_scopes_leaves_the_request_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = self._capture(monkeypatch)
        proxy = self._proxy(())
        ScopedOIDCProxy._build_upstream_authorize_url(  # pyright: ignore[reportPrivateUsage]
            proxy, "txn", {"scopes": ["openid"]}
        )
        assert captured["scopes"] == ["openid"]

    def test_required_scopes_are_used_when_the_transaction_names_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = self._capture(monkeypatch)
        proxy = self._proxy(("offline_access",))
        ScopedOIDCProxy._build_upstream_authorize_url(  # pyright: ignore[reportPrivateUsage]
            proxy, "txn", {}
        )
        assert captured["scopes"] == ["openid", "offline_access"]


class TestHealthEndpoint:
    """The load balancer's only view of this process."""

    @staticmethod
    def _health_route(groups: list[str]) -> Any:
        app = build_server(groups).http_app()
        for route in app.routes:
            if getattr(route, "path", None) == "/health":
                return route
        return None

    def test_health_is_registered(self) -> None:
        assert self._health_route(["core"]) is not None

    def test_health_is_registered_whatever_groups_are_selected(self) -> None:
        """A deployment running only reports still has to become healthy."""
        for groups in (["core"], ["reports"], ["all"]):
            assert self._health_route(groups) is not None, groups

    async def test_health_answers_without_authentication(self) -> None:
        """It has to answer before anyone signs in, or the instance never
        becomes healthy enough to sign in to."""
        from starlette.requests import Request

        route = self._health_route(["core"])
        assert route is not None
        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [],
            "query_string": b"",
        }
        response = await route.endpoint(Request(scope))
        assert response.status_code == 200
        assert response.body == b"OK"
