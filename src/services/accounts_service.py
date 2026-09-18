"""Chart of accounts."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from src.client import get_client
from src.query import build_query
from src.services.base import execute, row_limit
from src.shaping import envelope

__all__ = ["register_account_tools"]


def register_account_tools(mcp: FastMCP[Any]) -> None:
    @mcp.tool
    async def list_accounts(
        ctx: Context,  # noqa: ARG001
        account_type: str | None = None,
        active_only: bool = True,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List the chart of accounts.

        Never infer what an account is for from its name. QuickBooks files
        routinely name a bank account "Receivable" or "Payable"; the account's
        actual role is in AcctNum and AccountType, and reading the name instead
        is how a bank balance gets reported as accounts receivable.

        Anything this tool does not cover goes through query_quickbooks rather
        than being added here as another parameter.

        Args:
            account_type: Filter by type, e.g. Bank, "Accounts Receivable",
                "Credit Card", Expense, Income. Call list_accounts with no
                filter first if unsure what types this company uses.
            active_only: Exclude deactivated accounts. QuickBooks never deletes
                an account; it marks it inactive, and inactive accounts still
                carry historical balances.
            limit: Rows returned in full; defaults to the configured cap.
        """

        async def run() -> dict[str, Any]:
            where: list[tuple[str, str, Any]] = []
            if account_type:
                where.append(("AccountType", "=", account_type))
            if active_only:
                where.append(("Active", "=", True))
            # Sorted by name, not number: QuickBooks refuses ORDER BY AcctNum
            # outright. Re-sorted below so the result reads like a chart of
            # accounts rather than an alphabetical list.
            rows = await get_client().query(
                build_query("Account", where=where, order_by="Name")
            )
            rows.sort(
                key=lambda row: (
                    str(row.get("AcctNum") or "~"),
                    str(row.get("Name") or ""),
                )
            )
            return envelope(
                source="Account",
                rows=[
                    {
                        "id": row.get("Id"),
                        "number": row.get("AcctNum"),
                        "name": row.get("Name"),
                        "type": row.get("AccountType"),
                        "subtype": row.get("AccountSubType"),
                        "balance": row.get("CurrentBalance"),
                        "active": row.get("Active"),
                    }
                    for row in rows
                ],
                max_rows=row_limit(limit),
            )

        return await execute("listing accounts", run, tool="list_accounts")
