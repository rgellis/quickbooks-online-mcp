"""Configuration."""

from __future__ import annotations

import pytest

from src.settings import DEFAULT_MAX_ROWS, MissingConfiguration, load_settings

REQUIRED = {
    "QBO_CLIENT_ID": "id",
    "QBO_CLIENT_SECRET": "secret",
    "QBO_REALM_ID": "123",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from nothing rather than from whatever .env the developer has."""
    for name in list(REQUIRED) + [
        "QBO_TOKEN_STORE",
        "QBO_READ_ONLY",
        "QBO_MINOR_VERSION",
        "QBO_MAX_ROWS",
        "MCP_TRANSPORT",
        "MCP_AUTH",
        "MCP_HOST",
        "MCP_PORT",
    ]:
        monkeypatch.delenv(name, raising=False)


def configured(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    for name, value in {**REQUIRED, **extra}.items():
        monkeypatch.setenv(name, value)


class TestRequired:
    def test_every_missing_variable_is_named_at_once(self) -> None:
        with pytest.raises(MissingConfiguration) as exc:
            load_settings()
        message = str(exc.value)
        for name in REQUIRED:
            assert name in message

    def test_only_the_missing_one_is_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QBO_CLIENT_ID", "id")
        monkeypatch.setenv("QBO_CLIENT_SECRET", "secret")
        with pytest.raises(MissingConfiguration, match="QBO_REALM_ID"):
            load_settings()

    def test_whitespace_does_not_count_as_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured(monkeypatch, QBO_REALM_ID="   ")
        with pytest.raises(MissingConfiguration, match="QBO_REALM_ID"):
            load_settings()


class TestDefaults:
    def test_writes_are_refused_unless_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ledger is the wrong place for a permissive default."""
        configured(monkeypatch)
        assert load_settings().read_only is True

    def test_transport_defaults_to_stdio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configured(monkeypatch)
        assert load_settings().transport == "stdio"

    def test_auth_defaults_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configured(monkeypatch)
        assert load_settings().auth == "none"

    def test_max_rows_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configured(monkeypatch)
        assert load_settings().max_rows == DEFAULT_MAX_ROWS

    def test_token_store_defaults_under_home(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured(monkeypatch)
        assert load_settings().token_store.endswith("tokens.json")


class TestOverrides:
    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE"])
    def test_read_only_can_be_turned_off(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        configured(monkeypatch, QBO_READ_ONLY=value)
        assert load_settings().read_only is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
    def test_read_only_stays_on(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        configured(monkeypatch, QBO_READ_ONLY=value)
        assert load_settings().read_only is True

    def test_blank_read_only_keeps_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured(monkeypatch, QBO_READ_ONLY="  ")
        assert load_settings().read_only is True

    def test_http_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configured(monkeypatch, MCP_TRANSPORT="http", MCP_PORT="9000")
        settings = load_settings()
        assert settings.transport == "http"
        assert settings.port == 9000

    def test_an_unknown_transport_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured(monkeypatch, MCP_TRANSPORT="carrier-pigeon")
        with pytest.raises(MissingConfiguration, match="MCP_TRANSPORT"):
            load_settings()

    def test_a_non_numeric_integer_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured(monkeypatch, QBO_MAX_ROWS="lots")
        with pytest.raises(MissingConfiguration, match="whole number"):
            load_settings()

    def test_max_rows_must_be_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configured(monkeypatch, QBO_MAX_ROWS="-1")
        with pytest.raises(MissingConfiguration, match="at least 1"):
            load_settings()

    def test_minor_version_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configured(monkeypatch, QBO_MINOR_VERSION="70")
        assert load_settings().minor_version == 70
