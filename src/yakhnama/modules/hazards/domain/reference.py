"""Validation models for the hazard taxonomy reference file.

``data/reference/hazard_types.yaml`` (task T8) is parsed with ``yaml.safe_load`` outside
the domain and validated here immediately, so the untyped structure never travels
further. Each entry says where it comes from (``source``: a citation, or ``proposed``
until one exists). Codes are never removed from the file; retired entries keep their
code with ``status: retired`` and a ``retirement``.

Patterns: Value Object.
"""

from collections import Counter
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from yakhnama.modules.hazards.domain.entities import find_parent_cycle
from yakhnama.modules.hazards.domain.value_objects import (
    NOTES_MAX_LENGTH,
    SOURCE_MAX_LENGTH,
    HazardCode,
    HazardTypeStatus,
    IrdrAlignment,
    RetirementReason,
)
from yakhnama.shared_kernel.value_objects import (
    LOCALIZED_TEXT_MAX_LANGUAGES,
    LanguageCode,
    LocalizedString,
    LocalizedText,
)

SourceText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=SOURCE_MAX_LENGTH
    ),
]
NotesText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTES_MAX_LENGTH),
]
VersionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
]
LicenceText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
LanguageTexts = Annotated[
    Mapping[LanguageCode, LocalizedString],
    Field(min_length=1, max_length=LOCALIZED_TEXT_MAX_LANGUAGES),
]
ENGLISH = "en"


class HazardTypeReferenceEntry(BaseModel):
    """One hazard type as written in the reference file.

    Implements: Value Object.

    Attributes:
        code: Stable code; never reused.
        parent: Code of the broader type in the same file, or ``None`` for a root.
        labels: Display labels keyed by language code; ``en`` is required (other
            languages only with a cited source, open question Q2).
        description: Optional longer explanation keyed by language code.
        alignment: IRDR 2014 placement.
        attributes_schema: Registry code of the attribute schema, or ``None``.
        status: ``active`` or ``retired``.
        retirement: Why the entry is retired; required exactly when retired.
        source: Citation for the entry, or ``"proposed"`` until one exists.
        notes: Free-text remarks for reviewers, for example the open question.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: HazardCode
    parent: HazardCode | None = None
    labels: LanguageTexts
    description: LanguageTexts | None = None
    alignment: IrdrAlignment
    attributes_schema: HazardCode | None = None
    status: HazardTypeStatus = HazardTypeStatus.ACTIVE
    retirement: RetirementReason | None = None
    source: SourceText
    notes: NotesText | None = None

    @field_validator("labels", "description", mode="after")
    @classmethod
    def _freeze_texts(cls, texts: Mapping[str, str] | None) -> Mapping[str, str] | None:
        return None if texts is None else MappingProxyType(dict(texts))

    @field_serializer("labels", "description")
    def _serialise_texts(
        self, texts: Mapping[str, str] | None
    ) -> dict[str, str] | None:
        return None if texts is None else dict(texts)

    @model_validator(mode="after")
    def _check_entry(self) -> Self:
        if ENGLISH not in self.labels:
            message = f"{self.code}: an {ENGLISH!r} label is required"
            raise ValueError(message)
        if self.parent == self.code:
            message = f"{self.code}: an entry cannot be its own parent"
            raise ValueError(message)
        is_retired = self.status is HazardTypeStatus.RETIRED
        if is_retired != (self.retirement is not None):
            message = f"{self.code}: retirement is required exactly when retired"
            raise ValueError(message)
        if self.retirement is not None and self.retirement.replaced_by == self.code:
            message = f"{self.code}: an entry cannot be replaced by itself"
            raise ValueError(message)
        return self

    def localized_labels(self) -> LocalizedText:
        """Return the labels as the kernel's ``LocalizedText``.

        Returns:
            The labels, ready for ``HazardType.labels``.
        """
        return LocalizedText(texts=self.labels)

    def localized_description(self) -> LocalizedText | None:
        """Return the description as ``LocalizedText``, if there is one.

        Returns:
            The description, or ``None``.
        """
        return (
            None if self.description is None else LocalizedText(texts=self.description)
        )


class HazardTypeReferenceFile(BaseModel):
    """The whole hazard taxonomy reference file.

    Implements: Value Object.

    Attributes:
        schema_version: Version of this file's structure; only ``1`` exists.
        data_version: Version of the content, bumped on every change.
        source: Where the taxonomy as a whole comes from.
        licence: Licence of the file's content.
        entries: Every hazard type ever defined, including retired ones.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    data_version: VersionText
    source: SourceText
    licence: LicenceText
    entries: tuple[HazardTypeReferenceEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_tree(self) -> Self:
        counts = Counter(entry.code for entry in self.entries)
        duplicates = sorted(code for code, count in counts.items() if count > 1)
        if duplicates:
            message = f"duplicate hazard codes: {duplicates}"
            raise ValueError(message)
        unknown_parents = sorted(
            entry.code
            for entry in self.entries
            if entry.parent is not None and entry.parent not in counts
        )
        if unknown_parents:
            message = (
                f"hazard types with a parent missing from the file: {unknown_parents}"
            )
            raise ValueError(message)
        unknown_replacements = sorted(
            entry.code
            for entry in self.entries
            if entry.retirement is not None
            and entry.retirement.replaced_by is not None
            and entry.retirement.replaced_by not in counts
        )
        if unknown_replacements:
            message = (
                "retired hazard types replaced by a code missing from the file: "
                f"{unknown_replacements}"
            )
            raise ValueError(message)
        cycle = find_parent_cycle({entry.code: entry.parent for entry in self.entries})
        if cycle is not None:
            message = f"hazard type parents form a cycle: {list(cycle)}"
            raise ValueError(message)
        return self

    def codes(self) -> frozenset[str]:
        """Return every code in the file, active or retired.

        Returns:
            The codes.
        """
        return frozenset(entry.code for entry in self.entries)
