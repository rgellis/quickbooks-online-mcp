"""The tools, exercised through a client as a model would call them.

No test reaches QuickBooks. Every HTTP exchange is mocked, so what is asserted
here is the shape of what a caller gets back -- including on failure, which is
the path least likely to be exercised by hand.
"""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import respx
from fastmcp import Client
from mcp.types import TextContent

from main import build_server
from tests.conftest import BASE, sent_url

TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"


def _payload(result: Any) -> dict[str, Any]:
    """The JSON a tool returned.

    Tool results carry a list of content blocks that may be text or image;
    every tool here returns one text block, and narrowing says so rather than
    assuming it.
    """
    blocks = list(result.content)
    if not blocks:
        return {}
    first = blocks[0]
    assert isinstance(first, TextContent), f"expected text, got {type(first).__name__}"
    decoded: Any = json.loads(first.text)
    if isinstance(decoded, dict):
        return cast("dict[str, Any]", decoded)
    return {"value": decoded}


async def call(tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    async with Client(build_server(["all"])) as client:
        return _payload(await client.call_tool(tool, args or {}))


def query_response(entity: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "QueryResponse": {entity: rows, "startPosition": 1, "maxResults": len(rows)}
    }


class TestCore:
    @respx.mock
    async def test_check_connection_reports_configuration(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(
                200, json=query_response("CompanyInfo", [{"CompanyName": "Acme"}])
            )
        )
        payload = await call("check_connection")
        assert payload["data"]["connected"] is True
        assert payload["data"]["company"] == "Acme"
        assert payload["data"]["writes"] == "permitted"

    @respx.mock
    async def test_check_connection_never_returns_a_credential(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json=query_response("CompanyInfo", [{}]))
        )
        assert "test-secret" not in json.dumps(await call("check_connection"))

    @respx.mock
    async def test_query_returns_rows_and_states_truncation(self) -> None:
        rows = [{"Id": str(i)} for i in range(120)]
        respx.get(f"{BASE}/query").mock(
            side_effect=[
                httpx.Response(200, json=query_response("Invoice", rows)),
                httpx.Response(200, json={"QueryResponse": {}}),
            ]
        )
        payload = await call(
            "query_quickbooks", {"statement": "SELECT * FROM Invoice", "limit": 10}
        )
        assert payload["count"] == 120
        assert payload["returned"] == 10
        assert payload["omitted"] == 110

    @respx.mock
    async def test_a_failing_query_is_described_not_raised(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(
                400, json={"Fault": {"type": "ValidationFault"}}
            )
        )
        payload = await call("query_quickbooks", {"statement": "SELECT bad"})
        assert payload["error"] == "QboApiError"
        assert payload["kind"] == "api"
        assert "attempted" in payload

    @respx.mock
    async def test_get_entity(self) -> None:
        respx.get(f"{BASE}/invoice/130").mock(
            return_value=httpx.Response(200, json={"Invoice": {"Id": "130"}})
        )
        assert (await call("get_entity", {"entity": "Invoice", "entity_id": "130"}))[
            "data"
        ] == {"Id": "130"}

    async def test_get_entity_rejects_an_unknown_entity(self) -> None:
        payload = await call("get_entity", {"entity": "Widget", "entity_id": "1"})
        assert payload["error"] == "KeyError"
        assert "list_entities" in payload["message"]

    async def test_list_entities_is_marked_derived(self) -> None:
        payload = await call("list_entities")
        assert payload["derived"] is True
        assert payload["count"] == 43

    async def test_describe_entity_states_what_is_required(self) -> None:
        payload = await call("describe_entity", {"entity": "JournalEntry"})
        assert payload["data"]["required_to_update"] == ["SyncToken"]
        assert "CREATE" in payload["data"]["operations"]

    async def test_describe_entity_rejects_an_unknown_entity(self) -> None:
        assert (await call("describe_entity", {"entity": "Widget"}))["error"] == (
            "KeyError"
        )

    async def test_list_reports_flags_the_renamed_ones(self) -> None:
        payload = await call("list_reports")
        renamed = {r["report"]: r["route"] for r in payload["rows"] if r["differs"]}
        assert renamed["APAgingDetail"] == "AgedPayableDetail"
        assert len(renamed) == 9


class TestListings:
    @respx.mock
    async def test_list_accounts_orders_by_number(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(
                200,
                json=query_response(
                    "Account",
                    [
                        {
                            "Id": "2",
                            "AcctNum": "2000",
                            "Name": "B",
                            "AccountType": "Bank",
                        },
                        {
                            "Id": "1",
                            "AcctNum": "1000",
                            "Name": "A",
                            "AccountType": "Bank",
                        },
                    ],
                ),
            )
        )
        payload = await call("list_accounts", {"account_type": "Bank"})
        assert [row["number"] for row in payload["rows"]] == ["1000", "2000"]

    @respx.mock
    async def test_accounts_without_a_number_sort_last(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(
                200,
                json=query_response(
                    "Account",
                    [
                        {"Id": "1", "Name": "Cash"},
                        {"Id": "2", "AcctNum": "1000", "Name": "A"},
                    ],
                ),
            )
        )
        payload = await call("list_accounts")
        assert payload["rows"][-1]["name"] == "Cash"

    @respx.mock
    async def test_list_invoices_unpaid_filter(self) -> None:
        route = respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json=query_response("Invoice", []))
        )
        await call("list_invoices", {"unpaid_only": True})
        assert "Balance > 0" in sent_url(route)

    @respx.mock
    async def test_list_customers_escapes_the_search_term(self) -> None:
        """A name with an apostrophe must not break out of the literal."""
        route = respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(200, json=query_response("Customer", []))
        )
        await call("list_customers", {"name_contains": "O'Brien"})
        assert "O''Brien" in sent_url(route)

    @respx.mock
    async def test_list_payments(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(
                200,
                json=query_response(
                    "Payment",
                    [{"Id": "1", "TotalAmt": 100, "CustomerRef": {"name": "X"}}],
                ),
            )
        )
        assert (await call("list_payments"))["rows"][0]["customer"] == "X"

    @respx.mock
    async def test_list_bills(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(
                200,
                json=query_response("Bill", [{"Id": "1", "VendorRef": {"name": "V"}}]),
            )
        )
        assert (await call("list_bills", {"vendor_id": "9"}))["rows"][0][
            "vendor"
        ] == "V"

    @respx.mock
    async def test_list_vendors(self) -> None:
        respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(
                200, json=query_response("Vendor", [{"Id": "1", "DisplayName": "V"}])
            )
        )
        assert (await call("list_vendors"))["rows"][0]["name"] == "V"

    @respx.mock
    async def test_purchases_are_filtered_by_payment_type_locally(self) -> None:
        """QuickBooks accepts WHERE PaymentType and returns zero rows whatever
        the ledger holds, so the filter cannot be sent."""
        route = respx.get(f"{BASE}/query").mock(
            return_value=httpx.Response(
                200,
                json=query_response(
                    "Purchase",
                    [
                        {"Id": "1", "PaymentType": "CreditCard"},
                        {"Id": "2", "PaymentType": "Cash"},
                    ],
                ),
            )
        )
        payload = await call("list_purchases", {"payment_type": "creditcard"})
        assert payload["count"] == 1
        assert payload["rows"][0]["id"] == "1"
        assert "PaymentType" not in sent_url(route)


class TestReports:
    REPORT = {
        "Header": {
            "ReportName": "ProfitAndLoss",
            "Currency": "USD",
            "StartPeriod": "2026-08-01",
            "EndPeriod": "2026-08-31",
        },
        "Columns": {
            "Column": [
                {"ColTitle": "", "ColType": "Account"},
                {
                    "ColTitle": "Total",
                    "ColType": "Money",
                    "MetaData": [{"Name": "ColKey", "Value": "total"}],
                },
            ]
        },
        "Rows": {
            "Row": [
                {
                    "Header": {"ColData": [{"value": "Income"}, {"value": ""}]},
                    "group": "Income",
                    "Summary": {
                        "ColData": [{"value": "Total Income"}, {"value": "100.00"}]
                    },
                    "Rows": {
                        "Row": [
                            {
                                "ColData": [{"value": "Sales"}, {"value": "100.00"}],
                                "type": "Data",
                            }
                        ]
                    },
                },
            ]
        },
    }

    @respx.mock
    async def test_profit_and_loss_returns_flat_rows(self) -> None:
        respx.get(f"{BASE}/reports/ProfitAndLoss").mock(
            return_value=httpx.Response(200, json=self.REPORT)
        )
        payload = await call(
            "get_profit_and_loss",
            {"start_date": "2026-08-01", "end_date": "2026-08-31"},
        )
        assert payload["invariants"] == "passed"
        assert payload["currency"] == "USD"
        assert payload["rows"][0]["section"] == "Income"

    @respx.mock
    async def test_a_self_contradicting_report_is_refused(self) -> None:
        broken = json.loads(json.dumps(self.REPORT))
        broken["Rows"]["Row"][0]["Summary"]["ColData"][1]["value"] = "0.00"
        respx.get(f"{BASE}/reports/ProfitAndLoss").mock(
            return_value=httpx.Response(200, json=broken)
        )
        payload = await call(
            "get_profit_and_loss",
            {"start_date": "2026-08-01", "end_date": "2026-08-31"},
        )
        assert payload["kind"] == "invariant"
        assert "withheld" in payload["meaning"]

    @respx.mock
    async def test_get_report_also_refuses_a_self_contradicting_report(self) -> None:
        """The generic tool validates too, not just the named ones. Mutation
        testing found this: removing the check from get_report broke nothing,
        because every invariant test went through get_profit_and_loss."""
        broken = json.loads(json.dumps(self.REPORT))
        broken["Rows"]["Row"][0]["Summary"]["ColData"][1]["value"] = "0.00"
        respx.get(f"{BASE}/reports/ProfitAndLoss").mock(
            return_value=httpx.Response(200, json=broken)
        )
        payload = await call("get_report", {"name": "ProfitAndLoss"})
        assert payload["kind"] == "invariant"

    @respx.mock
    async def test_get_report_resolves_a_renamed_route(self) -> None:
        route = respx.get(f"{BASE}/reports/AgedPayableDetail").mock(
            return_value=httpx.Response(200, json={"Header": {}})
        )
        await call("get_report", {"name": "APAgingDetail"})
        assert route.called

    @respx.mock
    async def test_balance_sheet(self) -> None:
        respx.get(f"{BASE}/reports/BalanceSheet").mock(
            return_value=httpx.Response(
                200, json={"Header": {"ReportName": "BalanceSheet"}}
            )
        )
        assert (await call("get_balance_sheet", {"as_of": "2026-09-17"}))["report"] == (
            "BalanceSheet"
        )

    @respx.mock
    async def test_general_ledger_passes_columns_through(self) -> None:
        route = respx.get(f"{BASE}/reports/GeneralLedger").mock(
            return_value=httpx.Response(200, json={"Header": {}})
        )
        await call(
            "get_general_ledger",
            {
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
                "columns": "tx_date",
            },
        )
        assert "columns=tx_date" in sent_url(route)

    @respx.mock
    async def test_trial_balance(self) -> None:
        respx.get(f"{BASE}/reports/TrialBalance").mock(
            return_value=httpx.Response(200, json={"Header": {}})
        )
        payload = await call(
            "get_trial_balance", {"start_date": "2026-08-01", "end_date": "2026-08-31"}
        )
        assert payload["count"] == 0

    @respx.mock
    async def test_extra_params_are_passed_through(self) -> None:
        route = respx.get(f"{BASE}/reports/ProfitAndLoss").mock(
            return_value=httpx.Response(200, json={"Header": {}})
        )
        await call(
            "get_report",
            {
                "name": "ProfitAndLoss",
                "accounting_method": "Cash",
                "summarize_column_by": "Month",
                "params": {"class": "1"},
            },
        )
        url = sent_url(route)
        assert "accounting_method=Cash" in url
        assert "summarize_column_by=Month" in url
        assert "class=1" in url


class TestChanges:
    @respx.mock
    async def test_changes_include_deletions(self) -> None:
        respx.get(f"{BASE}/cdc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "CDCResponse": [
                        {
                            "QueryResponse": [
                                {
                                    "Invoice": [
                                        {
                                            "Id": "1",
                                            "status": "Deleted",
                                            "MetaData": {
                                                "LastUpdatedTime": "2026-09-01T00:00:00Z"
                                            },
                                        },
                                        {
                                            "Id": "2",
                                            "MetaData": {
                                                "LastUpdatedTime": "2026-09-02T00:00:00Z"
                                            },
                                        },
                                    ],
                                    "startPosition": 1,
                                },
                            ]
                        }
                    ]
                },
            )
        )
        payload = await call(
            "get_changes",
            {"entities": ["Invoice"], "changed_since": "2026-09-01T00:00:00-04:00"},
        )
        assert payload["count"] == 2
        assert payload["rows"][0]["change"] == "Deleted"
        assert payload["rows"][1]["change"] == "Updated"
        assert "30 days" in payload["note"]

    async def test_unknown_entities_are_refused(self) -> None:
        payload = await call(
            "get_changes", {"entities": ["Widget"], "changed_since": "2026-09-01"}
        )
        assert payload["error"] == "KeyError"

    async def test_an_empty_entity_list_is_refused(self) -> None:
        payload = await call("get_changes", {"entities": [], "changed_since": "x"})
        assert payload["error"] == "ValueError"

    @respx.mock
    async def test_a_malformed_cdc_body_yields_no_rows(self) -> None:
        respx.get(f"{BASE}/cdc").mock(
            return_value=httpx.Response(200, json={"CDCResponse": "unexpected"})
        )
        payload = await call(
            "get_changes", {"entities": ["Invoice"], "changed_since": "x"}
        )
        assert payload["count"] == 0
