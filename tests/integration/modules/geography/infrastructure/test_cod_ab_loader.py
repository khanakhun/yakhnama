"""``CodAbBoundaryLoader`` against real archives on disk and a mocked transport.

The archive is built from the committed synthetic fixture
(``data/fixtures/boundaries/synthetic_admin2.geojson``): six synthetic districts in
region ``XX1`` and one in ``XX2``. No test touches the network: downloads go through
``httpx.MockTransport``.
"""

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from shapely.geometry import Point, shape

from tests.factories.boundaries import boundary_source
from yakhnama.modules.geography.domain.boundaries import DistrictBoundarySource
from yakhnama.modules.geography.domain.errors import BoundarySourceError
from yakhnama.modules.geography.infrastructure.adapters import cod_ab
from yakhnama.modules.geography.infrastructure.adapters.cod_ab import (
    CodAbBoundaryLoader,
    cached_archive_path,
    sha256_of,
)

pytestmark = pytest.mark.integration

FIXTURE: Final = (
    Path(__file__).resolve().parents[5]
    / "data"
    / "fixtures"
    / "boundaries"
    / "synthetic_admin2.geojson"
)
MEMBER: Final = "synthetic_admin2.geojson"
LINKS: Final = [(f"XX10{index}", None) for index in range(1, 7)]


def _archive(path: Path, content: bytes, member: str = MEMBER) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, content)
    return sha256_of(path)


def _entries(directory: Path) -> list[Path]:
    return list(directory.iterdir())


def _fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def _source(sha256: str) -> DistrictBoundarySource:
    return boundary_source(LINKS, sha256=sha256)


def _cached(tmp_path: Path, content: bytes) -> tuple[Path, DistrictBoundarySource]:
    staging = tmp_path / "staging.zip"
    source = _source(_archive(staging, content))
    cache = tmp_path / "cache"
    cache.mkdir()
    staging.rename(cached_archive_path(cache, source))
    return cache, source


def _document(**changes: Any) -> bytes:  # noqa: ANN401  # reason: raw GeoJSON values
    document = json.loads(_fixture_bytes())
    document.update(changes)
    return json.dumps(document).encode()


async def test_load_from_cache_returns_the_region_districts_with_attribution(
    tmp_path: Path,
) -> None:
    cache, source = _cached(tmp_path, _fixture_bytes())
    path = cached_archive_path(cache, source)

    result = await CodAbBoundaryLoader(cache).load(source)

    assert [district.code for district in result.districts] == [
        f"XX10{index}" for index in range(1, 7)
    ]
    assert result.districts[0].name == "Synthetic A"
    assert result.sha256 == source.sha256
    assert result.region_code == "XX1"
    assert result.attribution.retrieved_at == datetime.fromtimestamp(
        path.stat().st_mtime, UTC
    )
    assert result.attribution.licence == source.licence
    for district in result.districts:
        footprint = shape(district.geometry.geojson.model_dump(mode="json"))
        point = district.representative_point
        assert footprint.contains(Point(point.longitude, point.latitude))


async def test_load_downloads_once_then_reads_the_cache(tmp_path: Path) -> None:
    staging = tmp_path / "staging.zip"
    source = _source(_archive(staging, _fixture_bytes()))
    payload = staging.read_bytes()
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=payload)

    loader = CodAbBoundaryLoader(
        tmp_path / "cache", transport=httpx.MockTransport(respond)
    )

    first = await loader.load(source)
    second = await loader.load(source)

    assert len(requests) == 1
    assert str(requests[0].url) == source.download_url
    assert first.districts == second.districts
    assert cached_archive_path(tmp_path / "cache", source).is_file()


async def test_load_follows_an_https_redirect(tmp_path: Path) -> None:
    staging = tmp_path / "staging.zip"
    source = _source(_archive(staging, _fixture_bytes()))
    payload = staging.read_bytes()

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.org":
            return httpx.Response(302, headers={"Location": "https://files.test/a.zip"})
        return httpx.Response(200, content=payload)

    loader = CodAbBoundaryLoader(tmp_path, transport=httpx.MockTransport(respond))

    result = await loader.load(source)

    assert len(result.districts) == len(LINKS)


async def test_load_with_download_of_other_content_deletes_it_and_raises(
    tmp_path: Path,
) -> None:
    source = _source("b" * 64)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"other"))
    loader = CodAbBoundaryLoader(tmp_path, transport=transport)

    with pytest.raises(BoundarySourceError) as raised:
        await loader.load(source)

    assert raised.value.details["reason"] == "checksum_mismatch"
    assert raised.value.details["actual"] == hashlib.sha256(b"other").hexdigest()
    assert _entries(tmp_path) == []


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(404), "download_failed"),
        (
            httpx.Response(302, headers={"Location": "http://insecure.test/a.zip"}),
            "insecure_redirect",
        ),
    ],
)
async def test_load_with_failed_download_raises_and_keeps_no_file(
    tmp_path: Path, response: httpx.Response, reason: str
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "http":
            return httpx.Response(200, content=b"never read")
        return response

    loader = CodAbBoundaryLoader(tmp_path, transport=httpx.MockTransport(respond))

    with pytest.raises(BoundarySourceError) as raised:
        await loader.load(_source("b" * 64))

    assert raised.value.details["reason"] == reason
    assert _entries(tmp_path) == []


async def test_load_with_transport_error_raises_download_failed(
    tmp_path: Path,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        message = "no route"
        raise httpx.ConnectError(message, request=request)

    loader = CodAbBoundaryLoader(tmp_path, transport=httpx.MockTransport(respond))

    with pytest.raises(BoundarySourceError) as raised:
        await loader.load(_source("b" * 64))

    assert raised.value.details == {
        "reason": "download_failed",
        "error_type": "ConnectError",
    }


async def test_load_with_oversized_download_raises_too_large(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cod_ab, "MAX_ARCHIVE_BYTES", 4)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"12345"))
    loader = CodAbBoundaryLoader(tmp_path, transport=transport)

    with pytest.raises(BoundarySourceError) as raised:
        await loader.load(_source("b" * 64))

    assert raised.value.details["reason"] == "too_large"
    assert _entries(tmp_path) == []


async def test_load_with_tampered_cache_raises_checksum_mismatch(
    tmp_path: Path,
) -> None:
    cache, source = _cached(tmp_path, _fixture_bytes())
    cached_archive_path(cache, source).write_bytes(b"tampered")

    with pytest.raises(BoundarySourceError) as raised:
        await CodAbBoundaryLoader(cache).load(source)

    assert raised.value.details["reason"] == "checksum_mismatch"


async def test_load_with_missing_member_raises_member_missing(tmp_path: Path) -> None:
    staging = tmp_path / "staging.zip"
    with zipfile.ZipFile(staging, "w") as archive:
        archive.writestr("other.geojson", _fixture_bytes())
    source = _source(sha256_of(staging))
    staging.rename(cached_archive_path(tmp_path, source))

    with pytest.raises(BoundarySourceError) as raised:
        await CodAbBoundaryLoader(tmp_path).load(source)

    assert raised.value.details["reason"] == "member_missing"


async def test_load_with_oversized_member_raises_too_large(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, source = _cached(tmp_path, _fixture_bytes())
    monkeypatch.setattr(cod_ab, "MAX_MEMBER_BYTES", 10)

    with pytest.raises(BoundarySourceError) as raised:
        await CodAbBoundaryLoader(cache).load(source)

    assert raised.value.details["reason"] == "too_large"


async def test_load_with_non_zip_cache_raises_not_zip(tmp_path: Path) -> None:
    staging = tmp_path / "staging.zip"
    staging.write_bytes(b"not a zip archive")
    source = _source(sha256_of(staging))
    staging.rename(cached_archive_path(tmp_path, source))

    with pytest.raises(BoundarySourceError) as raised:
        await CodAbBoundaryLoader(tmp_path).load(source)

    assert raised.value.details["reason"] == "not_zip"


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"\xff\xfe not json", "not_json"),
        (b'{"type": "Feature"}', "not_feature_collection"),
        (b'{"type": "FeatureCollection", "features": 3}', "not_feature_collection"),
        (b"[]", "not_feature_collection"),
        (
            _document(crs={"type": "name", "properties": {"name": "EPSG:32643"}}),
            "unsupported_crs",
        ),
        (_document(crs="EPSG:4326"), "unsupported_crs"),
        (_document(crs={"type": "name"}), "unsupported_crs"),
        (_document(features=[]), "region_missing"),
        (_document(features=["not a feature"]), "invalid_features"),
    ],
)
async def test_load_with_unexpected_content_raises_with_reason(
    tmp_path: Path, content: bytes, reason: str
) -> None:
    cache, source = _cached(tmp_path, content)

    with pytest.raises(BoundarySourceError) as raised:
        await CodAbBoundaryLoader(cache).load(source)

    assert raised.value.details["reason"] == reason


async def test_load_without_crs_member_accepts_rfc7946_coordinates(
    tmp_path: Path,
) -> None:
    document = json.loads(_fixture_bytes())
    del document["crs"]
    cache, source = _cached(tmp_path, json.dumps(document).encode())

    result = await CodAbBoundaryLoader(cache).load(source)

    assert len(result.districts) == len(LINKS)


async def test_load_with_malformed_region_feature_raises_invalid_features(
    tmp_path: Path,
) -> None:
    document = json.loads(_fixture_bytes())
    document["features"][0]["geometry"] = {"type": "Point", "coordinates": [1, 2]}
    cache, source = _cached(tmp_path, json.dumps(document).encode())

    with pytest.raises(BoundarySourceError) as raised:
        await CodAbBoundaryLoader(cache).load(source)

    assert raised.value.details == {"reason": "invalid_features", "invalid_count": 1}


async def test_load_with_duplicated_district_raises_invalid_districts(
    tmp_path: Path,
) -> None:
    document = json.loads(_fixture_bytes())
    document["features"].append(document["features"][0])
    cache, source = _cached(tmp_path, json.dumps(document).encode())

    with pytest.raises(BoundarySourceError) as raised:
        await CodAbBoundaryLoader(cache).load(source)

    assert raised.value.details["reason"] == "invalid_districts"
