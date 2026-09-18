"""Tools that change the ledger, and the three things standing in front of them."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest
import respx
from fastmcp import Client
from mcp.types import TextContent

import src.client as client_module
from main import build_server
from tests.conftest import BASE, make_settings, sent_body


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


async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    async with Client(build_server(["all"])) as client:
        return _payload(await client.call_tool(tool, args))


class TestCreate:
    @respx.mock
    async def test_create_returns_the_new_record(self) -> None:
        respx.post(f"{BASE}/invoice").mock(
            return_value=httpx.Response(200, json={"Invoice": {"Id": "130"}})
        )
        payload = await call(
            "create_entity", {"entity": "Invoice", "data": {"Line": []}}
        )
        assert payload["data"]["Id"] == "130"
        assert "Created Invoice 130" in payload["note"]

    async def test_an_unsupported_operation_is_refused_locally(self) -> None:
        """QuickBooks does not delete accounts; it deactivates them."""
        payload = await call(
            "delete_entity",
            {"entity": "Account", "entity_id": "1", "sync_token": "0"},
        )
        assert payload["error"] == "ValueError"
        assert "does not support" in payload["message"]
        assert "deactivated by update" in payload["message"]

    async def test_an_unknown_entity_is_refused(self) -> None:
        payload = await call("create_entity", {"entity": "Widget", "data": {}})
        assert payload["error"] == "KeyError"


class TestUpdateAndDelete:
    @respx.mock
    async def test_sparse_update_is_the_default(self) -> None:
        route = respx.post(f"{BASE}/invoice").mock(
            return_value=httpx.Response(200, json={"Invoice": {"Id": "1"}})
        )
        await call(
            "update_entity",
            {
                "entity": "Invoice",
                "data": {"Id": "1", "SyncToken": "2", "DocNumber": "X"},
            },
        )
        assert "sparse" in json.dumps(sent_body(route))

    @respx.mock
    async def test_a_full_replacement_says_what_it_did(self) -> None:
        respx.post(f"{BASE}/invoice").mock(
            return_value=httpx.Response(200, json={"Invoice": {"Id": "1"}})
        )
        payload = await call(
            "update_entity",
            {
                "entity": "Invoice",
                "data": {"Id": "1", "SyncToken": "2"},
                "sparse": False,
            },
        )
        assert "replacing every field" in payload["note"]

    async def test_update_without_a_sync_token_is_refused(self) -> None:
        payload = await call(
            "update_entity", {"entity": "Invoice", "data": {"Id": "1"}}
        )
        assert "SyncToken" in payload["message"]

    @respx.mock
    async def test_delete_warns_that_it_cannot_be_undone(self) -> None:
        respx.post(f"{BASE}/invoice").mock(
            return_value=httpx.Response(200, json={"Invoice": {"status": "Deleted"}})
        )
        payload = await call(
            "delete_entity",
            {"entity": "Invoice", "entity_id": "1", "sync_token": "2"},
        )
        assert "cannot be undone" in payload["note"]

    @respx.mock
    async def test_void_keeps_the_record(self) -> None:
        respx.post(f"{BASE}/invoice").mock(
            return_value=httpx.Response(200, json={"Invoice": {"Id": "1"}})
        )
        payload = await call(
            "void_transaction",
            {"entity": "Invoice", "entity_id": "1", "sync_token": "2"},
        )
        assert "remains at zero" in payload["note"]


class TestJournalEntry:
    BALANCED = [
        {"account_id": "84", "amount": "955.85", "posting_type": "Debit"},
        {"account_id": "12", "amount": "955.85", "posting_type": "Credit"},
    ]

    @respx.mock
    async def test_a_balanced_entry_is_posted(self) -> None:
        route = respx.post(f"{BASE}/journalentry").mock(
            return_value=httpx.Response(200, json={"JournalEntry": {"Id": "9"}})
        )
        payload = await call(
            "create_journal_entry",
            {"lines": self.BALANCED, "private_note": "August accrual"},
        )
        assert payload["data"]["Id"] == "9"
        body = sent_body(route)
        assert body["PrivateNote"] == "August accrual"
        assert body["Line"][0]["JournalEntryLineDetail"]["PostingType"] == "Debit"

    async def test_an_unbalanced_entry_is_refused_before_sending(self) -> None:
        """QuickBooks' own rejection does not say which side is short."""
        payload = await call(
            "create_journal_entry",
            {
                "lines": [
                    {"account_id": "84", "amount": "100.00", "posting_type": "Debit"},
                    {"account_id": "12", "amount": "90.00", "posting_type": "Credit"},
                ]
            },
        )
        assert payload["error"] == "ValueError"
        assert "does not balance" in payload["message"]
        assert "10.00" in payload["message"]
        assert "Nothing was sent" in payload["message"]

    async def test_no_lines_is_refused(self) -> None:
        assert (await call("create_journal_entry", {"lines": []}))["error"] == (
            "ValueError"
        )

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            (
                {"account_id": "1", "amount": "1", "posting_type": "Sideways"},
                "posting_type",
            ),
            ({"amount": "1", "posting_type": "Debit"}, "account_id is required"),
            (
                {"account_id": "1", "amount": "abc", "posting_type": "Debit"},
                "not a number",
            ),
            (
                {"account_id": "1", "amount": "-5", "posting_type": "Debit"},
                "must be positive",
            ),
        ],
    )
    async def test_malformed_lines_are_refused(
        self, line: dict[str, Any], expected: str
    ) -> None:
        payload = await call("create_journal_entry", {"lines": [line]})
        assert expected in payload["message"]

    @respx.mock
    async def test_optional_line_fields_are_carried(self) -> None:
        route = respx.post(f"{BASE}/journalentry").mock(
            return_value=httpx.Response(200, json={"JournalEntry": {"Id": "9"}})
        )
        lines = [
            {
                **self.BALANCED[0],
                "description": "d",
                "class_id": "5",
                "customer_id": "7",
            },
            self.BALANCED[1],
        ]
        await call(
            "create_journal_entry",
            {"lines": lines, "txn_date": "2026-08-31", "doc_number": "JE-1"},
        )
        body = sent_body(route)
        assert body["TxnDate"] == "2026-08-31"
        assert body["DocNumber"] == "JE-1"
        detail = body["Line"][0]["JournalEntryLineDetail"]
        assert detail["ClassRef"] == {"value": "5"}
        assert detail["Entity"]["EntityRef"] == {"value": "7"}


class TestGuards:
    async def test_read_only_refuses_before_the_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from qbo.auth import AuthClient, MemoryTokenStore, TokenSet
        from qbo.client import QboClient

        auth = AuthClient(
            client_id="a",
            client_secret="b",
            store=MemoryTokenStore(TokenSet(access_token="x", refresh_token="y")),
        )
        client_module.set_client(
            QboClient(realm_id="9130347", auth=auth, read_only=True),
            make_settings(read_only=True),
        )
        payload = await call("create_entity", {"entity": "Invoice", "data": {}})
        assert "read-only mode" in payload["message"]
