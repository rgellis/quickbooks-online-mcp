"""Declared mapping from API capability to the tool that exposes it.

This module is the contract behind the claim that the server covers the
Accounting API. ``tests/test_coverage.py`` checks it in both directions:

- every tool registered on the server appears here, so a tool cannot be added
  without saying what it is for;
- every entity and report the SDK knows about is reachable, whether through a
  tool of its own or through the generic ones.

The second direction is the one that matters. QuickBooks documents 43 entities
and 29 reports; a server with a tool for invoices and nothing for journal
entries would look complete and not be. Reachability through ``query_quickbooks``,
``get_entity``, ``create_entity`` and ``get_report`` is what makes the coverage
claim true, and the named tools are convenience on top of it.
"""

from __future__ import annotations

from typing import Final, Mapping

__all__ = ["TOOL_PURPOSE", "GENERIC_TOOLS", "WRITE_TOOLS"]

#: Every tool, and the capability it exposes.
TOOL_PURPOSE: Final[Mapping[str, str]] = {
    # core
    "check_connection": "CompanyInfo query; reports configuration and reachability",
    "query_quickbooks": "the query endpoint, for any entity and any filter",
    "get_entity": "read any entity by id",
    "list_entities": "the entity registry: what exists and what each supports",
    "describe_entity": "one entity's operations and field requirements",
    "list_reports": "the report registry, including route names that differ",
    # accounts
    "list_accounts": "Account query, the chart of accounts",
    # reports
    "get_report": "all 29 report endpoints, with invariants applied",
    "get_profit_and_loss": "reports/ProfitAndLoss",
    "get_balance_sheet": "reports/BalanceSheet",
    "get_general_ledger": "reports/GeneralLedger",
    "get_trial_balance": "reports/TrialBalance",
    # sales
    "list_invoices": "Invoice query",
    "list_customers": "Customer query",
    "list_payments": "Payment query",
    # expenses
    "list_bills": "Bill query",
    "list_vendors": "Vendor query",
    "list_purchases": "Purchase query",
    # sync
    "get_changes": "the cdc endpoint, including deletions",
    # writes
    "create_entity": "create, for any entity that documents it",
    "update_entity": "update, sparse or full",
    "delete_entity": "delete, for any entity that documents it",
    "void_transaction": "void, keeping the record at zero",
    "create_journal_entry": "JournalEntry create, with balance checked locally",
}

#: Tools that reach any entity, which is what makes coverage complete rather
#: than merely broad.
GENERIC_TOOLS: Final[Mapping[str, str]] = {
    "query_quickbooks": "QUERY",
    "get_entity": "READ",
    "create_entity": "CREATE",
    "update_entity": "UPDATE",
    "delete_entity": "DELETE",
    "get_report": "REPORT",
}

#: Tools that change the ledger. Registered only in the ``writes`` group, so a
#: deployment that omits the group has no way to call them at all -- which is a
#: stronger guarantee than a flag. The test asserts none has leaked elsewhere.
WRITE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "create_entity",
        "update_entity",
        "delete_entity",
        "void_transaction",
        "create_journal_entry",
    }
)
