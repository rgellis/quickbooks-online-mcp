"""Response shaping.

Raw API dumps are what made the manual version of this work expensive and
unreliable, so nothing here hands back an unbounded list and nothing hands back
a number without saying where it came from.

Every response carries:

``source``   which endpoint produced it
``asOf``     when it was read, so a stale answer is identifiable as stale
``period``   the date range it covers, when it covers one
``derived``  whether this package computed the figure or the API stated it

and, for anything list-shaped, ``count``/``returned``/``omitted`` so a truncated
answer can never be mistaken for a complete one.

Money is rendered as a decimal string, never a float. A float would be a
different number from the one in the ledger, and JSON gives no way to tell the
reader that happened.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Final, Mapping, Sequence, cast

from qbo.errors import QboApiError, QboInvariantError, redact

__all__ = ["jsonable", "envelope", "error_payload", "ref_name", "SOURCE"]

SOURCE: Final[str] = "QuickBooks Online Accounting API v3"


def jsonable(value: Any) -> Any:
    """Convert a decoded QuickBooks value into something JSON can carry.

    Decimals become strings rather than floats: a ledger amount that survives
    JSON as 2780.9199999999996 is a wrong number, and silently so.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        mapping = cast("Mapping[Any, Any]", value)
        return {str(key): jsonable(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast("Sequence[Any]", value)
        return [jsonable(item) for item in sequence]
    return value


def ref_name(value: Any) -> str | None:
    """The display name out of a QuickBooks ReferenceType, if it has one.

    References arrive as ``{"value": "24", "name": "Acme"}``, and the name is
    optional -- QuickBooks omits it on some responses. Returning None rather
    than an empty string keeps "absent" distinguishable from "blank".
    """
    if not isinstance(value, Mapping):
        return None
    name = cast("Mapping[str, Any]", value).get("name")
    return str(name) if name else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def envelope(
    *,
    source: str,
    rows: Sequence[Any] | None = None,
    data: Any = None,
    period: tuple[str | None, str | None] | None = None,
    derived: bool = False,
    max_rows: int | None = None,
    note: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a result with its provenance, truncating long lists honestly.

    Args:
        source: The endpoint or operation, appended to the API name.
        rows: A list-shaped result. Truncated to ``max_rows`` with the omission
            stated in the response, never silently.
        data: A single-object result, used instead of ``rows``.
        period: Start and end of the range covered, when there is one.
        derived: True when this package computed the value rather than reading
            it from the API. Anything derived must say so.
        max_rows: Truncation threshold. None returns every row, which callers
            request explicitly by passing a limit.
        note: Additional context worth stating alongside the result.
        extra: Further top-level fields, already JSON-safe.
    """
    payload: dict[str, Any] = {
        "source": f"{SOURCE} · {source}",
        "asOf": _now(),
        "derived": derived,
    }
    if period is not None:
        payload["period"] = {"start": period[0], "end": period[1]}

    if rows is not None:
        total = len(rows)
        shown = list(rows) if max_rows is None else list(rows[:max_rows])
        payload["count"] = total
        payload["returned"] = len(shown)
        if len(shown) < total:
            payload["omitted"] = total - len(shown)
            payload["note"] = (
                f"{total} rows matched; the first {len(shown)} are listed. "
                "Pass a higher limit, or narrow the query, to see the rest."
            )
        payload["rows"] = jsonable(shown)
    elif data is not None:
        payload["data"] = jsonable(data)

    if note:
        payload["note"] = note if "note" not in payload else f"{payload['note']} {note}"
    if extra:
        payload.update(jsonable(dict(extra)))
    return payload


def error_payload(exc: Exception, *, attempted: str) -> dict[str, Any]:
    """Describe a failure without leaking a credential or a stack trace.

    An invariant failure is reported as an error, not a caveat attached to a
    number. The whole point is that the number should not be returned.
    """
    payload: dict[str, Any] = {
        "error": type(exc).__name__,
        "attempted": attempted,
        "message": str(redact(str(exc))),
        "asOf": _now(),
    }
    if isinstance(exc, QboInvariantError):
        payload["kind"] = "invariant"
        payload["meaning"] = (
            "QuickBooks returned a report that contradicts itself. The figure "
            "was withheld rather than returned, because a total its own line "
            "items disprove is a wrong number, not a caveat."
        )
    elif isinstance(exc, QboApiError):
        payload["kind"] = "api"
        payload["status"] = exc.status_code
        if exc.intuit_tid:
            # The first thing Intuit support asks for.
            payload["intuit_tid"] = exc.intuit_tid
    else:
        payload["kind"] = "client"
    return payload
