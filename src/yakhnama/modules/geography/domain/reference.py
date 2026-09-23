"""Schema of the versioned place reference file (``data/reference/*.yaml``).

These models describe the file *after* it has been parsed; reading YAML is the job of
the loader in the application and infrastructure layers, so this module stays
framework-free. A file is self-contained: every ``parent_code`` names an entry in the
same file, so the file can be checked and loaded on its own.

Patterns: Value Object.
"""

from collections import Counter
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from yakhnama.modules.geography.domain.entities import PLACE_MAX_NAMES
from yakhnama.modules.geography.domain.factories import PlaceDraft
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceCode,
    PlaceName,
    PlaceNameKind,
    PlaceNameText,
    ScriptCode,
)
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    LanguageCode,
)

SUPPORTED_SCHEMA_VERSION = 1
REFERENCE_MAX_ENTRIES = 10_000

ReferenceText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
"""A free-text citation or description, 1 to 500 characters."""

DataVersion = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=32, pattern=r"^[0-9A-Za-z][0-9A-Za-z.+-]*$"
    ),
]
"""Version of the data in a file, for example ``2026.09.0`` or ``0.1.0``."""

ReferenceStatus = Literal["sourced", "proposed", "fixture"]
"""How far an entry can be trusted: taken from its cited ``source``, proposed by a
contributor and awaiting confirmation, or test fixture data that is not a claim about
the world."""

# Proposed guard, from the Phase 1 plan: a name in any language other than English
# needs its own source, so no local-language label is shipped on a guess.
_UNSOURCED_LANGUAGE = "en"


class PlaceReferenceName(BaseModel):
    """One name of a reference entry.

    Implements: Value Object.

    Attributes:
        text: The name, normalised like ``PlaceName.text``.
        language: BCP 47 language code.
        script: ISO 15924 script, if recorded.
        kind: Role of the name; ``official`` by default.
        is_preferred: Whether it is the display name for its language.
        source: Citation for the name; required for every language except English.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: PlaceNameText
    language: LanguageCode
    script: ScriptCode | None = None
    kind: PlaceNameKind = "official"
    is_preferred: bool = False
    source: ReferenceText | None = None

    @model_validator(mode="after")
    def _require_source_for_local_names(self) -> Self:
        primary_language = self.language.partition("-")[0]
        if primary_language != _UNSOURCED_LANGUAGE and self.source is None:
            message = (
                f"name {self.text!r} in {self.language!r} needs a source; only "
                "English names may be shipped without one"
            )
            raise ValueError(message)
        return self

    def to_place_name(self) -> PlaceName:
        """Return the domain name.

        The textual ``source`` is not carried over: ``PlaceName.source_id`` refers
        to a provenance record, which arrives in Phase 3.

        Returns:
            The equivalent ``PlaceName`` without a ``source_id``.

        Raises:
            pydantic.ValidationError: If ``script`` contradicts the script subtag
                of ``language``.
        """
        return PlaceName(
            text=self.text,
            language=self.language,
            script=self.script,
            kind=self.kind,
            is_preferred=self.is_preferred,
        )


class PlaceReferenceEntry(BaseModel):
    """One place in a reference file.

    Implements: Value Object.

    Attributes:
        code: Stable machine code.
        level: Administrative level.
        parent_code: Code of the enclosing entry in the same file; ``None`` only for
            a country.
        names: At least one name.
        centroid: Representative point, if known.
        bbox: Bounding box, if known. It is *not* turned into a place geometry: a
            placeholder rectangle is not a boundary.
        status: ``sourced``, ``proposed`` or ``fixture``.
        source: Citation for the entry; required when ``status`` is ``sourced``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: PlaceCode
    level: AdminLevel
    parent_code: PlaceCode | None = None
    names: tuple[PlaceReferenceName, ...] = Field(
        min_length=1, max_length=PLACE_MAX_NAMES
    )
    centroid: Coordinates | None = None
    bbox: BoundingBox | None = None
    status: ReferenceStatus
    source: ReferenceText | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.status == "sourced" and self.source is None:
            message = f"entry {self.code!r} is 'sourced' but has no source"
            raise ValueError(message)
        if self.parent_code == self.code:
            message = f"entry {self.code!r} cannot be its own parent"
            raise ValueError(message)
        if (
            self.centroid is not None
            and self.bbox is not None
            and not self.bbox.contains(self.centroid)
        ):
            message = f"entry {self.code!r} has a centroid outside its bbox"
            raise ValueError(message)
        return self

    def to_draft(self) -> PlaceDraft:
        """Return the input ``PlaceFactory.create`` needs for this entry.

        Returns:
            A draft with the entry's code, level, parent code, names and centroid,
            and no geometry.

        Raises:
            pydantic.ValidationError: If the names break a ``Place`` name
                invariant (duplicates, several preferred names in one language).
        """
        return PlaceDraft(
            code=self.code,
            level=self.level,
            parent_code=self.parent_code,
            names=tuple(name.to_place_name() for name in self.names),
            centroid=self.centroid,
        )


class PlaceReferenceFile(BaseModel):
    """A whole versioned place reference file.

    Implements: Value Object.

    Attributes:
        schema_version: Version of this file format; only ``1`` exists.
        data_version: Version of the data in the file.
        source: Citation for the file as a whole.
        licence: Licence the data is published under.
        entries: The places, codes unique, parents defined in the same file.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    data_version: DataVersion
    source: ReferenceText
    licence: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    entries: tuple[PlaceReferenceEntry, ...] = Field(
        min_length=1, max_length=REFERENCE_MAX_ENTRIES
    )

    @model_validator(mode="after")
    def _check_hierarchy(self) -> Self:
        counts = Counter(entry.code for entry in self.entries)
        duplicates = sorted(code for code, count in counts.items() if count > 1)
        if duplicates:
            message = f"duplicate entry codes: {duplicates}"
            raise ValueError(message)
        levels = {entry.code: entry.level for entry in self.entries}
        for entry in self.entries:
            if entry.parent_code is not None and entry.parent_code not in levels:
                message = (
                    f"entry {entry.code!r} names parent {entry.parent_code!r}, "
                    "which is not in this file"
                )
                raise ValueError(message)
            parent_level = (
                None if entry.parent_code is None else levels[entry.parent_code]
            )
            if not entry.level.can_be_child_of(parent_level):
                message = (
                    f"entry {entry.code!r} ({entry.level.value}) cannot sit under "
                    f"{entry.parent_code!r}"
                )
                raise ValueError(message)
        return self

    def to_factory_inputs(self) -> tuple[PlaceDraft, ...]:
        """Return one draft per entry, every parent before its children.

        Entries are ordered by level from the top of the hierarchy down, keeping file
        order within a level; since a parent is always at a strictly higher level, a
        loader creating drafts in this order always finds the parent already created.

        Returns:
            The drafts in creation order.

        Raises:
            pydantic.ValidationError: If an entry's names break a ``Place`` name
                invariant.
        """
        ordered = sorted(self.entries, key=lambda entry: entry.level.rank)
        return tuple(entry.to_draft() for entry in ordered)
