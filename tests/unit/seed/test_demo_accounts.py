"""Unit tests for the seed's demo accounts step, its wiring and the dev realm."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tests.fakes.clock import FrozenClock
from tests.fakes.geography import InMemoryGeographyUnitOfWork
from tests.fakes.hazards import InMemoryHazardsUnitOfWork
from tests.fakes.identity import InMemoryIdentityUnitOfWork, actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.impacts import InMemoryImpactsUnitOfWork
from tests.fakes.seed import FakeReferenceFileReader
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.geography.application.handlers import (
    LoadReferencePlacesHandler,
)
from yakhnama.modules.hazards.application.handlers import (
    LoadReferenceHazardTypesHandler,
)
from yakhnama.modules.identity.public import (
    CanManageReferenceData,
    Role,
    SeedAccountsHandler,
)
from yakhnama.modules.impacts.application.handlers import (
    LoadReferenceImpactMetricsHandler,
)
from yakhnama.platform.container import build_container, build_demo_accounts_step
from yakhnama.platform.settings import Settings
from yakhnama.seed.application import (
    DemoAccountsSeedStep,
    SeedReferenceData,
    SeedReferenceDataHandler,
)
from yakhnama.seed.demo import (
    DEMO_ORG_MEMBER_SUBJECT,
    DEMO_ORGANIZATION,
    DEMO_ORGANIZATION_ID,
    DEMO_TRUSTED_REPORTER_SUBJECT,
    demo_accounts,
)

NOW: Final = datetime(2026, 10, 5, tzinfo=UTC)
ISSUER: Final = "http://127.0.0.1:18080/realms/yakhnama"
SYSTEM_ACTOR: Final = actor_with(
    {Role.ADMIN}, user_id=SequentialIdGenerator(seed=97).new_id()
)
REALM_FILE: Final = (
    Path(__file__).resolve().parents[3] / "docker" / "keycloak" / "yakhnama-realm.json"
)


def wire() -> tuple[SeedReferenceDataHandler, InMemoryIdentityUnitOfWork]:
    """Wire the seed with the demo accounts step over fakes."""
    policy = CanManageReferenceData()
    clock = FrozenClock(NOW)
    ids = SequentialIdGenerator()
    identity = InMemoryIdentityUnitOfWork()
    handler = SeedReferenceDataHandler(
        reader=FakeReferenceFileReader(),
        policy=policy,
        load_hazard_types=LoadReferenceHazardTypesHandler(
            InMemoryUnitOfWorkFactory(InMemoryHazardsUnitOfWork()), policy, clock, ids
        ),
        load_impact_metrics=LoadReferenceImpactMetricsHandler(
            InMemoryUnitOfWorkFactory(InMemoryImpactsUnitOfWork()), policy, clock, ids
        ),
        load_places=LoadReferencePlacesHandler(
            InMemoryUnitOfWorkFactory(InMemoryGeographyUnitOfWork()),
            policy,
            clock,
            ids,
        ),
        accounts=DemoAccountsSeedStep(
            load=SeedAccountsHandler(InMemoryUnitOfWorkFactory(identity), clock, ids),
            organization=DEMO_ORGANIZATION,
            accounts=demo_accounts(ISSUER),
        ),
    )
    return handler, identity


async def test_seed_with_demo_step_creates_accounts_and_membership() -> None:
    handler, identity = wire()

    report = await handler(SeedReferenceData(actor=SYSTEM_ACTOR))

    subjects = {user.subject: user for user in identity.users.committed.values()}
    members = await identity.memberships.list_for_organization(DEMO_ORGANIZATION_ID)
    assert report.accounts is not None
    assert report.accounts.users_created == 2
    assert set(subjects) == {DEMO_TRUSTED_REPORTER_SUBJECT, DEMO_ORG_MEMBER_SUBJECT}
    assert all(user.issuer == ISSUER for user in subjects.values())
    assert [member.user_id for member in members.members] == [
        subjects[DEMO_ORG_MEMBER_SUBJECT].id
    ]
    assert "development only" in DEMO_ORGANIZATION.name


async def test_seed_with_demo_step_second_run_is_unchanged() -> None:
    handler, _ = wire()
    await handler(SeedReferenceData(actor=SYSTEM_ACTOR))

    report = await handler(SeedReferenceData(actor=SYSTEM_ACTOR))

    assert report.accounts is not None
    assert report.accounts.is_unchanged is True


async def test_build_demo_accounts_step_only_outside_production_with_an_issuer(
    settings: Settings,
) -> None:
    without_issuer = build_container(settings)
    with_issuer = build_container(settings.model_copy(update={"oidc_issuer": ISSUER}))

    missing = build_demo_accounts_step(without_issuer)
    step = build_demo_accounts_step(with_issuer)
    await without_issuer.aclose()
    await with_issuer.aclose()

    assert missing is None
    assert step is not None
    assert {account.identity.issuer for account in step.accounts} == {ISSUER}


def test_dev_realm_demo_users_match_the_seed_subjects_and_roles() -> None:
    realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    users = {user["username"]: user for user in realm["users"]}

    trusted = users["demo-trusted-reporter"]
    member = users["demo-org-member"]

    assert trusted["id"] == DEMO_TRUSTED_REPORTER_SUBJECT
    assert member["id"] == DEMO_ORG_MEMBER_SUBJECT
    assert set(trusted["realmRoles"]) == {"citizen", "trusted_reporter"}
    assert set(member["realmRoles"]) == {"citizen", "org_member"}
    assert trusted["email"].endswith("@example.invalid")
    assert member["email"].endswith("@example.invalid")
