"""Building QuickBooks queries safely.

QuickBooks' query language looks like SQL and is not. There are no joins, no
aggregates, no parameter binding -- a filter value is interpolated into the
statement as a literal. That makes escaping this module's job: a customer named
``O'Brien`` will otherwise terminate the string early and produce either a
syntax error or, worse, a query that runs and means something else.

There is no prepared-statement facility to fall back on, so every value that
reaches a statement goes through :func:`literal`.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Sequence, cast

from qbo.entities import ENTITIES

__all__ = ["literal", "build_query", "QueryError"]


class QueryError(ValueError):
    """A query could not be built from the arguments given."""


def _check_field(entity: str, field: str, *, sorting: bool) -> None:
    """Refuse a field QuickBooks will not filter or sort on.

    This is not defensive tidiness. QuickBooks does not consistently reject a
    query that filters on a field it has not marked filterable -- sometimes it
    returns HTTP 400, and sometimes it returns **zero rows**. Filtering
    Purchase by PaymentType is the second kind: 202 credit card purchases in
    the ledger, and the query answers "none", with no error anywhere.

    A silently empty result is worse than a refusal, because it reads as a
    fact. So the documented capability is checked before the query is sent.
    """
    spec = ENTITIES.get(entity)
    if spec is None:
        return
    allowed = spec.sortable if sorting else spec.filterable
    if not allowed or field in allowed:
        return
    verb = "sort by" if sorting else "filter on"
    raise QueryError(
        f"QuickBooks will not {verb} {entity}.{field}. It is not marked "
        f"{'sortable' if sorting else 'filterable'} in Intuit's documentation, "
        "and querying it anyway returns either an error or an empty result "
        "that looks like a genuine answer. "
        f"Fields that can be used: {', '.join(allowed)}."
    )


def literal(value: Any) -> str:
    """Render a Python value as a QuickBooks query literal.

    Single quotes are doubled, which is how the query language escapes them.
    Backslashes are not escape characters here and are left alone.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, date):
        return f"'{value.isoformat()}'"
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def _condition(entity: str, field: str, operator: str, value: Any) -> str:
    if not field.replace("_", "").replace(".", "").isalnum():
        raise QueryError(f"Not a usable field name: {field!r}")
    _check_field(entity, field, sorting=False)
    if operator.upper() == "IN":
        if not isinstance(value, (list, tuple, set)):
            raise QueryError("IN needs a list of values")
        items = ", ".join(literal(v) for v in cast("Sequence[Any]", value))
        return f"{field} IN ({items})"
    return f"{field} {operator} {literal(value)}"


def build_query(
    entity: str,
    *,
    where: Sequence[tuple[str, str, Any]] | None = None,
    order_by: str | None = None,
    descending: bool = False,
    select: str = "*",
) -> str:
    """Assemble a query from structured parts.

    Args:
        entity: The entity to select from.
        where: ``(field, operator, value)`` triples, combined with AND.
        order_by: Field to sort on.
        descending: Sort descending.
        select: Columns, or ``*``.

    Returns:
        A statement. Paging is not added -- the client handles that.
    """
    if not entity.isalnum():
        raise QueryError(f"Not a usable entity name: {entity!r}")

    statement = f"SELECT {select} FROM {entity}"
    conditions: Iterable[str] = (
        _condition(entity, field, operator, value)
        for field, operator, value in (where or ())
    )
    joined = " AND ".join(conditions)
    if joined:
        statement += f" WHERE {joined}"
    if order_by:
        if not order_by.replace("_", "").isalnum():
            raise QueryError(f"Not a usable sort field: {order_by!r}")
        _check_field(entity, order_by, sorting=True)
        statement += f" ORDER BY {order_by}" + (" DESC" if descending else "")
    return statement
