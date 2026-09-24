"""Value objects of the ``provenance`` bounded context.

A ``Source`` is the provenance record every fact in Yakhnama links to (``AGENTS.md``
§7). This module holds its parts: the ``SourceType``, the free-text ``Citation``,
``SourceTitle`` and ``Publisher``, the ``SourceUrl``, the ``Licence``, the
``RetrievalTime`` and the ``SourceRef`` other modules store to point at a source.

Every free-text field uses the kernel's safe-text rules
(``yakhnama.shared_kernel.text.safe_text``): Unicode NFC, surrounding whitespace
stripped, single line, and no control, surrogate or bidirectional override and
isolate characters.

Several rules below are **proposed defaults, not domain facts** (the licence model,
the URL rules, the length limits); each is marked where it is defined and listed in
``docs/data-dictionary/provenance.md``.

Patterns: Value Object.
"""

import re
from enum import StrEnum
from typing import Annotated, Final, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.provenance.domain.errors import InvalidSourceUrlError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.text import safe_text
from yakhnama.shared_kernel.value_objects import DateWithPrecision, LanguageCode

# --------------------------------------------------------------------------- #
# Source type                                                                 #
# --------------------------------------------------------------------------- #


class SourceType(StrEnum):
    """What kind of party or instrument a source is (``AGENTS.md`` §7, glossary).

    Implements: Value Object.
    """

    CITIZEN = "citizen"
    ORGANISATION = "organisation"
    GOVERNMENT = "government"
    NEWS = "news"
    SATELLITE = "satellite"
    RESEARCH = "research"
    DATASET = "dataset"


# --------------------------------------------------------------------------- #
# Free-text fields                                                            #
# --------------------------------------------------------------------------- #

CITATION_MAX_LENGTH = 1000
SOURCE_TITLE_MAX_LENGTH = 300
PUBLISHER_MAX_LENGTH = 200

Citation = Annotated[str, *safe_text(CITATION_MAX_LENGTH)]
"""How to cite the source, 1 to 1000 characters after the safe-text rules.

Free text, for example a bibliographic reference or "Field report, Passu, 2022".
It stays on the ``Source`` and is never copied into domain events.
"""

SourceTitle = Annotated[str, *safe_text(SOURCE_TITLE_MAX_LENGTH)]
"""Short human-readable title of the source, 1 to 300 characters."""

Publisher = Annotated[str, *safe_text(PUBLISHER_MAX_LENGTH)]
"""Who published the source (an agency, newspaper or data provider), 1 to 200."""

# --------------------------------------------------------------------------- #
# URL                                                                         #
# --------------------------------------------------------------------------- #

SOURCE_URL_MAX_LENGTH = 2048
ALLOWED_URL_SCHEMES: Final = frozenset({"http", "https"})


def _check_source_url(value: str) -> str:
    # Printable ASCII without spaces: a URL with raw Unicode or whitespace must be
    # percent-encoded (and an IDN host punycoded) by the client first, so the stored
    # value is unambiguous and safe to render as a link.
    if any(not "\x21" <= character <= "\x7e" for character in value):
        message = "url must be printable ASCII without spaces"
        raise ValueError(message)
    parts = urlsplit(value)
    # The scheme allow-list is what rejects ``javascript:``, ``data:`` and ``file:``.
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES:
        message = "url must use http or https"
        raise ValueError(message)
    # Credentials in a stored, publicly served URL would leak them to every reader.
    # "@" anywhere in the authority is refused, not only a parsed user name, so a
    # crafted authority cannot hide a password from ``urlsplit``.
    if "@" in parts.netloc:
        message = "url must not contain user information (credentials)"
        raise ValueError(message)
    if not parts.hostname:
        message = "url must have a host"
        raise ValueError(message)
    try:
        # Accessing ``port`` validates it (digits, 0-65535); the value is unused.
        _ = parts.port
    except ValueError as error:
        message = "url has an invalid port"
        raise ValueError(message) from error
    return value


SourceUrl = Annotated[
    str,
    StringConstraints(min_length=1, max_length=SOURCE_URL_MAX_LENGTH),
    AfterValidator(_check_source_url),
]
"""Where the source can be found: an ``http`` or ``https`` URL, at most 2048 characters.

Stored verbatim. It must have a host, a valid port if any, and no user information,
and must be printable ASCII (**proposed** rules). Other schemes such as
``javascript:`` or ``data:`` are refused.
"""

_SOURCE_URL: TypeAdapter[str] = TypeAdapter(SourceUrl)


def parse_source_url(value: str) -> str:
    """Validate ``value`` as a ``SourceUrl`` outside a Pydantic model.

    For code paths (importers, command handlers) that hold a raw string and want the
    domain error rather than Pydantic's.

    Args:
        value: The candidate URL.

    Returns:
        The URL, unchanged.

    Raises:
        InvalidSourceUrlError: If the URL breaks any ``SourceUrl`` rule. The URL
            itself is never repeated in the error, because it may carry a token.
    """
    try:
        return _SOURCE_URL.validate_python(value)
    except PydanticValidationError as error:
        raise InvalidSourceUrlError.because(error.errors()[0]["msg"]) from error


# --------------------------------------------------------------------------- #
# Licence                                                                     #
# --------------------------------------------------------------------------- #

SPDX_ID_PATTERN = r"^[A-Za-z0-9.+-]{2,64}$"
LICENCE_TEXT_MAX_LENGTH = 500

SpdxLicenceId = Annotated[str, StringConstraints(pattern=SPDX_ID_PATTERN)]
"""An SPDX licence identifier or expression-free id such as ``CC-BY-4.0``.

Only the shape is checked (2 to 64 of ``A-Z a-z 0-9 . + -``); membership of the SPDX
licence list is not, because the list changes and the domain must not bundle it
(**proposed**).
"""

LicenceText = Annotated[str, *safe_text(LICENCE_TEXT_MAX_LENGTH)]
"""The terms of a licence that has no SPDX identifier, 1 to 500 characters."""


class Licence(BaseModel):
    """The terms under which a source may be reused (**proposed** model).

    Exactly one of ``spdx_id`` and ``custom_text`` is set: an SPDX identifier where
    one exists, otherwise a short statement of the custom terms (for example
    "Shared with Yakhnama for research use only").

    Implements: Value Object.

    Attributes:
        spdx_id: SPDX licence identifier, or ``None`` for a custom licence.
        custom_text: The custom terms, or ``None`` for an SPDX licence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spdx_id: SpdxLicenceId | None = None
    custom_text: LicenceText | None = None

    @model_validator(mode="after")
    def _require_exactly_one(self) -> Self:
        if (self.spdx_id is None) == (self.custom_text is None):
            message = "a licence has exactly one of spdx_id and custom_text"
            raise ValueError(message)
        return self

    @property
    def is_custom(self) -> bool:
        """Tell whether the licence is custom rather than an SPDX licence.

        Returns:
            ``True`` if ``custom_text`` is set.
        """
        return self.custom_text is not None

    @classmethod
    def spdx(cls, spdx_id: str) -> Self:
        """Build an SPDX licence.

        Args:
            spdx_id: The SPDX identifier, for example ``"CC-BY-4.0"``.

        Returns:
            The licence.

        Raises:
            pydantic.ValidationError: If the identifier is malformed.
        """
        return cls(spdx_id=spdx_id)

    @classmethod
    def custom(cls, text: str) -> Self:
        """Build a custom licence.

        Args:
            text: The terms, 1 to 500 characters after the safe-text rules.

        Returns:
            The licence.

        Raises:
            pydantic.ValidationError: If the text is empty, too long or unsafe.
        """
        return cls(custom_text=text)


# --------------------------------------------------------------------------- #
# Time, references, versions                                                  #
# --------------------------------------------------------------------------- #

RetrievalTime = DateWithPrecision
"""When the source was retrieved or accessed, with how precisely that is known.

An alias rather than a subclass, so a ``DateWithPrecision`` from anywhere is accepted
and the storage mapping is shared with every other dated field.
"""

RECORD_VERSION_MAX = 2_147_483_647
RecordVersion = Annotated[int, Field(ge=1, le=RECORD_VERSION_MAX)]
"""Optimistic-concurrency version: 1 when created, +1 per change with an effect.

Bounded by the largest signed 32-bit integer so it fits a PostgreSQL ``integer``.
"""


class SourceRef(BaseModel):
    """A pointer from a fact (claim, event, report) to the source it came from.

    Other modules store this rather than a copy of the source, so the provenance of a
    fact can always be followed back to one immutable record.

    Implements: Value Object.

    Attributes:
        source_id: Id of the referenced ``Source``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: EntityId


class SourceOwner(BaseModel):
    """Who registered a source and on whose behalf.

    Implements: Value Object.

    Attributes:
        actor_id: The registering user, or ``None`` when the system registered it
            (for example an importer).
        organization_id: The organisation it was registered for, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: EntityId | None = None
    organization_id: EntityId | None = None


SYSTEM_OWNER: Final = SourceOwner()
"""Owner of sources registered by the system, for no organisation."""


class SourceDetails(BaseModel):
    """The descriptive fields of a source, registered together and edited together.

    Grouping them lets registration and ``Source.update_details`` share one set of
    rules, and lets an update be compared with the current details as a whole.

    Implements: Value Object.

    Attributes:
        title: Short title.
        citation: How to cite the source.
        url: Where it can be found, if anywhere online.
        licence: Reuse terms, if known.
        retrieved_at: When it was retrieved, if known.
        publisher: Who published it, if known.
        language: Language of the source's content, if known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: SourceTitle
    citation: Citation
    url: SourceUrl | None = None
    licence: Licence | None = None
    retrieved_at: RetrievalTime | None = None
    publisher: Publisher | None = None
    language: LanguageCode | None = None

    def as_fields(self) -> dict[str, object]:
        """Return the details as field values, keeping nested value objects intact.

        Unlike ``model_dump`` this does not turn ``licence`` or ``retrieved_at`` into
        dictionaries, so the aggregate receives the validated objects themselves.

        Returns:
            A new mapping from field name to value.
        """
        return {name: getattr(self, name) for name in type(self).model_fields}

    def changed_fields(self, other: "SourceDetails") -> frozenset[str]:
        """Return the names of the fields whose values differ from ``other``.

        Args:
            other: The details to compare with.

        Returns:
            Field names, empty if the details are equal.
        """
        return frozenset(
            name
            for name in type(self).model_fields
            if getattr(self, name) != getattr(other, name)
        )


_SPDX_ID_REGEX = re.compile(SPDX_ID_PATTERN)


def is_spdx_licence_id(value: str) -> bool:
    """Tell whether ``value`` has the shape of an SPDX licence identifier.

    Args:
        value: A candidate such as ``"CC-BY-4.0"``.

    Returns:
        ``True`` if it matches ``SPDX_ID_PATTERN``.
    """
    return _SPDX_ID_REGEX.fullmatch(value) is not None
