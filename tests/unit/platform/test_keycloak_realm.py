"""Tests for ``yakhnama.platform.keycloak_realm`` (production realm derivation)."""

import copy
import io
import json
from pathlib import Path
from typing import Final

import pytest

from yakhnama.platform.keycloak_realm import (
    PORTAL_CLIENT_ID,
    JsonObject,
    JsonValue,
    check_portal_origin,
    derive_production_realm,
    find_forbidden_markers,
    main,
    render_realm,
)

DEVELOPMENT_REALM_FILE: Final = (
    Path(__file__).resolve().parents[3] / "docker" / "keycloak" / "yakhnama-realm.json"
)
ORIGIN: Final = "https://example.org"


def _development_realm() -> JsonObject:
    return {
        "realm": "yakhnama",
        "sslRequired": "none",
        "bruteForceProtected": True,
        "smtpServer": {"host": "mailpit", "port": "1025"},
        "roles": {"realm": [{"name": "moderator"}]},
        "clients": [
            {"clientId": "yakhnama-api", "bearerOnly": True},
            {
                "clientId": "yakhnama-dev-cli",
                "directAccessGrantsEnabled": True,
                "description": "DEVELOPMENT ONLY.",
            },
            {
                "clientId": PORTAL_CLIENT_ID,
                "description": "DEVELOPMENT REGISTRATION of the web portal.",
                "redirectUris": ["http://localhost:3000/auth/callback"],
                "webOrigins": ["http://localhost:3000"],
                "attributes": {
                    "pkce.code.challenge.method": "S256",
                    "post.logout.redirect.uris": "http://localhost:3000/*",
                },
            },
        ],
        "users": [
            {"username": "demo-citizen", "credentials": [{"value": "x-dev-only"}]}
        ],
    }


def _client(realm: JsonObject, client_id: str) -> JsonObject:
    clients = realm["clients"]
    assert isinstance(clients, list)
    for client in clients:
        assert isinstance(client, dict)
        if client["clientId"] == client_id:
            return client
    pytest.fail(f"no client {client_id}")


def _client_ids(realm: JsonObject) -> list[JsonValue]:
    clients = realm["clients"]
    assert isinstance(clients, list)
    return [client["clientId"] for client in clients if isinstance(client, dict)]


def test_derive_production_realm_removes_every_user() -> None:
    derived = derive_production_realm(_development_realm(), ORIGIN)

    assert "users" not in derived


def test_derive_production_realm_removes_the_development_cli_client() -> None:
    derived = derive_production_realm(_development_realm(), ORIGIN)

    assert _client_ids(derived) == ["yakhnama-api", PORTAL_CLIENT_ID]


def test_derive_production_realm_registers_the_portal_for_one_origin_only() -> None:
    derived = derive_production_realm(_development_realm(), ORIGIN)

    portal = _client(derived, PORTAL_CLIENT_ID)
    assert portal["redirectUris"] == ["https://example.org/auth/callback"]
    assert portal["webOrigins"] == ["https://example.org"]
    assert portal["attributes"] == {
        "pkce.code.challenge.method": "S256",
        "post.logout.redirect.uris": "https://example.org/*",
    }


def test_derive_production_realm_requires_tls_for_external_addresses() -> None:
    derived = derive_production_realm(_development_realm(), ORIGIN)

    assert derived["sslRequired"] == "external"


def test_derive_production_realm_replaces_mail_catcher_with_placeholders() -> None:
    derived = derive_production_realm(_development_realm(), ORIGIN)

    smtp = derived["smtpServer"]
    assert isinstance(smtp, dict)
    assert all(
        str(smtp[key]).startswith("${KC_SMTP_")
        for key in ("host", "port", "from", "user", "password")
    )
    assert smtp["starttls"] == "true"
    assert smtp["auth"] == "true"


def test_derive_production_realm_keeps_roles_and_leaves_input_unchanged() -> None:
    realm = _development_realm()
    before = copy.deepcopy(realm)

    derived = derive_production_realm(realm, ORIGIN)

    assert derived["roles"] == before["roles"]
    assert realm == before


def test_derive_production_realm_trailing_slash_origin_is_normalised() -> None:
    derived = derive_production_realm(_development_realm(), "https://example.org/")

    assert _client(derived, PORTAL_CLIENT_ID)["webOrigins"] == ["https://example.org"]


def test_derive_production_realm_without_brute_force_protection_is_refused() -> None:
    realm = _development_realm()
    realm["bruteForceProtected"] = False

    with pytest.raises(ValueError, match="brute-force"):
        derive_production_realm(realm, ORIGIN)


def test_derive_production_realm_without_portal_client_is_refused() -> None:
    realm = _development_realm()
    realm["clients"] = [{"clientId": "yakhnama-api"}]

    with pytest.raises(ValueError, match=PORTAL_CLIENT_ID):
        derive_production_realm(realm, ORIGIN)


@pytest.mark.parametrize(
    ("clients", "where"),
    [({"clientId": "x"}, "clients"), (["x"], r"clients\[\]")],
)
def test_derive_production_realm_malformed_clients_are_refused(
    clients: JsonValue, where: str
) -> None:
    realm = _development_realm()
    realm["clients"] = clients

    with pytest.raises(ValueError, match=where):
        derive_production_realm(realm, ORIGIN)


def test_derive_production_realm_leftover_loopback_value_is_refused() -> None:
    realm = _development_realm()
    realm["frontendUrl"] = "http://localhost:8080"

    with pytest.raises(ValueError, match="development values remain: localhost"):
        derive_production_realm(realm, ORIGIN)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.org",
        "https://",
        "example.org",
        "https://localhost:3000",
        "https://127.0.0.1",
        "https://example.org/portal",
        "https://example.org?x=1",
        "https://example.org#top",
        "https://user@example.org",
    ],
)
def test_check_portal_origin_unusable_origin_is_refused(origin: str) -> None:
    with pytest.raises(ValueError, match="portal origin"):
        check_portal_origin(origin)


def test_check_portal_origin_keeps_an_explicit_port() -> None:
    assert check_portal_origin("https://example.org:8443") == "https://example.org:8443"


def test_find_forbidden_markers_reports_each_marker_case_insensitively() -> None:
    value: JsonValue = {"Description": "DEVELOPMENT only", "url": ["http://[::1]/"]}

    assert find_forbidden_markers(value) == {"development", "[::1]"}


def test_derive_production_realm_from_committed_development_realm_is_clean() -> None:
    realm = json.loads(DEVELOPMENT_REALM_FILE.read_text(encoding="utf-8"))

    derived = derive_production_realm(realm, ORIGIN)

    assert find_forbidden_markers(derived) == set()
    assert _client_ids(derived) == ["yakhnama-api", PORTAL_CLIENT_ID]
    assert derived["registrationAllowed"] is True
    assert derived["verifyEmail"] is True


def test_render_realm_is_indented_json_with_final_newline() -> None:
    rendered = render_realm({"realm": "yakhnama", "enabled": True})

    assert rendered == '{\n  "realm": "yakhnama",\n  "enabled": true\n}\n'


def test_main_reads_standard_input_and_writes_standard_output() -> None:
    stdin = io.StringIO(json.dumps(_development_realm()))
    stdout = io.StringIO()

    code = main(
        ["--portal-origin", ORIGIN, "--source", "-"], stdin=stdin, stdout=stdout
    )

    assert code == 0
    assert json.loads(stdout.getvalue())["sslRequired"] == "external"


def test_main_writes_output_file(tmp_path: Path) -> None:
    source = tmp_path / "realm.json"
    source.write_text(json.dumps(_development_realm()), encoding="utf-8")
    output = tmp_path / "yakhnama-realm.production.json"

    code = main(
        ["--portal-origin", ORIGIN, "--source", str(source), "--output", str(output)]
    )

    assert code == 0
    assert "users" not in json.loads(output.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("text", "origin", "reason"),
    [
        ("{not json", ORIGIN, "Expecting property name"),
        ("[]", ORIGIN, "the realm must be a JSON object"),
        (json.dumps(_development_realm()), "http://example.org", "https URL"),
    ],
)
def test_main_unusable_input_returns_one_with_reason(
    text: str, origin: str, reason: str
) -> None:
    stderr = io.StringIO()

    code = main(
        ["--portal-origin", origin, "--source", "-"],
        stdin=io.StringIO(text),
        stderr=stderr,
    )

    assert code == 1
    assert reason in stderr.getvalue()


def test_main_missing_source_file_returns_one(tmp_path: Path) -> None:
    stderr = io.StringIO()

    code = main(
        ["--portal-origin", ORIGIN, "--source", str(tmp_path / "missing.json")],
        stderr=stderr,
    )

    assert code == 1
    assert "missing.json" in stderr.getvalue()
