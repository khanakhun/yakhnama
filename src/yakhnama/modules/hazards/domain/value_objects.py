"""Value objects of the hazards bounded context.

Every model is frozen and rejects unknown fields. Several values here encode external
conventions (the IRDR 2014 peril classification, the GLIMS glacier id format, the
ICIMOD lake inventory); where the project has no cited source yet the docstring says
**proposed** and ``docs/data-dictionary/hazards.md`` lists the open question.

Patterns: Value Object.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

HazardCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
"""Stable machine code of a hazard type, for example ``"glof"``; never reused."""

IrdrFamily = Literal[
    "geophysical",
    "hydrological",
    "meteorological",
    "climatological",
    "extraterrestrial",
    "biological",
]
"""The six IRDR Peril Classification families (IRDR DATA Publication No. 1, 2014).

Lower-case so the stored value is a stable key rather than a display label.
"""

IrdrTerm = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
"""A main event or peril name as written in the IRDR 2014 classification."""

# Proposed, after the GLIMS id convention "G" + longitude (degrees east x 1000, six
# digits) + "E" + latitude (degrees x 1000, five digits) + hemisphere. The exact rule is
# an open question in docs/data-dictionary/hazards.md; widen it only with a source.
GLIMS_ID_PATTERN = r"^G\d{6}E\d{5}[NS]$"
GlimsId = Annotated[str, StringConstraints(pattern=GLIMS_ID_PATTERN)]

InventoryId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
RetirementText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]


class HazardTypeRef(BaseModel):
    """Immutable reference to a hazard type in the taxonomy.

    Implements: Value Object.

    Attributes:
        code: Stable machine code such as ``"glof"``. Never reused once retired.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: HazardCode


class HazardTypeStatus(StrEnum):
    """Lifecycle status of a hazard type code.

    Implements: Value Object.
    """

    ACTIVE = "active"
    RETIRED = "retired"


class IrdrAlignment(BaseModel):
    """Where a hazard type sits in the IRDR 2014 peril classification.

    The values a hazard type carries are reference data (``data/reference``); until a
    placement is confirmed against the publication it is marked proposed there.

    Implements: Value Object.

    Attributes:
        family: IRDR family.
        main_event: IRDR main event, for example ``"Flood"``.
        peril: IRDR peril under the main event, or ``None`` when the hazard type maps
            to the main event as a whole.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: IrdrFamily
    main_event: IrdrTerm
    peril: IrdrTerm | None = None


class GlacierRef(BaseModel):
    """Reference to a glacier by its GLIMS id.

    The id format is **proposed** (see ``GLIMS_ID_PATTERN``).

    Implements: Value Object.

    Attributes:
        glims_id: GLIMS glacier id, for example ``"G074567E36234N"``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    glims_id: GlimsId


class GlacialLakeRef(BaseModel):
    """Reference to a glacial lake in a named inventory.

    Implements: Value Object.

    Attributes:
        inventory: Which inventory the id belongs to; ``"other"`` needs a source on the
            record that uses it.
        inventory_id: The lake's id in that inventory, kept verbatim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    inventory: Literal["icimod", "glims", "other"]
    inventory_id: InventoryId


class RetirementReason(BaseModel):
    """Why a hazard type was retired and what, if anything, replaces it.

    Implements: Value Object.

    Attributes:
        text: Human-readable reason kept for the audit trail.
        replaced_by: Code of the hazard type to use instead, if any; never the retired
            code itself (checked by ``HazardType``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: RetirementText
    replaced_by: HazardCode | None = None


# Bounds on free-text, kept in one place so reference.py and entities.py agree.
SOURCE_MAX_LENGTH = 500
NOTES_MAX_LENGTH = 2000
