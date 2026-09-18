"""Entry point for the QuickBooks Online MCP server.

Two deliberate choices, both mirroring the Search Console server this is
modelled on:

1. Tools are registered directly onto one FastMCP instance rather than mounted
   as prefixed sub-servers. Prefixed mounts exist to keep hundreds of
   same-named service tools apart; with this many uniquely-named tools the
   indirection would buy nothing and would turn ``list_invoices`` into
   ``sales_list_invoices``.
2. Nothing happens at import time. Registration, configuration and argument
   parsing all sit behind ``main()``, so the test suite can import this module
   and assert against the real server without needing credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, Callable, Dict

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from src.auth import allow_list_configured, build_auth
from src.services.accounts_service import register_account_tools
from src.services.core_service import register_core_tools
from src.services.expenses_service import register_expense_tools
from src.services.reports_service import register_report_tools
from src.services.sales_service import register_sales_tools
from src.services.sync_service import register_sync_tools
from src.services.write_service import register_write_tools
from src.settings import load_settings

#: Tool groups, selectable with --groups. A deployment that has no business
#: writing can omit the write group entirely, which is a stronger guarantee
#: than a flag: the tools are not registered, so there is nothing to call.
TOOL_GROUPS: Dict[str, Callable[[FastMCP[Any]], None]] = {
    "core": register_core_tools,
    "accounts": register_account_tools,
    "reports": register_report_tools,
    "sales": register_sales_tools,
    "expenses": register_expense_tools,
    "sync": register_sync_tools,
    "writes": register_write_tools,
}


def build_server(groups: list[str] | None = None, *, auth: Any = None) -> FastMCP[Any]:
    """Construct the server with the requested tool groups registered.

    Args:
        groups: Group names, or None/["all"] for every group.
        auth: An authentication provider, or None for an unauthenticated
            server. Built from the environment by ``main``.

    Returns:
        A configured server. No QuickBooks call has been made and no credential
        has been read at this point.
    """
    mcp: FastMCP[Any] = FastMCP("QuickBooks Online", auth=auth)
    selected = (
        list(TOOL_GROUPS)
        if not groups or "all" in groups
        else [g for g in groups if g in TOOL_GROUPS]
    )
    unknown = [g for g in (groups or []) if g not in TOOL_GROUPS and g != "all"]
    if unknown:
        raise SystemExit(
            f"Unknown tool group(s): {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(TOOL_GROUPS))}, or 'all'."
        )
    for name in selected:
        TOOL_GROUPS[name](mcp)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> PlainTextResponse:  # noqa: ARG001
        """Liveness, for a load balancer.

        Deliberately unauthenticated and deliberately shallow: it reports that
        the process is up and serving, not that QuickBooks is reachable. A
        health check that called the API would fail the instance out of service
        during an Intuit outage, and replacing it would not help. Use
        check_connection to find out whether QuickBooks is answering.
        """
        return PlainTextResponse("OK")

    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--groups",
        default="all",
        help=(
            "Comma-separated tool groups to register, or 'all'. "
            f"Available: {', '.join(sorted(TOOL_GROUPS))}."
        ),
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print the registered tools and exit, without starting the server.",
    )
    args = parser.parse_args(argv)

    if args.list_tools:
        # Listing tools needs neither credentials nor an identity provider.
        server = build_server([g.strip() for g in args.groups.split(",") if g.strip()])
        for tool in sorted(asyncio.run(server.list_tools()), key=lambda t: t.name):
            summary = (tool.description or "").strip().splitlines()
            print(f"{tool.name:24} {summary[0] if summary else ''}")
        return 0

    settings = load_settings()
    auth = build_auth(settings)
    server = build_server(
        [g.strip() for g in args.groups.split(",") if g.strip()], auth=auth
    )

    if auth is None and settings.transport == "http":
        print(
            "WARNING: serving HTTP with MCP_AUTH=none. Anyone who can reach "
            "this port has full access to the company's books.",
            file=sys.stderr,
        )
    if auth is not None and settings.auth == "oidc" and not allow_list_configured():
        print(
            "WARNING: MCP_AUTH=oidc with no MCP_OIDC_ALLOWED_SUBJECTS. Anyone "
            "the provider will authenticate can use this server. That is fine "
            "for an organisational provider, where being in the app is the "
            "permission -- and wrong for a consumer one like Intuit, where "
            "any account in the world completes sign-in and Intuit exposes no "
            "way to ask whether they have anything to do with this company.",
            file=sys.stderr,
        )
    if auth is not None and not settings.read_only:
        print(
            "NOTE: every authenticated caller has the same access this "
            "server's QuickBooks token has, including writes. QuickBooks "
            "issues one token per app per company and cannot scope it to a "
            "user. To give someone less, deploy a second instance with "
            "QBO_READ_ONLY=true or a narrower --groups.",
            file=sys.stderr,
        )

    if settings.transport == "http":
        server.run(transport="http", host=settings.host, port=settings.port)
    else:
        server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
