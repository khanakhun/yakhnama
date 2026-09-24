"""Unit tests for ``EventGraph`` in ``yakhnama.modules.events.domain.entities``."""

import itertools
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.events import EventRelationTestFactory
from tests.unit.modules.events.domain.samples import ids
from yakhnama.modules.events.domain.entities import EventGraph
from yakhnama.modules.events.domain.errors import InvalidRelationError
from yakhnama.modules.events.domain.events import EventsRelated
from yakhnama.modules.events.domain.value_objects import EventRelation, RelationKind

K = RelationKind
_NODE_IDS = ids(seed=40)
NODES = tuple(_NODE_IDS.new_id() for _ in range(6))
EDGES = st.lists(
    st.tuples(st.sampled_from(NODES), st.sampled_from(NODES)).filter(
        lambda pair: pair[0] != pair[1]
    ),
    max_size=15,
)


def _relation(from_id: UUID, to_id: UUID, kind: RelationKind) -> EventRelation:
    return EventRelationTestFactory.build(
        from_event_id=from_id, to_event_id=to_id, kind=kind
    )


def _reaches(edges: set[tuple[UUID, UUID]], start: UUID, goal: UUID) -> bool:
    seen: set[UUID] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        for source, target in edges:
            if source == current and target not in seen:
                if target == goal:
                    return True
                seen.add(target)
                pending.append(target)
    return False


def test_event_graph_empty_has_no_relations_or_cycle() -> None:
    graph = EventGraph()

    assert graph.related(NODES[0]) == ()
    assert graph.find_part_of_cycle() is None
    assert graph.same_as_group(NODES[0]) == frozenset({NODES[0]})


def test_event_graph_related_returns_relations_touching_event() -> None:
    first = _relation(NODES[0], NODES[1], K.TRIGGERED_BY)
    second = _relation(NODES[2], NODES[0], K.PART_OF)
    other = _relation(NODES[3], NODES[4], K.SAME_AS)
    graph = EventGraph(relations=(first, second, other))

    related = graph.related(NODES[0])

    assert related == (first, second)


def test_event_graph_same_as_group_is_symmetric_and_transitive() -> None:
    graph = EventGraph(
        relations=(
            _relation(NODES[0], NODES[1], K.SAME_AS),
            _relation(NODES[2], NODES[1], K.SAME_AS),
            _relation(NODES[2], NODES[3], K.PART_OF),
        )
    )

    group = graph.same_as_group(NODES[2])

    assert group == frozenset(NODES[:3])
    assert graph.same_as_group(NODES[0]) == group


def test_event_graph_duplicate_relation_rejected_on_construction() -> None:
    relation = _relation(NODES[0], NODES[1], K.PART_OF)

    with pytest.raises(PydanticValidationError, match="same relation twice"):
        EventGraph(relations=(relation, _relation(NODES[0], NODES[1], K.PART_OF)))


def test_event_graph_part_of_cycle_rejected_on_construction() -> None:
    with pytest.raises(PydanticValidationError, match="cycle"):
        EventGraph(
            relations=(
                _relation(NODES[0], NODES[1], K.PART_OF),
                _relation(NODES[1], NODES[0], K.PART_OF),
            )
        )


def test_event_graph_triggered_by_loop_allowed() -> None:
    graph = EventGraph(
        relations=(
            _relation(NODES[0], NODES[1], K.TRIGGERED_BY),
            _relation(NODES[1], NODES[0], K.TRIGGERED_BY),
        )
    )

    assert graph.find_part_of_cycle() is None


def test_with_relation_reversed_same_as_raises_invalid_relation() -> None:
    graph = EventGraph(relations=(_relation(NODES[0], NODES[1], K.SAME_AS),))

    with pytest.raises(InvalidRelationError, match="already recorded") as raised:
        graph.with_relation(_relation(NODES[1], NODES[0], K.SAME_AS))

    assert raised.value.details["kind"] == "same_as"


def test_with_relation_closing_three_cycle_raises_invalid_relation() -> None:
    graph = EventGraph(
        relations=(
            _relation(NODES[0], NODES[1], K.PART_OF),
            _relation(NODES[1], NODES[2], K.PART_OF),
        )
    )

    with pytest.raises(InvalidRelationError, match="part of itself"):
        graph.with_relation(_relation(NODES[2], NODES[0], K.PART_OF))


def test_with_relation_new_relation_returns_new_graph() -> None:
    graph = EventGraph()
    relation = _relation(NODES[0], NODES[1], K.PART_OF)

    extended = graph.with_relation(relation)

    assert extended.relations == (relation,)
    assert graph.relations == ()


def test_relate_new_relation_emits_events_related() -> None:
    relation = _relation(NODES[0], NODES[1], K.TRIGGERED_BY)

    change = EventGraph().relate(relation, ids=ids())

    assert change.state.relations == (relation,)
    (event,) = change.events
    assert isinstance(event, EventsRelated)
    assert event.event_type == "events.events_related"
    assert event.aggregate_id == NODES[0]
    assert event.to_event_id == NODES[1]
    assert event.kind is K.TRIGGERED_BY
    assert event.actor_id == relation.related_by
    assert event.occurred_at == relation.related_at


@given(edges=EDGES)
def test_with_relation_random_part_of_edges_never_leave_a_cycle(
    edges: list[tuple[UUID, UUID]],
) -> None:
    graph = EventGraph()
    accepted: set[tuple[UUID, UUID]] = set()

    for source, target in edges:
        try:
            graph = graph.with_relation(_relation(source, target, K.PART_OF))
        except InvalidRelationError:
            is_duplicate = (source, target) in accepted
            assert is_duplicate or _reaches(accepted, target, source)
        else:
            accepted.add((source, target))

    assert graph.find_part_of_cycle() is None
    assert {(r.from_event_id, r.to_event_id) for r in graph.relations} == accepted


@given(edges=EDGES)
def test_find_part_of_cycle_any_edges_agrees_with_reachability(
    edges: list[tuple[UUID, UUID]],
) -> None:
    unique = list(dict.fromkeys(edges))
    graph = EventGraph.model_construct(
        relations=tuple(
            _relation(source, target, K.PART_OF) for source, target in unique
        )
    )
    edge_set = set(unique)

    cycle = graph.find_part_of_cycle()

    has_cycle = any(_reaches(edge_set, target, source) for source, target in unique)
    assert (cycle is not None) == has_cycle
    if cycle is not None:
        assert cycle[0] == cycle[-1]
        for source, target in itertools.pairwise(cycle):
            assert (source, target) in edge_set
