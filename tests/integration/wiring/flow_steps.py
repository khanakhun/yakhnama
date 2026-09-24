"""HTTP and worker steps the wiring gate flows share.

Each step drives the application the way a client or the worker would: signed
bearer headers the locally keyed validator accepts, a checked JSON body, draining
the in-memory broker, rebuilding a broker task, walking a verification case to
``verified`` and publishing an event made from one report.
"""

from typing import Any, Final

import httpx
from taskiq import InMemoryBroker

from tests.fakes.auth import access_token_claims, issue_token, session_key_pair
from yakhnama.platform.container import Container
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId

API: Final = "/api/v1"
MODERATION: Final = f"{API}/moderation"
CASES: Final = f"{MODERATION}/verification-cases"
VERIFY_PATH: Final = ("submitted", "under_review", "verified")
# Any: decoded JSON bodies, the external boundary of these tests.
type Json = dict[str, Any]


def bearer_headers(subject: str, roles: tuple[str, ...] = ()) -> dict[str, str]:
    """Return an ``Authorization`` header signed by the session key.

    Args:
        subject: The token's ``sub``; one subject is one mirrored user.
        roles: Realm role names, for example ``("moderator",)``.

    Returns:
        ``{"Authorization": "Bearer <token>"}``.
    """
    claims = access_token_claims(subject=subject, realm_roles=roles)
    return {"Authorization": f"Bearer {issue_token(claims, session_key_pair())}"}


def json_body(response: httpx.Response, status: int) -> Json:
    """Return the decoded body after checking the status.

    Args:
        response: The response.
        status: The expected status code.

    Returns:
        The JSON object.
    """
    assert response.status_code == status, response.text
    body: Json = response.json()
    return body


async def drain_tasks(container: Container) -> None:
    """Wait for every task the in-memory broker started in this process.

    Args:
        container: The container whose broker is the memory one.
    """
    broker = container.task_broker
    assert isinstance(broker, InMemoryBroker)
    await broker.wait_all()


def scheduled_task(task_name: str, payload: dict[str, object]) -> ScheduledTask:
    """Rebuild a task as the worker receives it from the broker.

    Args:
        task_name: The task name.
        payload: The JSON payload.

    Returns:
        The task.
    """
    return ScheduledTask.model_validate(
        {
            "task_id": TaskId(value=f"{task_name}-1"),
            "task_name": task_name,
            "payload": payload,
        }
    )


async def verify_event(
    client: httpx.AsyncClient, event_id: str, headers: dict[str, str]
) -> None:
    """Walk the event's verification case to ``verified``.

    Args:
        client: The application client.
        event_id: The event.
        headers: A moderator's headers.
    """
    cases = json_body(
        await client.get(CASES, params={"target_kind": "event"}, headers=headers), 200
    )
    case_id = next(
        item["id"] for item in cases["items"] if item["target"]["target_id"] == event_id
    )
    for state in VERIFY_PATH:
        case = json_body(await client.get(f"{CASES}/{case_id}", headers=headers), 200)
        if case["state"] == state:
            continue
        json_body(
            await client.post(
                f"{MODERATION}/verification/{case_id}/transitions",
                json={"to_state": state, "reason": "Checked against the field team."},
                headers=headers,
            ),
            200,
        )


async def publish_verified_event_from_report(
    client: httpx.AsyncClient,
    *,
    report: Json,
    reporter_headers: dict[str, str],
    moderator_headers: dict[str, str],
    hazard_code: str,
) -> Json:
    """Submit one report, make an event of it, publish and verify the event.

    Args:
        client: The application client.
        report: The report body to submit.
        reporter_headers: The reporter's headers.
        moderator_headers: A moderator's headers.
        hazard_code: The event's hazard type.

    Returns:
        The created event body.
    """
    submitted = json_body(
        await client.post(f"{API}/reports", json=report, headers=reporter_headers),
        201,
    )
    event = json_body(
        await client.post(
            f"{MODERATION}/events",
            json={
                "report_ids": [submitted["id"]],
                "hazard_type": hazard_code,
                "title": "Outburst flood below the glacier",
            },
            headers=moderator_headers,
        ),
        201,
    )
    json_body(
        await client.post(
            f"{MODERATION}/events/{event['id']}/publication",
            headers=moderator_headers,
        ),
        200,
    )
    await verify_event(client, event["id"], moderator_headers)
    return event
