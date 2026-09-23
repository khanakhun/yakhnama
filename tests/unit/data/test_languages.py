"""``data/reference/languages.yaml`` against a test-local model.

No domain model exists for the language list yet (the application layer may adopt
the file later), so the shape is pinned here to stop the file drifting meanwhile.
"""

from collections import Counter
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tests.unit.data.reference_files import load_reference
from yakhnama.modules.geography.domain.value_objects import ScriptCode
from yakhnama.shared_kernel.value_objects import LanguageCode

FILE_NAME = "languages.yaml"
EXPECTED_ISO_639_3 = frozenset({"eng", "urd", "scl", "bsk", "bft", "wbl", "khw"})
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class LanguageReferenceEntry(BaseModel):
    """One language of the reference list.

    Implements: Value Object.

    Attributes:
        code: Language subtag as used in ``LanguageCode``.
        iso_639_3: Three-letter ISO 639-3 code.
        english_name: English name of the language.
        default_script: Proposed default ISO 15924 script.
        alternative_scripts: Other scripts the language is written in.
        status: Always ``proposed`` for now.
        source: Citation for the code.
        notes: Remarks for reviewers, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: LanguageCode
    iso_639_3: Annotated[str, StringConstraints(pattern=r"^[a-z]{3}$")]
    english_name: Text
    default_script: ScriptCode
    alternative_scripts: tuple[ScriptCode, ...] = ()
    status: Literal["proposed"]
    source: Text
    notes: Text | None = None


class LanguageReferenceFile(BaseModel):
    """The whole language list.

    Implements: Value Object.

    Attributes:
        schema_version: Only ``1`` exists.
        data_version: Version of the content.
        source: Citation for the file.
        licence: Licence of the content.
        entries: The languages.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    data_version: Text
    source: Text
    licence: Text
    entries: tuple[LanguageReferenceEntry, ...] = Field(min_length=1)


@pytest.fixture(scope="module")
def reference() -> LanguageReferenceFile:
    """Return the validated language file."""
    return LanguageReferenceFile.model_validate(load_reference(FILE_NAME))


def test_languages_yaml_validates_and_round_trips_equal(
    reference: LanguageReferenceFile,
) -> None:
    dumped = reference.model_dump(mode="json")

    result = LanguageReferenceFile.model_validate(dumped)

    assert result == reference


def test_languages_yaml_lists_the_seven_expected_languages(
    reference: LanguageReferenceFile,
) -> None:
    result = frozenset(entry.iso_639_3 for entry in reference.entries)

    assert result == EXPECTED_ISO_639_3


def test_languages_yaml_codes_are_unique(reference: LanguageReferenceFile) -> None:
    counts = Counter(entry.code for entry in reference.entries)

    duplicates = sorted(code for code, count in counts.items() if count > 1)

    assert duplicates == []


def test_languages_yaml_alternative_scripts_exclude_default(
    reference: LanguageReferenceFile,
) -> None:
    result = sorted(
        entry.code
        for entry in reference.entries
        if entry.default_script in entry.alternative_scripts
    )

    assert result == []


def test_languages_yaml_local_languages_default_to_arabic_script(
    reference: LanguageReferenceFile,
) -> None:
    result = sorted(
        entry.code
        for entry in reference.entries
        if entry.code != "en" and entry.default_script is not ScriptCode.ARAB
    )

    assert result == []
