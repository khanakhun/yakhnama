"""Safe free text: one normalisation and one character policy for every module.

Every free-text field a person can type (report descriptions, reasons, names, labels)
is normalised and checked the same way, so a value that exists is safe to store,
compare, log and export (Phase 2 security review, carried into Phase 3; ADR 0018,
proposed).

Normalisation (``normalise_text``):

- Unicode NFC, so text typed with combining characters equals its precomposed form
  and compares, deduplicates and indexes as one value.
- Leading and trailing whitespace stripped, so ``"  Hunza "`` and ``"Hunza"`` are one
  value and a whitespace-only string fails the minimum length.

Rejected characters (``validate_safe_text``):

- Category ``Cc`` (C0 and C1 controls, NUL included): unsafe in log lines, CSV and
  terminal output, and NUL is refused by PostgreSQL ``text`` columns.
- Category ``Cs`` (lone surrogates): cannot be encoded as UTF-8, so they would fail
  only later, at serialisation or storage.
- Bidirectional embeddings and overrides (U+202A-U+202E) and isolates
  (U+2066-U+2069): they reorder the text around them, so a value could display
  differently from how it is stored and compared ("Trojan Source", CVE-2021-42574).

Deliberately allowed: the implicit directional marks LRM (U+200E), RLM (U+200F) and
ALM (U+061C), and the joiners ZWJ (U+200D) and ZWNJ (U+200C). They are ``Cf`` format
characters that Urdu, Shina, Balti and Burushaski text in Arabic script needs for
correct shaping and for mixing right-to-left text with Latin numerals and names; they
never reorder text beyond the neighbouring characters, unlike the embeddings and
isolates above. Other ``Cf`` characters (for example U+200B, U+FEFF) and the line and
paragraph separators U+2028 and U+2029 are not rejected by this policy.

Line breaks are ``Cc`` and therefore rejected by default. Long-form fields opt in with
``safe_text(..., allow_line_breaks=True)``, which first turns ``CRLF`` and ``CR`` into
``LF`` and then accepts ``LF`` as the only control character.

The validators raise ``ValueError`` because Pydantic collects only ``ValueError`` into
one ``pydantic.ValidationError`` listing every problem; ``safe_text`` runs outside a
validator and raises the kernel's ``ValidationError``.

Patterns: Value Object (constrained string types).
"""

import unicodedata
from typing import Annotated, Final

from pydantic import AfterValidator, BeforeValidator, StringConstraints

from yakhnama.shared_kernel.errors import ValidationError

FORBIDDEN_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cs"})
"""Unicode general categories refused anywhere in safe text."""

BIDI_CONTROL_CHARACTERS: Final = frozenset(
    chr(code_point) for code_point in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
)
"""Embeddings, overrides (U+202A-U+202E) and isolates (U+2066-U+2069)."""

# Written as code points: the literal characters are invisible and ruff (PLE2502)
# rightly refuses them in source.
ALLOWED_FORMAT_CHARACTERS: Final = frozenset(
    chr(code_point) for code_point in (0x200C, 0x200D, 0x200E, 0x200F, 0x061C)
)
"""ZWNJ, ZWJ, LRM, RLM and ALM: kept for Arabic-script languages (see module docs)."""

LINE_FEED: Final = "\n"

_UNSAFE_TEXT_MESSAGE: Final = (
    "text must not contain control, surrogate or bidirectional formatting characters"
)


def normalise_text(value: str) -> str:
    """Return ``value`` in Unicode NFC with surrounding whitespace removed.

    NFC never turns an allowed character into a forbidden one and leaves lone
    surrogates as they are, so validating after normalising misses nothing.

    Args:
        value: Text as received.

    Returns:
        The normalised text.
    """
    return unicodedata.normalize("NFC", value).strip()


def normalise_multiline_text(value: str) -> str:
    """Return ``value`` normalised like ``normalise_text``, with line breaks as ``LF``.

    Args:
        value: Text as received, with any mix of ``CRLF``, ``CR`` and ``LF``.

    Returns:
        The normalised text, whose only line break is ``LF``.
    """
    return normalise_text(value.replace("\r\n", LINE_FEED).replace("\r", LINE_FEED))


def is_forbidden_character(character: str) -> bool:
    """Tell whether one character is refused in safe text.

    Args:
        character: A single character.

    Returns:
        ``True`` for ``Cc`` and ``Cs`` characters and bidirectional controls.
    """
    return (
        unicodedata.category(character) in FORBIDDEN_TEXT_CATEGORIES
        or character in BIDI_CONTROL_CHARACTERS
    )


def validate_safe_text(value: str) -> str:
    """Return ``value`` unchanged if it contains no forbidden character.

    Args:
        value: Text, normally already normalised.

    Returns:
        ``value`` itself.

    Raises:
        ValueError: If any character is a control (NUL and line breaks included), a
            lone surrogate or a bidirectional embedding, override or isolate.
    """
    if any(is_forbidden_character(character) for character in value):
        raise ValueError(_UNSAFE_TEXT_MESSAGE)
    return value


def validate_safe_multiline_text(value: str) -> str:
    """Return ``value`` unchanged if its only forbidden characters are ``LF``.

    Args:
        value: Text, normally already normalised by ``normalise_multiline_text``.

    Returns:
        ``value`` itself.

    Raises:
        ValueError: If any character other than ``LF`` is forbidden in safe text.
    """
    if any(
        character != LINE_FEED and is_forbidden_character(character)
        for character in value
    ):
        raise ValueError(_UNSAFE_TEXT_MESSAGE)
    return value


def _normalise_if_text(value: object) -> object:
    # Non-strings pass through untouched so Pydantic's own ``str`` check reports them
    # with its usual error instead of this validator failing with a TypeError.
    return normalise_text(value) if isinstance(value, str) else value


def _normalise_if_multiline_text(value: object) -> object:
    return normalise_multiline_text(value) if isinstance(value, str) else value


SafeText = Annotated[
    str,
    BeforeValidator(_normalise_if_text),
    AfterValidator(validate_safe_text),
]
"""Normalised, single-line safe text without a length bound.

Fields should prefer a bounded type built with ``safe_text``; this alias suits
``TypeAdapter`` checks and values bounded elsewhere.
"""

type SafeTextMetadata = tuple[BeforeValidator, StringConstraints, AfterValidator]


def safe_text(
    max_length: int, min_length: int = 1, *, allow_line_breaks: bool = False
) -> SafeTextMetadata:
    """Return the ``Annotated`` metadata for bounded safe text.

    It returns the metadata rather than an ``Annotated`` type because mypy accepts
    only statically written aliases as types. Unpack it into a module-level alias::

        Description = Annotated[str, *safe_text(4000, allow_line_breaks=True)]

    Lengths count code points after normalisation, so surrounding whitespace never
    counts and a whitespace-only value fails ``min_length``.

    Args:
        max_length: Maximum length, at least ``min_length``.
        min_length: Minimum length, at least 0; 1 by default so empty text is refused.
        allow_line_breaks: Accept ``LF`` (after turning ``CRLF`` and ``CR`` into
            ``LF``) for long-form text.

    Returns:
        The normaliser, the length constraints and the character check, in the
        order Pydantic must apply them.

    Raises:
        ValidationError: If the bounds are negative or ``max_length < min_length``;
            a programming error caught when the alias is defined.
    """
    if min_length < 0 or max_length < min_length:
        message = (
            f"invalid bounds: min_length={min_length}, max_length={max_length}; "
            "need 0 <= min_length <= max_length"
        )
        raise ValidationError(message)
    if allow_line_breaks:
        return (
            BeforeValidator(_normalise_if_multiline_text),
            StringConstraints(min_length=min_length, max_length=max_length),
            AfterValidator(validate_safe_multiline_text),
        )
    return (
        BeforeValidator(_normalise_if_text),
        StringConstraints(min_length=min_length, max_length=max_length),
        AfterValidator(validate_safe_text),
    )
