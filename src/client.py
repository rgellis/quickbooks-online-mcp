"""The QuickBooks client this server talks through.

Built lazily on first use and reused, so the token is read once and the access
token cached across tool calls. ``set_client`` exists for tests, which supply a
client wired to a mock transport.
"""

from __future__ import annotations

from qbo.auth import AuthClient, FileTokenStore
from qbo.client import DEFAULT_MINOR_VERSION, QboClient

from src.settings import Settings, load_settings

__all__ = ["get_client", "set_client", "close_client", "current_settings"]

_client: QboClient | None = None
_settings: Settings | None = None


def current_settings() -> Settings:
    """The loaded settings, reading them on first call."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def get_client() -> QboClient:
    """The shared client, built on first use."""
    global _client
    if _client is None:
        settings = current_settings()
        auth = AuthClient(
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            store=FileTokenStore(settings.token_store),
        )
        _client = QboClient(
            realm_id=settings.realm_id,
            auth=auth,
            read_only=settings.read_only,
            minor_version=settings.minor_version or DEFAULT_MINOR_VERSION,
        )
    return _client


def set_client(client: QboClient | None, settings: Settings | None = None) -> None:
    """Replace the shared client. For tests."""
    global _client, _settings
    _client = client
    if settings is not None:
        _settings = settings


async def close_client() -> None:
    """Release the shared client, if one was built."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
