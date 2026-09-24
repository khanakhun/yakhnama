"""HTTP routes of the ingestion context, mounted under ``/api/v1``.

The dataset catalog, run reports, observations and raster assets are open data
(``authorisation`` module docs; run reports public per the Phase 4 default), so
every ``GET`` here is anonymous. A sent-but-rejected token still answers 401.
Datasets are addressed by their stable catalog code; runs by id under
``/ingestion-runs``, because a run names only its dataset version and the read
port cannot confirm which dataset a run id belongs to.

``GET /raster-assets`` negotiates: ``?format=geojson`` or
``Accept: application/geo+json`` returns a GeoJSON ``FeatureCollection`` of STAC
Items (``RasterAssetSummary.to_stac_item``), anything else the JSON page;
``?format=`` wins, and every response says ``Vary: Accept``. The GeoJSON form
carries its next page only in the ``Link`` header.

Catalog changes and run requests live under ``/admin`` and need ``IsAdmin``
(``catalog_policy``) before anything is read.

Errors are never mapped here: handlers and query services raise ``YakhnamaError``
subclasses and the exception handlers in ``main.py`` render Problem Details.

Patterns: none from the catalog (thin transport layer over commands and queries).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status
from pydantic import BaseModel

from yakhnama.modules.ingestion.api.dependencies import (
    AdminActor,
    Services,
    require_valid_credentials,
)
from yakhnama.modules.ingestion.api.schemas import (
    CatalogueRasterAssetRequest,
    ChangeDatasetStatusRequest,
    DatasetCodeText,
    DatasetPage,
    DatasetStatusChange,
    ListDatasetsParameters,
    ListRasterAssetsParameters,
    ListRunsParameters,
    ObservationPage,
    QueryObservationsParameters,
    RasterAssetPage,
    RasterFormat,
    RecordDatasetVersionRequest,
    RegisterDatasetRequest,
    RunIngestionRequest,
    RunPage,
    StacItemCollection,
)
from yakhnama.modules.ingestion.public import (
    CatalogueRasterAsset,
    DatasetDetail,
    DatasetSummary,
    DatasetVersionSummary,
    DeprecateDataset,
    GetDataset,
    GetRun,
    ListDatasets,
    ListRasterAssets,
    ListRuns,
    QueryObservations,
    RasterAssetSummary,
    RecordDatasetVersion,
    RegisterDataset,
    RetireDataset,
    RunDetail,
    RunIngestion,
)
from yakhnama.platform.etag import (
    IF_MATCH_HEADER,
    expected_version_from_if_match,
    make_etag,
    set_etag,
)
from yakhnama.shared_kernel.errors import NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest

API_PREFIX: Final = "/api/v1"
DATASETS_PATH: Final = f"{API_PREFIX}/datasets"
RUNS_PATH: Final = f"{API_PREFIX}/ingestion-runs"
OBSERVATIONS_PATH: Final = f"{API_PREFIX}/observations"
RASTER_ASSETS_PATH: Final = f"{API_PREFIX}/raster-assets"
GEOJSON_MEDIA_TYPE: Final = "application/geo+json"
LINK_HEADER: Final = "Link"
LOCATION_HEADER: Final = "Location"
VARY_HEADER: Final = "Vary"
ACCEPT_MAX_LENGTH: Final = 1024
IF_MATCH_MAX_LENGTH: Final = 128
DATASET_NOT_FOUND_MESSAGE: Final = "No dataset has this code."
RUN_NOT_FOUND_MESSAGE: Final = "No ingestion run has this id."

# Any: the value type of FastAPI's own ``responses`` argument.
_PROBLEM: Final[dict[str, Any]] = {
    "description": "RFC 9457 Problem Details",
    "content": {"application/problem+json": {}},
}
_STAC_ALTERNATIVE: Final[dict[str, Any]] = {
    "description": "JSON, or a GeoJSON FeatureCollection of STAC Items when negotiated",
    "content": {GEOJSON_MEDIA_TYPE: {}},
}
_READ_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: _PROBLEM,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
    status.HTTP_429_TOO_MANY_REQUESTS: _PROBLEM,
    status.HTTP_503_SERVICE_UNAVAILABLE: _PROBLEM,
}
_ADMIN_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **_READ_RESPONSES,
    status.HTTP_400_BAD_REQUEST: _PROBLEM,
    status.HTTP_403_FORBIDDEN: _PROBLEM,
    status.HTTP_404_NOT_FOUND: _PROBLEM,
    status.HTTP_409_CONFLICT: _PROBLEM,
}
_NOT_FOUND: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_404_NOT_FOUND: _PROBLEM
}

router = APIRouter(
    prefix=API_PREFIX,
    tags=["ingestion"],
    dependencies=[Depends(require_valid_credentials)],
    responses=_READ_RESPONSES,
)
admin_router = APIRouter(
    prefix=f"{API_PREFIX}/admin", tags=["admin"], responses=_ADMIN_RESPONSES
)

DatasetCodePath = Annotated[DatasetCodeText, Path()]
AcceptHeader = Annotated[str | None, Header(max_length=ACCEPT_MAX_LENGTH)]
IfMatchHeader = Annotated[
    str | None,
    Header(
        alias=IF_MATCH_HEADER,
        max_length=IF_MATCH_MAX_LENGTH,
        description="Optional; the dataset's ETag, checked when sent.",
    ),
]
IdempotencyKey = Annotated[
    UUID | None,
    Header(
        alias="Idempotency-Key",
        description=(
            "A UUID chosen by the client; replaying it with the same request "
            "returns the stored response."
        ),
    ),
]


async def _dataset_by_code(services: Services, code: str) -> DatasetDetail:
    detail = await services.ingestion_queries.get_dataset(GetDataset(code=code))
    if detail is None:
        raise NotFoundError(DATASET_NOT_FOUND_MESSAGE)
    return detail


def _next_link(path: str, parameters: BaseModel, next_cursor: str | None) -> str | None:
    if next_cursor is None:
        return None
    following = parameters.model_copy(update={"cursor": next_cursor})
    query = urlencode(
        following.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    return f'<{path}?{query}>; rel="next"'


def _link_next(
    response: Response, path: str, parameters: BaseModel, next_cursor: str | None
) -> None:
    link = _next_link(path, parameters, next_cursor)
    if link is not None:
        response.headers[LINK_HEADER] = link


def wants_geojson(output_format: RasterFormat | None, accept: str | None) -> bool:
    """Tell whether the client asked for GeoJSON.

    Args:
        output_format: The ``format`` query parameter, if sent.
        accept: The ``Accept`` header, if sent.

    Returns:
        ``True`` for ``format=geojson``, or with no ``format`` when ``Accept``
        names ``application/geo+json``.
    """
    if output_format is not None:
        return output_format is RasterFormat.GEOJSON
    # A substring match: quality values are not weighed, because the only
    # alternative representation is JSON, which is also the default.
    return accept is not None and GEOJSON_MEDIA_TYPE in accept.lower()


# --------------------------------------------------------------------------- #
# Public reads                                                                #
# --------------------------------------------------------------------------- #


@router.get("/datasets")
async def list_datasets(
    parameters: Annotated[ListDatasetsParameters, Query()],
    response: Response,
    services: Services,
) -> DatasetPage:
    """List the dataset catalog, ordered by code.

    Args:
        parameters: Validated status filter, cursor and limit.
        response: Used to set the ``Link`` header.
        services: Use cases bound by the composition root.

    Returns:
        One page of datasets.
    """
    page = await services.ingestion_queries.list_datasets(
        ListDatasets(
            status=parameters.status,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    _link_next(response, DATASETS_PATH, parameters, page.next_cursor)
    return DatasetPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/datasets/{code}", responses=_NOT_FOUND)
async def get_dataset(
    code: DatasetCodePath, response: Response, services: Services
) -> DatasetDetail:
    """Return one dataset with its most recent versions and its ``ETag``.

    Args:
        code: The dataset's catalog code.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The dataset, licence and coverage included.
    """
    detail = await _dataset_by_code(services, code)
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


@router.get("/datasets/{code}/runs", responses=_NOT_FOUND)
async def list_dataset_runs(
    code: DatasetCodePath,
    parameters: Annotated[ListRunsParameters, Query()],
    response: Response,
    services: Services,
) -> RunPage:
    """List a dataset's ingestion runs, newest first.

    Args:
        code: The dataset's catalog code.
        parameters: Validated cursor and limit.
        response: Used to set the ``Link`` header.
        services: Use cases bound by the composition root.

    Returns:
        One page of run summaries; who requested a run is never shown.
    """
    dataset = await _dataset_by_code(services, code)
    page = await services.ingestion_queries.list_runs(
        ListRuns(
            dataset_id=dataset.id,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    _link_next(response, f"{DATASETS_PATH}/{code}/runs", parameters, page.next_cursor)
    return RunPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/ingestion-runs/{run_id}", responses=_NOT_FOUND)
async def get_run(run_id: EntityId, services: Services) -> RunDetail:
    """Return one ingestion run with its full report.

    Args:
        run_id: The run.
        services: Use cases bound by the composition root.

    Returns:
        The run, counts, checksum and report; who requested it is never shown.
    """
    detail = await services.ingestion_queries.get_run(GetRun(run_id=run_id))
    if detail is None:
        raise NotFoundError(RUN_NOT_FOUND_MESSAGE)
    return detail


@router.get("/observations", responses=_NOT_FOUND)
async def query_observations(
    parameters: Annotated[QueryObservationsParameters, Query()],
    response: Response,
    services: Services,
) -> ObservationPage:
    """Return one page of one variable's time series from one dataset.

    Args:
        parameters: Dataset code, variable, half-open window, optional site and
            version, cursor and limit.
        response: Used to set the ``Link`` header.
        services: Use cases bound by the composition root.

    Returns:
        Observations ordered by ``(observed_at, site_ref, dataset_version_id)``.
    """
    dataset = await _dataset_by_code(services, parameters.dataset)
    page = await services.ingestion_queries.query_observations(
        QueryObservations(
            dataset_id=dataset.id,
            variable=parameters.variable,
            observed_from=parameters.observed_from,
            observed_to=parameters.observed_to,
            site_ref=parameters.site_ref,
            dataset_version_id=parameters.version,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    _link_next(response, OBSERVATIONS_PATH, parameters, page.next_cursor)
    return ObservationPage(items=page.items, next_cursor=page.next_cursor)


@router.get(
    "/raster-assets",
    response_model=RasterAssetPage,
    responses={status.HTTP_200_OK: _STAC_ALTERNATIVE, **_NOT_FOUND},
)
async def list_raster_assets(
    parameters: Annotated[ListRasterAssetsParameters, Query()],
    response: Response,
    services: Services,
    accept: AcceptHeader = None,
) -> RasterAssetPage | Response:
    """List catalogued rasters, as JSON or as a STAC ``FeatureCollection``.

    Args:
        parameters: Validated box, window, dataset code, format, cursor and
            limit.
        response: Used to set ``Link`` and ``Vary`` on the JSON response.
        services: Use cases bound by the composition root.
        accept: ``application/geo+json`` selects GeoJSON.

    Returns:
        One page of rasters, newest acquisition first, in the negotiated
        representation.
    """
    dataset_id = None
    if parameters.dataset is not None:
        dataset_id = (await _dataset_by_code(services, parameters.dataset)).id
    page = await services.ingestion_queries.list_raster_assets(
        ListRasterAssets(
            bbox=parameters.bounding_box(),
            acquired_from=parameters.acquired_from,
            acquired_to=parameters.acquired_to,
            dataset_id=dataset_id,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    headers = {VARY_HEADER: "Accept"}
    link = _next_link(RASTER_ASSETS_PATH, parameters, page.next_cursor)
    if link is not None:
        headers[LINK_HEADER] = link
    if wants_geojson(parameters.output_format, accept):
        collection = StacItemCollection(
            features=tuple(asset.to_stac_item() for asset in page.items)
        )
        return Response(
            content=collection.model_dump_json(),
            media_type=GEOJSON_MEDIA_TYPE,
            headers=headers,
        )
    response.headers.update(headers)
    return RasterAssetPage(items=page.items, next_cursor=page.next_cursor)


# --------------------------------------------------------------------------- #
# Administration                                                              #
# --------------------------------------------------------------------------- #


@admin_router.post("/datasets", status_code=status.HTTP_201_CREATED)
async def register_dataset(
    body: RegisterDatasetRequest,
    actor: AdminActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> DatasetSummary:
    """Register a dataset in the catalog; a licence is required.

    Args:
        body: Code, title, publisher, licence and descriptions.
        actor: The administrator.
        response: Used to set ``Location`` and ``ETag``.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The registered dataset.
    """
    del idempotency_key
    summary = await services.register_dataset_handler(
        RegisterDataset(actor=actor, details=body.details)
    )
    response.headers[LOCATION_HEADER] = f"{DATASETS_PATH}/{summary.code}"
    set_etag(response, make_etag(summary.version, summary.id))
    return summary


@admin_router.post("/datasets/{code}/versions", status_code=status.HTTP_201_CREATED)
async def record_dataset_version(
    code: DatasetCodePath,
    body: RecordDatasetVersionRequest,
    actor: AdminActor,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> DatasetVersionSummary:
    """Record one release of an active dataset, pinned by its input checksum.

    Args:
        code: The dataset's catalog code.
        body: Label, retrieval time, checksum and notes.
        actor: The administrator.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The recorded version; it also appears in the dataset's
        ``recent_versions``.
    """
    del idempotency_key
    dataset = await _dataset_by_code(services, code)
    return await services.record_dataset_version_handler(
        RecordDatasetVersion(actor=actor, dataset_id=dataset.id, details=body.details)
    )


@admin_router.post(
    "/datasets/{code}/status",
    responses={
        status.HTTP_412_PRECONDITION_FAILED: _PROBLEM,
        status.HTTP_428_PRECONDITION_REQUIRED: _PROBLEM,
    },
)
async def change_dataset_status(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    code: DatasetCodePath,
    body: ChangeDatasetStatusRequest,
    actor: AdminActor,
    response: Response,
    services: Services,
    if_match: IfMatchHeader = None,
) -> DatasetSummary:
    """Deprecate or retire a dataset; its history stays.

    Args:
        code: The dataset's catalog code.
        body: The target status.
        actor: The administrator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: The dataset's ``ETag``; checked when sent.

    Returns:
        The dataset after the change.
    """
    dataset = await _dataset_by_code(services, code)
    expected_version = (
        None
        if if_match is None
        else expected_version_from_if_match(if_match, dataset.id)
    )
    if body.status is DatasetStatusChange.DEPRECATED:
        summary = await services.deprecate_dataset_handler(
            DeprecateDataset(
                actor=actor, dataset_id=dataset.id, expected_version=expected_version
            )
        )
    else:
        summary = await services.retire_dataset_handler(
            RetireDataset(
                actor=actor, dataset_id=dataset.id, expected_version=expected_version
            )
        )
    set_etag(response, make_etag(summary.version, summary.id))
    return summary


@admin_router.post("/datasets/{code}/runs", status_code=status.HTTP_202_ACCEPTED)
async def request_run(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    code: DatasetCodePath,
    body: RunIngestionRequest,
    actor: AdminActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> RunDetail:
    """Request an ingestion run of one version through one source adapter.

    Args:
        code: The dataset's catalog code.
        body: The version and the adapter.
        actor: The administrator.
        response: Used to set ``Location``.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The pending run; a worker executes it.
    """
    del idempotency_key
    dataset = await _dataset_by_code(services, code)
    run_id = await services.run_ingestion_handler(
        RunIngestion(
            actor=actor,
            dataset_id=dataset.id,
            version_id=body.version_id,
            adapter_name=body.adapter_name,
        )
    )
    detail = await services.ingestion_queries.get_run(GetRun(run_id=run_id))
    if detail is None:
        # The handler committed the run just now; missing means the read side
        # lags or broke, which the client should see as a missing resource.
        raise NotFoundError(RUN_NOT_FOUND_MESSAGE)
    response.headers[LOCATION_HEADER] = f"{RUNS_PATH}/{run_id}"
    return detail


@admin_router.post("/raster-assets", status_code=status.HTTP_201_CREATED)
async def catalogue_raster_asset(
    body: CatalogueRasterAssetRequest,
    actor: AdminActor,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> RasterAssetSummary:
    """Add one STAC-aligned raster of a dataset version to the catalog.

    Args:
        body: Dataset code, version and the raster's description.
        actor: The administrator.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The catalogued raster.
    """
    del idempotency_key
    dataset = await _dataset_by_code(services, body.dataset)
    return await services.catalogue_raster_asset_handler(
        CatalogueRasterAsset(
            actor=actor,
            dataset_id=dataset.id,
            version_id=body.version_id,
            description=body.description,
        )
    )
