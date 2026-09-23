"""Schema of the versioned impact metric reference file.

``data/reference/impact_metrics.yaml`` (task T8) is parsed into these models before
anything is seeded. An entry carries the same definition rules as ``ImpactMetric``
plus its provenance: ``source`` is a citation, or the word ``proposed`` while a
mapping or definition awaits the maintainer.

Patterns: Value Object.
"""

from collections import Counter
from collections.abc import Mapping
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from yakhnama.modules.impacts.domain.value_objects import (
    REQUIRED_LABEL_LANGUAGE,
    Aggregation,
    CurrencyCode,
    DesInventarField,
    MetricCategory,
    MetricCode,
    MetricStatus,
    RetirementReason,
    SendaiIndicator,
    ValueKind,
    definition_problems,
)
from yakhnama.shared_kernel.value_objects import LocalizedText, Unit


def _accept_bare_texts(value: object) -> object:
    # YAML authors write ``labels: {en: ...}``; ``texts`` is not a valid language
    # code, so a mapping without that key can only be a bare language mapping.
    if isinstance(value, Mapping) and "texts" not in value:
        return {"texts": value}
    return value


ReferenceText = Annotated[LocalizedText, BeforeValidator(_accept_bare_texts)]
"""``LocalizedText`` that also accepts a bare ``{language: text}`` mapping."""

_PROVENANCE = Field(min_length=1, max_length=500)


class ImpactMetricReferenceEntry(BaseModel):
    """One metric as written in the reference file.

    Implements: Value Object.

    Attributes:
        code: Stable metric code.
        labels: Display names, English required.
        description: Exact meaning, if written.
        category: The metric's group.
        value_kind: Count, SI measurement or money.
        unit: Unit, following the ``value_kind`` rule.
        currency: ISO 4217 code, only for monetary metrics.
        sendai: Proposed Sendai indicator, or ``None``.
        desinventar: Proposed DesInventar field, or ``None``.
        aggregation: How claims combine.
        status: ``active`` or ``retired``.
        retirement: Why the metric was retired; set exactly when retired.
        source: Citation for the definition and mappings, or ``proposed``.
        notes: Free-text caveats for reviewers, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: MetricCode
    labels: ReferenceText
    description: ReferenceText | None = None
    category: MetricCategory
    value_kind: ValueKind
    unit: Unit | None = None
    currency: CurrencyCode | None = None
    sendai: SendaiIndicator | None = None
    desinventar: DesInventarField | None = None
    aggregation: Aggregation
    status: MetricStatus = MetricStatus.ACTIVE
    retirement: RetirementReason | None = None
    source: str = _PROVENANCE
    notes: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _check_definition(self) -> Self:
        problems = list(definition_problems(self.value_kind, self.unit, self.currency))
        if REQUIRED_LABEL_LANGUAGE not in self.labels.texts:
            problems.append("labels need an English ('en') entry")
        if (self.status is MetricStatus.RETIRED) != (self.retirement is not None):
            problems.append("a retirement reason is required exactly when retired")
        if problems:
            message = f"metric {self.code!r}: " + "; ".join(problems)
            raise ValueError(message)
        return self


class ImpactMetricReferenceFile(BaseModel):
    """The whole reference file: version header plus entries with unique codes.

    Implements: Value Object.

    Attributes:
        schema_version: Version of this file layout; only ``1`` exists.
        data_version: Version of the content, bumped on every change.
        source: Where the list as a whole comes from, or ``proposed``.
        licence: Licence of the file's content.
        entries: The metrics, codes unique across active and retired entries.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(ge=1, le=1)
    data_version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z.-]+$")
    source: str = _PROVENANCE
    licence: str = _PROVENANCE
    entries: tuple[ImpactMetricReferenceEntry, ...] = Field(max_length=10_000)

    @model_validator(mode="after")
    def _check_unique_codes(self) -> Self:
        counts = Counter(entry.code for entry in self.entries)
        duplicates = sorted(code for code, count in counts.items() if count > 1)
        if duplicates:
            message = f"duplicate metric codes: {duplicates}"
            raise ValueError(message)
        return self
