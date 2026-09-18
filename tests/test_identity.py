"""Identity extraction from the verified token.

Providers disagree about claim names. Identity here is recorded, not enforced
on, so getting it wrong produces a write logged against "unidentified caller"
rather than a refusal -- which is quiet, and worth a test for that reason.
"""

from __future__ import annotations

from typing import Any

import pytest

import src.identity as identity_module
from src.identity import LOCAL_OPERATOR, current_identity


class FakeToken:
    def __init__(self, **kwargs: Any) -> None:
        self.subject: str | None = kwargs.get("subject")
        self.client_id: str = kwargs.get("client_id", "client")
        self.scopes: list[str] = kwargs.get("scopes", [])
        self.claims: dict[str, Any] = kwargs.get("claims", {})


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def install(**kwargs: Any) -> None:
        monkeypatch.setattr(
            identity_module, "get_access_token", lambda: FakeToken(**kwargs)
        )

    return install


class TestUnauthenticated:
    def test_no_token_means_a_local_operator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(identity_module, "get_access_token", lambda: None)
        assert current_identity() is LOCAL_OPERATOR
        assert not LOCAL_OPERATOR.authenticated

    def test_the_local_operator_has_a_readable_label(self) -> None:
        assert LOCAL_OPERATOR.label == "local"


class TestClaims:
    def test_email_claim(self, token: Any) -> None:
        token(subject="abc", claims={"email": "ryan@example.com"})
        assert current_identity().email == "ryan@example.com"

    @pytest.mark.parametrize("claim", ["email", "preferred_username", "upn"])
    def test_alternative_email_claims(self, token: Any, claim: str) -> None:
        token(subject="abc", claims={claim: "a@b.c"})
        assert current_identity().email == "a@b.c"

    def test_subject_falls_back_to_client_id(self, token: Any) -> None:
        token(subject=None, client_id="svc-1", claims={})
        assert current_identity().subject == "svc-1"

    def test_label_prefers_email_then_subject(self, token: Any) -> None:
        token(subject="abc", claims={})
        assert current_identity().label == "abc"
        token(subject="abc", claims={"email": "a@b.c"})
        assert current_identity().label == "a@b.c"
