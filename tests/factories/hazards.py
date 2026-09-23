"""Factories for the ``hazards`` domain: hazard types and every attribute schema.

``HazardTypeTestFactory`` is suffixed ``TestFactory`` because the domain already has a
``HazardTypeFactory`` (``yakhnama.modules.hazards.domain.factories``) that enforces the
creation rules against a taxonomy; this factory builds a ``HazardType`` directly for
arranging state. Codes come from a counter (``test_hazard_<n>``), so they never collide
with each other or with a real taxonomy code.

The attribute factories fill every measurement with a non-negative value in the unit
the schema demands, and pick enumerated fields at random. None of the values is a
domain fact.

Patterns: Factory.
"""

from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import get_args

from polyfactory import PostGenerated, Use

from tests.factories.base import (
    FACTORY_IDS,
    YakhnamaModelFactory,
    pick,
    random_instant,
    sequence,
    uniform,
)
from tests.factories.shared_kernel import (
    TEST_REGION_MAX_LATITUDE,
    TEST_REGION_MAX_LONGITUDE,
    TEST_REGION_MIN_LATITUDE,
    TEST_REGION_MIN_LONGITUDE,
    DateWithPrecisionFactory,
    LocalizedTextFactory,
    MeasurementFactory,
)
from yakhnama.modules.hazards.domain.attributes import (
    AvalancheAttributes,
    AvalancheTrigger,
    AvalancheType,
    CloudburstAttributes,
    DebrisFlowAttributes,
    DebrisFlowTrigger,
    FlashFloodAttributes,
    FlashFloodTrigger,
    GlacierSurgeAttributes,
    GlofAttributes,
    GlofMechanism,
    HazardAttributes,
    LandslideAttributes,
    LandslideMaterial,
    LandslideMovementType,
    LandslideTrigger,
)
from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.domain.value_objects import (
    GlacialLakeRef,
    GlacierRef,
    IrdrAlignment,
)
from yakhnama.shared_kernel.value_objects import (
    DateWithPrecision,
    Measurement,
    SiUnit,
)

TEST_ALIGNMENT = IrdrAlignment(family="hydrological", main_event="Flood")
"""The alignment every factory-built hazard type carries unless overridden.

Chosen because the hazards domain tests already use it; it says nothing about where
any real hazard type belongs in the IRDR classification.
"""

_AVALANCHE_SIZE_CLASSES = (1, 2, 3, 4, 5)
_next_lake_id = sequence("test-lake-{:05d}")


def _measurement_in(unit: SiUnit) -> Callable[[], Measurement]:
    return lambda: MeasurementFactory.build(unit=unit.value)


def _glacier() -> GlacierRef:
    # The shape of GLIMS_ID_PATTERN only: six digits, "E", five digits, hemisphere.
    longitude = round(
        uniform(TEST_REGION_MIN_LONGITUDE, TEST_REGION_MAX_LONGITUDE) * 1000
    )
    latitude = round(uniform(TEST_REGION_MIN_LATITUDE, TEST_REGION_MAX_LATITUDE) * 1000)
    return GlacierRef(glims_id=f"G{longitude:06d}E{latitude:05d}N")


def _lake() -> GlacialLakeRef:
    return GlacialLakeRef(inventory="icimod", inventory_id=_next_lake_id())


def _surge_end(_name: str, values: Mapping[str, object]) -> DateWithPrecision | None:
    # The schema requires surge_end >= surge_start, so the end is drawn after it.
    start = values.get("surge_start")
    if not isinstance(start, DateWithPrecision):
        return None
    offset = timedelta(days=round(uniform(0.0, 365.0)))
    return DateWithPrecision(value=start.value + offset, precision=start.precision)


def _same_as_created_at(_name: str, values: Mapping[str, object]) -> object:
    # A hazard type at version 1 has not changed since it was created.
    return values["created_at"]


class HazardTypeTestFactory(YakhnamaModelFactory[HazardType]):
    """Builds active root hazard types at version 1 with unique codes.

    Implements: Factory.
    """

    __model__ = HazardType

    id = Use(FACTORY_IDS.new_id)
    code = sequence("test_hazard_{:05d}")
    labels = Use(LocalizedTextFactory.build)
    alignment = TEST_ALIGNMENT
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)


class GlofAttributesFactory(YakhnamaModelFactory[GlofAttributes]):
    """Builds GLOF attributes with every measurement filled in.

    Implements: Factory.
    """

    __model__ = GlofAttributes

    source_lake = Use(_lake)
    source_glacier = Use(_glacier)
    mechanism = pick(get_args(GlofMechanism))
    peak_discharge = Use(_measurement_in(SiUnit.CUBIC_METRE_PER_SECOND))
    flood_volume = Use(_measurement_in(SiUnit.CUBIC_METRE))
    lake_area_before = Use(_measurement_in(SiUnit.SQUARE_METRE))
    lake_area_after = Use(_measurement_in(SiUnit.SQUARE_METRE))


class LandslideAttributesFactory(YakhnamaModelFactory[LandslideAttributes]):
    """Builds landslide attributes with every measurement filled in.

    Implements: Factory.
    """

    __model__ = LandslideAttributes

    movement_type = pick(get_args(LandslideMovementType))
    material = pick(get_args(LandslideMaterial))
    volume = Use(_measurement_in(SiUnit.CUBIC_METRE))
    runout_length = Use(_measurement_in(SiUnit.METRE))
    trigger = pick(get_args(LandslideTrigger))


class DebrisFlowAttributesFactory(YakhnamaModelFactory[DebrisFlowAttributes]):
    """Builds debris-flow attributes with every measurement filled in.

    Implements: Factory.
    """

    __model__ = DebrisFlowAttributes

    volume = Use(_measurement_in(SiUnit.CUBIC_METRE))
    runout_length = Use(_measurement_in(SiUnit.METRE))
    trigger = pick(get_args(DebrisFlowTrigger))
    channel_blocked = pick([True, False, None])


class CloudburstAttributesFactory(YakhnamaModelFactory[CloudburstAttributes]):
    """Builds cloudburst attributes with every measurement filled in.

    Implements: Factory.
    """

    __model__ = CloudburstAttributes

    rainfall_total = Use(_measurement_in(SiUnit.METRE))
    duration = Use(_measurement_in(SiUnit.SECOND))
    peak_intensity = Use(_measurement_in(SiUnit.METRE_PER_SECOND))


class FlashFloodAttributesFactory(YakhnamaModelFactory[FlashFloodAttributes]):
    """Builds flash-flood attributes with the peak discharge filled in.

    Implements: Factory.
    """

    __model__ = FlashFloodAttributes

    peak_discharge = Use(_measurement_in(SiUnit.CUBIC_METRE_PER_SECOND))
    trigger = pick(get_args(FlashFloodTrigger))


class AvalancheAttributesFactory(YakhnamaModelFactory[AvalancheAttributes]):
    """Builds avalanche attributes with a size class from 1 to 5.

    Implements: Factory.
    """

    __model__ = AvalancheAttributes

    avalanche_type = pick(get_args(AvalancheType))
    size_class = pick(_AVALANCHE_SIZE_CLASSES)
    trigger = pick(get_args(AvalancheTrigger))


class GlacierSurgeAttributesFactory(YakhnamaModelFactory[GlacierSurgeAttributes]):
    """Builds glacier-surge attributes whose surge never ends before it starts.

    Implements: Factory.
    """

    __model__ = GlacierSurgeAttributes

    glacier = Use(_glacier)
    advance_distance = Use(_measurement_in(SiUnit.METRE))
    surge_start = Use(DateWithPrecisionFactory.build)
    surge_end = PostGenerated(_surge_end)
    river_blocked = pick([True, False, None])


HAZARD_ATTRIBUTES_BUILDERS: Mapping[str, Callable[[], HazardAttributes]] = {
    "glof": GlofAttributesFactory.build,
    "landslide": LandslideAttributesFactory.build,
    "debris_flow": DebrisFlowAttributesFactory.build,
    "cloudburst": CloudburstAttributesFactory.build,
    "flash_flood": FlashFloodAttributesFactory.build,
    "avalanche": AvalancheAttributesFactory.build,
    "glacier_surge": GlacierSurgeAttributesFactory.build,
}
"""The ``build`` of one factory per ``DEFAULT_REGISTRY`` code, to loop over all."""
