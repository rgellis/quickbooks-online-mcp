"""Lazy construction, limits, and the branches that only run on odd input."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.server.auth import OIDCProxy
from mcp.types import TextContent

import src.client as client_module
from main import build_server
from src.auth import ScopedOIDCProxy, build_auth
from src.services.base import row_limit
from tests.conftest import BASE, make_settings, sent_url


async def call(tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    async with Client(build_server(["all"])) as client:
        result = await client.call_tool(tool, args or {})
        blocks = list(result.content)
        if not blocks:
            return {}
        first = blocks[0]
        assert isinstance(first, TextContent)
        decoded: Any = json.loads(first.text)
        return cast("dict[str, Any]", decoded) if isinstance(decoded, dict) else {}


class TestClientLifecycle:
    async def test_the_client_is_built_once_and_reused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        settings = make_settings(token_store=str(tmp_path / "t.json"))
        client_module.set_client(None, settings)
        first = client_module.get_client()
        assert client_module.get_client() is first
        assert first.realm_id == settings.realm_id
        await client_module.close_client()

    async def test_closing_when_nothing_was_built_is_harmless(self) -> None:
        client_module.set_client(None)
        await client_module.close_client()

    def test_settings_are_read_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client_module._settings = None  # pyright: ignore[reportPrivateUsage]
        for name, value in (
            ("QBO_CLIENT_ID", "a"),
            ("QBO_CLIENT_SECRET", "b"),
            ("QBO_REALM_ID", "c"),
        ):
            monkeypatch.setenv(name, value)
        first = client_module.current_settings()
        assert client_module.current_settings() is first

    async def test_read_only_flows_from_settings_to_the_client(
        self, tmp_path: Any
    ) -> None:
        client_module.set_client(
            None, make_settings(read_only=True, token_store=str(tmp_path / "t.json"))
        )
        assert client_module.get_client().read_only is True
        await client_module.close_client()


class TestRowLimit:
    def test_none_uses_the_configured_cap(self) -> None:
        assert row_limit(None) == 50

    def test_zero_means_everything(self) -> None:
        """A caller asking for no cap has said so explicitly."""
        assert row_limit(0) is None

    def test_a_negative_limit_also_means_everything(self) -> None:
        assert row_limit(-1) is None

    def test_an_explicit_limit_is_honoured_even_above_the_cap(self) -> None:
        assert row_limit(500) == 500


class TestAuthConstruction:
    def test_scoped_proxy_stores_its_extra_scopes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Constructed without reaching the provider's discovery endpoint."""

        def no_upstream(self: object, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr(OIDCProxy, "__init__", no_upstream)
        proxy = ScopedOIDCProxy(
            extra_scopes=("offline_access",), config_url="https://x"
        )
        assert proxy._extra_scopes == ("offline_access",)  # pyright: ignore[reportPrivateUsage]

    def test_csv_settings_fall_back_to_their_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_JWT_JWKS_URI", "https://issuer.example/jwks")
        monkeypatch.delenv("MCP_JWT_REQUIRED_SCOPES", raising=False)
        assert build_auth(make_settings(auth="jwt")) is not None

    @pytest.mark.parametrize("value", ["", "off", "disabled"])
    def test_disabled_spellings(self, value: str) -> None:
        assert build_auth(make_settings(auth=value)) is None


class TestOptionalArguments:
    """Branches that only run when an optional filter is supplied, or is not."""

    @respx.mock
    async def test_listings_without_any_date_filter(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json={"QueryResponse": {}})
        )
        for tool in ("list_invoices", "list_bills", "list_payments", "list_purchases"):
            assert (await call(tool))["count"] == 0

    @respx.mock
    async def test_listings_with_only_a_start_date(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json={"QueryResponse": {}})
        )
        for tool in ("list_invoices", "list_bills", "list_purchases"):
            assert (await call(tool, {"start_date": "2026-08-01"}))["count"] == 0

    @respx.mock
    async def test_listings_with_only_an_end_date(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json={"QueryResponse": {}})
        )
        for tool in ("list_invoices", "list_bills", "list_purchases"):
            assert (await call(tool, {"end_date": "2026-08-31"}))["count"] == 0

    @respx.mock
    async def test_inactive_records_can_be_included(self) -> None:
        route = respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json={"QueryResponse": {}})
        )
        await call("list_customers", {"active_only": False})
        await call("list_vendors", {"active_only": False})
        await call("list_accounts", {"active_only": False})
        for index in range(len(cast("list[Any]", route.calls))):
            assert "Active" not in sent_url(route, index)

    @respx.mock
    async def test_reports_without_optional_parameters(self) -> None:
        for name in ("ProfitAndLoss", "BalanceSheet", "GeneralLedger", "TrialBalance"):
            respx.get(f"{BASE}/reports/{name}").mock(
                return_value=httpx.Response(200, json={"Header": {}})
            )
        assert (await call("get_report", {"name": "ProfitAndLoss"}))["count"] == 0
        for tool, args in (
            ("get_profit_and_loss", {"start_date": "a", "end_date": "b"}),
            ("get_balance_sheet", {"as_of": "a"}),
            ("get_general_ledger", {"start_date": "a", "end_date": "b"}),
            ("get_trial_balance", {"start_date": "a", "end_date": "b"}),
        ):
            assert (await call(tool, args))["count"] == 0

    @respx.mock
    async def test_reports_with_an_accounting_method(self) -> None:
        for name in ("ProfitAndLoss", "BalanceSheet", "GeneralLedger", "TrialBalance"):
            respx.get(f"{BASE}/reports/{name}").mock(
                return_value=httpx.Response(200, json={"Header": {}})
            )
        for tool, args in (
            (
                "get_profit_and_loss",
                {
                    "start_date": "a",
                    "end_date": "b",
                    "accounting_method": "Cash",
                    "summarize_column_by": "Month",
                },
            ),
            ("get_balance_sheet", {"as_of": "a", "accounting_method": "Cash"}),
            (
                "get_general_ledger",
                {"start_date": "a", "end_date": "b", "accounting_method": "Cash"},
            ),
            (
                "get_trial_balance",
                {"start_date": "a", "end_date": "b", "accounting_method": "Cash"},
            ),
        ):
            assert (await call(tool, args))["count"] == 0


class TestCdcNarrowing:
    @respx.mock
    @pytest.mark.parametrize(
        "body",
        [
            {"CDCResponse": [{"QueryResponse": "not a list"}]},
            {"CDCResponse": [{"QueryResponse": [{"Invoice": "not a list"}]}]},
            {"CDCResponse": [{"QueryResponse": [{"Invoice": ["not an object"]}]}]},
            {"CDCResponse": ["not an object"]},
        ],
    )
    async def test_malformed_shapes_yield_no_rows(self, body: dict[str, Any]) -> None:
        respx.get(f"{BASE}/cdc").mock(return_value=httpx.Response(200, json=body))
        payload = await call(
            "get_changes", {"entities": ["Invoice"], "changed_since": "x"}
        )
        assert payload["count"] == 0


class TestRemainingBranches:
    """The last few paths, each reachable only through a specific argument."""

    @respx.mock
    async def test_filtering_invoices_by_customer(self) -> None:
        route = respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json={"QueryResponse": {}})
        )
        await call("list_invoices", {"customer_id": "24"})
        assert "CustomerRef" in sent_url(route, 0)

    @respx.mock
    async def test_filtering_payments_by_customer(self) -> None:
        route = respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json={"QueryResponse": {}})
        )
        await call("list_payments", {"customer_id": "24"})
        assert "CustomerRef" in sent_url(route, 0)

    @respx.mock
    async def test_filtering_bills_to_unpaid_only(self) -> None:
        route = respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json={"QueryResponse": {}})
        )
        await call("list_bills", {"unpaid_only": True})
        assert "Balance" in sent_url(route, 0)

    @respx.mock
    async def test_searching_vendors_by_name(self) -> None:
        route = respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json={"QueryResponse": {}})
        )
        await call("list_vendors", {"name_contains": "AT&T"})
        assert "DisplayName" in sent_url(route, 0)

    def test_csv_values_are_split_and_trimmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_JWT_JWKS_URI", "https://issuer.example/jwks")
        monkeypatch.setenv("MCP_JWT_REQUIRED_SCOPES", " read , write ,, ")
        assert build_auth(make_settings(auth="jwt")) is not None

    def test_an_envelope_carrying_neither_rows_nor_data(self) -> None:
        """Both are optional; a bare envelope is still valid provenance."""
        from src.shaping import envelope

        payload = envelope(source="ping")
        assert "rows" not in payload
        assert "data" not in payload
        assert payload["source"].endswith("ping")
