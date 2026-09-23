"""Shared pytest fixtures: settings, the app, an HTTP client, a clock and an id source.

``clock`` and ``id_generator`` are the deterministic fakes from ``tests/fakes``.
"""

import logging
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import httpx
import pytest
import structlog
from fastapi import FastAPI

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.main import create_app
from yakhnama.platform.settings import Settings, get_settings

_ENV_PREFIX = "YAKHNAMA_"
_HANDLER_NAME = "yakhnama"
_ACCESS_LOGGER_NAME = "uvicorn.access"
FIXED_NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
"""The instant the ``clock`` fixture is frozen at."""


@pytest.fixture(autouse=True)
def isolate_configuration(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear cached settings, ``YAKHNAMA_*`` env vars and global logging state.

    Settings are cached process-wide and logging is global, so without this fixture
    one test's environment or logging setup would leak into the next.
    """
    for key in [key for key in os.environ if key.startswith(_ENV_PREFIX)]:
        monkeypatch.delenv(key)
    get_settings.cache_clear()
    root = logging.getLogger()
    original_level = root.level
    access_logger = logging.getLogger(_ACCESS_LOGGER_NAME)
    original_access_level = access_logger.level

    yield

    get_settings.cache_clear()
    for handler in [
        installed_handler
        for installed_handler in root.handlers
        if installed_handler.get_name() == _HANDLER_NAME
    ]:
        root.removeHandler(handler)
    root.setLevel(original_level)
    access_logger.setLevel(original_access_level)
    structlog.reset_defaults()


@pytest.fixture
def settings() -> Settings:
    """Return test settings that ignore any developer ``.env`` file."""
    return Settings(_env_file=None, environment="test", log_format="console")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """Return an application built by the factory with the test settings."""
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an HTTP client that calls the app in-process, without a network."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def clock() -> FrozenClock:
    """Return a clock frozen at ``FIXED_NOW``; tests move it explicitly."""
    return FrozenClock(FIXED_NOW)


@pytest.fixture
def id_generator() -> SequentialIdGenerator:
    """Return a fresh generator of deterministic, increasing UUIDv7 ids."""
    return SequentialIdGenerator()
