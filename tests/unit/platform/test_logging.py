"""Unit tests for ``yakhnama.platform.logging``."""

import dataclasses
import json
import logging
from collections import UserDict
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, NoReturn
from uuid import UUID

import pytest
import structlog
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict

from yakhnama.platform.logging import (
    CYCLE,
    DEPTH_EXCEEDED,
    MAX_DEPTH,
    NON_PERSONAL_NAME_KEYS,
    REDACTED,
    SENSITIVE_KEY_SUBSTRINGS,
    SENSITIVE_KEY_SUFFIXES,
    UNLOGGABLE,
    configure_logging,
    is_sensitive_key,
    redact_personal_data,
)
from yakhnama.platform.settings import Settings


class _ReporterPayload(BaseModel):
    """Stand-in for an application model carrying personal data."""

    model_config = ConfigDict(frozen=True)

    phone: str
    role: str


@dataclasses.dataclass(frozen=True)
class _SubmissionPayload:
    """Stand-in for a dataclass carrying personal data."""

    reporter_email: str
    hazard_code: str


class _Opaque:
    """An object whose ``repr`` would leak personal data if it were rendered."""

    def __repr__(self) -> str:
        return "_Opaque(phone='+92300')"


class _Colour(Enum):
    """A plain enum, rendered as-is."""

    RED = "red"


class _ExplodingMapping(Mapping[str, object]):
    """A mapping whose iteration fails, as a broken third-party object might."""

    def __getitem__(self, key: str) -> object:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError

    def __len__(self) -> int:
        return 1

    def items(self) -> NoReturn:
        raise RuntimeError


class _ExplodingEventDict(UserDict[str, Any]):
    """A non-``dict`` event mapping whose iteration fails."""

    def items(self) -> NoReturn:
        raise RuntimeError


def _redact(event: dict[str, Any]) -> dict[str, Any]:
    return dict(redact_personal_data(None, "info", event))


def _root_formatter() -> structlog.stdlib.ProcessorFormatter:
    handlers = [
        handler
        for handler in logging.getLogger().handlers
        if handler.get_name() == "yakhnama"
    ]
    assert len(handlers) == 1
    formatter = handlers[0].formatter
    assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
    return formatter


def _assert_no_sensitive_value(value: object) -> None:
    """Fail if any mapping in ``value`` keeps a value under a sensitive key."""
    if isinstance(value, dict):
        for key, item in value.items():
            if is_sensitive_key(key):
                assert item == REDACTED
            else:
                _assert_no_sensitive_value(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_sensitive_value(item)


def _nested(depth: int) -> dict[str, Any]:
    """Return a dict nested ``depth`` levels deep, built without recursion."""
    root: dict[str, Any] = {}
    current = root
    for _ in range(depth):
        child: dict[str, Any] = {}
        current["child"] = child
        current = child
    return root


# --------------------------------------------------------------------------- #
# is_sensitive_key                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", [1, None, 3.5, ("email",), b"email"])
def test_is_sensitive_key_non_string_key_returns_false(key: object) -> None:
    result = is_sensitive_key(key)

    assert result is False


@pytest.mark.parametrize(
    "key", ["reporterPhone", "contactEmail", "idToken", "firstName", "clientIp"]
)
def test_is_sensitive_key_camel_case_variant_returns_true(key: str) -> None:
    result = is_sensitive_key(key)

    assert result is True


@pytest.mark.parametrize(
    "key",
    [
        "reporter_phone",
        "contact_email",
        "db_password",
        "id_token",
        "cookie",
        "client_ip",
        "geometry",
        "session_id",
        "whatsapp_number",
        "home_addr",
        "reporter_lat",
        "name",
        "casualty_name",
    ],
)
def test_is_sensitive_key_reviewed_leaking_key_returns_true(key: str) -> None:
    result = is_sensitive_key(key)

    assert result is True


@pytest.mark.parametrize(
    "key",
    [
        *sorted(NON_PERSONAL_NAME_KEYS),
        "hazard_name",
        "func_name",
        "event",
        "level",
        "logger",
        "timestamp",
        "hazard_type",
        "count",
        "description",
        "translate",
    ],
)
def test_is_sensitive_key_non_personal_key_returns_false(key: str) -> None:
    result = is_sensitive_key(key)

    assert result is False


_AFFIXES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", max_size=6)


@st.composite
def _spelling_variants(draw: st.DrawFn, normalised: str) -> str:
    """Re-spell ``normalised`` with random case and ``_``/``-`` separators."""
    characters = [
        draw(st.sampled_from([character.lower(), character.upper()]))
        + draw(st.sampled_from(["", "", "_", "-"]))
        for character in normalised
    ]
    return draw(st.sampled_from(["", "_", "-"])) + "".join(characters)


@st.composite
def _substring_keys(draw: st.DrawFn) -> str:
    marker = draw(st.sampled_from(SENSITIVE_KEY_SUBSTRINGS))
    normalised = draw(_AFFIXES) + marker + draw(_AFFIXES)
    return draw(_spelling_variants(normalised))


@st.composite
def _suffix_keys(draw: st.DrawFn) -> str:
    marker = draw(st.sampled_from(SENSITIVE_KEY_SUFFIXES))
    normalised = draw(_AFFIXES) + marker
    assume(normalised not in NON_PERSONAL_NAME_KEYS)
    return draw(_spelling_variants(normalised))


@given(key=st.one_of(_substring_keys(), _suffix_keys()))
def test_is_sensitive_key_case_separator_and_affix_variants_return_true(
    key: str,
) -> None:
    result = is_sensitive_key(key)

    assert result is True


@given(data=st.data())
def test_is_sensitive_key_allow_listed_name_in_any_spelling_returns_false(
    data: st.DataObject,
) -> None:
    normalised = data.draw(st.sampled_from(sorted(NON_PERSONAL_NAME_KEYS)))
    key = data.draw(_spelling_variants(normalised))

    result = is_sensitive_key(key)

    assert result is False


# --------------------------------------------------------------------------- #
# redact_personal_data                                                        #
# --------------------------------------------------------------------------- #


def test_redact_personal_data_top_level_sensitive_key_is_redacted() -> None:
    event = {"event": "report submitted", "email": "reporter@example.org"}

    result = _redact(event)

    assert result == {"event": "report submitted", "email": REDACTED}


def test_redact_personal_data_nested_dict_value_is_redacted() -> None:
    event = {"event": "x", "reporter": {"phone_number": "+92300", "role": "citizen"}}

    result = _redact(event)

    assert result["reporter"] == {"phone_number": REDACTED, "role": "citizen"}


def test_redact_personal_data_dict_inside_list_is_redacted() -> None:
    event = {"event": "x", "points": [{"lat": 35.9, "lon": 74.3}, {"label": "Hunza"}]}

    result = _redact(event)

    assert result["points"] == [{"lat": REDACTED, "lon": REDACTED}, {"label": "Hunza"}]


def test_redact_personal_data_dict_inside_tuple_is_redacted() -> None:
    event = {"event": "x", "pair": ({"token": "abc"}, 1)}

    result = _redact(event)

    assert result["pair"] == [{"token": REDACTED}, 1]


def test_redact_personal_data_dict_inside_set_like_is_redacted() -> None:
    event = {"event": "x", "codes": {"glof"}, "frozen": frozenset({3})}

    result = _redact(event)

    assert result["codes"] == ["glof"]
    assert result["frozen"] == [3]


def test_redact_personal_data_pydantic_model_phone_field_is_redacted() -> None:
    event = {"event": "x", "reporter": _ReporterPayload(phone="+92300", role="citizen")}

    result = _redact(event)

    assert result["reporter"] == {"phone": REDACTED, "role": "citizen"}


def test_redact_personal_data_dataclass_email_field_is_redacted() -> None:
    submission = _SubmissionPayload(reporter_email="a@b.org", hazard_code="glof")
    event = {"event": "x", "submission": submission}

    result = _redact(event)

    assert result["submission"] == {"reporter_email": REDACTED, "hazard_code": "glof"}


def test_redact_personal_data_arbitrary_object_is_replaced_by_type_name() -> None:
    event = {"event": "x", "payload": _Opaque(), "kind": _Opaque}

    result = _redact(event)

    assert result["payload"] == "<_Opaque>"
    assert result["kind"] == "<type>"


def test_redact_personal_data_scalar_types_are_kept_as_is() -> None:
    moment = datetime(2026, 9, 23, tzinfo=UTC)
    identifier = UUID(int=7)
    event = {
        "event": "x",
        "values": [1, 2.5, True, None, moment, date(2026, 9, 23), identifier],
        "colour": _Colour.RED,
    }

    result = _redact(event)

    assert result == event


@pytest.mark.parametrize("key", ["reporter_phone", "contactEmail", "Client-IP"])
def test_redact_personal_data_suffixed_or_prefixed_key_is_redacted(key: str) -> None:
    event = {"event": "x", key: "secret-value"}

    result = _redact(event)

    assert result[key] == REDACTED


def test_redact_personal_data_sensitive_key_with_container_value_is_redacted() -> None:
    event = {"event": "x", "coordinates": [74.3, 35.9], "address": {"village": "Passu"}}

    result = _redact(event)

    assert result["coordinates"] == REDACTED
    assert result["address"] == REDACTED


def test_redact_personal_data_non_sensitive_values_are_untouched() -> None:
    event = {"event": "x", "hazard_type": "glof", "count": 3, "tags": ["flood", 1]}

    result = _redact(event)

    assert result == event


def test_redact_personal_data_input_is_not_mutated() -> None:
    reporter = {"name": "Karim"}
    event = {"event": "x", "reporter": reporter}

    _redact(event)

    assert reporter == {"name": "Karim"}


def test_redact_personal_data_non_string_keys_are_kept() -> None:
    event: dict[str, Any] = {"event": "x", "lookup": {1: "one", None: "none"}}

    result = _redact(event)

    assert result["lookup"] == {1: "one", None: "none"}


def test_redact_personal_data_self_referencing_structure_does_not_recurse() -> None:
    loop: dict[str, Any] = {"email": "a@b.org"}
    loop["self"] = loop
    event = {"event": "x", "loop": loop}

    result = _redact(event)

    assert result["loop"] == {"email": REDACTED, "self": CYCLE}


def test_redact_personal_data_structlog_meta_keys_pass_through_untouched() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    event = {"event": "x", "_record": record, "_from_structlog": False}

    result = _redact(event)

    assert result["_record"] is record
    assert result["_from_structlog"] is False


def test_redact_personal_data_3000_deep_dict_is_cut_at_max_depth() -> None:
    event = {"event": "x", "deep": _nested(3_000)}

    result = _redact(event)

    level: Any = result["deep"]
    for _ in range(MAX_DEPTH):
        level = level["child"]
    assert level == DEPTH_EXCEEDED


def test_redact_personal_data_hostile_mapping_returns_redaction_error() -> None:
    event = {
        "event": "report received",
        "level": "info",
        "email": "a@b.org",
        "payload": _ExplodingMapping(),
        "_record": None,
        "_from_structlog": True,
    }

    result = _redact(event)

    assert result == {
        "event": "report received",
        "redaction_error": "RuntimeError",
        "level": "info",
        "_record": None,
        "_from_structlog": True,
    }


def test_redact_personal_data_failure_with_non_string_event_is_unloggable() -> None:
    event = {"event": {"email": "a@b.org"}, "payload": _ExplodingMapping()}

    result = _redact(event)

    assert result == {"event": UNLOGGABLE, "redaction_error": "RuntimeError"}


def test_redact_personal_data_hostile_event_mapping_returns_unloggable() -> None:
    event = _ExplodingEventDict({"event": "report received", "email": "a@b.org"})

    result = dict(redact_personal_data(None, "info", event))

    assert result == {"event": UNLOGGABLE, "redaction_error": "RuntimeError"}


_KEYS = st.one_of(_substring_keys(), _suffix_keys(), st.text(max_size=12))
_LEAVES = st.one_of(st.text(max_size=12), st.integers(), st.none())
_TREES = st.recursive(
    _LEAVES,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.tuples(children, children),
        st.dictionaries(_KEYS, children, max_size=4),
    ),
    max_leaves=20,
)


@given(event=st.dictionaries(_KEYS, _TREES, max_size=6))
def test_redact_personal_data_arbitrary_nesting_never_raises_or_leaks(
    event: dict[str, Any],
) -> None:
    result = _redact(event)

    _assert_no_sensitive_value(result)
    assert result.keys() == event.keys()


# --------------------------------------------------------------------------- #
# configure_logging                                                           #
# --------------------------------------------------------------------------- #


def test_configure_logging_json_format_selects_json_renderer() -> None:
    settings = Settings(_env_file=None, log_format="json")

    configure_logging(settings)

    renderer = _root_formatter().processors[-1]
    assert isinstance(renderer, structlog.processors.JSONRenderer)


def test_configure_logging_console_format_selects_console_renderer() -> None:
    settings = Settings(_env_file=None, log_format="console")

    configure_logging(settings)

    renderer = _root_formatter().processors[-1]
    assert isinstance(renderer, structlog.dev.ConsoleRenderer)


def test_configure_logging_structlog_chain_redacts_and_hands_off_to_stdlib() -> None:
    settings = Settings(_env_file=None)

    configure_logging(settings)

    processors = structlog.get_config()["processors"]
    assert redact_personal_data in processors
    assert processors[-1] is structlog.stdlib.ProcessorFormatter.wrap_for_formatter


def test_configure_logging_called_twice_installs_single_handler() -> None:
    settings = Settings(_env_file=None)

    configure_logging(settings)
    configure_logging(settings)

    installed = [
        handler
        for handler in logging.getLogger().handlers
        if handler.get_name() == "yakhnama"
    ]
    assert len(installed) == 1


def test_configure_logging_warning_level_setting_sets_root_level_to_warning() -> None:
    settings = Settings(_env_file=None, log_level="WARNING")

    configure_logging(settings)

    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_debug_level_setting_keeps_uvicorn_access_at_warning() -> (
    None
):
    settings = Settings(_env_file=None, log_level="DEBUG")

    configure_logging(settings)

    access_logger = logging.getLogger("uvicorn.access")
    assert access_logger.level == logging.WARNING
    assert not access_logger.isEnabledFor(logging.INFO)


def test_configure_logging_structlog_event_is_rendered_as_redacted_utc_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(Settings(_env_file=None, log_format="json"))

    structlog.get_logger("yakhnama.test").info("report received", email="a@b.org")

    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "report received"
    assert line["email"] == REDACTED
    assert line["level"] == "info"
    assert line["timestamp"].endswith("Z")


def test_configure_logging_uvicorn_record_uses_same_pipeline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(Settings(_env_file=None, log_format="json"))

    logging.getLogger("uvicorn.error").info("started on port %d", 8000)

    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "started on port 8000"
    assert line["logger"] == "uvicorn.error"
    assert line["timestamp"].endswith("Z")


def test_configure_logging_unloggable_structlog_event_renders_redaction_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(Settings(_env_file=None, log_format="json"))

    structlog.get_logger("yakhnama.test").info(
        "report received", payload=_ExplodingMapping()
    )

    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "report received"
    assert line["redaction_error"] == "RuntimeError"
    assert "payload" not in line
