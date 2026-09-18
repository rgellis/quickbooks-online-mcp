"""Shared fixtures.

No test reaches QuickBooks. The client is replaced with one wired to a mock
transport, and settings are set explicitly rather than read from the
environment -- otherwise a developer's own .env decides what the suite asserts,
which is how a test comes to pass for the wrong reason.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, cast
from urllib.parse import unquote_plus

import httpx
import pytest
from qbo.auth import AuthClient, MemoryTokenStore, TokenSet
from qbo.client import PRODUCTION_BASE_URL, QboClient

import src.client as client_module
from src.settings import Settings

REALM = "9130347"
BASE = f"{PRODUCTION_BASE_URL}/v3/company/{REALM}"


def sent_url(route: Any, index: int = 0) -> str:
    """The URL of a recorded request, percent-decoded.

    respx exposes recorded calls untyped, and the query string is encoded, so
    ``Balance > 0`` reads as ``Balance+%3E+0``. Narrowing and decoding once
    here keeps every assertion legible.
    """
    calls = cast("list[Any]", route.calls)
    request = cast("httpx.Request", calls[index].request)
    return unquote_plus(str(request.url))


def sent_body(route: Any, index: int = 0) -> dict[str, Any]:
    """The decoded JSON body of a recorded request."""
    calls = cast("list[Any]", route.calls)
    request = cast("httpx.Request", calls[index].request)
    decoded: Any = json.loads(request.content.decode())
    return cast("dict[str, Any]", decoded) if isinstance(decoded, dict) else {}


def make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "client_id": "test-client",
        "client_secret": "test-secret",
        "realm_id": REALM,
        "token_store": "/tmp/does-not-exist.json",
        "read_only": False,
        "minor_version": 75,
        "max_rows": 50,
        "transport": "stdio",
        "host": "0.0.0.0",
        "port": 8080,
        "auth": "none",
    }
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture(autouse=True)
def isolated(settings: Settings) -> Iterator[None]:
    """Every test starts with a known client and configuration."""
    tokens = TokenSet(
        access_token="test-access",
        refresh_token="test-refresh",
        realm_id=REALM,
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    auth = AuthClient(
        client_id="test-client",
        client_secret="test-secret",
        store=MemoryTokenStore(tokens),
    )
    client = QboClient(
        realm_id=REALM, auth=auth, read_only=settings.read_only, minor_version=75
    )
    client_module.set_client(client, settings)
    yield
    client_module.set_client(None)
