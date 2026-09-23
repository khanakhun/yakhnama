"""Search specifications over places.

Leaves are evaluated in memory by fakes and compiled to SQL by the infrastructure
query service. The in-memory text match is deliberately simple (case- and
accent-insensitive substring over every name); the SQL implementation may rank and
match more loosely (``pg_trgm``, ``unaccent``), but must return at least what this
rule matches.

Patterns: Specification.
"""

import unicodedata

from pydantic import TypeAdapter

from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.value_objects import AdminLevel
from yakhnama.shared_kernel.specification import Specification
from yakhnama.shared_kernel.value_objects import LanguageCode

_LANGUAGE_CODE: TypeAdapter[str] = TypeAdapter(LanguageCode)


def fold_search_text(text: str) -> str:
    """Return ``text`` in the form search compares: no marks, case-folded.

    Decomposes to NFKD and drops combining marks, so ``Hunzā`` matches ``hunza``
    and short-vowel marks in Perso-Arabic script do not block a match.

    Args:
        text: Any text.

    Returns:
        The folded text.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return stripped.casefold().strip()


class PlaceTextSpecification(Specification[Place]):
    """Matches places with a name containing the search text.

    Implements: Specification.

    Attributes:
        text: The search text as given.
        language: Only names in this language count, if set.
    """

    def __init__(self, text: str, language: str | None = None) -> None:
        """Create the specification.

        Args:
            text: The search text; compared after ``fold_search_text``.
            language: Restrict matching to names in this language code.

        Raises:
            pydantic.ValidationError: If ``language`` is not a valid language code.
        """
        self._text = text
        self._folded = fold_search_text(text)
        self._language = (
            None if language is None else _LANGUAGE_CODE.validate_python(language)
        )

    @property
    def text(self) -> str:
        """Return the search text as given."""
        return self._text

    @property
    def language(self) -> str | None:
        """Return the normalised language restriction, if any."""
        return self._language

    def is_satisfied_by(self, candidate: Place) -> bool:
        """Tell whether a name of ``candidate`` contains the text.

        Args:
            candidate: The place to test.

        Returns:
            ``True`` if a name (in ``language``, when set) contains the folded text.
        """
        return any(
            self._folded in fold_search_text(name.text)
            for name in candidate.names
            if self._language is None or name.language == self._language
        )


class PlaceLevelSpecification(Specification[Place]):
    """Matches places at one administrative level.

    Implements: Specification.

    Attributes:
        level: The level to match.
    """

    def __init__(self, level: AdminLevel) -> None:
        """Create the specification.

        Args:
            level: The level to match.
        """
        self._level = level

    @property
    def level(self) -> AdminLevel:
        """Return the level to match."""
        return self._level

    def is_satisfied_by(self, candidate: Place) -> bool:
        """Tell whether ``candidate`` is at ``level``.

        Args:
            candidate: The place to test.

        Returns:
            ``True`` if the levels are equal.
        """
        return candidate.level is self._level


class ActivePlaceSpecification(Specification[Place]):
    """Matches places that are neither merged nor retired.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: Place) -> bool:
        """Tell whether ``candidate`` is active.

        Args:
            candidate: The place to test.

        Returns:
            ``True`` while its status is ``active``.
        """
        return candidate.is_active
