"""Unit tests for the identity DTO builders."""

from tests.unit.modules.identity.application.support import (
    EARLIER,
    make_membership,
    make_organization,
    make_user,
)
from yakhnama.modules.identity.application.dto import (
    MeDetail,
    MembershipSummary,
    MemberSummary,
    OrganizationDetail,
    OrganizationSummary,
    UserDetail,
)
from yakhnama.modules.identity.domain.value_objects import (
    OrganizationRole,
    OrganizationStatus,
    Role,
    UserStatus,
)


def test_me_detail_from_entities_keeps_active_orgs_sorted_by_slug() -> None:
    user = make_user(Role.MODERATOR, display_name="Me")
    beta, alpha = make_organization("beta-org"), make_organization("alpha-org")
    retired = make_organization("gone-org", status=OrganizationStatus.RETIRED)
    pairs = [
        (make_membership(beta, user), beta),
        (make_membership(alpha, user, OrganizationRole.ADMIN), alpha),
        (make_membership(retired, user), retired),
    ]

    detail = MeDetail.from_entities(user, pairs)

    assert detail.id == user.id
    assert detail.display_name == "Me"
    assert detail.roles == {Role.CITIZEN, Role.MODERATOR}
    assert detail.version == 1
    assert detail.memberships == (
        MembershipSummary(
            organization_id=alpha.id, slug="alpha-org", role=OrganizationRole.ADMIN
        ),
        MembershipSummary(
            organization_id=beta.id, slug="beta-org", role=OrganizationRole.MEMBER
        ),
    )


def test_user_detail_from_entity_omits_status_reason() -> None:
    user = make_user(is_suspended=True)

    detail = UserDetail.from_entity(user)

    assert detail.status is UserStatus.SUSPENDED
    assert "status_reason" not in detail.model_dump()
    assert "test suspension" not in detail.model_dump_json()


def test_organization_summary_and_detail_from_entity_copy_fields() -> None:
    organization = make_organization("some-org")

    summary = OrganizationSummary.from_entity(organization)
    detail = OrganizationDetail.from_entity(organization, member_count=3)

    assert summary.model_dump() == {
        "id": organization.id,
        "slug": "some-org",
        "name": organization.name,
        "organization_type": organization.organization_type,
        "status": OrganizationStatus.ACTIVE,
    }
    assert detail.member_count == 3
    assert detail.version == organization.version


def test_member_summary_from_entities_uses_membership_start_as_since() -> None:
    organization, user = make_organization(), make_user(display_name="Member")
    membership = make_membership(organization, user, OrganizationRole.ADMIN)

    summary = MemberSummary.from_entities(membership, user)

    assert summary.membership_id == membership.id
    assert summary.user_id == user.id
    assert summary.display_name == "Member"
    assert summary.role is OrganizationRole.ADMIN
    assert summary.since == EARLIER
    assert summary.version == 1
