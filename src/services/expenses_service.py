"""Expense-side tools: bills, vendors, purchases."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from src.client import get_client
from src.query import build_query
from src.services.base import execute, row_limit
from src.shaping import envelope, ref_name

__all__ = ["register_expense_tools"]


def _date_range(
    field: str, start: str | None, end: str | None
) -> list[tuple[str, str, Any]]:
    where: list[tuple[str, str, Any]] = []
    if start:
        where.append((field, ">=", start))
    if end:
        where.append((field, "<=", end))
    return where


def register_expense_tools(mcp: FastMCP[Any]) -> None:
    @mcp.tool
    async def list_bills(
        ctx: Context,  # noqa: ARG001
        start_date: str | None = None,
        end_date: str | None = None,
        vendor_id: str | None = None,
        unpaid_only: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List vendor bills, most recent first.

        Anything this tool does not cover goes through query_quickbooks rather
        than being added here as another parameter.

        Args:
            start_date: ISO date, inclusive.
            end_date: ISO date, inclusive.
            vendor_id: QuickBooks vendor Id, not a name. Find it with
                list_vendors.
            unpaid_only: Only bills with an outstanding balance.
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            where = _date_range("TxnDate", start_date, end_date)
            if vendor_id:
                where.append(("VendorRef", "=", vendor_id))
            if unpaid_only:
                where.append(("Balance", ">", 0))
            rows = await get_client().query(
                build_query("Bill", where=where, order_by="TxnDate", descending=True)
            )
            return envelope(
                source="Bill",
                rows=[
                    {
                        "id": row.get("Id"),
                        "doc_number": row.get("DocNumber"),
                        "date": row.get("TxnDate"),
                        "due": row.get("DueDate"),
                        "vendor": ref_name(row.get("VendorRef")),
                        "total": row.get("TotalAmt"),
                        "balance": row.get("Balance"),
                    }
                    for row in rows
                ],
                max_rows=row_limit(limit),
                period=(start_date, end_date),
            )

        return await execute("listing bills", run, tool="list_bills")

    @mcp.tool
    async def list_vendors(
        ctx: Context,  # noqa: ARG001
        name_contains: str | None = None,
        active_only: bool = True,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List vendors.

        Args:
            name_contains: Match on display name; case-sensitive.
            active_only: Exclude deactivated vendors.
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            where: list[tuple[str, str, Any]] = []
            if name_contains:
                where.append(("DisplayName", "LIKE", f"%{name_contains}%"))
            if active_only:
                where.append(("Active", "=", True))
            rows = await get_client().query(
                build_query("Vendor", where=where, order_by="DisplayName")
            )
            return envelope(
                source="Vendor",
                rows=[
                    {
                        "id": row.get("Id"),
                        "name": row.get("DisplayName"),
                        "company": row.get("CompanyName"),
                        "balance": row.get("Balance"),
                        "vendor_1099": row.get("Vendor1099"),
                        "active": row.get("Active"),
                    }
                    for row in rows
                ],
                max_rows=row_limit(limit),
            )

        return await execute("listing vendors", run, tool="list_vendors")

    @mcp.tool
    async def list_purchases(
        ctx: Context,  # noqa: ARG001
        start_date: str | None = None,
        end_date: str | None = None,
        payment_type: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List purchases: expenses paid directly, rather than billed first.

        A Purchase is money out that did not go through a Bill -- a card
        charge, a cheque, a cash expense. Bills and purchases are separate
        entities in QuickBooks and neither includes the other, so a complete
        picture of spending needs both.

        Args:
            start_date: ISO date, inclusive.
            end_date: ISO date, inclusive.
            payment_type: "Cash", "Check" or "CreditCard".
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            where = _date_range("TxnDate", start_date, end_date)
            rows = await get_client().query(
                build_query(
                    "Purchase", where=where, order_by="TxnDate", descending=True
                )
            )
            if payment_type:
                # Filtered here, not in the query. QuickBooks accepts
                # `WHERE PaymentType = ...` and returns zero rows regardless of
                # what the ledger holds -- no error, just a wrong answer. The
                # documentation does not list PaymentType as filterable, and
                # that turns out to be exactly right.
                wanted = payment_type.strip().lower()
                rows = [
                    row
                    for row in rows
                    if str(row.get("PaymentType", "")).lower() == wanted
                ]
            return envelope(
                source="Purchase",
                rows=[
                    {
                        "id": row.get("Id"),
                        "date": row.get("TxnDate"),
                        "payment_type": row.get("PaymentType"),
                        "account": ref_name(row.get("AccountRef")),
                        "payee": ref_name(row.get("EntityRef")),
                        "total": row.get("TotalAmt"),
                        "memo": row.get("PrivateNote"),
                    }
                    for row in rows
                ],
                max_rows=row_limit(limit),
                period=(start_date, end_date),
            )

        return await execute("listing purchases", run, tool="list_purchases")
