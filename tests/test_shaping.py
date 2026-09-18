"""Response shaping.

Two properties matter: a truncated list must never read as a complete one, and
a money amount must never become a float on the way out.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from qbo.errors import QboApiError, QboInvariantError

from src.shaping import SOURCE, envelope, error_payload, jsonable


class TestJsonable:
    def test_decimals_become_strings_not_floats(self) -> None:
        """2780.92 through a float is 2780.9199999999996, and JSON gives no way
        to say that happened."""
        assert jsonable(Decimal("2780.92")) == "2780.92"
        assert not isinstance(jsonable(Decimal("2780.92")), float)

    def test_dates_and_datetimes_become_iso(self) -> None:
        assert jsonable(date(2026, 8, 1)) == "2026-08-01"
        moment = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        assert jsonable(moment).startswith("2026-08-01T12:00")

    def test_nested_structures_are_converted_throughout(self) -> None:
        payload = {"lines": [{"amount": Decimal("1.50"), "date": date(2026, 1, 1)}]}
        assert jsonable(payload) == {
            "lines": [{"amount": "1.50", "date": "2026-01-01"}]
        }

    def test_tuples_become_lists(self) -> None:
        assert jsonable((Decimal("1"), 2)) == ["1", 2]

    def test_other_values_pass_through(self) -> None:
        assert jsonable({"a": 1, "b": None, "c": True}) == {
            "a": 1,
            "b": None,
            "c": True,
        }


class TestEnvelope:
    def test_provenance_is_always_present(self) -> None:
        payload = envelope(source="query", data={"x": 1})
        assert payload["source"].startswith(SOURCE)
        assert "asOf" in payload
        assert payload["derived"] is False

    def test_derived_values_say_so(self) -> None:
        assert envelope(source="registry", data={}, derived=True)["derived"] is True

    def test_a_complete_list_reports_no_omission(self) -> None:
        payload = envelope(source="q", rows=[1, 2, 3], max_rows=10)
        assert payload["count"] == 3
        assert payload["returned"] == 3
        assert "omitted" not in payload

    def test_truncation_is_stated_never_silent(self) -> None:
        payload = envelope(source="q", rows=list(range(100)), max_rows=10)
        assert payload["count"] == 100
        assert payload["returned"] == 10
        assert payload["omitted"] == 90
        assert "100 rows matched" in payload["note"]

    def test_max_rows_none_returns_everything(self) -> None:
        payload = envelope(source="q", rows=list(range(100)), max_rows=None)
        assert payload["returned"] == 100

    def test_period_is_carried_when_given(self) -> None:
        payload = envelope(source="q", rows=[], period=("2026-08-01", "2026-08-31"))
        assert payload["period"] == {"start": "2026-08-01", "end": "2026-08-31"}

    def test_a_note_is_appended_to_a_truncation_note(self) -> None:
        payload = envelope(
            source="q", rows=list(range(10)), max_rows=2, note="Extra context."
        )
        assert "rows matched" in payload["note"]
        assert "Extra context." in payload["note"]

    def test_extra_fields_are_merged_and_converted(self) -> None:
        payload = envelope(source="q", rows=[], extra={"total": Decimal("5.00")})
        assert payload["total"] == "5.00"

    def test_data_and_rows_are_alternatives(self) -> None:
        assert "rows" in envelope(source="q", rows=[])
        assert "data" in envelope(source="q", data={"a": 1})


class TestErrorPayload:
    def test_an_invariant_failure_is_described_as_such(self) -> None:
        payload = error_payload(
            QboInvariantError("expenses 0.00 against 37,551.89"), attempted="the P&L"
        )
        assert payload["kind"] == "invariant"
        assert "withheld" in payload["meaning"]
        assert payload["attempted"] == "the P&L"

    def test_an_api_error_carries_status_and_tid(self) -> None:
        payload = error_payload(
            QboApiError(
                status_code=400,
                method="GET",
                url="https://x/y",
                body={"Fault": {}},
                intuit_tid="1-abc",
            ),
            attempted="a query",
        )
        assert payload["kind"] == "api"
        assert payload["status"] == 400
        assert payload["intuit_tid"] == "1-abc"

    def test_other_failures_are_client_errors(self) -> None:
        assert error_payload(ValueError("bad"), attempted="x")["kind"] == "client"

    def test_credentials_never_survive_into_a_message(self) -> None:
        exc = RuntimeError("failed with access_token=supersecretvalue123")
        assert "supersecretvalue123" not in error_payload(exc, attempted="x")["message"]

    @pytest.mark.parametrize("exc", [ValueError("x"), KeyError("y")])
    def test_every_error_states_what_was_attempted(self, exc: Exception) -> None:
        assert error_payload(exc, attempted="reading Invoice 1")["attempted"] == (
            "reading Invoice 1"
        )
