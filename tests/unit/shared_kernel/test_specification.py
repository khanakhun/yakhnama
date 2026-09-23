"""Unit tests for ``yakhnama.shared_kernel.specification``.

The algebraic laws are checked as equivalences: two specifications are equivalent when
they accept exactly the same candidates, sampled by hypothesis.
"""

from hypothesis import given
from hypothesis import strategies as st

from yakhnama.shared_kernel.specification import (
    AndSpecification,
    FalseSpecification,
    NotSpecification,
    OrSpecification,
    Specification,
    SpecificationVisitor,
    TrueSpecification,
)


class _DivisibleBySpecification(Specification[int]):
    """Leaf accepting multiples of ``divisor``.

    Implements: Specification.
    """

    def __init__(self, divisor: int) -> None:
        self.divisor = divisor

    def is_satisfied_by(self, candidate: int) -> bool:
        return candidate % self.divisor == 0


class _GreaterThanSpecification(Specification[int]):
    """Leaf accepting values above ``threshold``.

    Implements: Specification.
    """

    def __init__(self, threshold: int) -> None:
        self.threshold = threshold

    def is_satisfied_by(self, candidate: int) -> bool:
        return candidate > self.threshold


class _EvaluatingVisitor:
    """Re-evaluates a tree through the visitor protocol.

    Implements: Specification (visitor).
    """

    def __init__(self, candidate: int) -> None:
        self.candidate = candidate

    def visit_and(self, specification: AndSpecification[int]) -> bool:
        return specification.left.accept(self) and specification.right.accept(self)

    def visit_or(self, specification: OrSpecification[int]) -> bool:
        return specification.left.accept(self) or specification.right.accept(self)

    def visit_not(self, specification: NotSpecification[int]) -> bool:
        return not specification.operand.accept(self)

    def visit_leaf(self, specification: Specification[int]) -> bool:
        return specification.is_satisfied_by(self.candidate)


class _RenderingVisitor:
    """Renders a tree as text, standing in for a SQL compiler.

    Implements: Specification (visitor).
    """

    def visit_and(self, specification: AndSpecification[int]) -> str:
        left, right = specification.left.accept(self), specification.right.accept(self)
        return f"({left} AND {right})"

    def visit_or(self, specification: OrSpecification[int]) -> str:
        left, right = specification.left.accept(self), specification.right.accept(self)
        return f"({left} OR {right})"

    def visit_not(self, specification: NotSpecification[int]) -> str:
        return f"NOT {specification.operand.accept(self)}"

    def visit_leaf(self, specification: Specification[int]) -> str:
        if isinstance(specification, _DivisibleBySpecification):
            return f"x % {specification.divisor} = 0"
        if isinstance(specification, _GreaterThanSpecification):
            return f"x > {specification.threshold}"
        return "TRUE" if isinstance(specification, TrueSpecification) else "FALSE"


leaves: st.SearchStrategy[Specification[int]] = st.one_of(
    st.builds(_DivisibleBySpecification, st.integers(min_value=1, max_value=12)),
    st.builds(_GreaterThanSpecification, st.integers(min_value=-50, max_value=50)),
    st.just(TrueSpecification[int]()),
    st.just(FalseSpecification[int]()),
)


def _extend(
    children: st.SearchStrategy[Specification[int]],
) -> st.SearchStrategy[Specification[int]]:
    return st.one_of(
        st.builds(AndSpecification[int], children, children),
        st.builds(OrSpecification[int], children, children),
        st.builds(NotSpecification[int], children),
    )


specifications = st.recursive(leaves, _extend, max_leaves=8)
candidates = st.integers(min_value=-1_000, max_value=1_000)


@given(left=specifications, right=specifications, candidate=candidates)
def test_and_specification_operands_swapped_is_equivalent(
    left: Specification[int], right: Specification[int], candidate: int
) -> None:
    result = (left & right).is_satisfied_by(candidate)

    assert result == (right & left).is_satisfied_by(candidate)
    assert result == (
        left.is_satisfied_by(candidate) and right.is_satisfied_by(candidate)
    )


@given(left=specifications, right=specifications, candidate=candidates)
def test_or_specification_operands_swapped_is_equivalent(
    left: Specification[int], right: Specification[int], candidate: int
) -> None:
    result = (left | right).is_satisfied_by(candidate)

    assert result == (right | left).is_satisfied_by(candidate)
    assert result == (
        left.is_satisfied_by(candidate) or right.is_satisfied_by(candidate)
    )


@given(
    first=specifications,
    second=specifications,
    third=specifications,
    candidate=candidates,
)
def test_and_or_specification_regrouped_is_equivalent(
    first: Specification[int],
    second: Specification[int],
    third: Specification[int],
    candidate: int,
) -> None:
    left_and = ((first & second) & third).is_satisfied_by(candidate)
    left_or = ((first | second) | third).is_satisfied_by(candidate)

    assert left_and == (first & (second & third)).is_satisfied_by(candidate)
    assert left_or == (first | (second | third)).is_satisfied_by(candidate)


@given(left=specifications, right=specifications, candidate=candidates)
def test_not_specification_de_morgan_laws_hold(
    left: Specification[int], right: Specification[int], candidate: int
) -> None:
    not_and = (~(left & right)).is_satisfied_by(candidate)
    not_or = (~(left | right)).is_satisfied_by(candidate)

    assert not_and == (~left | ~right).is_satisfied_by(candidate)
    assert not_or == (~left & ~right).is_satisfied_by(candidate)


@given(specification=specifications, candidate=candidates)
def test_not_specification_double_negation_is_equivalent(
    specification: Specification[int], candidate: int
) -> None:
    result = (~~specification).is_satisfied_by(candidate)

    assert result == specification.is_satisfied_by(candidate)


@given(specification=specifications, candidate=candidates)
def test_true_and_false_specifications_are_identities(
    specification: Specification[int], candidate: int
) -> None:
    expected = specification.is_satisfied_by(candidate)

    with_true = specification.and_(TrueSpecification()).is_satisfied_by(candidate)
    with_false = specification.or_(FalseSpecification()).is_satisfied_by(candidate)

    assert with_true == expected
    assert with_false == expected
    assert specification.or_(TrueSpecification()).is_satisfied_by(candidate)
    assert not specification.and_(FalseSpecification()).is_satisfied_by(candidate)


@given(specification=specifications, candidate=candidates)
def test_specification_accept_evaluating_visitor_matches_is_satisfied_by(
    specification: Specification[int], candidate: int
) -> None:
    visitor: SpecificationVisitor[int, bool] = _EvaluatingVisitor(candidate)

    result = specification.accept(visitor)

    assert result == specification.is_satisfied_by(candidate)


def test_specification_methods_build_the_matching_nodes() -> None:
    left = _DivisibleBySpecification(2)
    right = _GreaterThanSpecification(10)

    conjunction = left.and_(right)
    disjunction = left.or_(right)
    negation = left.not_()

    assert isinstance(conjunction, AndSpecification)
    assert (conjunction.left, conjunction.right) == (left, right)
    assert isinstance(disjunction, OrSpecification)
    assert (disjunction.left, disjunction.right) == (left, right)
    assert isinstance(negation, NotSpecification)
    assert negation.operand is left


def test_specification_accept_rendering_visitor_compiles_tree() -> None:
    specification = (
        ~(_DivisibleBySpecification(3) & _GreaterThanSpecification(5))
        | TrueSpecification[int]() & FalseSpecification[int]()
    )

    rendered = specification.accept(_RenderingVisitor())

    assert rendered == "(NOT (x % 3 = 0 AND x > 5) OR (TRUE AND FALSE))"


def test_and_specification_left_rejects_right_is_not_evaluated() -> None:
    evaluated: list[int] = []

    class _RecordingSpecification(Specification[int]):
        """Leaf recording every evaluation.

        Implements: Specification.
        """

        def is_satisfied_by(self, candidate: int) -> bool:
            evaluated.append(candidate)
            return True

    conjunction = FalseSpecification[int]() & _RecordingSpecification()
    disjunction = TrueSpecification[int]() | _RecordingSpecification()

    results = (conjunction.is_satisfied_by(1), disjunction.is_satisfied_by(2))

    assert results == (False, True)
    assert evaluated == []
