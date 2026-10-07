"""Derive the production Keycloak realm from the development realm export.

``python -m yakhnama.platform.keycloak_realm --portal-origin https://example.org``
reads the development realm (``docker/keycloak/yakhnama-realm.json`` by default, or
standard input with ``--source -``) and writes a realm that is safe to import on a
production Keycloak (``docker/keycloak/production/README.md``, ADR 0023):

- every user is removed: the demo users and their ``-dev-only`` passwords exist for
  local development only;
- the development-only clients are removed (``yakhnama-dev-cli``, whose direct access
  grants would let anyone trade a password for a token);
- the portal's client ``yakhnama-web`` is registered for exactly one origin: its
  callback ``<origin>/auth/callback``, post-logout ``<origin>/*`` and web origin
  ``<origin>`` (open question Q212), never ``localhost``;
- ``sslRequired`` becomes ``external``, so Keycloak refuses plain http from any
  public address;
- the development mail catcher is replaced by Keycloak import placeholders
  (``${KC_SMTP_HOST}`` and so on), filled from the Keycloak container's environment
  when the realm is first imported, so no SMTP password is written to the file
  (open question Q213).

Roles, the user profile, the password policy, brute-force protection and the social
identity providers (disabled unless their ``KC_*_ENABLED`` placeholder says
otherwise) are kept as they are. The result is refused if any loopback address or
development marker is left anywhere in it.

The realm is Keycloak's document, not a Yakhnama model, so it is handled as parsed
JSON inside this module and never crosses a layer.

Patterns: none (command-line entry point; it defines no classes).
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, TextIO
from urllib.parse import urlsplit

type JsonValue = (
    dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None
)
type JsonObject = dict[str, JsonValue]

DEFAULT_SOURCE: Final = Path("docker/keycloak/yakhnama-realm.json")
STANDARD_STREAM: Final = "-"
PORTAL_CLIENT_ID: Final = "yakhnama-web"
DEVELOPMENT_ONLY_CLIENT_IDS: Final = frozenset({"yakhnama-dev-cli"})
CALLBACK_PATH: Final = "/auth/callback"
POST_LOGOUT_ATTRIBUTE: Final = "post.logout.redirect.uris"
PORTAL_CLIENT_DESCRIPTION: Final = (
    "PRODUCTION REGISTRATION of the web portal. Public client for its server-side "
    "BFF: authorization code flow, PKCE S256 required, no client secret. One origin "
    "only (open question Q212)."
)
# Keycloak replaces ${NAME:default} from its environment when it imports the file.
PRODUCTION_SMTP_SERVER: Final[JsonObject] = {
    "host": "${KC_SMTP_HOST:}",
    "port": "${KC_SMTP_PORT:587}",
    "from": "${KC_SMTP_FROM:}",
    "fromDisplayName": "${KC_SMTP_FROM_DISPLAY_NAME:Yakhnama}",
    "replyTo": "",
    "envelopeFrom": "",
    "auth": "true",
    "user": "${KC_SMTP_USER:}",
    "password": "${KC_SMTP_PASSWORD:}",
    "ssl": "false",
    "starttls": "true",
}
# Text that must not survive into a production realm, compared case-insensitively.
FORBIDDEN_MARKERS: Final = (
    "localhost",
    "127.0.0.1",
    "[::1]",
    "dev-only",
    "mailpit",
    "development",
)
LOOPBACK_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})


def check_portal_origin(origin: str) -> str:
    """Return ``origin`` if it is an ``https`` origin of a public host.

    Args:
        origin: For example ``https://example.org``.

    Returns:
        The origin without a trailing slash.

    Raises:
        ValueError: If it is not ``https``, has no host, names a loopback
            host, or carries a path, query, fragment or user information.
    """
    parts = urlsplit(origin.removesuffix("/"))
    if parts.scheme != "https" or not parts.hostname:
        message = "the portal origin must be an https URL with a host"
        raise ValueError(message)
    if parts.hostname in LOOPBACK_HOSTS:
        message = "the portal origin must not be a loopback host"
        raise ValueError(message)
    if parts.path or parts.query or parts.fragment or parts.username is not None:
        message = "the portal origin must be scheme, host and port only"
        raise ValueError(message)
    return f"{parts.scheme}://{parts.netloc}"


def _as_object(value: JsonValue, where: str) -> JsonObject:
    if not isinstance(value, dict):
        message = f"{where} must be a JSON object"
        raise ValueError(message)
    return value


def _as_list(value: JsonValue, where: str) -> list[JsonValue]:
    if not isinstance(value, list):
        message = f"{where} must be a JSON array"
        raise ValueError(message)
    return value


def _register_portal_client(client: JsonObject, origin: str) -> JsonObject:
    attributes = _as_object(
        client.get("attributes", {}), f"{PORTAL_CLIENT_ID}.attributes"
    )
    return {
        **client,
        "description": PORTAL_CLIENT_DESCRIPTION,
        "redirectUris": [f"{origin}{CALLBACK_PATH}"],
        "webOrigins": [origin],
        "attributes": {**attributes, POST_LOGOUT_ATTRIBUTE: f"{origin}/*"},
    }


def _production_clients(clients: list[JsonValue], origin: str) -> list[JsonValue]:
    kept: list[JsonValue] = []
    has_portal_client = False
    for entry in clients:
        client = _as_object(entry, "clients[]")
        client_id = client.get("clientId")
        if client_id in DEVELOPMENT_ONLY_CLIENT_IDS:
            continue
        if client_id == PORTAL_CLIENT_ID:
            has_portal_client = True
            client = _register_portal_client(client, origin)
        kept.append(client)
    if not has_portal_client:
        message = f"the realm has no {PORTAL_CLIENT_ID} client"
        raise ValueError(message)
    return kept


def find_forbidden_markers(value: JsonValue) -> set[str]:
    """Return every forbidden marker found in any key or string of ``value``.

    Args:
        value: A parsed JSON value.

    Returns:
        The markers from ``FORBIDDEN_MARKERS`` that occur, compared
        case-insensitively; empty when none does.
    """
    text = json.dumps(value, ensure_ascii=False).lower()
    return {marker for marker in FORBIDDEN_MARKERS if marker in text}


def derive_production_realm(realm: JsonObject, portal_origin: str) -> JsonObject:
    """Return the production realm derived from the development ``realm``.

    Args:
        realm: The parsed development realm export.
        portal_origin: The portal's public origin, for example
            ``https://example.org``.

    Returns:
        A new realm; ``realm`` itself is not changed.

    Raises:
        ValueError: If the origin is unusable, the realm lacks the
            portal's client or brute-force protection, or development values
            remain after the derivation.
    """
    origin = check_portal_origin(portal_origin)
    if realm.get("bruteForceProtected") is not True:
        message = "the realm must have brute-force protection on"
        raise ValueError(message)
    derived: JsonObject = {
        key: value for key, value in realm.items() if key not in {"users"}
    }
    derived["sslRequired"] = "external"
    derived["smtpServer"] = dict(PRODUCTION_SMTP_SERVER)
    derived["clients"] = _production_clients(
        _as_list(realm.get("clients", []), "clients"), origin
    )
    leftovers = find_forbidden_markers(derived)
    if leftovers:
        message = "development values remain: " + ", ".join(sorted(leftovers))
        raise ValueError(message)
    return derived


def render_realm(realm: JsonObject) -> str:
    """Render ``realm`` as stable, readable JSON ending in a newline.

    Args:
        realm: The realm to render.

    Returns:
        Two-space indented JSON with the keys in their original order.
    """
    return json.dumps(realm, indent=2, ensure_ascii=False) + "\n"


def _read_text(source: str, stdin: TextIO) -> str:
    if source == STANDARD_STREAM:
        return stdin.read()
    return Path(source).read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of ``python -m yakhnama.platform.keycloak_realm``.

    Returns:
        A parser for ``--portal-origin URL [--source PATH] [--output PATH]``.
    """
    parser = argparse.ArgumentParser(
        prog="python -m yakhnama.platform.keycloak_realm",
        description=(
            "Derive the production Keycloak realm from the development realm export."
        ),
    )
    parser.add_argument(
        "--portal-origin",
        required=True,
        metavar="URL",
        help="the portal's public https origin, for example https://example.org",
    )
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        metavar="PATH",
        help=f"the development realm export, or '-' for standard input "
        f"(default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--output",
        default=STANDARD_STREAM,
        metavar="PATH",
        help="where to write the production realm, or '-' for standard output "
        "(the default)",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name; ``sys.argv`` when ``None``.
        stdin: Read when ``--source -``.
        stdout: Written when ``--output -``.
        stderr: Receives the reason of a refusal.

    Returns:
        ``0`` on success, ``1`` when the realm cannot be derived.
    """
    arguments = build_parser().parse_args(argv)
    try:
        parsed: JsonValue = json.loads(_read_text(arguments.source, stdin))
        realm = derive_production_realm(
            _as_object(parsed, "the realm"), arguments.portal_origin
        )
    # JSONDecodeError is a ValueError; OSError covers a missing or unreadable file.
    except (OSError, ValueError) as error:
        stderr.write(f"keycloak_realm: {error}\n")
        return 1
    rendered = render_realm(realm)
    if arguments.output == STANDARD_STREAM:
        stdout.write(rendered)
    else:
        Path(arguments.output).write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
