"""Change tracking."""

from __future__ import annotations

from typing import Any, cast

from fastmcp import Context, FastMCP
from qbo.entities import ENTITIES

from src.client import get_client
from src.services.base import execute, row_limit
from src.shaping import envelope

__all__ = ["register_sync_tools"]

#: Intuit caps how far back change data capture will look.
CDC_LOOKBACK_DAYS = 30

#: Paging metadata that sits alongside the entity arrays in a CDC response.
_ENVELOPE_KEYS = frozenset({"startPosition", "maxResults", "totalCount"})


def _as_dict(value: Any) -> dict[str, Any]:
    """Narrow a decoded JSON value to an object, or an empty one."""
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in cast("dict[Any, Any]", value).items()}


def _as_list(value: Any) -> list[Any]:
    """Narrow a decoded JSON value to an array, or an empty one."""
    if not isinstance(value, list):
        return []
    return list(cast("list[Any]", value))


def register_sync_tools(mcp: FastMCP[Any]) -> None:
    @mcp.tool
    async def get_changes(
        ctx: Context,  # noqa: ARG001
        entities: list[str],
        changed_since: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List records that changed since a timestamp, including deletions.

        The right tool for "what has happened since I last looked". It reports
        deletions as well as changes, which a query cannot -- a query returns
        what exists now, so a voided or deleted transaction simply disappears
        from the results with no indication it was ever there.

        QuickBooks will not look back more than 30 days. Beyond that, query by
        date range instead and accept that deletions will be invisible.

        Timestamps are UTC. If the business runs in another timezone, resolve
        the boundary there first or the edges of the window will be wrong.

        Args:
            entities: Entity names to watch, e.g. ["Invoice", "Payment"].
            changed_since: ISO 8601 timestamp, e.g. "2026-09-01T00:00:00-04:00".
            limit: Rows returned in full.
        """

        async def run() -> dict[str, Any]:
            unknown = [name for name in entities if name not in ENTITIES]
            if unknown:
                raise KeyError(
                    f"Not entities this API exposes: {', '.join(unknown)}. "
                    "Call list_entities to see what is available."
                )
            if not entities:
                raise ValueError("Name at least one entity to watch.")

            payload = await get_client().cdc(entities, changed_since)
            rows: list[dict[str, Any]] = []
            for raw_response in _as_list(payload.get("CDCResponse")):
                response = _as_dict(raw_response)
                for raw_block in _as_list(response.get("QueryResponse")):
                    block = _as_dict(raw_block)
                    for key, raw_records in block.items():
                        if key in _ENVELOPE_KEYS:
                            continue
                        for raw_record in _as_list(raw_records):
                            record = _as_dict(raw_record)
                            if not record:
                                continue
                            metadata = _as_dict(record.get("MetaData"))
                            rows.append(
                                {
                                    "entity": key,
                                    "id": record.get("Id"),
                                    # Absent status means the record was changed
                                    # rather than removed; QuickBooks only marks
                                    # the removals.
                                    "change": record.get("status") or "Updated",
                                    "last_updated": metadata.get("LastUpdatedTime"),
                                }
                            )
            return envelope(
                source=f"cdc · {', '.join(entities)}",
                rows=rows,
                max_rows=row_limit(limit),
                note=(
                    f"QuickBooks looks back at most {CDC_LOOKBACK_DAYS} days. "
                    "Deletions appear here with change='Deleted' and cannot be "
                    "seen any other way."
                ),
            )

        return await execute("reading changes", run, tool="get_changes")
