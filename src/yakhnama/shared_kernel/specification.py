"""Composable business predicates (Specification) with a visitor for compilation.

A specification answers one yes/no question about a candidate in memory
(``is_satisfied_by``) and can be combined with ``&``, ``|`` and ``~`` or the equivalent
``and_``, ``or_`` and ``not_`` methods. Infrastructure compiles the same tree to SQL by
implementing ``SpecificationVisitor``, so search filters are written once in the
domain and never as SQL fragments in application code (``AGENTS.md`` §3).

Leaf specifications are defined by modules: subclass ``Specification`` and implement
``is_satisfied_by``; the inherited ``accept`` dispatches to ``visit_leaf``.

Patterns: Specification.
"""

# Nodes refer to each other and to the visitor defined below them; Python 3.13
# evaluates annotations eagerly, and this module has no Pydantic models that would need
# them at runtime.
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol


class Specification[CandidateT](ABC):
    """A predicate over candidates of type ``CandidateT``.

    Nodes are immutable once built.

    Implements: Specification.
    """

    @abstractmethod
    def is_satisfied_by(self, candidate: CandidateT) -> bool:
        """Tell whether ``candidate`` meets the specification.

        Args:
            candidate: The value to test.

        Returns:
            ``True`` if the candidate satisfies the predicate.
        """

    def accept[ResultT](
        self, visitor: SpecificationVisitor[CandidateT, ResultT]
    ) -> ResultT:
        """Dispatch to the visitor method for this node; leaves use ``visit_leaf``.

        Args:
            visitor: The compiler or interpreter walking the tree.

        Returns:
            Whatever the visitor produces for this node.
        """
        return visitor.visit_leaf(self)

    def and_(self, other: Specification[CandidateT]) -> AndSpecification[CandidateT]:
        """Return a specification satisfied when both operands are.

        Args:
            other: The right-hand operand.

        Returns:
            The conjunction ``self AND other``.
        """
        return AndSpecification(self, other)

    def or_(self, other: Specification[CandidateT]) -> OrSpecification[CandidateT]:
        """Return a specification satisfied when either operand is.

        Args:
            other: The right-hand operand.

        Returns:
            The disjunction ``self OR other``.
        """
        return OrSpecification(self, other)

    def not_(self) -> NotSpecification[CandidateT]:
        """Return a specification satisfied exactly when this one is not.

        Returns:
            The negation ``NOT self``.
        """
        return NotSpecification(self)

    def __and__(self, other: Specification[CandidateT]) -> AndSpecification[CandidateT]:
        """Operator form of ``and_``.

        Args:
            other: The right-hand operand.

        Returns:
            The conjunction ``self AND other``.
        """
        return self.and_(other)

    def __or__(self, other: Specification[CandidateT]) -> OrSpecification[CandidateT]:
        """Operator form of ``or_``.

        Args:
            other: The right-hand operand.

        Returns:
            The disjunction ``self OR other``.
        """
        return self.or_(other)

    def __invert__(self) -> NotSpecification[CandidateT]:
        """Operator form of ``not_``.

        Returns:
            The negation ``NOT self``.
        """
        return self.not_()


class AndSpecification[CandidateT](Specification[CandidateT]):
    """Conjunction of two specifications.

    Implements: Specification.

    Attributes:
        left: The first operand, evaluated first.
        right: The second operand, evaluated only if ``left`` is satisfied.
    """

    def __init__(
        self, left: Specification[CandidateT], right: Specification[CandidateT]
    ) -> None:
        """Create the conjunction.

        Args:
            left: The first operand.
            right: The second operand.
        """
        self._left = left
        self._right = right

    @property
    def left(self) -> Specification[CandidateT]:
        """Return the first operand."""
        return self._left

    @property
    def right(self) -> Specification[CandidateT]:
        """Return the second operand."""
        return self._right

    def is_satisfied_by(self, candidate: CandidateT) -> bool:
        """Tell whether both operands are satisfied.

        Args:
            candidate: The value to test.

        Returns:
            ``True`` if ``left`` and ``right`` both accept the candidate.
        """
        return self._left.is_satisfied_by(candidate) and self._right.is_satisfied_by(
            candidate
        )

    def accept[ResultT](
        self, visitor: SpecificationVisitor[CandidateT, ResultT]
    ) -> ResultT:
        """Dispatch to ``visit_and``.

        Args:
            visitor: The compiler or interpreter walking the tree.

        Returns:
            The visitor's result for this conjunction.
        """
        return visitor.visit_and(self)


class OrSpecification[CandidateT](Specification[CandidateT]):
    """Disjunction of two specifications.

    Implements: Specification.

    Attributes:
        left: The first operand, evaluated first.
        right: The second operand, evaluated only if ``left`` is not satisfied.
    """

    def __init__(
        self, left: Specification[CandidateT], right: Specification[CandidateT]
    ) -> None:
        """Create the disjunction.

        Args:
            left: The first operand.
            right: The second operand.
        """
        self._left = left
        self._right = right

    @property
    def left(self) -> Specification[CandidateT]:
        """Return the first operand."""
        return self._left

    @property
    def right(self) -> Specification[CandidateT]:
        """Return the second operand."""
        return self._right

    def is_satisfied_by(self, candidate: CandidateT) -> bool:
        """Tell whether at least one operand is satisfied.

        Args:
            candidate: The value to test.

        Returns:
            ``True`` if ``left`` or ``right`` accepts the candidate.
        """
        return self._left.is_satisfied_by(candidate) or self._right.is_satisfied_by(
            candidate
        )

    def accept[ResultT](
        self, visitor: SpecificationVisitor[CandidateT, ResultT]
    ) -> ResultT:
        """Dispatch to ``visit_or``.

        Args:
            visitor: The compiler or interpreter walking the tree.

        Returns:
            The visitor's result for this disjunction.
        """
        return visitor.visit_or(self)


class NotSpecification[CandidateT](Specification[CandidateT]):
    """Negation of a specification.

    Implements: Specification.

    Attributes:
        operand: The negated specification.
    """

    def __init__(self, operand: Specification[CandidateT]) -> None:
        """Create the negation.

        Args:
            operand: The specification to negate.
        """
        self._operand = operand

    @property
    def operand(self) -> Specification[CandidateT]:
        """Return the negated specification."""
        return self._operand

    def is_satisfied_by(self, candidate: CandidateT) -> bool:
        """Tell whether the operand is not satisfied.

        Args:
            candidate: The value to test.

        Returns:
            ``True`` if ``operand`` rejects the candidate.
        """
        return not self._operand.is_satisfied_by(candidate)

    def accept[ResultT](
        self, visitor: SpecificationVisitor[CandidateT, ResultT]
    ) -> ResultT:
        """Dispatch to ``visit_not``.

        Args:
            visitor: The compiler or interpreter walking the tree.

        Returns:
            The visitor's result for this negation.
        """
        return visitor.visit_not(self)


class TrueSpecification[CandidateT](Specification[CandidateT]):
    """Satisfied by every candidate; the identity of ``&``.

    Useful as the neutral starting point when filters are folded together. It is a
    leaf, so visitors handle it in ``visit_leaf``.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: CandidateT) -> bool:
        """Accept any candidate.

        Args:
            candidate: Ignored.

        Returns:
            Always ``True``.
        """
        return True


class FalseSpecification[CandidateT](Specification[CandidateT]):
    """Satisfied by no candidate; the identity of ``|``.

    It is a leaf, so visitors handle it in ``visit_leaf``.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: CandidateT) -> bool:
        """Reject any candidate.

        Args:
            candidate: Ignored.

        Returns:
            Always ``False``.
        """
        return False


class SpecificationVisitor[CandidateT, ResultT](Protocol):
    """Walks a specification tree, for example to compile it to a SQL expression.

    Composite methods receive the node and recurse by calling ``accept`` on its
    operands, so a visitor controls evaluation order and can short-circuit.

    Implements: Specification (visitor port).
    """

    def visit_and(self, specification: AndSpecification[CandidateT]) -> ResultT:
        """Handle a conjunction.

        Args:
            specification: The conjunction node; recurse via its operands' ``accept``.

        Returns:
            The visitor's result for the conjunction.
        """
        ...

    def visit_or(self, specification: OrSpecification[CandidateT]) -> ResultT:
        """Handle a disjunction.

        Args:
            specification: The disjunction node; recurse via its operands' ``accept``.

        Returns:
            The visitor's result for the disjunction.
        """
        ...

    def visit_not(self, specification: NotSpecification[CandidateT]) -> ResultT:
        """Handle a negation.

        Args:
            specification: The negation node; recurse via its operands' ``accept``.

        Returns:
            The visitor's result for the negation.
        """
        ...

    def visit_leaf(self, specification: Specification[CandidateT]) -> ResultT:
        """Handle a module-defined leaf, including the constant specifications.

        Args:
            specification: The leaf node, typically dispatched on its concrete type.

        Returns:
            The visitor's result for the leaf.
        """
        ...
