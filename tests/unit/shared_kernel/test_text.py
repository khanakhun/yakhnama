"""Unit tests for ``yakhnama.shared_kernel.text``."""

import unicodedata
from typing import Annotated

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.text import (
    ALLOWED_FORMAT_CHARACTERS,
    BIDI_CONTROL_CHARACTERS,
    FORBIDDEN_TEXT_CATEGORIES,
    LINE_FEED,
    SafeText,
    is_forbidden_character,
    normalise_multiline_text,
    normalise_text,
    safe_text,
    validate_safe_multiline_text,
    validate_safe_text,
)


def _text(*code_points: int) -> str:
    return "".join(chr(code_point) for code_point in code_points)


# Every non-ASCII test string is built from code points: invisible and right-to-left
# characters must not sit literally in the source (ruff PLE2502, Trojan Source).
ZWNJ, ZWJ, LRM, RLM, ALM = (
    _text(0x200C),
    _text(0x200D),
    _text(0x200E),
    _text(0x200F),
    _text(0x061C),
)
RLO, FSI, LONE_SURROGATE = _text(0x202E), _text(0x2068), _text(0xD800)
SPACE = 0x20
# The samples are letter sequences for exercising the validator, not glossed words.
# Urdu letters (gaf, lam, gaf, te; be, lam, te, sin, te, alef, nun).
URDU_SAMPLE = _text(
    0x06AF,
    0x0644,
    0x06AF,
    0x062A,
    SPACE,
    0x0628,
    0x0644,
    0x062A,
    0x0633,
    0x062A,
    0x0627,
    0x0646,
)
# Extended Arabic letters of the kind Shina orthographies add (retroflex and extra
# sibilant letters), mixed with a Latin numeral as a report would.
SHINA_SAMPLE = _text(0x0699, 0x0688, 0x0679, SPACE, 0x0685, 0x0686, 0x0698) + " 12"
# Balti in Perso-Arabic letters and, separately, in Tibetan script.
BALTI_ARABIC_SAMPLE = _text(0x0628, 0x0644, 0x062A, 0x06CC, SPACE, 0x06A9, 0x06BE)
BALTI_TIBETAN_SAMPLE = _text(0x0F56, 0x0F63, 0x0F0B, 0x0F4F, 0x0F72)
SCRIPT_SAMPLES = (URDU_SAMPLE, SHINA_SAMPLE, BALTI_ARABIC_SAMPLE, BALTI_TIBETAN_SAMPLE)

control_characters = st.one_of(
    st.integers(min_value=0x00, max_value=0x1F), st.integers(0x7F, 0x9F)
).map(chr)
surrogates = st.integers(min_value=0xD800, max_value=0xDFFF).map(chr)
bidi_controls = st.sampled_from(sorted(BIDI_CONTROL_CHARACTERS))
forbidden_characters = st.one_of(control_characters, surrogates, bidi_controls)
safe_characters = st.characters(
    exclude_categories=("Cc", "Cs"),
    exclude_characters="".join(BIDI_CONTROL_CHARACTERS),
)
safe_texts = st.text(alphabet=safe_characters, max_size=40)
arabic_script_characters = st.one_of(
    st.integers(min_value=0x0621, max_value=0x064A).map(chr),
    st.integers(min_value=0x0679, max_value=0x06D3).map(chr),
    st.sampled_from(sorted(ALLOWED_FORMAT_CHARACTERS)),
    st.just(" "),
)

safe_text_adapter: TypeAdapter[str] = TypeAdapter(SafeText)
bounded_adapter: TypeAdapter[str] = TypeAdapter(Annotated[str, *safe_text(5)])
multiline_adapter: TypeAdapter[str] = TypeAdapter(
    Annotated[str, *safe_text(20, allow_line_breaks=True)]
)

# --------------------------------------------------------------------------- #
# Normalisation                                                               #
# --------------------------------------------------------------------------- #


def test_normalise_text_combining_sequence_returns_precomposed_form() -> None:
    decomposed = "  Cafe" + _text(0x0301) + "	"

    normalised = normalise_text(decomposed)

    assert normalised == "Caf" + _text(0x00E9)


@given(value=st.text())
def test_normalise_text_applied_twice_returns_same_value(value: str) -> None:
    once = normalise_text(value)

    twice = normalise_text(once)

    assert twice == once
    assert once == once.strip()
    assert unicodedata.is_normalized("NFC", once)


def test_normalise_multiline_text_mixed_line_breaks_returns_line_feeds_only() -> None:
    value = "\r\n first\r\nsecond\rthird\n "

    normalised = normalise_multiline_text(value)

    assert normalised == "first\nsecond\nthird"


# --------------------------------------------------------------------------- #
# Character policy                                                            #
# --------------------------------------------------------------------------- #


@given(character=forbidden_characters)
def test_is_forbidden_character_forbidden_ranges_returns_true(character: str) -> None:
    result = is_forbidden_character(character)

    assert result is True


@given(character=safe_characters)
def test_is_forbidden_character_other_characters_returns_false(
    character: str,
) -> None:
    result = is_forbidden_character(character)

    assert result is False


def test_is_forbidden_character_allowed_marks_returns_false() -> None:
    results = {is_forbidden_character(mark) for mark in ALLOWED_FORMAT_CHARACTERS}

    assert results == {False}


def test_bidi_control_characters_are_embeddings_overrides_and_isolates() -> None:
    expected = {chr(code_point) for code_point in range(0x202A, 0x202F)} | {
        chr(code_point) for code_point in range(0x2066, 0x206A)
    }

    assert expected == BIDI_CONTROL_CHARACTERS
    assert frozenset({"Cc", "Cs"}) == FORBIDDEN_TEXT_CATEGORIES


def test_allowed_format_characters_are_zwnj_zwj_lrm_rlm_alm() -> None:
    expected = {ZWNJ, ZWJ, LRM, RLM, ALM}

    categories = {unicodedata.category(mark) for mark in ALLOWED_FORMAT_CHARACTERS}

    assert expected == ALLOWED_FORMAT_CHARACTERS
    assert categories == {"Cf"}
    assert not ALLOWED_FORMAT_CHARACTERS & BIDI_CONTROL_CHARACTERS


@given(prefix=safe_texts, character=forbidden_characters, suffix=safe_texts)
def test_validate_safe_text_forbidden_character_raises_value_error(
    prefix: str, character: str, suffix: str
) -> None:
    value = f"{prefix}{character}{suffix}"

    with pytest.raises(ValueError, match="control, surrogate or bidirectional"):
        validate_safe_text(value)


@pytest.mark.parametrize("character", ["\x00", "\n", "\t", RLO, FSI])
def test_validate_safe_text_named_characters_raises_value_error(
    character: str,
) -> None:
    value = f"a{character}b"

    with pytest.raises(ValueError, match="control"):
        validate_safe_text(value)


@given(value=safe_texts)
def test_validate_safe_text_safe_value_returns_it_unchanged(value: str) -> None:
    result = validate_safe_text(value)

    assert result is value


@given(character=forbidden_characters.filter(lambda character: character != "\n"))
def test_validate_safe_multiline_text_other_forbidden_raises_value_error(
    character: str,
) -> None:
    value = f"line{LINE_FEED}{character}"

    with pytest.raises(ValueError, match="control"):
        validate_safe_multiline_text(value)


def test_validate_safe_multiline_text_line_feeds_returns_value() -> None:
    value = f"first{LINE_FEED}{LINE_FEED}second"

    result = validate_safe_multiline_text(value)

    assert result == value


# --------------------------------------------------------------------------- #
# Types                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("sample", SCRIPT_SAMPLES)
def test_safe_text_script_samples_with_marks_returns_normalised_value(
    sample: str,
) -> None:
    value = f" {RLM}{sample}{ZWJ}{ZWNJ}{ALM}{LRM}12 "

    result = safe_text_adapter.validate_python(value)

    assert result == unicodedata.normalize("NFC", value).strip()


@given(value=st.text(alphabet=arabic_script_characters, min_size=1, max_size=60))
def test_safe_text_arabic_script_with_allowed_marks_accepts_value(value: str) -> None:
    result = safe_text_adapter.validate_python(value)

    assert result == normalise_text(value)


@given(prefix=safe_texts, character=forbidden_characters, suffix=safe_texts)
def test_safe_text_forbidden_character_raises_validation_error(
    prefix: str, character: str, suffix: str
) -> None:
    # Whitespace controls at either edge would be stripped away, so the forbidden
    # character is kept strictly inside the value.
    value = f"x{prefix}{character}{suffix}x"

    with pytest.raises(PydanticValidationError) as caught:
        safe_text_adapter.validate_python(value)

    assert caught.value.errors()[0]["type"] == "value_error"


def test_safe_text_non_string_raises_string_type_error() -> None:
    with pytest.raises(PydanticValidationError) as caught:
        safe_text_adapter.validate_python(5)

    assert caught.value.errors()[0]["type"] == "string_type"


def test_safe_text_bounded_padding_is_not_counted_returns_stripped_value() -> None:
    value = "   abcde   "

    result = bounded_adapter.validate_python(value)

    assert result == "abcde"


@pytest.mark.parametrize(
    ("value", "error_type"),
    [
        ("", "too_short"),
        ("   ", "too_short"),
        ("abcdef", "too_long"),
        (1, "string_type"),
    ],
)
def test_safe_text_bounded_invalid_value_raises_validation_error(
    value: object, error_type: str
) -> None:
    with pytest.raises(PydanticValidationError) as caught:
        bounded_adapter.validate_python(value)

    assert caught.value.errors()[0]["type"] == error_type


def test_safe_text_bounded_line_break_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError) as caught:
        bounded_adapter.validate_python("a\nb")

    assert caught.value.errors()[0]["type"] == "value_error"


def test_safe_text_min_length_zero_accepts_empty_value() -> None:
    adapter: TypeAdapter[str] = TypeAdapter(Annotated[str, *safe_text(3, 0)])

    result = adapter.validate_python("  ")

    assert result == ""


def test_safe_text_bounded_json_schema_carries_lengths() -> None:
    schema = bounded_adapter.json_schema()

    assert schema == {"type": "string", "minLength": 1, "maxLength": 5}


@pytest.mark.parametrize(("max_length", "min_length"), [(5, -1), (2, 3)])
def test_safe_text_invalid_bounds_raises_validation_error(
    max_length: int, min_length: int
) -> None:
    with pytest.raises(ValidationError, match="invalid bounds"):
        safe_text(max_length, min_length)


def test_safe_text_multiline_mixed_breaks_returns_line_feeds() -> None:
    value = " first\r\nsecond\rthird "

    result = multiline_adapter.validate_python(value)

    assert result == "first\nsecond\nthird"


@pytest.mark.parametrize("character", ["\t", "\x00", RLO, LONE_SURROGATE])
def test_safe_text_multiline_other_forbidden_raises_validation_error(
    character: str,
) -> None:
    with pytest.raises(PydanticValidationError) as caught:
        multiline_adapter.validate_python(f"a\n{character}b")

    assert caught.value.errors()[0]["type"] == "value_error"


def test_safe_text_multiline_non_string_raises_string_type_error() -> None:
    with pytest.raises(PydanticValidationError) as caught:
        multiline_adapter.validate_python(5)

    assert caught.value.errors()[0]["type"] == "string_type"
