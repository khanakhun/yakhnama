"""Unit tests for the storage key layout, the policies and the facade."""

import pytest

from tests.fakes.identity import AllowAllPolicy
from tests.unit.modules.exchange.application.support import (
    ANONYMOUS,
    CITIZEN,
    MODERATOR,
    OTHER,
    USER_ID,
)
from yakhnama.modules.exchange import public
from yakhnama.modules.exchange.application import handlers as handlers_module
from yakhnama.modules.exchange.application.authorisation import (
    export_job_policy,
    export_policy,
    import_policy,
)
from yakhnama.modules.exchange.application.ports import (
    export_artifact_key,
    export_sidecar_key,
    inline_import_key,
)
from yakhnama.modules.exchange.domain.value_objects import ArtifactRef, ExportDataset
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.errors import PermissionDeniedError


def test_storage_keys_follow_the_documented_layout() -> None:
    keys = (
        export_artifact_key(USER_ID, ExportDataset.CLAIMS, ".parquet"),
        export_sidecar_key(USER_ID, ExportDataset.CLAIMS),
        inline_import_key(USER_ID, ".csv"),
    )

    assert keys == (
        f"exports/{USER_ID}/claims.parquet",
        f"exports/{USER_ID}/claims.sidecar.json",
        f"imports/{USER_ID}/source.csv",
    )


def test_storage_keys_are_valid_object_keys() -> None:
    key = export_artifact_key(USER_ID, ExportDataset.EVENTS, ".geojson")

    artifact = ArtifactRef(
        object_key=key, byte_size=0, sha256="0" * 64, media_type="application/geo+json"
    )

    assert artifact.object_key == key


@pytest.mark.parametrize(
    ("dataset", "actor", "expected"),
    [
        (ExportDataset.EVENTS, CITIZEN, True),
        (ExportDataset.CLAIMS, CITIZEN, True),
        (ExportDataset.EVENTS, ANONYMOUS, False),
        (ExportDataset.REPORTS, CITIZEN, False),
        (ExportDataset.REPORTS, MODERATOR, True),
    ],
)
def test_export_policy_allows_by_dataset(
    dataset: ExportDataset, actor: Actor, *, expected: bool
) -> None:
    allowed = export_policy(dataset).is_allowed(actor)

    assert allowed is expected


@pytest.mark.parametrize(
    ("actor", "expected"),
    [(CITIZEN, True), (MODERATOR, True), (OTHER, False), (ANONYMOUS, False)],
)
def test_export_job_policy_allows_owner_and_moderators(
    actor: Actor, *, expected: bool
) -> None:
    allowed = export_job_policy(USER_ID).is_allowed(actor)

    assert allowed is expected


def test_import_policy_allows_moderators_only() -> None:
    allowed = [import_policy().is_allowed(actor) for actor in (MODERATOR, CITIZEN)]

    assert allowed == [True, False]


def test_public_facade_exports_every_listed_name() -> None:
    missing = [name for name in public.__all__ if not hasattr(public, name)]

    assert missing == []


def test_authorise_with_permissive_policy_still_refuses_anonymous_owner() -> None:
    # The guard is unreachable through the module's own policies, which all refuse
    # anonymous actors, so it is exercised directly with a permissive one.
    with pytest.raises(PermissionDeniedError):
        handlers_module._authorise(AllowAllPolicy(), ANONYMOUS, "export events")
