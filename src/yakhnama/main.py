"""Application factory and composition root.

``create_app`` is the only place in the code base that builds the FastAPI application.
It loads settings, configures logging, and wires routers; later phases add exception
handlers (RFC 9457 Problem Details) and bind ports to adapters here and in
``platform/container.py``. Keeping construction in one function lets tests build an
isolated app with their own settings and fakes.

Patterns: Composition Root.
"""

from importlib.metadata import version

from fastapi import FastAPI

from yakhnama.platform import health
from yakhnama.platform.logging import configure_logging
from yakhnama.platform.settings import Settings, get_settings

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and wire the Yakhnama FastAPI application.

    Args:
        settings: Settings to use; when omitted they are loaded from the environment
            via ``get_settings``. Tests pass explicit settings for isolation.

    Returns:
        The configured application, with its settings stored on ``app.state.settings``.

    Raises:
        pydantic.ValidationError: If settings are loaded and the environment is invalid.
    """
    resolved_settings = settings if settings is not None else get_settings()
    configure_logging(resolved_settings)

    app = FastAPI(
        title=resolved_settings.app_name,
        version=version("yakhnama"),
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
    )
    app.state.settings = resolved_settings
    app.include_router(health.router)
    return app
