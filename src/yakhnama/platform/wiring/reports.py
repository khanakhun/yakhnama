"""Adapters answering the reports module's ports from the media facade.

- ``MediaOwnershipAdapter`` (``MediaOwnershipChecker``): every asset a report
  attaches must exist and be the reporter's upload.
- ``PhotoEvidenceAdapter`` (``PhotoEvidenceProvider``): the EXIF facts of the
  reporter's completed uploads, for the triage chain.
- ``RunTriageTaskAdapter``: the ``reports.run_triage`` task handler; it turns the
  task payload into ``RunTriage`` and calls ``RunTriageHandler``.

Both ports read ``media.public.MediaQueryService.list_assets``, the internal read
model with owners and EXIF. The ownership rule is applied here, not by an actor
policy: the caller is the reporter's own use case (or the system triage task), and
the port contract is "assets ``owner_id`` uploaded".

Patterns: Adapter.
"""

from collections.abc import Sequence

from yakhnama.modules.media.public import MediaQueryService, UploadStatus
from yakhnama.modules.reports.public import PhotoEvidence, RunTriage, RunTriageHandler
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.tasks import ScheduledTask


class MediaOwnershipAdapter:
    """``MediaOwnershipChecker`` over the media read model.

    Implements: Adapter.
    """

    def __init__(self, media: MediaQueryService) -> None:
        """Create the adapter.

        Args:
            media: The media module's read port.
        """
        self._media = media

    async def is_owned_by(
        self, media_ids: Sequence[EntityId], owner_id: EntityId
    ) -> bool:
        """Tell whether every listed asset exists and was uploaded by ``owner_id``.

        Args:
            media_ids: The assets a report wants to attach.
            owner_id: The reporter.

        Returns:
            ``True`` if all of them are ``owner_id``'s; ``True`` for no assets.
        """
        wanted = set(media_ids)
        if not wanted:
            return True
        records = await self._media.list_assets(tuple(wanted))
        owned = {record.id for record in records if record.owner_id == owner_id}
        return owned == wanted


class PhotoEvidenceAdapter:
    """``PhotoEvidenceProvider`` over the media read model.

    Implements: Adapter.
    """

    def __init__(self, media: MediaQueryService) -> None:
        """Create the adapter.

        Args:
            media: The media module's read port.
        """
        self._media = media

    async def photos_for(
        self, media_ids: Sequence[EntityId], owner_id: EntityId
    ) -> tuple[PhotoEvidence, ...]:
        """Return the EXIF facts of the listed assets that ``owner_id`` uploaded.

        Assets that do not exist, belong to someone else or have not completed
        their upload are left out; a completed asset without EXIF is kept, with no
        facts, so the rules can tell "no metadata" from "no photo".

        Args:
            media_ids: The report's media assets.
            owner_id: The reporter.

        Returns:
            One entry per usable asset, in ``media_ids`` order.
        """
        if not media_ids:
            return ()
        records = {
            record.id: record for record in await self._media.list_assets(media_ids)
        }
        evidence: list[PhotoEvidence] = []
        for media_id in dict.fromkeys(media_ids):
            record = records.get(media_id)
            if (
                record is None
                or record.owner_id != owner_id
                or record.upload_status is not UploadStatus.COMPLETED
            ):
                continue
            exif = record.exif
            evidence.append(
                PhotoEvidence(
                    media_id=record.id,
                    taken_at=None if exif is None else exif.taken_at,
                    location=None if exif is None else exif.location,
                )
            )
        return tuple(evidence)


class RunTriageTaskAdapter:
    """The ``reports.run_triage`` task handler over ``RunTriageHandler``.

    The payload crossed the broker as JSON, so it is validated into ``RunTriage``
    rather than read by key (``platform.tasks.handlers`` contract).

    Implements: Adapter.
    """

    def __init__(self, handler: RunTriageHandler) -> None:
        """Create the task handler.

        Args:
            handler: The reports triage use case.
        """
        self._handler = handler

    async def __call__(self, task: ScheduledTask) -> None:
        """Run triage for the report named in the payload.

        Args:
            task: The task; payload ``{report_id}``.

        Raises:
            pydantic.ValidationError: If the payload is not a ``RunTriage``.
            ReportNotFoundError: If the report does not exist.
        """
        await self._handler(RunTriage.model_validate(dict(task.payload)))
