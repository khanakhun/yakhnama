"""District boundaries from an external dataset, and the edges districts share.

A boundary dataset (OCHA COD-AB for Pakistan, ADR 0021) is described by a committed
``DistrictBoundarySource``: where to download it, the SHA-256 it must have, how to
attribute it, and an explicit table (``DistrictLink``) from the dataset's district
codes (``PK301``) to gazetteer place codes (``pk.gb.astore``). Links are written by
hand and reviewed; nothing here matches names automatically, so a district the table
does not link stays unlinked and is reported (``match_district_boundaries``).

The public map shows only ``DistrictEdge`` lines: the boundary two districts of the
region share. The outer edge of the region is never one of them, because it traces
international borders and the Line of Control (maintainer decision, 2026-10-05). A
``DistrictEdgeSet`` is one computed, immutable snapshot of those lines with the
attribution of the data they come from.

Several rules here are **proposed defaults, not domain facts** (length bounds, the
code pattern of a source district, what counts as a mismatch); each is listed in
``docs/data-dictionary/geography.md``.

Patterns: Value Object, Entity.
"""

import hashlib
import math
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Annotated, Final, Literal, Self
from urllib.parse import urlsplit

from geojson_pydantic import LineString, MultiLineString
from geojson_pydantic.types import Position
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from yakhnama.modules.geography.domain.reference import DataVersion
from yakhnama.modules.geography.domain.value_objects import PlaceCode, PlaceGeometry
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.text import safe_text
from yakhnama.shared_kernel.value_objects import Coordinates

# --------------------------------------------------------------------------- #
# Scalars                                                                     #
# --------------------------------------------------------------------------- #

SOURCE_DISTRICT_CODE_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$"

SourceDistrictCode = Annotated[
    str, StringConstraints(pattern=SOURCE_DISTRICT_CODE_PATTERN)
]
"""A district's code in the boundary dataset (a COD-AB P-code such as ``PK301``).

1 to 32 ASCII letters, digits, ``_``, ``.`` and ``-``, starting with a letter or
digit (**proposed** bound).
"""

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""A SHA-256 digest as 64 lower-case hexadecimal characters."""

SourceName = Annotated[str, *safe_text(200)]
"""A district name as the boundary dataset writes it, 1 to 200 characters."""

AttributionText = Annotated[str, *safe_text(500)]
"""A citation or note, 1 to 500 characters."""

ShortText = Annotated[str, *safe_text(100)]
"""A licence name or dataset version, 1 to 100 characters."""

ArchiveMember = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]
"""The name of one file inside a downloaded archive, without any directory."""

HTTPS_URL_MAX_LENGTH: Final = 2048


def _check_https_url(value: str) -> str:
    # The same shape as provenance's and ingestion's URL rules, restated because a
    # domain layer imports no other module (AGENTS.md §2.1), but https only: these
    # URLs are downloaded from or shown as the licence of published data.
    if any(not "\x21" <= character <= "\x7e" for character in value):
        message = "url must be printable ASCII without spaces"
        raise ValueError(message)
    parts = urlsplit(value)
    if parts.scheme != "https":
        message = "url must use https"
        raise ValueError(message)
    if "@" in parts.netloc or not parts.hostname:
        message = "url must have a host and no user information"
        raise ValueError(message)
    return value


HttpsUrl = Annotated[
    str,
    StringConstraints(min_length=9, max_length=HTTPS_URL_MAX_LENGTH),
    AfterValidator(_check_https_url),
]
"""An ``https`` URL with a host and no credentials, printable ASCII, stored verbatim."""

DISTRICT_LINKS_MAX: Final = 200
"""Most links one boundary source may list (**proposed**; Pakistan has 160 ADM2)."""

DISTRICT_EDGES_MAX: Final = 10_000
"""Most edges one edge set may hold (**proposed**; far above any region's count)."""

# --------------------------------------------------------------------------- #
# Attribution and the committed source description                            #
# --------------------------------------------------------------------------- #


class BoundaryAttribution(BaseModel):
    """How boundary-derived data must be credited wherever it is shown.

    Implements: Value Object.

    Attributes:
        source: Who made the data and where it was obtained.
        source_url: The dataset's page.
        licence: The licence's name, for example ``CC BY-IGO 3.0``.
        licence_url: The licence's legal text.
        dataset_version: The dataset's own version and validity date.
        retrieved_at: When the file was downloaded, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: AttributionText
    source_url: HttpsUrl
    licence: ShortText
    licence_url: HttpsUrl
    dataset_version: ShortText
    retrieved_at: AwareDatetime

    @field_validator("retrieved_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class DistrictLink(BaseModel):
    """One row of the committed table from dataset districts to gazetteer places.

    Implements: Value Object.

    Attributes:
        source_code: The district's code in the dataset.
        source_name: The district's name in the dataset, recorded so a reused or
            renamed code is noticed.
        place_code: The gazetteer place it is, or ``None`` while the gazetteer has
            no such place; never filled in by guessing.
        note: Why the row is as it is, for reviewers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_code: SourceDistrictCode
    source_name: SourceName
    place_code: PlaceCode | None
    note: AttributionText | None = None


class DistrictBoundarySource(BaseModel):
    """The committed description of one boundary dataset and its district links.

    It pins the download by URL and SHA-256, names the region whose districts are
    read, says how to attribute the data, and carries the link table.

    Implements: Value Object.

    Attributes:
        schema_version: Version of this file's structure; only ``1`` exists.
        data_version: Version of this file's content.
        dataset: The dataset's identifier where it is published (``cod-ab-pak``).
        download_url: Where the archive is downloaded from.
        sha256: The digest the downloaded archive must have.
        archive_member: The GeoJSON file inside the archive holding the districts.
        region_code: The region's code in the dataset (``PK3``).
        region_place_code: The same region in the gazetteer (``pk.gb``).
        source: Attribution: who made the data and where it was obtained.
        source_url: The dataset's page.
        licence: The licence's name.
        licence_url: The licence's legal text.
        dataset_version: The dataset's own version.
        links: One row per district of the region, unique by source code and by
            linked place code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    data_version: DataVersion
    dataset: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")]
    download_url: HttpsUrl
    sha256: Sha256Hex
    archive_member: ArchiveMember
    region_code: SourceDistrictCode
    region_place_code: PlaceCode
    source: AttributionText
    source_url: HttpsUrl
    licence: ShortText
    licence_url: HttpsUrl
    dataset_version: ShortText
    links: tuple[DistrictLink, ...] = Field(min_length=1, max_length=DISTRICT_LINKS_MAX)

    @field_validator("links", mode="after")
    @classmethod
    def _check_links(cls, links: tuple[DistrictLink, ...]) -> tuple[DistrictLink, ...]:
        codes = _duplicates(link.source_code for link in links)
        places = _duplicates(
            link.place_code for link in links if link.place_code is not None
        )
        if codes or places:
            message = (
                f"each source code and place code may appear once; duplicated "
                f"source codes {codes}, place codes {places}"
            )
            raise ValueError(message)
        return links

    def attribution(self, retrieved_at: datetime) -> BoundaryAttribution:
        """Return the attribution of a file of this source.

        Args:
            retrieved_at: When the file was downloaded.

        Returns:
            The attribution to show with anything derived from the file.
        """
        return BoundaryAttribution(
            source=self.source,
            source_url=self.source_url,
            licence=self.licence,
            licence_url=self.licence_url,
            dataset_version=self.dataset_version,
            retrieved_at=retrieved_at,
        )

    def link_for(self, source_code: str) -> DistrictLink | None:
        """Return the link row of ``source_code``.

        Args:
            source_code: A district's code in the dataset.

        Returns:
            The row, or ``None`` if the table has none.
        """
        return next(
            (link for link in self.links if link.source_code == source_code), None
        )


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


# --------------------------------------------------------------------------- #
# The districts read from a file                                              #
# --------------------------------------------------------------------------- #


class SourceDistrict(BaseModel):
    """One district polygon read from the boundary dataset.

    Implements: Value Object.

    Attributes:
        code: The district's code in the dataset.
        name: The district's name in the dataset.
        geometry: Its footprint, a Polygon or MultiPolygon in WGS84.
        representative_point: A point guaranteed to lie inside ``geometry``
            (computed by the loader, which can use a geometry library; a plain
            centroid can fall outside a concave district). It becomes the place's
            centroid (Q239).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: SourceDistrictCode
    name: SourceName
    geometry: PlaceGeometry
    representative_point: Coordinates

    @field_validator("geometry", mode="after")
    @classmethod
    def _require_area(cls, geometry: PlaceGeometry) -> PlaceGeometry:
        if geometry.geometry_type == "Point":
            message = "a district boundary must be a Polygon or MultiPolygon"
            raise ValueError(message)
        return geometry


class DistrictBoundarySet(BaseModel):
    """Every district of one region, read from one verified file.

    Implements: Value Object.

    Attributes:
        attribution: How the data must be credited.
        sha256: The digest of the file the districts were read from.
        region_code: The region's code in the dataset.
        districts: The region's districts, unique by code, in file order.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    attribution: BoundaryAttribution
    sha256: Sha256Hex
    region_code: SourceDistrictCode
    districts: tuple[SourceDistrict, ...] = Field(
        min_length=1, max_length=DISTRICT_LINKS_MAX
    )

    @field_validator("districts", mode="after")
    @classmethod
    def _check_unique(
        cls, districts: tuple[SourceDistrict, ...]
    ) -> tuple[SourceDistrict, ...]:
        duplicated = _duplicates(district.code for district in districts)
        if duplicated:
            message = f"duplicated district codes {duplicated}"
            raise ValueError(message)
        return districts


# --------------------------------------------------------------------------- #
# Matching the file against the link table and the gazetteer                  #
# --------------------------------------------------------------------------- #


class GazetteerDistrict(BaseModel):
    """What matching needs to know about one gazetteer place.

    Implements: Value Object.

    Attributes:
        code: The place code.
        name: Its English display name.
        is_active: Whether it can still take a geometry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: PlaceCode
    name: str
    is_active: bool


class NameDifference(BaseModel):
    """A district whose name in the file differs from another record of it.

    Differences are reported, never acted on: a spelling variant (``Diamir`` and
    ``Diamer``) and a reused code look alike to a program.

    Implements: Value Object.

    Attributes:
        source_code: The district's code in the dataset.
        source_name: Its name in the file.
        other_name: The name it was compared with.
        compared_with: ``link_table`` (the name recorded in the link row) or
            ``gazetteer`` (the linked place's English name).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_code: SourceDistrictCode
    source_name: str
    other_name: str
    compared_with: Literal["link_table", "gazetteer"]


class DistrictMatch(BaseModel):
    """How the districts of a file line up with the link table and the gazetteer.

    Implements: Value Object.

    Attributes:
        linked: Districts in the file linked to an active gazetteer place.
        unlinked: Districts in the file whose link row has no place code.
        not_in_link_table: Districts in the file with no link row at all.
        not_in_source: Link rows whose district the file does not have.
        missing_places: Linked place codes the gazetteer lacks or has retired.
        places_without_boundary: Gazetteer districts of the region no district
            of the file is linked to.
        name_differences: Names that differ between the file and another record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    linked: tuple[DistrictLink, ...]
    unlinked: tuple[DistrictLink, ...]
    not_in_link_table: tuple[SourceDistrictCode, ...]
    not_in_source: tuple[SourceDistrictCode, ...]
    missing_places: tuple[PlaceCode, ...]
    places_without_boundary: tuple[PlaceCode, ...]
    name_differences: tuple[NameDifference, ...]

    @property
    def has_mismatches(self) -> bool:
        """Tell whether anything needs a human's attention.

        Returns:
            ``True`` unless every district of the file and every gazetteer district
            of the region is linked one to one under the same names.
        """
        return bool(
            self.unlinked
            or self.not_in_link_table
            or self.not_in_source
            or self.missing_places
            or self.places_without_boundary
            or self.name_differences
        )

    def place_code_of(self, source_code: str) -> str | None:
        """Return the gazetteer place a district is linked to, if any.

        Args:
            source_code: A district's code in the dataset.

        Returns:
            The place code of a ``linked`` district, else ``None``.
        """
        return next(
            (
                link.place_code
                for link in self.linked
                if link.source_code == source_code
            ),
            None,
        )


def match_district_boundaries(
    source: DistrictBoundarySource,
    boundary_set: DistrictBoundarySet,
    *,
    places: Mapping[str, GazetteerDistrict],
    region_districts: Iterable[str],
) -> DistrictMatch:
    """Line up a file's districts with the link table and the gazetteer.

    Args:
        source: The committed source description with its link table.
        boundary_set: The districts read from the file.
        places: The gazetteer places the link table names, by code; a code that is
            missing here is reported as a missing place.
        region_districts: Codes of the gazetteer's active districts in the region.

    Returns:
        The match; nothing in it is guessed from names.
    """
    in_file = {district.code: district for district in boundary_set.districts}
    linked: list[DistrictLink] = []
    unlinked: list[DistrictLink] = []
    missing: list[str] = []
    differences: list[NameDifference] = []
    for district in boundary_set.districts:
        link = source.link_for(district.code)
        if link is None:
            continue
        if link.source_name != district.name:
            differences.append(
                NameDifference(
                    source_code=district.code,
                    source_name=district.name,
                    other_name=link.source_name,
                    compared_with="link_table",
                )
            )
        if link.place_code is None:
            unlinked.append(link)
            continue
        place = places.get(link.place_code)
        if place is None or not place.is_active:
            missing.append(link.place_code)
            continue
        linked.append(link)
        if place.name != district.name:
            differences.append(
                NameDifference(
                    source_code=district.code,
                    source_name=district.name,
                    other_name=place.name,
                    compared_with="gazetteer",
                )
            )
    linked_codes = {link.place_code for link in linked}
    return DistrictMatch(
        linked=tuple(linked),
        unlinked=tuple(unlinked),
        not_in_link_table=tuple(
            code for code in in_file if source.link_for(code) is None
        ),
        not_in_source=tuple(
            link.source_code for link in source.links if link.source_code not in in_file
        ),
        missing_places=tuple(missing),
        places_without_boundary=tuple(
            sorted(set(region_districts) - linked_codes - set(missing))
        ),
        name_differences=tuple(differences),
    )


# --------------------------------------------------------------------------- #
# Shared edges                                                                #
# --------------------------------------------------------------------------- #

EdgeGeoJson = Annotated[LineString | MultiLineString, Field(discriminator="type")]
"""A GeoJSON LineString or MultiLineString; the ``type`` member selects the class."""

_TWO_DIMENSIONS: Final = 2


def _line_positions(geojson: LineString | MultiLineString) -> Iterator[Position]:
    lines = (
        [geojson.coordinates]
        if isinstance(geojson, LineString)
        else geojson.coordinates
    )
    for line in lines:
        yield from line


class EdgeGeometry(BaseModel):
    """The line of a shared edge in WGS84, longitude first, two-dimensional.

    ``geojson-pydantic`` checks the structure (at least two positions per line);
    this wrapper adds finite, in-range, two-dimensional positions, as
    ``PlaceGeometry`` does for areas. The wrapped model is copied on the way in.

    Implements: Value Object.

    Attributes:
        geojson: The line.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    geojson: EdgeGeoJson

    @field_validator("geojson", mode="before")
    @classmethod
    def _copy_geometry(cls, value: object) -> object:
        if isinstance(value, LineString | MultiLineString):
            return value.model_dump()
        return value

    @field_validator("geojson", mode="after")
    @classmethod
    def _check_positions(
        cls, geojson: LineString | MultiLineString
    ) -> LineString | MultiLineString:
        for position in _line_positions(geojson):
            if len(position) != _TWO_DIMENSIONS:
                message = "positions must be two-dimensional (longitude, latitude)"
                raise ValueError(message)
            longitude, latitude = position.longitude, position.latitude
            if not (
                math.isfinite(longitude)
                and math.isfinite(latitude)
                and abs(longitude) <= 180.0  # noqa: PLR2004  # reason: WGS84 bound
                and abs(latitude) <= 90.0  # noqa: PLR2004  # reason: WGS84 bound
            ):
                message = "positions must be finite and inside WGS84 bounds"
                raise ValueError(message)
        return geojson

    @property
    def position_count(self) -> int:
        """Return how many positions the line has, over every part.

        Returns:
            The vertex count, which drives the size of a published payload.
        """
        return sum(1 for _ in _line_positions(self.geojson))


def _check_pair(pair: tuple[str, str]) -> tuple[str, str]:
    if pair[0] >= pair[1]:
        message = "a pair of districts must be two different codes in sorted order"
        raise ValueError(message)
    return pair


class SharedEdge(BaseModel):
    """The boundary two districts of a file share, as computed from their polygons.

    Implements: Value Object.

    Attributes:
        source_codes: The two districts, in sorted order.
        geometry: The simplified line.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_codes: Annotated[
        tuple[SourceDistrictCode, SourceDistrictCode], AfterValidator(_check_pair)
    ]
    geometry: EdgeGeometry


class DistrictEdge(BaseModel):
    """A shared edge with the gazetteer places of its two districts.

    Implements: Value Object.

    Attributes:
        source_codes: The two districts in the dataset, in sorted order.
        place_codes: The gazetteer place of each, in the same order; ``None`` for a
            district the link table does not link.
        geometry: The simplified line.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_codes: Annotated[
        tuple[SourceDistrictCode, SourceDistrictCode], AfterValidator(_check_pair)
    ]
    place_codes: tuple[PlaceCode | None, PlaceCode | None]
    geometry: EdgeGeometry

    @property
    def is_fully_linked(self) -> bool:
        """Tell whether both districts are linked to gazetteer places.

        Returns:
            ``True`` when neither place code is ``None``.
        """
        return None not in self.place_codes

    @classmethod
    def from_shared_edge(cls, edge: SharedEdge, match: DistrictMatch) -> Self:
        """Attach the gazetteer places of a match to a computed edge.

        Args:
            edge: The computed edge.
            match: The match of the same file.

        Returns:
            The edge with the place code of each linked district.
        """
        first, second = edge.source_codes
        return cls(
            source_codes=edge.source_codes,
            place_codes=(match.place_code_of(first), match.place_code_of(second)),
            geometry=edge.geometry,
        )


class DistrictCentroid(BaseModel):
    """A centroid the boundary load set on a gazetteer place, kept as provenance.

    The next load replaces a place's centroid only if it is still the one recorded
    here, so a centroid set by anyone else is never overwritten (Q239).

    Implements: Value Object.

    Attributes:
        source_code: The district's code in the dataset.
        place_code: The gazetteer place whose centroid was set.
        point: The centroid set: the district's representative point.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_code: SourceDistrictCode
    place_code: PlaceCode
    point: Coordinates


class DistrictEdgeSet(BaseModel):
    """One immutable snapshot of a region's shared district edges.

    Snapshots are never changed: loading different data adds a new one, and the
    newest is current. ``fingerprint`` identifies the content, so loading the same
    data again is recognised and adds nothing. A snapshot also records the
    centroids its load set on places (``centroids``), which the next load uses as
    provenance; they are not published with the edges.

    Implements: Entity.

    Attributes:
        id: Stable identity (UUIDv7).
        region_code: The region's code in the dataset.
        attribution: How the data must be credited.
        sha256: The digest of the file the edges were computed from.
        fingerprint: ``fingerprint_of`` the content.
        edges: The edges, unique by pair of districts.
        centroids: The centroids the load set, unique by place.
        created_at: When the snapshot was stored, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    region_code: SourceDistrictCode
    attribution: BoundaryAttribution
    sha256: Sha256Hex
    fingerprint: Sha256Hex
    edges: tuple[DistrictEdge, ...] = Field(max_length=DISTRICT_EDGES_MAX)
    centroids: tuple[DistrictCentroid, ...] = Field(
        default=(), max_length=DISTRICT_LINKS_MAX
    )
    created_at: AwareDatetime

    @field_validator("created_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _check_edges(self) -> Self:
        duplicated = [
            pair
            for pair, count in Counter(edge.source_codes for edge in self.edges).items()
            if count > 1
        ]
        if duplicated:
            message = f"duplicated pairs of districts {duplicated}"
            raise ValueError(message)
        places = _duplicates(centroid.place_code for centroid in self.centroids)
        if places:
            message = f"duplicated centroids for places {places}"
            raise ValueError(message)
        expected = self.fingerprint_of(
            self.region_code, self.sha256, self.edges, self.centroids
        )
        if self.fingerprint != expected:
            message = "fingerprint does not match the content"
            raise ValueError(message)
        return self

    @staticmethod
    def fingerprint_of(
        region_code: str,
        sha256: str,
        edges: Iterable[DistrictEdge],
        centroids: Iterable[DistrictCentroid] = (),
    ) -> str:
        """Return the digest identifying an edge set's content.

        The download time is left out on purpose: downloading the same file again
        changes nothing a reader sees.

        Args:
            region_code: The region's code in the dataset.
            sha256: The digest of the source file.
            edges: The edges, in their stored order.
            centroids: The centroids the load set, in their stored order.

        Returns:
            A SHA-256 hex digest over the region, the file digest, every edge and
            every centroid.
        """
        digest = hashlib.sha256(f"{region_code}\x1f{sha256}".encode())
        for edge in edges:
            digest.update(b"\x1e")
            digest.update(edge.model_dump_json().encode())
        for centroid in centroids:
            digest.update(b"\x1d")
            digest.update(centroid.model_dump_json().encode())
        return digest.hexdigest()

    def centroid_set_for(self, place_code: str) -> Coordinates | None:
        """Return the centroid this snapshot's load set on a place, if any.

        Args:
            place_code: The gazetteer place.

        Returns:
            The recorded point, or ``None``.
        """
        return next(
            (
                centroid.point
                for centroid in self.centroids
                if centroid.place_code == place_code
            ),
            None,
        )

    @property
    def position_count(self) -> int:
        """Return how many positions every edge has together.

        Returns:
            The vertex count of the snapshot.
        """
        return sum(edge.geometry.position_count for edge in self.edges)
