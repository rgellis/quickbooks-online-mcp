"""Configuration, read once from the environment.

This server talks to exactly one QuickBooks company. Multi-company means
running it more than once, which keeps each deployment's credentials, token
store and read-only posture entirely separate -- there is no code path where
one company's realm can be reached with another's token.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = ["Settings", "load_settings", "MissingConfiguration"]

DEFAULT_TOKEN_STORE: Final[str] = str(
    Path.home() / ".quickbooks-online" / "tokens.json"
)
#: Rows returned in full before a response is summarised instead. Raw dumps are
#: what made the manual version of this work expensive and unreliable.
DEFAULT_MAX_ROWS: Final[int] = 50


class MissingConfiguration(RuntimeError):
    """A required setting is absent.

    Raised at first use rather than import, so the server still starts and can
    report the problem through a tool call instead of dying before any client
    can ask what is wrong.
    """


@dataclass(frozen=True)
class Settings:
    """Everything this server needs to know."""

    client_id: str
    client_secret: str
    realm_id: str
    token_store: str
    read_only: bool
    minor_version: int | None
    max_rows: int
    transport: str
    host: str
    port: int
    auth: str


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise MissingConfiguration(
            f"{name} must be a whole number, got {raw!r}"
        ) from exc


def load_settings() -> Settings:
    """Read configuration from the environment.

    Raises:
        MissingConfiguration: naming every absent variable at once, rather than
            one per attempt.
    """
    required = {
        "QBO_CLIENT_ID": os.environ.get("QBO_CLIENT_ID", "").strip(),
        "QBO_CLIENT_SECRET": os.environ.get("QBO_CLIENT_SECRET", "").strip(),
        "QBO_REALM_ID": os.environ.get("QBO_REALM_ID", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise MissingConfiguration(
            "Not configured: " + ", ".join(missing) + ". "
            "Copy .env.example to .env and fill it in. Credentials come from "
            "your own Intuit app -- see the README."
        )

    max_rows = _int("QBO_MAX_ROWS") or DEFAULT_MAX_ROWS
    if max_rows < 1:
        raise MissingConfiguration("QBO_MAX_ROWS must be at least 1")

    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower() or "stdio"
    if transport not in {"stdio", "http"}:
        raise MissingConfiguration(
            f"MCP_TRANSPORT must be 'stdio' or 'http', got {transport!r}"
        )
    auth = os.environ.get("MCP_AUTH", "none").strip().lower() or "none"

    return Settings(
        client_id=required["QBO_CLIENT_ID"],
        client_secret=required["QBO_CLIENT_SECRET"],
        realm_id=required["QBO_REALM_ID"],
        token_store=os.environ.get("QBO_TOKEN_STORE", "").strip()
        or DEFAULT_TOKEN_STORE,
        # Defaults to refusing writes. A ledger is the wrong place for a
        # permissive default, and an operator who wants writes can say so.
        read_only=_flag("QBO_READ_ONLY", True),
        minor_version=_int("QBO_MINOR_VERSION"),
        max_rows=max_rows,
        transport=transport,
        host=os.environ.get("MCP_HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=_int("MCP_PORT") or 8080,
        auth=auth,
    )
