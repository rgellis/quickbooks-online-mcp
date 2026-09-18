"""Query construction, and the capability guard.

The guard exists because of a verified failure, not a theoretical one:
filtering Purchase by PaymentType against a live company with 202 credit card
purchases returns zero rows, with no error. A silently empty result reads as a
fact, which makes it worse than a rejection.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.query import QueryError, build_query, literal


class TestLiteral:
    def test_strings_are_quoted(self) -> None:
        assert literal("Bank") == "'Bank'"

    def test_single_quotes_are_doubled(self) -> None:
        """A customer called O'Brien would otherwise end the string early."""
        assert literal("O'Brien") == "'O''Brien'"

    def test_injection_attempt_stays_inside_the_literal(self) -> None:
        rendered = literal("x' OR '1'='1")
        assert rendered == "'x'' OR ''1''=''1'"
        assert rendered.count("'") % 2 == 0

    def test_booleans_are_unquoted(self) -> None:
        assert literal(True) == "true"
        assert literal(False) == "false"

    def test_numbers_are_unquoted(self) -> None:
        assert literal(0) == "0"
        assert literal(12.5) == "12.5"

    def test_dates_render_iso(self) -> None:
        assert literal(date(2026, 8, 1)) == "'2026-08-01'"

    def test_backslashes_are_left_alone(self) -> None:
        """Not an escape character in this query language."""
        assert literal("a\\b") == "'a\\b'"


class TestBuildQuery:
    def test_plain_select(self) -> None:
        assert build_query("Invoice") == "SELECT * FROM Invoice"

    def test_conditions_are_anded(self) -> None:
        statement = build_query(
            "Invoice", where=[("TxnDate", ">=", "2026-08-01"), ("Balance", ">", 0)]
        )
        assert statement == (
            "SELECT * FROM Invoice WHERE TxnDate >= '2026-08-01' AND Balance > 0"
        )

    def test_ordering(self) -> None:
        assert build_query("Invoice", order_by="TxnDate", descending=True).endswith(
            "ORDER BY TxnDate DESC"
        )

    def test_in_operator(self) -> None:
        statement = build_query("Invoice", where=[("Id", "IN", ["1", "2"])])
        assert "Id IN ('1', '2')" in statement

    def test_in_requires_a_list(self) -> None:
        with pytest.raises(QueryError, match="IN needs a list"):
            build_query("Invoice", where=[("Id", "IN", "1")])

    def test_select_columns(self) -> None:
        assert build_query("Invoice", select="Id, DocNumber").startswith(
            "SELECT Id, DocNumber FROM"
        )

    @pytest.mark.parametrize("entity", ["Invoice; DROP", "In voice", ""])
    def test_unusable_entity_names_are_refused(self, entity: str) -> None:
        with pytest.raises(QueryError, match="entity name"):
            build_query(entity)

    def test_unusable_field_names_are_refused(self) -> None:
        with pytest.raises(QueryError, match="field name"):
            build_query("Invoice", where=[("Txn Date; --", "=", "x")])

    def test_unusable_sort_field_is_refused(self) -> None:
        with pytest.raises(QueryError, match="sort field"):
            build_query("Invoice", order_by="TxnDate; DROP")


class TestCapabilityGuard:
    def test_sorting_by_an_unsortable_field_is_refused(self) -> None:
        """QuickBooks returns HTTP 400 for ORDER BY AcctNum."""
        with pytest.raises(QueryError, match="will not sort by Account.AcctNum"):
            build_query("Account", order_by="AcctNum")

    def test_filtering_on_an_unfilterable_field_is_refused(self) -> None:
        """QuickBooks returns zero rows for WHERE PaymentType = ..., with 202
        matching purchases in the ledger and no error anywhere."""
        with pytest.raises(QueryError, match="will not filter on Purchase.PaymentType"):
            build_query("Purchase", where=[("PaymentType", "=", "CreditCard")])

    def test_the_refusal_says_what_can_be_used_instead(self) -> None:
        with pytest.raises(QueryError) as exc:
            build_query("Account", order_by="AcctNum")
        assert "Name" in str(exc.value)

    def test_documented_fields_are_allowed(self) -> None:
        build_query("Account", where=[("AccountType", "=", "Bank")], order_by="Name")
        build_query(
            "Invoice", where=[("TxnDate", ">=", "2026-01-01")], order_by="TxnDate"
        )

    def test_an_unknown_entity_is_not_guarded(self) -> None:
        """Nothing is known about it, so nothing is claimed about it."""
        assert "FROM Widget" in build_query("Widget", where=[("Anything", "=", 1)])
