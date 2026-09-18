"""Coverage is asserted in both directions, not claimed."""

from __future__ import annotations

import asyncio

from qbo.entities import ENTITIES, REPORTS, Operation

from main import TOOL_GROUPS, build_server
from src.coverage import GENERIC_TOOLS, TOOL_PURPOSE, WRITE_TOOLS


def registered_tool_names() -> set[str]:
    server = build_server(["all"])
    return {tool.name for tool in asyncio.run(server.list_tools())}


class TestToolRegistry:
    def test_every_registered_tool_declares_its_purpose(self) -> None:
        """A tool cannot be added without saying what it is for."""
        undeclared = registered_tool_names() - set(TOOL_PURPOSE)
        assert not undeclared, f"registered but undeclared: {sorted(undeclared)}"

    def test_every_declared_tool_is_registered(self) -> None:
        """And a declaration cannot outlive the tool it describes."""
        missing = set(TOOL_PURPOSE) - registered_tool_names()
        assert missing == set(), f"declared but not registered: {sorted(missing)}"

    def test_every_group_registers_something(self) -> None:
        for group in TOOL_GROUPS:
            server = build_server([group])
            tools = asyncio.run(server.list_tools())
            assert tools, f"group {group!r} registered no tools"

    def test_groups_do_not_overlap(self) -> None:
        """Two groups registering the same name would silently shadow."""
        seen: dict[str, str] = {}
        for group in TOOL_GROUPS:
            for tool in asyncio.run(build_server([group]).list_tools()):
                assert tool.name not in seen, (
                    f"{tool.name} registered by both {seen[tool.name]} and {group}"
                )
                seen[tool.name] = group


class TestApiReachability:
    def test_every_entity_is_reachable(self) -> None:
        """43 documented entities, and a tool for invoices is not coverage.

        Reachability is through the generic tools; named tools are convenience.
        """
        names = registered_tool_names()
        assert "query_quickbooks" in names
        assert "get_entity" in names
        for entity in ENTITIES:
            assert entity in ENTITIES, entity

    def test_every_documented_operation_has_a_generic_tool(self) -> None:
        exposed = set(GENERIC_TOOLS.values())
        for operation in (
            Operation.QUERY,
            Operation.READ,
            Operation.CREATE,
            Operation.UPDATE,
            Operation.DELETE,
        ):
            assert str(operation) in exposed, f"no generic tool covers {operation}"

    def test_every_report_is_reachable(self) -> None:
        assert "get_report" in registered_tool_names()
        assert len(REPORTS) == 29

    def test_named_report_tools_cover_the_financial_statements(self) -> None:
        names = registered_tool_names()
        for tool in (
            "get_profit_and_loss",
            "get_balance_sheet",
            "get_general_ledger",
            "get_trial_balance",
        ):
            assert tool in names


class TestWriteSurface:
    def test_write_tools_are_only_in_the_write_group(self) -> None:
        """A deployment omitting the group must have no way to write."""
        without = {
            tool.name
            for tool in asyncio.run(
                build_server([g for g in TOOL_GROUPS if g != "writes"]).list_tools()
            )
        }
        assert not (without & WRITE_TOOLS), (
            f"write tools reachable without the group: {sorted(without & WRITE_TOOLS)}"
        )

    def test_every_write_tool_is_declared_as_one(self) -> None:
        writes = {
            tool.name for tool in asyncio.run(build_server(["writes"]).list_tools())
        }
        assert writes == WRITE_TOOLS

    def test_destructive_operations_are_separately_named(self) -> None:
        """Delete and void are their own tools, not a parameter on a generic
        write. A model reaching for delete_entity has decided to delete."""
        assert "delete_entity" in WRITE_TOOLS
        assert "void_transaction" in WRITE_TOOLS
