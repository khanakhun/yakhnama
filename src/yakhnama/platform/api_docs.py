"""The Scalar API reference page and its Content Security Policy (ADR 0014).

``scalar-fastapi`` renders a small HTML page that loads the Scalar bundle from
``docs_scalar_js_url`` and starts it with one inline script holding the
configuration. The package ships no JavaScript, so the bundle comes from the jsDelivr
CDN unless an operator hosts a copy and points the setting at it.

The page gets its own CSP instead of the API's ``default-src 'none'``: scripts only
from the bundle URL plus the SHA-256 hash of the one inline script (computed from the
rendered page, so it can never drift from it), styles inline (Scalar injects them),
data from this origin only. Scalar's telemetry, its default web fonts and the
FastAPI favicon, all third-party requests, are switched off.

The page exists only when ``docs_enabled`` is true, which the production guard in
``platform/settings.py`` forbids.

Patterns: Value Object (the rendered page).
"""

import base64
import hashlib
import re
from typing import Final

from pydantic import BaseModel, ConfigDict
from scalar_fastapi import get_scalar_api_reference

_INLINE_SCRIPT: Final = re.compile(r"<script>(?P<body>.*?)</script>", re.DOTALL)
# An empty data URL: no favicon request leaves the browser.
_NO_FAVICON: Final = "data:,"


class ApiReferencePage(BaseModel):
    """The rendered API reference and the CSP that lets it run.

    Implements: Value Object.

    Attributes:
        html: The page.
        content_security_policy: The ``Content-Security-Policy`` for the page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    html: str
    content_security_policy: str


def script_hash_sources(html: str) -> tuple[str, ...]:
    """Return a CSP ``'sha256-...'`` source for each inline script in ``html``.

    Args:
        html: A rendered HTML page.

    Returns:
        One source per ``<script>`` element without attributes, in page order.
    """
    return tuple(
        "'sha256-"
        + base64.b64encode(hashlib.sha256(match["body"].encode()).digest()).decode()
        + "'"
        for match in _INLINE_SCRIPT.finditer(html)
    )


def build_api_reference(
    *, openapi_url: str, title: str, script_url: str
) -> ApiReferencePage:
    """Render the Scalar page for an OpenAPI document and derive its CSP.

    Args:
        openapi_url: Path of the OpenAPI document, same origin as the page.
        title: The browser tab title.
        script_url: Absolute URL of the Scalar bundle.

    Returns:
        The page and its policy.
    """
    response = get_scalar_api_reference(
        openapi_url=openapi_url,
        title=title,
        scalar_js_url=script_url,
        scalar_favicon_url=_NO_FAVICON,
        with_default_fonts=False,
        telemetry=False,
        show_developer_tools="never",
    )
    html = bytes(response.body).decode()
    script_sources = " ".join((script_url, *script_hash_sources(html)))
    policy = "; ".join(
        (
            "default-src 'none'",
            f"script-src {script_sources}",
            "style-src 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        )
    )
    return ApiReferencePage(html=html, content_security_policy=policy)
