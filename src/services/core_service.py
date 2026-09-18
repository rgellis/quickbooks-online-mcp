"""Core tools: connection state, free-form query, entity access, introspection.

``query_quickbooks`` is the escape hatch. The structured tools in the other
modules cover what is asked for daily; anything they do not cover -- an
unanticipated filter, a join, an entity with no dedicated tool -- goes through
here rather than accreting one-off parameters onto the structured tools.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP
from qbo.entities import ENTITIES, REPORTS, Operation

from src.client import current_settings, get_client
from src.services.base import execute, row_limit
from src.shaping import envelope

__all__ = ["register_core_tools"]


def register_core_tools(mcp: FastMCP[Any]) -> None:
    @mcp.tool
    async def check_connection(ctx: Context) -> dict[str, Any]:  # noqa: ARG001
        """Verify the server can reach QuickBooks and report how it is configured.

        Call this first when something is not working. It distinguishes a
        configuration problem from an authorization problem from an API
        problem, which otherwise all present as a failing tool.

        Returns:
            The realm, whether writes are permitted, the minorversion in use,
            and whether a live call succeeds. Never returns a credential.
        """

        async def run() -> dict[str, Any]:
            settings = current_settings()
            client = get_client()
            rows = await client.query("SELECT * FROM CompanyInfo")
            company = rows[0] if rows else {}
            return envelope(
                source="CompanyInfo",
                data={
                    "connected": True,
                    "company": company.get("CompanyName"),
                    "realm": settings.realm_id,
                    "read_only": settings.read_only,
                    "minor_version": client.minor_version,
                    "environment": client.environment,
                    "writes": "refused — QBO_READ_ONLY is set"
                    if settings.read_only
                    else "permitted",
                },
            )

        return await execute(
            "checking the QuickBooks connection", run, tool="check_connection"
        )

    @mcp.tool
    async def query_quickbooks(
        ctx: Context,  # noqa: ARG001
        statement: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Run a QuickBooks query and return the matching records.

        The general escape hatch. Use it for anything the structured tools do
        not cover: unusual filters, ordering, or an entity without its own tool.

        QuickBooks' query language resembles SQL but is not SQL. There are no
        joins, no aggregate functions, and no wildcards in SELECT beyond ``*``.
        Filters use ``WHERE``, dates are quoted ISO strings, and string
        comparison is with ``=`` or ``LIKE``.

            SELECT * FROM Invoice WHERE TxnDate >= '2026-08-01'
            SELECT * FROM Account WHERE AccountType = 'Bank'
            SELECT * FROM JournalEntry WHERE TxnDate >= '2026-08-01' ORDER BY TxnDate

        Paging past the API's 1,000-row cap is handled transparently; ask for
        what you want and every matching row is fetched.

        Args:
            statement: The query. Do not add STARTPOSITION or MAXRESULTS --
                supply ``limit`` instead, or paging will be left to you.
            limit: Rows to return in full. Defaults to the configured cap;
                pass 0 for every row.

        Returns:
            The matching records, with the total count stated separately from
            the number listed.
        """

        async def run() -> dict[str, Any]:
            client = get_client()
            resolved = row_limit(limit)
            rows = await client.query(statement)
            return envelope(
                source=f"query · {statement[:120]}",
                rows=rows,
                max_rows=resolved,
            )

        return await execute(
            f"running the query {statement[:120]!r}", run, tool="query_quickbooks"
        )

    @mcp.tool
    async def get_entity(
        ctx: Context,  # noqa: ARG001
        entity: str,
        entity_id: str,
    ) -> dict[str, Any]:
        """Read one record by its QuickBooks id.

        Args:
            entity: Documented entity name, e.g. Invoice, Account, JournalEntry.
                Call list_entities for the full set.
            entity_id: The record's Id, as QuickBooks reports it.
        """

        async def run() -> dict[str, Any]:
            if entity not in ENTITIES:
                raise KeyError(
                    f"{entity!r} is not an entity this API exposes. "
                    "Call list_entities to see what is available."
                )
            record = await get_client().get(entity, entity_id)
            return envelope(source=f"{entity}/{entity_id}", data=record)

        return await execute(f"reading {entity} {entity_id}", run, tool="get_entity")

    @mcp.tool
    async def list_entities(ctx: Context) -> dict[str, Any]:  # noqa: ARG001
        """List every entity the Accounting API exposes, and what each supports.

        Useful before writing a query or a create: it shows which operations an
        entity actually accepts, which is not uniform. Accounts cannot be
        deleted, TaxService only accepts creates, ReimburseCharge is read-only.
        """

        async def run() -> dict[str, Any]:
            rows = [
                {
                    "entity": spec.name,
                    "path": spec.path,
                    "operations": sorted(str(op) for op in spec.operations),
                }
                for spec in sorted(ENTITIES.values(), key=lambda s: s.name)
            ]
            return envelope(
                source="entity registry",
                rows=rows,
                max_rows=None,
                derived=True,
                note=(
                    "Generated from Intuit's published documentation; see "
                    "API_COVERAGE.md in the SDK for how it is verified."
                ),
            )

        return await execute("listing entities", run, tool="list_entities")

    @mcp.tool
    async def describe_entity(ctx: Context, entity: str) -> dict[str, Any]:  # noqa: ARG001
        """Describe one entity: its operations and which fields are required.

        Call this before creating or updating a record. It states what
        QuickBooks requires, which differs between creating and updating: an
        update always needs Id and SyncToken, and SyncToken must be the current
        one -- it is QuickBooks' concurrency check, and a stale value is
        rejected outright.

        Args:
            entity: Documented entity name, e.g. Invoice.
        """

        async def run() -> dict[str, Any]:
            spec = ENTITIES.get(entity)
            if spec is None:
                raise KeyError(
                    f"{entity!r} is not an entity this API exposes. "
                    "Call list_entities to see what is available."
                )
            return envelope(
                source=f"entity registry · {entity}",
                derived=True,
                data={
                    "entity": spec.name,
                    "path": spec.path,
                    "operations": sorted(str(op) for op in spec.operations),
                    "required_to_create": list(spec.required),
                    "required_to_update": list(spec.required_for_update),
                    "conditionally_required": list(spec.conditionally_required),
                    "queryable": Operation.QUERY in spec.operations,
                    "deletable": Operation.DELETE in spec.operations,
                },
            )

        return await execute(f"describing {entity}", run, tool="describe_entity")

    @mcp.tool
    async def list_reports(ctx: Context) -> dict[str, Any]:  # noqa: ARG001
        """List every report available, with the name the API actually takes.

        Nine reports are documented under a name their URL rejects --
        APAgingDetail is served from AgedPayableDetail, SalesByCustomer from
        CustomerSales. Either name works when calling get_report; this shows
        both so the difference is visible rather than surprising.
        """

        async def run() -> dict[str, Any]:
            rows = [
                {
                    "report": spec.name,
                    "route": spec.route,
                    "differs": spec.route != spec.name,
                    "locale_variants": list(spec.variants),
                }
                for spec in sorted(REPORTS.values(), key=lambda s: s.name)
            ]
            return envelope(
                source="report registry", rows=rows, max_rows=None, derived=True
            )

        return await execute("listing reports", run, tool="list_reports")
