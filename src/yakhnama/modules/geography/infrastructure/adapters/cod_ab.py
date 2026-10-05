"""Download, verify and read an OCHA COD-AB administrative boundary archive.

The archive is downloaded once from the URL a ``DistrictBoundarySource`` pins into a
local cache directory (git-ignored; ``Settings.boundary_cache_dir``), under a name
derived from the pinned SHA-256, so a new pin never reuses an old file. Its digest is
checked after every download and before every read: a file that does not match the
pin is never parsed, and a download that does not match is deleted. The download
time recorded in the attribution is the cached file's modification time.

The GeoJSON member named by the source is read with the standard library and only
the features of the source's region are kept. Each district gets a representative
point from Shapely (``representative_point``, GEOS ``PointOnSurface``), which always
lies inside the polygon, unlike a plain centroid of a concave district; it becomes the
linked place's centroid (Q239). Every COD-AB property this module
relies on (``adm1_pcode``, ``adm2_pcode``, ``adm2_name``) is read here and nowhere
else, so a change in the dataset's schema surfaces as one ``BoundarySourceError``.
Errors carry codes, digests and counts only, never file content.

The download is the only network call in the module. It runs when an operator loads
boundaries (``poetry run poe load-boundaries``), never in the API process and never
in tests, which pass an ``httpx.MockTransport`` or a pre-filled cache.

Patterns: Adapter, Anti-Corruption Layer.
"""

import asyncio
import hashlib
import json
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx
import pydantic
from shapely.geometry import shape

from yakhnama.modules.geography.domain.boundaries import (
    DistrictBoundarySet,
    DistrictBoundarySource,
    SourceDistrict,
)
from yakhnama.modules.geography.domain.errors import BoundarySourceError
from yakhnama.modules.geography.domain.value_objects import PlaceGeometry
from yakhnama.shared_kernel.value_objects import Coordinates

MAX_ARCHIVE_BYTES: Final = 256 * 1024 * 1024
"""Largest archive downloaded (**proposed**; the Pakistan archive is about 29 MB)."""

MAX_MEMBER_BYTES: Final = 128 * 1024 * 1024
"""Largest uncompressed GeoJSON member read, so a malformed archive cannot exhaust
memory (**proposed**; the Pakistan ADM2 member is about 10 MB)."""

DOWNLOAD_TIMEOUT_SECONDS: Final = 120.0
"""Time allowed per network operation of the download."""

_CHUNK_BYTES: Final = 1024 * 1024
_ACCEPTED_CRS: Final = frozenset(
    {"urn:ogc:def:crs:OGC:1.3:CRS84", "urn:ogc:def:crs:EPSG::4326", "EPSG:4326"}
)
REGION_PROPERTY: Final = "adm1_pcode"
DISTRICT_CODE_PROPERTY: Final = "adm2_pcode"
DISTRICT_NAME_PROPERTY: Final = "adm2_name"


def sha256_of(path: Path) -> str:
    """Return the SHA-256 of a file.

    Args:
        path: The file.

    Returns:
        The lower-case hexadecimal digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def cached_archive_path(cache_dir: Path, source: DistrictBoundarySource) -> Path:
    """Return where the archive of ``source`` is cached.

    Args:
        cache_dir: The cache directory.
        source: The boundary source.

    Returns:
        ``<cache_dir>/<dataset>-<sha256>.zip``: a new pin never reuses a file.
    """
    return cache_dir / f"{source.dataset}-{source.sha256}.zip"


def _error(message: str, reason: str, **details: object) -> BoundarySourceError:
    return BoundarySourceError(message, details={"reason": reason, **details})


class CodAbBoundaryLoader:
    """``BoundaryLoader`` for OCHA COD-AB GeoJSON archives.

    Implements: Adapter, Anti-Corruption Layer.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS,
    ) -> None:
        """Create the loader.

        Args:
            cache_dir: Where archives are kept; created on the first download.
            transport: The HTTP transport; ``None`` for the network. Tests pass an
                ``httpx.MockTransport``.
            timeout_seconds: Time allowed per network operation.
        """
        self._cache_dir = cache_dir
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def load(self, source: DistrictBoundarySource) -> DistrictBoundarySet:
        """Return the region's districts from the verified archive.

        Args:
            source: The committed description of the archive.

        Returns:
            Every district of ``source.region_code``, in file order.

        Raises:
            BoundarySourceError: If the download fails, the digest does not match,
                or the archive's content is not the expected GeoJSON.
        """
        path = cached_archive_path(self._cache_dir, source)
        if not path.is_file():
            await self._download(source, path)
        actual = await asyncio.to_thread(sha256_of, path)
        if actual != source.sha256:
            message = "the cached boundary archive does not have the pinned SHA-256"
            raise _error(
                message, "checksum_mismatch", expected=source.sha256, actual=actual
            )
        retrieved_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        document = await asyncio.to_thread(_read_member, path, source.archive_member)
        districts = _region_districts(document, source.region_code)
        try:
            return DistrictBoundarySet(
                attribution=source.attribution(retrieved_at),
                sha256=actual,
                region_code=source.region_code,
                districts=districts,
            )
        except pydantic.ValidationError as error:
            message = "the region's districts do not form a valid boundary set"
            raise _error(
                message, "invalid_districts", error_count=error.error_count()
            ) from None

    async def _download(self, source: DistrictBoundarySource, path: Path) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        digest = hashlib.sha256()
        size = 0
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self._timeout_seconds,
                    follow_redirects=True,
                ) as client,
                client.stream("GET", source.download_url) as response,
            ):
                if response.url.scheme != "https":
                    message = "the boundary download was redirected away from https"
                    raise _error(message, "insecure_redirect")
                if response.status_code != httpx.codes.OK:
                    message = "the boundary download did not succeed"
                    raise _error(
                        message, "download_failed", status=response.status_code
                    )
                with partial.open("wb") as stream:
                    async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                        size += len(chunk)
                        if size > MAX_ARCHIVE_BYTES:
                            message = "the boundary archive is larger than allowed"
                            raise _error(
                                message, "too_large", max_bytes=MAX_ARCHIVE_BYTES
                            )
                        digest.update(chunk)
                        stream.write(chunk)
        except httpx.HTTPError as error:
            partial.unlink(missing_ok=True)
            message = "the boundary download failed"
            raise _error(
                message, "download_failed", error_type=type(error).__name__
            ) from None
        except BoundarySourceError:
            partial.unlink(missing_ok=True)
            raise
        if digest.hexdigest() != source.sha256:
            partial.unlink(missing_ok=True)
            message = "the downloaded boundary archive does not have the pinned SHA-256"
            raise _error(
                message,
                "checksum_mismatch",
                expected=source.sha256,
                actual=digest.hexdigest(),
            )
        partial.replace(path)


def _read_member(path: Path, member: str) -> Any:  # noqa: ANN401  # reason: parsed JSON of an external file, narrowed by _region_districts
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(member)
            if info.file_size > MAX_MEMBER_BYTES:
                message = "the boundary archive member is larger than allowed"
                raise _error(message, "too_large", max_bytes=MAX_MEMBER_BYTES)
            content = archive.read(info)
    except KeyError:
        message = "the boundary archive lacks the named member"
        raise _error(message, "member_missing", member=member) from None
    except zipfile.BadZipFile:
        message = "the boundary archive is not a zip file"
        raise _error(message, "not_zip") from None
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        message = "the boundary archive member is not JSON"
        raise _error(message, "not_json", member=member) from None


def _region_districts(
    document: Any,  # noqa: ANN401  # reason: parsed JSON of an external file, narrowed here
    region_code: str,
) -> tuple[SourceDistrict, ...]:
    if not isinstance(document, Mapping) or document.get("type") != (
        "FeatureCollection"
    ):
        message = "the boundary member is not a GeoJSON FeatureCollection"
        raise _error(message, "not_feature_collection")
    _check_crs(document.get("crs"))
    features = document.get("features")
    if not isinstance(features, Sequence):
        message = "the boundary member has no list of features"
        raise _error(message, "not_feature_collection")
    districts: list[SourceDistrict] = []
    invalid = 0
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, Mapping) else None
        if not isinstance(properties, Mapping):
            invalid += 1
            continue
        if properties.get(REGION_PROPERTY) != region_code:
            continue
        try:
            geometry = PlaceGeometry.model_validate(
                {"geojson": feature.get("geometry")}
            )
            point = shape(
                geometry.geojson.model_dump(mode="json")
            ).representative_point()
            districts.append(
                SourceDistrict.model_validate(
                    {
                        "code": properties.get(DISTRICT_CODE_PROPERTY),
                        "name": properties.get(DISTRICT_NAME_PROPERTY),
                        "geometry": geometry,
                        "representative_point": Coordinates(
                            longitude=point.x, latitude=point.y
                        ),
                    }
                )
            )
        except pydantic.ValidationError:
            invalid += 1
    if invalid:
        message = "some boundary features are malformed"
        raise _error(message, "invalid_features", invalid_count=invalid)
    if not districts:
        message = "the boundary member has no district in the region"
        raise _error(message, "region_missing", region_code=region_code)
    return tuple(districts)


def _check_crs(crs: object) -> None:
    # RFC 7946 removed "crs" and fixes WGS84 longitude/latitude; COD-AB still writes
    # the CRS84 name. Anything else would put the coordinates in another system.
    if crs is None:
        return
    name = None
    if isinstance(crs, Mapping):
        properties = crs.get("properties")
        if isinstance(properties, Mapping):
            name = properties.get("name")
    if name not in _ACCEPTED_CRS:
        message = "the boundary member is not in WGS84 longitude/latitude"
        raise _error(message, "unsupported_crs")
