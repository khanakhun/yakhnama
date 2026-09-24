"""Unit tests for ``yakhnama.modules.provenance.domain.value_objects``."""

import string
import unicodedata
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.provenance.domain.errors import InvalidSourceUrlError
from yakhnama.modules.provenance.domain.value_objects import (
    CITATION_MAX_LENGTH,
    LICENCE_TEXT_MAX_LENGTH,
    PUBLISHER_MAX_LENGTH,
    SOURCE_TITLE_MAX_LENGTH,
    SOURCE_URL_MAX_LENGTH,
    SPDX_ID_PATTERN,
    SYSTEM_OWNER,
    Citation,
    Licence,
    Publisher,
    RetrievalTime,
    SourceDetails,
    SourceOwner,
    SourceRef,
    SourceTitle,
    SourceType,
    SourceUrl,
    is_spdx_licence_id,
    parse_source_url,
)
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

IDS = SequentialIdGenerator()
SOURCE_URL: TypeAdapter[str] = TypeAdapter(SourceUrl)
FREE_TEXT_TYPES = [
    (Citation, CITATION_MAX_LENGTH),
    (SourceTitle, SOURCE_TITLE_MAX_LENGTH),
    (Publisher, PUBLISHER_MAX_LENGTH),
]

_host_label = st.text(
    alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=20
)
hosts = st.lists(_host_label, min_size=1, max_size=4).map(".".join)
paths = st.text(
    alphabet=string.ascii_letters + string.digits + "-._~/%", max_size=60
).map(lambda path: "/" + path)
url_user_info = st.text(
    alphabet=string.ascii_letters + string.digits + ":", min_size=0, max_size=20
)


# --------------------------------------------------------------------------- #
# Source type                                                                 #
# --------------------------------------------------------------------------- #


def test_source_type_values_match_glossary_exactly() -> None:
    expected = {
        "citizen",
        "organisation",
        "government",
        "news",
        "satellite",
        "research",
        "dataset",
    }

    values = {member.value for member in SourceType}

    assert values == expected


# --------------------------------------------------------------------------- #
# Free text                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("text_type", "max_length"), FREE_TEXT_TYPES)
def test_free_text_surrounding_whitespace_and_decomposed_form_is_normalised(
    text_type: object, max_length: int
) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(text_type)

    value = adapter.validate_python("  Pasú  ")

    assert value == "Pasú"
    assert max_length > 0


@pytest.mark.parametrize(("text_type", "max_length"), FREE_TEXT_TYPES)
def test_free_text_at_max_length_is_accepted_and_one_more_rejected(
    text_type: object, max_length: int
) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(text_type)

    accepted = adapter.validate_python("a" * max_length)

    assert len(accepted) == max_length
    with pytest.raises(PydanticValidationError):
        adapter.validate_python("a" * (max_length + 1))


@pytest.mark.parametrize(("text_type", "max_length"), FREE_TEXT_TYPES)
def test_free_text_whitespace_only_is_rejected(
    text_type: object, max_length: int
) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(text_type)

    with pytest.raises(PydanticValidationError):
        adapter.validate_python("   ")
    assert max_length > 0


@given(
    st.characters(codec="utf-8", categories=["Cc"])
    | st.sampled_from([chr(0x202A), chr(0x202E), chr(0x2066), chr(0x2069)])
)
def test_citation_with_control_or_bidi_character_inside_is_rejected(
    character: str,
) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(Citation)

    with pytest.raises(PydanticValidationError):
        adapter.validate_python(f"Report{character}text")


@given(st.text(min_size=1, max_size=200))
def test_citation_accepted_values_are_nfc_and_stripped(text: str) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(Citation)

    try:
        value = adapter.validate_python(text)
    except PydanticValidationError:
        return

    assert value == unicodedata.normalize("NFC", value).strip()
    assert 1 <= len(value) <= CITATION_MAX_LENGTH


# --------------------------------------------------------------------------- #
# URL                                                                         #
# --------------------------------------------------------------------------- #


@given(st.sampled_from(["http", "https", "HTTPS"]), hosts, paths)
def test_source_url_http_or_https_with_host_is_accepted_verbatim(
    scheme: str, host: str, path: str
) -> None:
    url = f"{scheme}://{host}{path}"

    value = SOURCE_URL.validate_python(url)

    assert value == url


@given(st.sampled_from(["http", "https"]), url_user_info, hosts)
def test_source_url_with_user_information_is_rejected(
    scheme: str, user_info: str, host: str
) -> None:
    url = f"{scheme}://{user_info}@{host}/"

    with pytest.raises(PydanticValidationError, match="user information"):
        SOURCE_URL.validate_python(url)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JavaScript://example.test/%0Aalert(1)",
        "data:text/html,hi",
        "ftp://example.test/file",
        "file:///etc/passwd",
        "//example.test/no-scheme",
        "example.test",
    ],
)
def test_source_url_with_other_scheme_is_rejected(url: str) -> None:
    with pytest.raises(PydanticValidationError, match="http or https"):
        SOURCE_URL.validate_python(url)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://", "host"),
        ("https:///path", "host"),
        ("https://example.test:99999/", "port"),
        ("https://example.test:port/", "port"),
        ("https://example.test/a b", "printable ASCII"),
        ("https://exämple.test/", "printable ASCII"),
        ("https://example.test/\x00", "printable ASCII"),
        (" https://example.test/", "printable ASCII"),
    ],
)
def test_source_url_malformed_is_rejected_with_rule(url: str, reason: str) -> None:
    with pytest.raises(PydanticValidationError, match=reason):
        SOURCE_URL.validate_python(url)


def test_source_url_at_max_length_is_accepted_and_one_more_rejected() -> None:
    prefix = "https://example.test/"
    longest = prefix + "a" * (SOURCE_URL_MAX_LENGTH - len(prefix))

    accepted = SOURCE_URL.validate_python(longest)

    assert accepted == longest
    with pytest.raises(PydanticValidationError):
        SOURCE_URL.validate_python(longest + "a")


def test_source_url_with_port_query_and_fragment_is_accepted() -> None:
    url = "https://example.test:8443/report?id=7#section-2"

    value = SOURCE_URL.validate_python(url)

    assert value == url


def test_parse_source_url_valid_url_is_returned_unchanged() -> None:
    url = "https://example.test/report"

    value = parse_source_url(url)

    assert value == url


def test_parse_source_url_credentials_raise_domain_error_without_the_url() -> None:
    url = "https://reporter:secret-token@example.test/"

    with pytest.raises(InvalidSourceUrlError) as raised:
        parse_source_url(url)

    assert "secret-token" not in str(raised.value)
    assert "secret-token" not in repr(dict(raised.value.details))
    assert "user information" in str(raised.value.details["reason"])


# --------------------------------------------------------------------------- #
# Licence                                                                     #
# --------------------------------------------------------------------------- #


@given(st.from_regex(SPDX_ID_PATTERN, fullmatch=True))
def test_licence_spdx_well_formed_id_is_accepted(spdx_id: str) -> None:
    licence = Licence.spdx(spdx_id)

    assert licence.spdx_id == spdx_id
    assert not licence.is_custom
    assert is_spdx_licence_id(spdx_id)


@given(st.text(max_size=80))
def test_licence_spdx_validation_agrees_with_is_spdx_licence_id(
    candidate: str,
) -> None:
    expected = is_spdx_licence_id(candidate)

    try:
        Licence.spdx(candidate)
    except PydanticValidationError:
        accepted = False
    else:
        accepted = True

    assert accepted == expected


@pytest.mark.parametrize("spdx_id", ["A", "a" * 65, "CC BY 4.0", "MIT/X11", "", "é1"])
def test_licence_spdx_malformed_id_is_rejected(spdx_id: str) -> None:
    with pytest.raises(PydanticValidationError):
        Licence.spdx(spdx_id)


def test_licence_custom_text_is_normalised_and_marked_custom() -> None:
    licence = Licence.custom("  Research use only  ")

    assert licence.custom_text == "Research use only"
    assert licence.spdx_id is None
    assert licence.is_custom


def test_licence_custom_text_too_long_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        Licence.custom("a" * (LICENCE_TEXT_MAX_LENGTH + 1))


@pytest.mark.parametrize(
    "fields",
    [{}, {"spdx_id": "MIT", "custom_text": "Also custom"}],
)
def test_licence_with_neither_or_both_forms_is_rejected(
    fields: dict[str, str],
) -> None:
    with pytest.raises(PydanticValidationError, match="exactly one"):
        Licence.model_validate(fields)


# --------------------------------------------------------------------------- #
# References, owners, details                                                 #
# --------------------------------------------------------------------------- #


def test_retrieval_time_is_a_date_with_precision() -> None:
    retrieved = RetrievalTime(
        value=datetime(2022, 7, 1, tzinfo=UTC), precision=DatePrecision.DAY
    )

    assert isinstance(retrieved, DateWithPrecision)


def test_source_ref_non_uuid7_id_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        SourceRef.model_validate({"source_id": "00000000-0000-4000-8000-000000000000"})


def test_source_ref_equal_ids_are_equal_values() -> None:
    source_id = IDS.new_id()

    first, second = SourceRef(source_id=source_id), SourceRef(source_id=source_id)

    assert first == second
    assert hash(first) == hash(second)


def test_system_owner_has_no_actor_and_no_organization() -> None:
    owner = SYSTEM_OWNER

    assert owner == SourceOwner(actor_id=None, organization_id=None)


def test_source_details_as_fields_keeps_nested_value_objects() -> None:
    licence = Licence.spdx("CC-BY-4.0")
    details = SourceDetails(title="Title", citation="Citation", licence=licence)

    fields = details.as_fields()

    assert fields["licence"] is licence
    assert set(fields) == set(SourceDetails.model_fields)


def test_source_details_changed_fields_names_only_differing_fields() -> None:
    before = SourceDetails(title="Title", citation="Citation")
    after = SourceDetails(
        title="Title", citation="New citation", url="https://example.test/"
    )

    changed = after.changed_fields(before)

    assert changed == frozenset({"citation", "url"})
    assert before.changed_fields(before) == frozenset()


def test_source_details_language_is_normalised() -> None:
    details = SourceDetails(title="Title", citation="Citation", language="UR-arab")

    assert details.language == "ur-Arab"
