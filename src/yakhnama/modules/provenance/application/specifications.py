"""Search specifications over sources.

Evaluated in memory by fakes and compiled to SQL by the infrastructure query
service, which must return exactly what ``is_satisfied_by`` accepts.

Patterns: Specification.
"""

from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.value_objects import SourceType
from yakhnama.shared_kernel.specification import Specification


class SourceTypeSpecification(Specification[Source]):
    """Matches sources of one type.

    Implements: Specification.

    Attributes:
        source_type: The type to match.
    """

    def __init__(self, source_type: SourceType) -> None:
        """Create the specification.

        Args:
            source_type: The type to match.
        """
        self._source_type = source_type

    @property
    def source_type(self) -> SourceType:
        """Return the type to match."""
        return self._source_type

    def is_satisfied_by(self, candidate: Source) -> bool:
        """Tell whether ``candidate`` has the type.

        Args:
            candidate: The source to test.

        Returns:
            ``True`` if the types are equal.
        """
        return candidate.source_type is self._source_type
