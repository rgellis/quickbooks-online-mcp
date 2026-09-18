"""Shared plumbing for tool implementations."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from src.client import current_settings
from src.identity import current_identity
from src.shaping import error_payload

__all__ = ["execute", "row_limit"]

logger = logging.getLogger(__name__)


async def execute(
    attempted: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    tool: str,
    write_entity: str | None = None,
) -> dict[str, Any]:
    """Run a tool body, recording mutations and describing any failure.

    ``write_entity`` is logged, not enforced. This server cannot grant one
    caller less than another: QuickBooks issues a single company-wide token per
    app, so every call carries the same access whoever made it. Refusing some
    callers here would look like QuickBooks permissions without being them.
    Access is decided by what this deployment is configured to do --
    ``QBO_READ_ONLY`` and which ``--groups`` are registered -- and by who is
    allowed to reach it at all.

    Any failure comes back described: what was attempted and what came back,
    redacted. An exception escaping a tool reaches the model as a stack trace,
    which tells it nothing actionable and may carry a credential.
    """
    if write_entity is not None:
        # Worth recording even though everyone who can call this could have.
        logger.info(
            "write: tool=%s entity=%s caller=%s",
            tool,
            write_entity,
            current_identity().label,
        )
    try:
        return await call()
    except Exception as exc:
        return error_payload(exc, attempted=attempted)


def row_limit(requested: int | None) -> int | None:
    """Resolve how many rows to return in full.

    None means the configured cap applies. An explicit limit is honoured as
    given -- a caller asking for 500 rows has said they want them.
    """
    if requested is None:
        return current_settings().max_rows
    if requested <= 0:
        return None
    return requested
