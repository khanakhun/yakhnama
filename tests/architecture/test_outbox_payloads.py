"""Structural rule for outbox payloads: identifiers and non-personal fields only.

The outbox stores every domain event whole (``platform/outbox/writer.py``) and keeps
it for ``outbox_retention_days``; subscribers and operators read it. Free text,
places people stood and measured figures belong on the aggregate, where corrections,
retractions and personal-data removal reach them, not in a copy that outlives them.
So every field of every ``DomainEvent`` subclass in ``modules/*/domain/events.py``
must be one of:

- an id (``UUID``, ``EntityId``), an ``Enum`` or a ``Literal``;
- a ``str`` that carries a ``pattern`` constraint (a code, slug or status: free text
  has no pattern);
- an ``int``, a ``bool`` or a ``datetime``;
- a value object whose own fields pass the same rule (``DateWithPrecision``);
- an optional, ``tuple`` or ``frozenset`` of the above.

Independently of its type, a non-boolean field whose name contains a free-text word
(``FREE_TEXT_WORDS``) is refused, and ``Coordinates``, ``BoundingBox`` and
``Measurement`` are refused by name. ``float`` and ``Decimal`` are refused because
every such field of the domain today is a coordinate, a measurement or an amount.

``ALLOWED_EXCEPTIONS`` names the fields that break the letter of the rule but not its
purpose. ``PENDING_REMOVAL`` names the offenders that existed when the rule was
introduced (Phase 4, T7); each belongs to a domain owner, who drops it from the event
(the aggregate keeps it) and deletes its entry. Both lists may only shrink: an entry
that no longer offends fails ``test_outbox_payload_allow_lists_name_live_offenders``.
"""

import importlib
import inspect
import types
import typing
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated, Final, Literal
from uuid import UUID

import pytest
from pydantic import AwareDatetime, BaseModel
from pydantic.fields import FieldInfo

from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.value_objects import BoundingBox, Coordinates, Measurement

MODULES_ROOT: Final = (
    Path(__file__).resolve().parents[2] / "src" / "yakhnama" / "modules"
)

FREE_TEXT_WORDS: Final = frozenset(
    {
        "description",
        "reason",
        "note",
        "name",
        "text",
        "title",
        "summary",
        "citation",
        "url",
        # Not in the T7 brief's list, but the same kind of content: moderator or
        # curator prose. "label" is left out on purpose: version labels are codes,
        # and display labels (LocalizedText) are refused by their mapping type.
        "explanation",
    }
)
FORBIDDEN_VALUE_OBJECTS: Final[dict[type[BaseModel], str]] = {
    Coordinates: "coordinates",
    BoundingBox: "coordinates (bounding box)",
    Measurement: "a measurement",
}

# AwareDatetime is Pydantic's marker for a timezone-aware datetime, not a subclass.
ALLOWED_SCALARS: Final = frozenset({UUID, bool, int, datetime, AwareDatetime})

type FieldKey = tuple[str, str]
"""``(event class name, top-level field name)``."""

ALLOWED_EXCEPTIONS: Final[dict[FieldKey, str]] = {
    # The names of the SourceDetails fields that changed, drawn from a closed set of
    # seven attribute names; never their values.
    ("SourceDetailsUpdated", "changed_fields"): "field names, not values",
    # A source-adapter registry key with a pattern (``local_csv_temperature``), not
    # a display name.
    ("IngestionRunRequested", "adapter_name"): "registry key, not a display name",
}

PENDING_REMOVAL: Final[dict[FieldKey, str]] = {
    # Owned by the events module's domain-modeler.
    ("EventGeometryChanged", "centroid"): "coordinates",
    # Owned by the geography module's domain-modeler.
    ("PlaceCentroidChanged", "previous_centroid"): "coordinates",
    ("PlaceCentroidChanged", "centroid"): "coordinates",
    ("PlaceCreated", "names"): "place names (free text)",
    ("PlaceCreated", "centroid"): "coordinates",
    ("PlaceGeometryChanged", "bounding_box"): "coordinates",
    ("PlaceMerged", "reason"): "free text",
    ("PlaceNameAdded", "name"): "place name (free text)",
    ("PlacePreferredNameChanged", "previous_text"): "free text",
    ("PlacePreferredNameChanged", "text"): "free text",
    ("PlaceRetired", "reason"): "free text",
    # Owned by the hazards module's domain-modeler.
    ("HazardTypeReactivated", "reason"): "free text",
    ("HazardTypeRelabelled", "labels"): "labels (free text)",
    ("HazardTypeRetired", "reason"): "free text",
    # Owned by the impacts module's domain-modeler.
    ("ImpactClaimCorrected", "value"): "a measurement or amount",
    ("ImpactClaimRecorded", "value"): "a measurement or amount",
    ("ImpactMetricCreated", "currency"): "unconstrained str (a code without a pattern)",
    ("ImpactMetricRelabelled", "labels"): "labels (free text)",
    ("ImpactMetricRetired", "reason"): "free text",
    ("InfrastructureAssetRelocated", "location"): "coordinates",
}


def _event_classes() -> list[type[DomainEvent]]:
    classes: list[type[DomainEvent]] = []
    for path in sorted(MODULES_ROOT.glob("*/domain/events.py")):
        module = importlib.import_module(
            f"yakhnama.modules.{path.parts[-3]}.domain.events"
        )
        classes.extend(
            member
            for _, member in inspect.getmembers(module, inspect.isclass)
            if issubclass(member, DomainEvent)
            and member is not DomainEvent
            and member.__module__ == module.__name__
        )
    return classes


def _name_offence(field_name: str) -> str | None:
    # "names" and "labels" are the same content as "name" and "label".
    words = {word.removesuffix("s") for word in field_name.split("_")} | set(
        field_name.split("_")
    )
    found = sorted(words & FREE_TEXT_WORDS)
    return f"name contains free-text word {found[0]!r}" if found else None


def _has_pattern(metadata: typing.Iterable[object]) -> bool:
    for item in metadata:
        if isinstance(item, FieldInfo) and _has_pattern(item.metadata):
            return True
        if getattr(item, "pattern", None) is not None:
            return True
    return False


def _is_boolean(annotation: object) -> bool:
    return annotation is bool


def _members_offence(
    members: typing.Iterable[object], metadata: tuple[object, ...], path: str
) -> str | None:
    for member in members:
        if member is not type(None) and member is not Ellipsis:
            offence = _type_offence(member, metadata, path)
            if offence is not None:
                return offence
    return None


def _type_offence(
    annotation: object, metadata: tuple[object, ...], path: str
) -> str | None:
    origin = typing.get_origin(annotation)
    if origin is Annotated:
        base, *extra = typing.get_args(annotation)
        return _type_offence(base, (*metadata, *extra), path)
    if origin in {typing.Union, types.UnionType}:
        return _members_offence(typing.get_args(annotation), metadata, path)
    if origin in {tuple, frozenset}:
        # The container's own constraints (length) say nothing about its items.
        return _members_offence(typing.get_args(annotation), (), f"{path}[]")
    if origin is Literal or annotation in ALLOWED_SCALARS:
        return None
    if isinstance(annotation, type):
        return _class_offence(annotation, metadata, path)
    return f"{path}: unsupported annotation {annotation!r}"


def _forbidden_class_offence(annotation: type, path: str) -> str | None:
    if annotation in {float, Decimal}:
        return f"{path}: {annotation.__name__} (a coordinate, measurement or amount)"
    for forbidden, description in FORBIDDEN_VALUE_OBJECTS.items():
        if issubclass(annotation, forbidden):
            return f"{path}: {description}"
    return None


def _class_offence(
    annotation: type, metadata: tuple[object, ...], path: str
) -> str | None:
    if annotation is str:
        return None if _has_pattern(metadata) else f"{path}: str without a pattern"
    if issubclass(annotation, Enum):
        return None
    forbidden = _forbidden_class_offence(annotation, path)
    if forbidden is not None:
        return forbidden
    if issubclass(annotation, BaseModel):
        for name, info in annotation.model_fields.items():
            offence = _field_offence(name, info, f"{path}.{name}")
            if offence is not None:
                return offence
        return None
    return f"{path}: {annotation.__name__} is not an id, code, number, flag or time"


def _field_offence(name: str, info: FieldInfo, path: str) -> str | None:
    if not _is_boolean(info.annotation):
        name_problem = _name_offence(name)
        if name_problem is not None:
            return f"{path}: {name_problem}"
    return _type_offence(info.annotation, tuple(info.metadata), path)


def payload_offences(event_class: type[DomainEvent]) -> dict[str, str]:
    """Return every top-level field of ``event_class`` that breaks the rule.

    Args:
        event_class: A domain event class.

    Returns:
        The offence message by field name; empty when the event is ids-only.
    """
    offences: dict[str, str] = {}
    for name, info in event_class.model_fields.items():
        offence = _field_offence(name, info, f"{event_class.__name__}.{name}")
        if offence is not None:
            offences[name] = offence
    return offences


def _all_offences() -> dict[FieldKey, str]:
    return {
        (event_class.__name__, field): offence
        for event_class in _event_classes()
        for field, offence in payload_offences(event_class).items()
    }


def test_outbox_payload_every_event_module_is_scanned() -> None:
    classes = _event_classes()

    modules = {event_class.__module__.split(".")[2] for event_class in classes}
    assert len(classes) > 0
    assert modules >= {"events", "reports", "verification", "impacts", "provenance"}


def test_outbox_payload_every_event_field_is_an_id_code_number_flag_or_time() -> None:
    offences = _all_offences()

    unexpected = {
        key: offence
        for key, offence in offences.items()
        if key not in ALLOWED_EXCEPTIONS and key not in PENDING_REMOVAL
    }
    assert unexpected == {}


def test_outbox_payload_allow_lists_name_live_offenders() -> None:
    offences = _all_offences()

    stale = sorted(
        key for key in (*ALLOWED_EXCEPTIONS, *PENDING_REMOVAL) if key not in offences
    )
    assert stale == []


def test_outbox_payload_allow_lists_do_not_overlap() -> None:
    overlap = set(ALLOWED_EXCEPTIONS) & set(PENDING_REMOVAL)

    assert overlap == set()


class _IdsOnlyEvent(DomainEvent):
    """Synthetic event whose fields all pass the rule.

    Implements: Domain Events (test fixture).
    """

    event_type = "architecture_test.ids_only"

    report_id: UUID
    related_ids: tuple[UUID, ...]
    status: Literal["open", "closed"]
    code: Annotated[str, FieldInfo(pattern=r"^[a-z]+$")]
    count: int
    has_reason: bool
    decided_at: datetime | None


class _FreeTextEvent(DomainEvent):
    """Synthetic event with one offending field of each kind.

    Implements: Domain Events (test fixture).
    """

    event_type = "architecture_test.free_text"

    reason: Annotated[str, FieldInfo(pattern=r"^.+$")]
    comment: str
    location: Coordinates | None
    amounts: tuple[Decimal, ...]
    extent: BoundingBox
    depth: Measurement
    urls: tuple[Annotated[str, FieldInfo(pattern=r"^https://")], ...]
    ratio: float


def test_payload_offences_ids_only_event_has_none() -> None:
    offences = payload_offences(_IdsOnlyEvent)

    assert offences == {}


def test_payload_offences_flags_each_forbidden_field_kind() -> None:
    offences = payload_offences(_FreeTextEvent)

    assert set(offences) == {
        "reason",
        "comment",
        "location",
        "amounts",
        "extent",
        "depth",
        "urls",
        "ratio",
    }
    assert "free-text word 'reason'" in offences["reason"]
    assert "str without a pattern" in offences["comment"]
    assert "coordinates" in offences["location"]
    assert "Decimal" in offences["amounts"]
    assert "bounding box" in offences["extent"]
    assert "measurement" in offences["depth"]
    assert "free-text word 'url'" in offences["urls"]
    assert "float" in offences["ratio"]


@pytest.mark.parametrize(
    ("field_name", "is_offending"),
    [
        ("status_reason", True),
        ("names", True),
        ("previous_text", True),
        ("place_code", False),
        ("flag_kinds", False),
        ("status", False),
    ],
)
def test_name_offence_matches_free_text_words(
    field_name: str, *, is_offending: bool
) -> None:
    offence = _name_offence(field_name)

    assert (offence is not None) is is_offending
