"""Sales-side tools: invoices, customers, payments received."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from src.client import get_client
from src.query import build_query
from src.services.base import execute, row_limit
from src.shaping import envelope, ref_name

__all__ = ["register_sales_tools"]


def _date_range(
    field: str, start: str | None, end: str | None
) -> list[tuple[str, str, Any]]:
    where: list[tuple[str, str, Any]] = []
    if start:
        where.append((field, ">=", start))
    if end:
        where.append((field, "<=", end))
    return where


def register_sales_tools(mcp: FastMCP[Any]) -> None:
    @mcp.tool
    async def list_invoices(
        ctx: Context,  # noqa: ARG001
        start_date: str | None = None,
        end_date: str | None = None,
        customer_id: str | None = None,
        unpaid_only: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List invoices, most recent first.

        Anything this tool does not cover goes through query_quickbooks rather
        than being added here as another parameter.

        Args:
            start_date: ISO date; filters on transaction date, inclusive.
            end_date: ISO date, inclusive.
            customer_id: QuickBooks customer Id, not a name. Find it with
                list_customers.
            unpaid_only: Only invoices with an outstanding balance.
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            where = _date_range("TxnDate", start_date, end_date)
            if customer_id:
                where.append(("CustomerRef", "=", customer_id))
            if unpaid_only:
                where.append(("Balance", ">", 0))
            rows = await get_client().query(
                build_query("Invoice", where=where, order_by="TxnDate", descending=True)
            )
            return envelope(
                source="Invoice",
                rows=[
                    {
                        "id": row.get("Id"),
                        "doc_number": row.get("DocNumber"),
                        "date": row.get("TxnDate"),
                        "due": row.get("DueDate"),
                        "customer": ref_name(row.get("CustomerRef")),
                        "total": row.get("TotalAmt"),
                        "balance": row.get("Balance"),
                    }
                    for row in rows
                ],
                max_rows=row_limit(limit),
                period=(start_date, end_date),
            )

        return await execute("listing invoices", run, tool="list_invoices")

    @mcp.tool
    async def list_customers(
        ctx: Context,  # noqa: ARG001
        name_contains: str | None = None,
        active_only: bool = True,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List customers.

        Args:
            name_contains: Match on display name. QuickBooks' LIKE is
                case-sensitive, so this may miss differently-cased matches.
            active_only: Exclude deactivated customers.
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            where: list[tuple[str, str, Any]] = []
            if name_contains:
                where.append(("DisplayName", "LIKE", f"%{name_contains}%"))
            if active_only:
                where.append(("Active", "=", True))
            rows = await get_client().query(
                build_query("Customer", where=where, order_by="DisplayName")
            )
            return envelope(
                source="Customer",
                rows=[
                    {
                        "id": row.get("Id"),
                        "name": row.get("DisplayName"),
                        "company": row.get("CompanyName"),
                        "balance": row.get("Balance"),
                        "active": row.get("Active"),
                    }
                    for row in rows
                ],
                max_rows=row_limit(limit),
            )

        return await execute("listing customers", run, tool="list_customers")

    @mcp.tool
    async def list_payments(
        ctx: Context,  # noqa: ARG001
        start_date: str | None = None,
        end_date: str | None = None,
        customer_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List payments received from customers, most recent first.

        Args:
            start_date: ISO date, inclusive.
            end_date: ISO date, inclusive.
            customer_id: QuickBooks customer Id.
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            where = _date_range("TxnDate", start_date, end_date)
            if customer_id:
                where.append(("CustomerRef", "=", customer_id))
            rows = await get_client().query(
                build_query("Payment", where=where, order_by="TxnDate", descending=True)
            )
            return envelope(
                source="Payment",
                rows=[
                    {
                        "id": row.get("Id"),
                        "date": row.get("TxnDate"),
                        "customer": ref_name(row.get("CustomerRef")),
                        "amount": row.get("TotalAmt"),
                        "unapplied": row.get("UnappliedAmt"),
                        "deposit_to": ref_name(row.get("DepositToAccountRef")),
                    }
                    for row in rows
                ],
                max_rows=row_limit(limit),
                period=(start_date, end_date),
            )

        return await execute("listing payments", run, tool="list_payments")
