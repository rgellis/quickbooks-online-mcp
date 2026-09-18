"""Report tools.

Every report is flattened out of QuickBooks' nested tree and checked before it
is returned. A report whose stated total its own line items disprove raises
rather than being handed back: that exact failure -- an expense total of 0.00
against line items summing to tens of thousands -- is what this package was
written to prevent.

``get_report`` reaches all 29 reports. The four named tools exist because they
are what people actually ask for, and because their parameters differ enough
that spelling them out is worth more than the uniformity.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP
from qbo.reports import check_report, parse_report

from src.client import get_client
from src.services.base import execute
from src.shaping import envelope

__all__ = ["register_report_tools"]

#: Reports summarise by period; QuickBooks accepts these values.
_SUMMARIZE = "Total, Month, Week, Days, Quarter, Year, Customers, Vendors, Classes"


def _rows(report: Any) -> list[dict[str, Any]]:
    """Flatten a parsed report into rows a reader can scan."""
    return [
        {
            "label": row.label,
            "section": " / ".join(row.path[:-1]),
            "is_total": row.is_summary,
            "account_id": row.account_id,
            **{key: value for key, value in row.values.items()},
        }
        for row in report.rows
    ]


def _report_envelope(report: Any, *, name: str, max_rows: int | None) -> dict[str, Any]:
    return envelope(
        source=f"reports/{name}",
        rows=_rows(report),
        max_rows=max_rows,
        period=(
            report.start_period.isoformat() if report.start_period else None,
            report.end_period.isoformat() if report.end_period else None,
        ),
        extra={
            "report": report.name or name,
            "currency": report.currency,
            "accounting_method": report.basis,
            "summarized_by": report.summarize_by,
            "columns": [c.title or c.key for c in report.columns],
            "invariants": "passed",
        },
    )


def register_report_tools(mcp: FastMCP[Any]) -> None:
    @mcp.tool
    async def get_report(
        ctx: Context,  # noqa: ARG001
        name: str,
        start_date: str | None = None,
        end_date: str | None = None,
        accounting_method: str | None = None,
        summarize_column_by: str | None = None,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Run any QuickBooks report and return its rows, arithmetic checked.

        Call list_reports for the available names. Either the documented name
        or the route name works -- nine of them differ, and using the wrong one
        against the API directly returns HTTP 400.

        The report is refused if it contradicts itself: a section total its own
        line items disprove, a balance sheet whose assets do not equal
        liabilities plus equity, or period columns that do not sum to their own
        total. That is deliberate. A number that fails its own arithmetic is
        wrong, and returning it with a caveat puts the burden of noticing on
        whoever reads it.

        Args:
            name: Report name, e.g. ProfitAndLoss, BalanceSheet, APAgingDetail.
            start_date: ISO date, for reports covering a period.
            end_date: ISO date.
            accounting_method: "Accrual" or "Cash". Defaults to the company's
                own setting, which is usually what you want.
            summarize_column_by: One of {summarize}. Month produces a column per
                month plus a total, and those columns are checked to sum.
            params: Any further report parameters, passed through untouched.
            limit: Rows returned in full; defaults to the configured cap.
        """

        async def run() -> dict[str, Any]:
            options: dict[str, str] = dict(params or {})
            for key, value in (
                ("start_date", start_date),
                ("end_date", end_date),
                ("accounting_method", accounting_method),
                ("summarize_column_by", summarize_column_by),
            ):
                if value:
                    options[key] = value
            payload = await get_client().report(name, **options)
            report = check_report(parse_report(payload))
            return _report_envelope(report, name=name, max_rows=limit)

        return await execute(f"running the {name} report", run, tool="get_report")

    get_report.__doc__ = (get_report.__doc__ or "").replace("{summarize}", _SUMMARIZE)

    @mcp.tool
    async def get_profit_and_loss(
        ctx: Context,  # noqa: ARG001
        start_date: str,
        end_date: str,
        accounting_method: str | None = None,
        summarize_column_by: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Profit and loss for a period, with its arithmetic checked.

        Args:
            start_date: ISO date, inclusive.
            end_date: ISO date, inclusive.
            accounting_method: "Accrual" or "Cash".
            summarize_column_by: "Month" for a column per month plus a total.
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            options: dict[str, str] = {"start_date": start_date, "end_date": end_date}
            if accounting_method:
                options["accounting_method"] = accounting_method
            if summarize_column_by:
                options["summarize_column_by"] = summarize_column_by
            payload = await get_client().report("ProfitAndLoss", **options)
            report = check_report(parse_report(payload))
            return _report_envelope(report, name="ProfitAndLoss", max_rows=limit)

        return await execute(
            "running the profit and loss report", run, tool="get_profit_and_loss"
        )

    @mcp.tool
    async def get_balance_sheet(
        ctx: Context,  # noqa: ARG001
        as_of: str,
        accounting_method: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Balance sheet as at a date, checked to balance.

        Assets are verified against liabilities plus equity two ways: against
        the total QuickBooks states, and against those two sections summing to
        it. A sheet can agree with itself at the top while its own equity rows
        contradict the total beneath, which is one of the failures that
        prompted this package.

        Args:
            as_of: ISO date the balance sheet is drawn at.
            accounting_method: "Accrual" or "Cash".
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            options: dict[str, str] = {"start_date": as_of, "end_date": as_of}
            if accounting_method:
                options["accounting_method"] = accounting_method
            payload = await get_client().report("BalanceSheet", **options)
            report = check_report(parse_report(payload))
            return _report_envelope(report, name="BalanceSheet", max_rows=limit)

        return await execute("running the balance sheet", run, tool="get_balance_sheet")

    @mcp.tool
    async def get_general_ledger(
        ctx: Context,  # noqa: ARG001
        start_date: str,
        end_date: str,
        accounting_method: str | None = None,
        columns: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """General ledger for a period: every transaction, by account.

        The most detailed report available, and usually large -- narrow the
        dates rather than raising the limit.

        Args:
            start_date: ISO date, inclusive.
            end_date: ISO date, inclusive.
            accounting_method: "Accrual" or "Cash".
            columns: Comma-separated column keys, e.g.
                "tx_date,txn_type,doc_num,name,memo,split_acc,subt_nat_amount".
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            options: dict[str, str] = {"start_date": start_date, "end_date": end_date}
            if accounting_method:
                options["accounting_method"] = accounting_method
            if columns:
                options["columns"] = columns
            payload = await get_client().report("GeneralLedger", **options)
            report = check_report(parse_report(payload))
            return _report_envelope(report, name="GeneralLedger", max_rows=limit)

        return await execute(
            "running the general ledger", run, tool="get_general_ledger"
        )

    @mcp.tool
    async def get_trial_balance(
        ctx: Context,  # noqa: ARG001
        start_date: str,
        end_date: str,
        accounting_method: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Trial balance for a period: debits and credits by account.

        Args:
            start_date: ISO date, inclusive.
            end_date: ISO date, inclusive.
            accounting_method: "Accrual" or "Cash".
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            options: dict[str, str] = {"start_date": start_date, "end_date": end_date}
            if accounting_method:
                options["accounting_method"] = accounting_method
            payload = await get_client().report("TrialBalance", **options)
            report = check_report(parse_report(payload))
            return _report_envelope(report, name="TrialBalance", max_rows=limit)

        return await execute("running the trial balance", run, tool="get_trial_balance")
