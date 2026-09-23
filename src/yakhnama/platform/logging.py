"""Structured logging with personal-data redaction.

Every log line, whether emitted through ``structlog`` or through the standard library
(uvicorn, SQLAlchemy, third-party libraries), passes through one processor chain:
context variables, log level, logger name, an ISO-8601 UTC timestamp, the personal-data
redaction processor and finally a JSON or console renderer.

Reporters in Gilgit-Baltistan may be at risk if their identity or location leaks, so
the redaction processor runs on every event before rendering (spec §10, ``AGENTS.md``
§5: never store personal data in logs).

Patterns: none from the catalog; this module configures a third-party pipeline.
"""

import dataclasses
import logging
import sys
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel
from structlog.typing import EventDict, Processor, WrappedLogger

# Imported at runtime (not under TYPE_CHECKING) because Python 3.13 evaluates
# annotations eagerly.
from yakhnama.platform.settings import Settings

REDACTED = "[REDACTED]"
CYCLE = "[CYCLE]"
DEPTH_EXCEEDED = "[DEPTH]"
UNLOGGABLE = "[UNLOGGABLE]"

# Deeper structures are cut rather than walked: a log payload never legitimately
# nests this far, and a hard cap keeps a hostile or accidental 3,000-level structure
# from exhausting the interpreter stack inside the logging pipeline.
MAX_DEPTH = 32

# Keys are compared after lower-casing and removing "_" and "-", so "Reporter-Phone",
# "reporterPhone" and "reporter_phone" all match. A key is sensitive when its
# normalised form CONTAINS one of these markers. Categories and why:
# - credentials (password, passwd, secret, token, apikey, authorization, cookie,
#   session, privatekey, credential): leaking them in logs grants account or system
#   access; substring matching catches "db_password", "id_token", "session_id".
# - contact and identity (email, phone, mobile, whatsapp, cnic, nationalid, address):
#   lets a third party identify, locate or contact a reporter; CNIC is Pakistan's
#   national id number.
# - location (latitude, longitude, coordinates, geometry, location, gps): precise
#   coordinates of a report can pinpoint a reporter's home; public output is coarsened
#   separately via ``Settings.public_coordinate_decimals``.
# Substring matching over-redacts some harmless keys (for example "allocation"
# contains "location"); losing a log value is cheap, leaking a reporter is not.
SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "authorization",
    "cookie",
    "session",
    "privatekey",
    "credential",
    "email",
    "phone",
    "mobile",
    "whatsapp",
    "cnic",
    "nationalid",
    "address",
    "latitude",
    "longitude",
    "coordinates",
    "geometry",
    "location",
    "gps",
)

# These markers are too short to match as substrings ("lat" is inside "translate",
# "ip" inside "description"), so they only match at the END of the normalised key:
# "first_name", "reporter_lat", "client_ip", "home_addr".
SENSITIVE_KEY_SUFFIXES: tuple[str, ...] = ("name", "lat", "lon", "lng", "ip", "addr")

# Normalised keys ending in "name" that are known not to name a person. Anything
# unknown ending in "name" is redacted; add to the allow-list deliberately, after
# checking the key can never hold a person's name.
NON_PERSONAL_NAME_KEYS: frozenset[str] = frozenset(
    {
        "hazardname",
        "placename",
        "metricname",
        "modulename",
        "funcname",
        "filename",
        "hostname",
        "datasetname",
        "eventname",
        "loggername",
        "appname",
        "typename",
        "classname",
        "fieldname",
        "tablename",
        "columnname",
        "servicename",
    }
)

# Values of these types are rendered as-is: their JSON or console form is a plain
# value that cannot hide nested personal data behind a custom ``repr``.
_SCALAR_TYPES: tuple[type, ...] = (
    str,
    int,
    float,
    bool,
    type(None),
    datetime,
    date,
    UUID,
    Enum,
)

# structlog's ProcessorFormatter stores the LogRecord and a flag under these keys and
# ``remove_processors_meta`` deletes them before rendering (raising if they are
# missing), so they pass through redaction untouched.
_STRUCTLOG_META_KEYS: frozenset[str] = frozenset({"_record", "_from_structlog"})

# Keys our own earlier processors fill with plain strings; kept in the fallback event
# so an unloggable event still shows when, where and how severe it was.
_FALLBACK_STRING_KEYS: tuple[str, ...] = ("level", "logger", "timestamp")

_HANDLER_NAME = "yakhnama"
_STDLIB_LOGGERS_TO_UNIFY = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _normalise_key(key: str) -> str:
    """Return ``key`` lower-cased with ``_`` and ``-`` removed."""
    return key.lower().replace("_", "").replace("-", "")


def is_sensitive_key(key: object) -> bool:
    """Tell whether a mapping key names personal or secret data.

    The key is normalised (lower-cased, ``_`` and ``-`` removed) and is sensitive if
    it contains a marker from ``SENSITIVE_KEY_SUBSTRINGS`` or ends with a marker from
    ``SENSITIVE_KEY_SUFFIXES``, unless it is one of ``NON_PERSONAL_NAME_KEYS``.

    Args:
        key: A mapping key of any type; only strings can be sensitive.

    Returns:
        ``True`` if ``key`` is a string that names personal or secret data.
    """
    if not isinstance(key, str):
        return False
    normalised = _normalise_key(key)
    if any(marker in normalised for marker in SENSITIVE_KEY_SUBSTRINGS):
        return True
    if normalised in NON_PERSONAL_NAME_KEYS:
        return False
    return normalised.endswith(SENSITIVE_KEY_SUFFIXES)


def _children_of(value: object) -> Mapping[Any, Any] | list[Any] | None:
    """Return the walkable contents of a container, or ``None`` for anything else.

    Pydantic models and dataclasses are opened up so their fields are redacted by key
    like any mapping; sets become lists because their order carries no meaning.
    Dataclass fields are read directly instead of through ``dataclasses.asdict`` so
    that nested values are not deep-copied and cycles are still detected here.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: getattr(value, field.name)
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return value
    if isinstance(value, list | tuple | set | frozenset):
        return list(value)
    return None


# Values are Any because a log event carries whatever the caller passed; this is the
# structlog boundary, not a layer boundary inside Yakhnama.
def _redact_value(value: Any, ancestors: frozenset[int]) -> Any:  # noqa: ANN401  # reason: arbitrary log payload at the structlog boundary
    """Return a redacted, render-safe copy of ``value`` without mutating it.

    ``ancestors`` holds the ids of the containers on the current path so that a
    self-referencing structure is cut instead of recursing forever, and its size is
    the current depth.
    """
    if isinstance(value, _SCALAR_TYPES):
        return value
    children = _children_of(value)
    if children is None:
        # Never let the renderer call repr() on an unknown object: its repr may
        # embed personal data that no key-based rule can see.
        return f"<{type(value).__name__}>"
    if id(value) in ancestors:
        return CYCLE
    if len(ancestors) >= MAX_DEPTH:
        return DEPTH_EXCEEDED
    path = ancestors | {id(value)}
    if isinstance(children, Mapping):
        return {
            key: REDACTED if is_sensitive_key(key) else _redact_value(item, path)
            for key, item in children.items()
        }
    return [_redact_value(item, path) for item in children]


def _fallback_event(event_dict: EventDict, error: Exception) -> EventDict:
    """Return a minimal, safe event for an event that could not be redacted.

    Only plain ``dict`` methods and type checks are used, so this cannot raise on a
    hostile event. structlog always passes a ``dict``; any other mapping is treated as
    empty rather than trusted.
    """
    source: dict[str, Any] = event_dict if isinstance(event_dict, dict) else {}
    original_event = dict.get(source, "event")
    fallback: EventDict = {
        "event": original_event if isinstance(original_event, str) else UNLOGGABLE,
        "redaction_error": type(error).__name__,
    }
    for key in _FALLBACK_STRING_KEYS:
        value = dict.get(source, key)
        if isinstance(value, str):
            fallback[key] = value
    for key in _STRUCTLOG_META_KEYS:
        if dict.__contains__(source, key):
            fallback[key] = dict.__getitem__(source, key)
    return fallback


def redact_personal_data(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Replace personal and secret values in a log event with ``"[REDACTED]"``.

    Keys are matched with ``is_sensitive_key``. Mappings, lists, tuples, sets,
    Pydantic models and dataclasses are traversed up to ``MAX_DEPTH`` levels; a
    matching value is replaced whole, whatever its type. Any other non-scalar value is
    replaced by ``"<TypeName>"`` so the renderer never calls ``repr`` on it. The
    caller's objects are never mutated because they may be live application data.

    The processor never raises: if walking the event fails (for example a mapping
    whose ``items()`` raises), a minimal event carrying the original message and a
    ``redaction_error`` field with the exception type is returned instead, so a bad
    log call can neither crash the request nor leak the unredacted payload.

    ``event_dict`` is a plain mutable mapping because that is structlog's processor
    contract; it is an external-library boundary, not a Yakhnama layer boundary.

    Args:
        logger: The wrapped logger (unused; required by the processor signature).
        method_name: The log method name (unused; required by the signature).
        event_dict: The event being logged.

    Returns:
        A new event dict with every sensitive value redacted.
    """
    del logger, method_name
    try:
        return {
            key: (
                value
                if key in _STRUCTLOG_META_KEYS
                else REDACTED
                if is_sensitive_key(key)
                else _redact_value(value, frozenset())
            )
            for key, value in event_dict.items()
        }
    # Blind by design: this processor sits inside the logging pipeline, where an
    # exception would either crash the caller or make logging print the raw record.
    # The error is not swallowed; its type is carried in the emitted event.
    except Exception as error:  # noqa: BLE001  # reason: logging must never raise; error type is logged
        return _fallback_event(event_dict, error)


def _build_renderer(settings: Settings) -> Processor:
    """Return the final renderer selected by ``settings.log_format``."""
    if settings.log_format == "console":
        return structlog.dev.ConsoleRenderer(colors=False)
    return structlog.processors.JSONRenderer()


def configure_logging(settings: Settings) -> None:
    """Configure structlog and the standard library to share one pipeline.

    structlog events are handed to the standard library, and a single root handler
    renders both them and foreign records (uvicorn, libraries) with the same
    processors, so every line has the same shape and is redacted. Calling this more
    than once replaces the previously installed handler instead of stacking handlers.

    Args:
        settings: Supplies ``log_level`` and ``log_format``.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_personal_data,
    ]
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _build_renderer(settings),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Only our own handler is replaced; handlers installed by others (for example
    # pytest's capture handler) stay in place.
    for existing_handler in [
        installed_handler
        for installed_handler in root.handlers
        if installed_handler.get_name() == _HANDLER_NAME
    ]:
        root.removeHandler(existing_handler)
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # uvicorn installs its own handlers; route its records through the root handler
    # instead so they are rendered and redacted like everything else.
    for name in _STDLIB_LOGGERS_TO_UNIFY:
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers.clear()
        stdlib_logger.propagate = True

    # uvicorn's access log is a positional message containing the client IP and the
    # full query string, which key-based redaction cannot see. It is silenced here;
    # request logging arrives in Phase 2 as a middleware that logs only the method,
    # route template, status and duration.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
