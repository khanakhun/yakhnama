"""Unit tests for the ``Event`` aggregate in ``yakhnama.modules.events.domain``."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from tests.factories.events import (
    AffectedPlaceTestFactory,
    EventTestFactory,
    ReportForEventTestFactory,
    ReportLinkTestFactory,
)
from tests.factories.hazards import HAZARD_ATTRIBUTES_BUILDERS
from tests.unit.modules.events.domain.samples import (
    MODERATOR_ID,
    NOW,
    REASON,
    at,
    ids,
    square_geometry,
    stepping_clock,
)
from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.errors import (
    AttributesMismatchError,
    EventImmutableError,
    InvalidEventStatusError,
    InvalidRelationError,
    ReportAlreadyLinkedError,
    ReportNotLinkedError,
)
from yakhnama.modules.events.domain.events import (
    AffectedPlaceAdded,
    EventAttributesChanged,
    EventGeometryChanged,
    EventMerged,
    EventPeriodChanged,
    EventPublished,
    EventRetracted,
    ReportLinkedToEvent,
    ReportUnlinkedFromEvent,
)
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    EventPeriod,
    EventStatus,
)
from yakhnama.modules.hazards.domain.attributes import (
    GlofAttributes,
    LandslideAttributes,
)
from yakhnama.modules.hazards.public import (
    HazardAttributesUnion,
    HazardTypeRef,
)
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.value_objects import Coordinates, Measurement

GLOF = GlofAttributes(
    mechanism="moraine_dam_breach",
    peak_discharge=Measurement(value=1200.0, unit="cubic_metre_per_second"),
)


ATTRIBUTES_ADAPTER: TypeAdapter[HazardAttributesUnion] = TypeAdapter(
    HazardAttributesUnion
)
HAZARD_CODES = sorted(HAZARD_ATTRIBUTES_BUILDERS)


def _attributes(code: str) -> HazardAttributesUnion:
    # The builders are typed with the schema base class; validating the instance
    # through the union narrows it without copying it.
    return ATTRIBUTES_ADAPTER.validate_python(HAZARD_ATTRIBUTES_BUILDERS[code]())


def _event(**overrides: object) -> Event:
    # Re-validated rather than passed to build(), so every override goes through
    # the aggregate's own validators exactly once.
    fields = dict(EventTestFactory.build(created_at=NOW, updated_at=NOW))
    fields.update(overrides)
    return Event.model_validate(fields)


def _fields(event: Event, **overrides: object) -> dict[str, object]:
    fields = dict(event)
    fields.update(overrides)
    return fields


def _retracted() -> Event:
    return (
        _event()
        .retract(REASON, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids())
        .state
    )


def _merged() -> Event:
    return (
        _event()
        .merge_into(
            ids(seed=50).new_id(),
            reason=REASON,
            actor_id=MODERATOR_ID,
            clock=stepping_clock(),
            ids=ids(),
        )
        .state
    )


# --------------------------------------------------------------------------- #
# Invariants                                                                  #
# --------------------------------------------------------------------------- #


def test_event_factory_defaults_build_draft_version_one() -> None:
    event = _event()

    assert event.status is EventStatus.DRAFT
    assert event.version == 1
    assert len(event.source_ids) == 1


def test_event_without_source_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _event(source_ids=())


def test_event_duplicate_source_rejected() -> None:
    source_id = ids().new_id()

    with pytest.raises(PydanticValidationError, match="source_ids"):
        _event(source_ids=(source_id, source_id))


def test_event_two_links_to_one_report_rejected() -> None:
    link = ReportLinkTestFactory.build()
    twin = ReportLinkTestFactory.build(report_id=link.report_id)

    with pytest.raises(PydanticValidationError, match="report links"):
        _event(report_links=(link, twin))


def test_event_duplicate_affected_place_rejected() -> None:
    place = AffectedPlaceTestFactory.build()

    with pytest.raises(PydanticValidationError, match="affected_places"):
        _event(affected_places=(place, place))


@pytest.mark.parametrize("title", ["ab", "x" * 201, "line\nbreak"])
def test_event_title_out_of_bounds_or_multiline_rejected(title: str) -> None:
    with pytest.raises(PydanticValidationError):
        _event(title=title)


def test_event_summary_multiline_accepted() -> None:
    event = _event(summary="First line.\r\nSecond line.")

    assert event.summary == "First line.\nSecond line."


def test_event_attributes_of_other_hazard_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="schema of the event"):
        _event(attributes=LandslideAttributes())


def test_event_attributes_raw_mapping_validated_into_schema() -> None:
    event = _event(attributes={"hazard_type": "glof", "mechanism": "overtopping"})

    assert isinstance(event.attributes, GlofAttributes)
    assert event.attributes.mechanism == "overtopping"


def test_event_attributes_unknown_discriminator_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _event(attributes={"hazard_type": "meteor_strike"})


def test_event_attributes_invalid_field_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _event(attributes={"hazard_type": "glof", "mechanism": "sabotage"})


@pytest.mark.parametrize("code", HAZARD_CODES)
def test_event_attributes_every_schema_round_trips_through_dump(code: str) -> None:
    event = _event(hazard_type=HazardTypeRef(code=code), attributes=_attributes(code))

    reloaded = Event.model_validate(event.model_dump())

    assert reloaded == event
    assert type(reloaded.attributes) is type(event.attributes)


def test_event_attributes_dump_includes_schema_fields() -> None:
    event = _event(attributes=GLOF)

    dumped = event.model_dump()

    assert dumped["attributes"]["mechanism"] == "moraine_dam_breach"
    assert event.attributes is GLOF


def test_event_centroid_differing_from_geometry_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="centroid"):
        _event(
            geometry=square_geometry(),
            centroid=Coordinates(longitude=0.0, latitude=0.0),
        )


def test_event_status_reason_without_final_status_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="status_reason"):
        _event(status_reason=REASON)


def test_event_retracted_without_reason_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="status_reason"):
        _event(status=EventStatus.RETRACTED)


def test_event_merged_without_target_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="merged_into"):
        _event(status=EventStatus.MERGED, status_reason=REASON)


def test_event_merged_into_itself_rejected() -> None:
    event = _event()

    with pytest.raises(PydanticValidationError, match="itself"):
        Event.model_validate(
            _fields(
                event,
                status=EventStatus.MERGED,
                status_reason=REASON,
                merged_into=event.id,
            )
        )


def test_event_updated_before_created_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="updated_at"):
        _event(updated_at=NOW - timedelta(seconds=1))


def test_event_properties_expose_report_ids_and_place_codes() -> None:
    links = tuple(ReportLinkTestFactory.batch(2))
    places = (
        AffectedPlace(place_code="pk.gb.hunza", kind="origin"),
        AffectedPlace(place_code="pk.gb.hunza", kind="impacted"),
    )

    event = _event(report_links=links, affected_places=places)

    assert event.report_ids == (links[0].report_id, links[1].report_id)
    assert event.place_codes == frozenset({"pk.gb.hunza"})


# --------------------------------------------------------------------------- #
# link_report and unlink_report                                               #
# --------------------------------------------------------------------------- #


def test_link_report_new_report_adds_link_and_cites_its_source() -> None:
    event = _event()
    report = ReportForEventTestFactory.build()

    change = event.link_report(
        report,
        role="supporting",
        linked_by=MODERATOR_ID,
        clock=stepping_clock(),
        ids=ids(),
    )

    linked = change.state
    assert linked.report_ids == (report.id,)
    assert linked.report_links[0].role == "supporting"
    assert linked.report_links[0].linked_at == NOW
    assert linked.source_ids == (*event.source_ids, report.source_id)
    assert linked.version == 2
    (domain_event,) = change.events
    assert isinstance(domain_event, ReportLinkedToEvent)
    assert domain_event.report_id == report.id
    assert domain_event.role == "supporting"
    assert domain_event.actor_id == MODERATOR_ID


def test_link_report_already_cited_source_not_duplicated() -> None:
    event = _event()
    report = ReportForEventTestFactory.build(source_id=event.source_ids[0])

    linked = event.link_report(
        report,
        role="primary",
        linked_by=MODERATOR_ID,
        clock=stepping_clock(),
        ids=ids(),
    ).state

    assert linked.source_ids == event.source_ids


def test_link_report_twice_raises_report_already_linked() -> None:
    report = ReportForEventTestFactory.build()
    event = _event().link_report(
        report,
        role="primary",
        linked_by=MODERATOR_ID,
        clock=stepping_clock(),
        ids=ids(),
    )

    with pytest.raises(ReportAlreadyLinkedError) as raised:
        event.state.link_report(
            report,
            role="supporting",
            linked_by=MODERATOR_ID,
            clock=stepping_clock(),
            ids=ids(),
        )

    assert raised.value.report_id == report.id


def test_unlink_report_linked_report_moves_it_to_unlinked_record() -> None:
    report = ReportForEventTestFactory.build()
    linked = (
        _event()
        .link_report(
            report,
            role="contradicting",
            linked_by=MODERATOR_ID,
            clock=stepping_clock(),
            ids=ids(),
        )
        .state
    )

    change = linked.unlink_report(
        report.id,
        reason=REASON,
        actor_id=MODERATOR_ID,
        clock=stepping_clock(NOW + timedelta(hours=1)),
        ids=ids(),
    )

    unlinked = change.state
    assert unlinked.report_links == ()
    (record,) = unlinked.unlinked_reports
    assert record.report_id == report.id
    assert record.role == "contradicting"
    assert record.reason == REASON
    assert record.unlinked_at == NOW + timedelta(hours=1)
    assert report.source_id in unlinked.source_ids
    (domain_event,) = change.events
    assert isinstance(domain_event, ReportUnlinkedFromEvent)
    assert REASON not in domain_event.model_dump_json()


def test_unlink_report_not_linked_raises_report_not_linked() -> None:
    event = _event()
    report_id = ids(seed=77).new_id()

    with pytest.raises(ReportNotLinkedError) as raised:
        event.unlink_report(
            report_id,
            reason=REASON,
            actor_id=MODERATOR_ID,
            clock=stepping_clock(),
            ids=ids(),
        )

    assert raised.value.event_id == event.id


def test_unlink_report_blank_reason_raises_pydantic_error() -> None:
    report = ReportForEventTestFactory.build()
    linked = (
        _event()
        .link_report(
            report,
            role="primary",
            linked_by=MODERATOR_ID,
            clock=stepping_clock(),
            ids=ids(),
        )
        .state
    )

    with pytest.raises(PydanticValidationError):
        linked.unlink_report(
            report.id,
            reason="   ",
            actor_id=MODERATOR_ID,
            clock=stepping_clock(),
            ids=ids(),
        )


# --------------------------------------------------------------------------- #
# Places, geometry, period, attributes                                        #
# --------------------------------------------------------------------------- #


def test_add_affected_place_new_place_appends_and_emits_event() -> None:
    event = _event()
    place = AffectedPlace(place_code="pk.gb.hunza", kind="impacted")

    change = event.add_affected_place(
        place, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state.affected_places == (place,)
    (domain_event,) = change.events
    assert isinstance(domain_event, AffectedPlaceAdded)
    assert (domain_event.place_code, domain_event.kind) == ("pk.gb.hunza", "impacted")


def test_add_affected_place_present_place_is_no_op() -> None:
    place = AffectedPlace(place_code="pk.gb.hunza", kind="impacted")
    event = _event(affected_places=(place,))

    change = event.add_affected_place(
        place, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state is event
    assert change.events == ()


def test_set_geometry_polygon_moves_centroid_to_geometry_centroid() -> None:
    event = _event()
    geometry = square_geometry()

    change = event.set_geometry(
        geometry, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state.geometry == geometry
    assert change.state.centroid == geometry.centroid()
    (domain_event,) = change.events
    assert isinstance(domain_event, EventGeometryChanged)
    assert domain_event.geometry_type == "Polygon"
    assert domain_event.centroid == geometry.centroid()


def test_set_geometry_none_keeps_centroid_and_reports_removal() -> None:
    geometry = square_geometry()
    event = _event(geometry=geometry, centroid=geometry.centroid())

    change = event.set_geometry(
        None, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state.geometry is None
    assert change.state.centroid == geometry.centroid()
    (domain_event,) = change.events
    assert isinstance(domain_event, EventGeometryChanged)
    assert domain_event.geometry_type is None


def test_set_geometry_same_geometry_is_no_op() -> None:
    geometry = square_geometry()
    event = _event(geometry=geometry, centroid=geometry.centroid())

    change = event.set_geometry(
        square_geometry(), actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state is event
    assert change.events == ()


def test_set_period_new_period_replaces_and_emits_event() -> None:
    event = _event()
    period = EventPeriod(started_at=at(2022, 8, 14), ended_at=at(2022, 8, 20))

    change = event.set_period(
        period, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state.period == period
    (domain_event,) = change.events
    assert isinstance(domain_event, EventPeriodChanged)
    assert domain_event.started_at == period.started_at
    assert domain_event.ended_at == period.ended_at


def test_set_period_same_period_is_no_op() -> None:
    event = _event()

    change = event.set_period(
        event.period, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state is event
    assert change.events == ()


def test_set_attributes_matching_schema_sets_and_emits_event() -> None:
    event = _event()

    change = event.set_attributes(
        GLOF, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state.attributes == GLOF
    (domain_event,) = change.events
    assert isinstance(domain_event, EventAttributesChanged)
    assert domain_event.hazard_code == "glof"
    assert domain_event.has_attributes


def test_set_attributes_other_hazard_raises_attributes_mismatch() -> None:
    event = _event()

    with pytest.raises(AttributesMismatchError) as raised:
        event.set_attributes(
            LandslideAttributes(),
            actor_id=MODERATOR_ID,
            clock=stepping_clock(),
            ids=ids(),
        )

    assert (raised.value.hazard_code, raised.value.attributes_code) == (
        "glof",
        "landslide",
    )


@pytest.mark.parametrize("code", HAZARD_CODES)
def test_set_attributes_every_schema_accepted_for_its_hazard_type(code: str) -> None:
    event = _event(hazard_type=HazardTypeRef(code=code))
    attributes = _attributes(code)

    change = event.set_attributes(
        attributes, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state.attributes == attributes
    (domain_event,) = change.events
    assert isinstance(domain_event, EventAttributesChanged)
    assert domain_event.hazard_code == code


@pytest.mark.parametrize("code", HAZARD_CODES)
def test_set_attributes_every_other_schema_raises_attributes_mismatch(
    code: str,
) -> None:
    event = _event(hazard_type=HazardTypeRef(code=code))
    others = [other for other in HAZARD_CODES if other != code]

    for other in others:
        with pytest.raises(AttributesMismatchError) as raised:
            event.set_attributes(
                _attributes(other),
                actor_id=MODERATOR_ID,
                clock=stepping_clock(),
                ids=ids(),
            )
        assert raised.value.attributes_code == other


def test_set_attributes_none_clears_them() -> None:
    event = _event(attributes=GLOF)

    change = event.set_attributes(
        None, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state.attributes is None
    (domain_event,) = change.events
    assert isinstance(domain_event, EventAttributesChanged)
    assert not domain_event.has_attributes


def test_set_attributes_equal_attributes_is_no_op() -> None:
    event = _event(attributes=GLOF)

    change = event.set_attributes(
        GLOF.model_copy(), actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state is event
    assert change.events == ()


# --------------------------------------------------------------------------- #
# publish, retract, merge_into                                                #
# --------------------------------------------------------------------------- #


def test_publish_draft_becomes_published() -> None:
    event = _event()

    change = event.publish(actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids())

    assert change.state.status is EventStatus.PUBLISHED
    (domain_event,) = change.events
    assert isinstance(domain_event, EventPublished)
    assert domain_event.event_type == "events.event_published"


def test_publish_published_raises_invalid_event_status() -> None:
    published = _event(status=EventStatus.PUBLISHED)

    with pytest.raises(InvalidEventStatusError) as raised:
        published.publish(actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids())

    assert raised.value.status == "published"


@pytest.mark.parametrize("status", [EventStatus.DRAFT, EventStatus.PUBLISHED])
def test_retract_open_event_keeps_record_with_reason(status: EventStatus) -> None:
    event = _event(status=status)

    change = event.retract(
        REASON, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    )

    assert change.state.status is EventStatus.RETRACTED
    assert change.state.status_reason == REASON
    assert change.state.id == event.id
    (domain_event,) = change.events
    assert isinstance(domain_event, EventRetracted)
    assert REASON not in domain_event.model_dump_json()


def test_retract_blank_reason_raises_pydantic_error() -> None:
    event = _event()

    with pytest.raises(PydanticValidationError):
        event.retract(" ", actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids())


def test_merge_into_other_event_marks_merged_with_target() -> None:
    event = _event(status=EventStatus.PUBLISHED)
    target_id = ids(seed=50).new_id()

    change = event.merge_into(
        target_id,
        reason=REASON,
        actor_id=MODERATOR_ID,
        clock=stepping_clock(),
        ids=ids(),
    )

    assert change.state.status is EventStatus.MERGED
    assert change.state.merged_into == target_id
    assert change.state.status_reason == REASON
    (domain_event,) = change.events
    assert isinstance(domain_event, EventMerged)
    assert domain_event.target_event_id == target_id


def test_merge_into_itself_raises_invalid_relation() -> None:
    event = _event()

    with pytest.raises(InvalidRelationError):
        event.merge_into(
            event.id,
            reason=REASON,
            actor_id=MODERATOR_ID,
            clock=stepping_clock(),
            ids=ids(),
        )


type Operation = Callable[[Event], AggregateChange[Event]]

OPERATIONS: dict[str, Operation] = {
    "link_report": lambda event: event.link_report(
        ReportForEventTestFactory.build(),
        role="primary",
        linked_by=MODERATOR_ID,
        clock=stepping_clock(),
        ids=ids(),
    ),
    "unlink_report": lambda event: event.unlink_report(
        ids(seed=3).new_id(),
        reason=REASON,
        actor_id=MODERATOR_ID,
        clock=stepping_clock(),
        ids=ids(),
    ),
    "add_affected_place": lambda event: event.add_affected_place(
        AffectedPlaceTestFactory.build(),
        actor_id=MODERATOR_ID,
        clock=stepping_clock(),
        ids=ids(),
    ),
    "set_geometry": lambda event: event.set_geometry(
        square_geometry(), actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    ),
    "set_period": lambda event: event.set_period(
        EventPeriod(started_at=at(2020, 1, 1)),
        actor_id=MODERATOR_ID,
        clock=stepping_clock(),
        ids=ids(),
    ),
    "set_attributes": lambda event: event.set_attributes(
        GLOF, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    ),
    "publish": lambda event: event.publish(
        actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    ),
    "retract": lambda event: event.retract(
        REASON, actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids()
    ),
    "merge_into": lambda event: event.merge_into(
        ids(seed=60).new_id(),
        reason=REASON,
        actor_id=MODERATOR_ID,
        clock=stepping_clock(),
        ids=ids(),
    ),
}


@pytest.mark.parametrize(
    "final_event", [_retracted, _merged], ids=["retracted", "merged"]
)
@pytest.mark.parametrize("operation", list(OPERATIONS))
def test_final_event_any_operation_raises_event_immutable(
    final_event: Callable[[], Event], operation: str
) -> None:
    event = final_event()

    with pytest.raises(EventImmutableError) as raised:
        OPERATIONS[operation](event)

    assert raised.value.operation == operation
    assert raised.value.status == event.status.value


def test_event_change_does_not_mutate_original() -> None:
    event = _event()

    event.publish(actor_id=MODERATOR_ID, clock=stepping_clock(), ids=ids())

    assert event.status is EventStatus.DRAFT
    assert event.version == 1
