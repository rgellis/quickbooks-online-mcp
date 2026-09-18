"""Tools that change the ledger.

Three things stand between a tool call and a modified book, and none of them
is the caller's identity:

1. **Whether these tools exist here at all.** They register only in the
   ``writes`` group, so a deployment that omits it cannot call them.
2. ``QBO_READ_ONLY`` -- refuses every write in the client, before a request is
   built. A deployment with no business writing sets this and nothing else
   matters.
3. QuickBooks' own validation -- SyncToken concurrency, required fields.

There is deliberately no per-caller check. QuickBooks issues one token per app
per company and cannot tell callers apart, so a gate here would resemble
QuickBooks permissions without being them. Two access levels means two
deployments.

Delete and void are separate, individually named tools rather than an
``operation`` parameter on a generic write. A model reaching for
``delete_entity`` has had to decide to delete something; one passing
``operation="delete"`` to a general-purpose tool may not have.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fastmcp import Context, FastMCP
from qbo.entities import ENTITIES, Operation

from src.client import get_client
from src.services.base import execute
from src.shaping import envelope

__all__ = ["register_write_tools"]


def _require_operation(entity: str, operation: Operation) -> None:
    spec = ENTITIES.get(entity)
    if spec is None:
        raise KeyError(
            f"{entity!r} is not an entity this API exposes. "
            "Call list_entities to see what is available."
        )
    if operation not in spec.operations:
        raise ValueError(
            f"QuickBooks does not support {operation} on {entity}. "
            f"It accepts: {', '.join(sorted(str(o) for o in spec.operations))}. "
            "Accounts, for instance, are deactivated by update, never deleted."
        )


def register_write_tools(mcp: FastMCP[Any]) -> None:
    @mcp.tool
    async def create_entity(
        ctx: Context,  # noqa: ARG001
        entity: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a record.

        Call describe_entity first to learn what the entity requires -- the
        requirements differ per entity and QuickBooks rejects an incomplete
        payload with a fault that does not always name the missing field.

        Args:
            entity: Documented entity name, e.g. Invoice, Bill, Customer.
            data: The record, using QuickBooks' own field names.
        """

        async def run() -> dict[str, Any]:
            _require_operation(entity, Operation.CREATE)
            created = await get_client().create(entity, data)
            return envelope(
                source=f"{entity} · create",
                data=created,
                note=f"Created {entity} {created.get('Id')}.",
            )

        return await execute(
            f"creating a {entity}", run, tool="create_entity", write_entity=entity
        )

    @mcp.tool
    async def update_entity(
        ctx: Context,  # noqa: ARG001
        entity: str,
        data: dict[str, Any],
        sparse: bool = True,
    ) -> dict[str, Any]:
        """Update a record.

        Both Id and SyncToken are required, and SyncToken must be the current
        one -- it is QuickBooks' concurrency check, and a stale value is
        rejected rather than merged. Read the record first to get it.

        Args:
            entity: Documented entity name.
            data: Fields to change, including Id and SyncToken.
            sparse: True changes only the fields supplied. False replaces the
                whole record, clearing anything omitted -- which is almost
                never what is wanted, and is irreversible.
        """

        async def run() -> dict[str, Any]:
            _require_operation(entity, Operation.UPDATE)
            updated = await get_client().update(entity, data, sparse=sparse)
            return envelope(
                source=f"{entity} · update",
                data=updated,
                note=(
                    f"Updated {entity} {updated.get('Id')}"
                    + ("." if sparse else ", replacing every field.")
                ),
            )

        return await execute(
            f"updating {entity} {data.get('Id')}",
            run,
            tool="update_entity",
            write_entity=entity,
        )

    @mcp.tool
    async def delete_entity(
        ctx: Context,  # noqa: ARG001
        entity: str,
        entity_id: str,
        sync_token: str,
    ) -> dict[str, Any]:
        """Delete a record. This cannot be undone.

        Most name-list entities cannot be deleted at all -- accounts, customers
        and vendors are deactivated by update instead, because deleting one
        would orphan the transactions referring to it. For a transaction that
        has already been reported on, voiding is usually correct: it keeps the
        record and its number while zeroing the amounts, so the audit trail
        survives.

        Args:
            entity: Documented entity name.
            entity_id: The record's Id.
            sync_token: The record's current SyncToken.
        """

        async def run() -> dict[str, Any]:
            _require_operation(entity, Operation.DELETE)
            result = await get_client().delete(
                entity, {"Id": entity_id, "SyncToken": sync_token}
            )
            return envelope(
                source=f"{entity} · delete",
                data=result,
                note=f"Deleted {entity} {entity_id}. This cannot be undone.",
            )

        return await execute(
            f"deleting {entity} {entity_id}",
            run,
            tool="delete_entity",
            write_entity=entity,
        )

    @mcp.tool
    async def void_transaction(
        ctx: Context,  # noqa: ARG001
        entity: str,
        entity_id: str,
        sync_token: str,
    ) -> dict[str, Any]:
        """Void a transaction, keeping the record with zeroed amounts.

        Preferable to deleting anything that has been reported on: the document
        and its number remain, so the sequence has no gap and the audit trail
        is intact.

        Args:
            entity: A transaction entity, e.g. Invoice, Payment, BillPayment.
            entity_id: The record's Id.
            sync_token: The record's current SyncToken.
        """

        async def run() -> dict[str, Any]:
            _require_operation(entity, Operation.UPDATE)
            result = await get_client().void(
                entity, {"Id": entity_id, "SyncToken": sync_token}
            )
            return envelope(
                source=f"{entity} · void",
                data=result,
                note=f"Voided {entity} {entity_id}; the record remains at zero.",
            )

        return await execute(
            f"voiding {entity} {entity_id}",
            run,
            tool="void_transaction",
            write_entity=entity,
        )

    @mcp.tool
    async def create_journal_entry(
        ctx: Context,  # noqa: ARG001
        lines: list[dict[str, Any]],
        txn_date: str | None = None,
        private_note: str | None = None,
        doc_number: str | None = None,
    ) -> dict[str, Any]:
        """Post a journal entry.

        Debits must equal credits. That is checked here before anything is
        sent, because QuickBooks' own rejection does not say which side is
        short, and an unbalanced entry is the single most common mistake.

        Args:
            lines: One per posting, each with:
                ``account_id`` (from list_accounts), ``amount``,
                ``posting_type`` ("Debit" or "Credit"), and optionally
                ``description``, ``class_id``, ``customer_id``.
            txn_date: ISO date. Defaults to today in the company's timezone.
            private_note: Memo on the entry. Worth writing: a journal entry
                with no explanation is unreadable a month later.
            doc_number: Reference number, if the company uses them.
        """

        async def run() -> dict[str, Any]:
            if not lines:
                raise ValueError("A journal entry needs at least two lines.")

            totals = {"Debit": Decimal("0"), "Credit": Decimal("0")}
            detail_lines: list[dict[str, Any]] = []
            for index, line in enumerate(lines, start=1):
                posting = str(line.get("posting_type", "")).capitalize()
                if posting not in totals:
                    raise ValueError(
                        f"Line {index}: posting_type must be 'Debit' or 'Credit', "
                        f"got {line.get('posting_type')!r}"
                    )
                account_id = line.get("account_id")
                if not account_id:
                    raise ValueError(f"Line {index}: account_id is required.")
                try:
                    amount = Decimal(str(line.get("amount")))
                except (InvalidOperation, TypeError) as exc:
                    raise ValueError(
                        f"Line {index}: amount {line.get('amount')!r} is not a number."
                    ) from exc
                if amount <= 0:
                    raise ValueError(
                        f"Line {index}: amount must be positive. Direction is set by "
                        "posting_type, not by a negative number."
                    )
                totals[posting] += amount

                detail: dict[str, Any] = {
                    "PostingType": posting,
                    "AccountRef": {"value": str(account_id)},
                }
                if line.get("class_id"):
                    detail["ClassRef"] = {"value": str(line["class_id"])}
                if line.get("customer_id"):
                    detail["Entity"] = {
                        "Type": "Customer",
                        "EntityRef": {"value": str(line["customer_id"])},
                    }
                entry: dict[str, Any] = {
                    "DetailType": "JournalEntryLineDetail",
                    "Amount": str(amount),
                    "JournalEntryLineDetail": detail,
                }
                if line.get("description"):
                    entry["Description"] = str(line["description"])
                detail_lines.append(entry)

            if totals["Debit"] != totals["Credit"]:
                difference = abs(totals["Debit"] - totals["Credit"])
                raise ValueError(
                    f"Journal entry does not balance: debits {totals['Debit']:,.2f} "
                    f"against credits {totals['Credit']:,.2f}, a difference of "
                    f"{difference:,.2f}. Nothing was sent."
                )

            payload: dict[str, Any] = {"Line": detail_lines}
            if txn_date:
                payload["TxnDate"] = txn_date
            if private_note:
                payload["PrivateNote"] = private_note
            if doc_number:
                payload["DocNumber"] = doc_number

            created = await get_client().create("JournalEntry", payload)
            return envelope(
                source="JournalEntry · create",
                data=created,
                note=(
                    f"Posted JournalEntry {created.get('Id')}: "
                    f"{len(detail_lines)} lines, {totals['Debit']:,.2f} each side."
                ),
            )

        return await execute(
            "posting a journal entry",
            run,
            tool="create_journal_entry",
            write_entity="JournalEntry",
        )
