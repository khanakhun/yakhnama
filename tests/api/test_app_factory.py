"""Tests for the application factory in ``yakhnama.main``."""

from importlib.metadata import version
from pathlib import Path

import pytest
from fastapi import FastAPI

from yakhnama.main import create_app
from yakhnama.platform.settings import Settings


def test_create_app_with_settings_stores_them_on_state(
    app: FastAPI, settings: Settings
) -> None:
    stored = app.state.settings

    assert stored is settings


def test_create_app_test_settings_exposes_title_version_and_versioned_urls(
    app: FastAPI,
) -> None:
    metadata = (app.title, app.version, app.openapi_url, app.docs_url, app.redoc_url)

    assert metadata == (
        "Yakhnama",
        version("yakhnama"),
        "/api/v1/openapi.json",
        # Swagger UI and ReDoc are off; Scalar is a route at /api/v1/docs (ADR 0014).
        None,
        None,
    )


def test_create_app_without_settings_loads_them_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("YAKHNAMA_APP_NAME", "Yakhnama Test")

    app = create_app()

    assert app.title == "Yakhnama Test"
    assert app.state.settings.app_name == "Yakhnama Test"
