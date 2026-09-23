"""Hazard-specific attribute schemas, selected by hazard type (Strategy + Registry).

Each hazard type that records extra attributes has one frozen schema class here. The
classes form a Pydantic discriminated union on ``hazard_type``
(``HazardAttributesUnion``) and are registered by code in ``DEFAULT_REGISTRY``;
adding a hazard adds a class, a union member and a registration, and never changes an
existing schema.

**Every field below is proposed.** None is taken from a cited schema (open question
Q2 of ``docs/plans/phase-1.md``); each is minimal, optional and bounded so a wrong guess
is cheap to change before Phase 3 stores real events. Quantities are ``Measurement``
values in SI units, each checked against the one unit its field allows.
The rationale for each field is in ``docs/data-dictionary/hazards.md``.

Patterns: Strategy, Registry.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Annotated, Literal, Self, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)

from yakhnama.modules.hazards.domain.errors import (
    HazardAttributesRegistrationError,
    UnknownHazardAttributesError,
)
from yakhnama.modules.hazards.domain.value_objects import GlacialLakeRef, GlacierRef
from yakhnama.shared_kernel.value_objects import (
    DateWithPrecision,
    Measurement,
    SiUnit,
)


def _require_unit(unit: SiUnit) -> Callable[[Measurement], Measurement]:
    """Build a validator accepting only non-negative measurements in ``unit``."""

    def check(measurement: Measurement) -> Measurement:
        if measurement.unit != unit:
            message = f"expected a measurement in {unit.value}, got {measurement.unit}"
            raise ValueError(message)
        if measurement.value < 0:
            message = "the measurement must not be negative"
            raise ValueError(message)
        return measurement

    return check


LengthMeasurement = Annotated[Measurement, AfterValidator(_require_unit(SiUnit.METRE))]
AreaMeasurement = Annotated[
    Measurement, AfterValidator(_require_unit(SiUnit.SQUARE_METRE))
]
VolumeMeasurement = Annotated[
    Measurement, AfterValidator(_require_unit(SiUnit.CUBIC_METRE))
]
DurationMeasurement = Annotated[
    Measurement, AfterValidator(_require_unit(SiUnit.SECOND))
]
DischargeMeasurement = Annotated[
    Measurement, AfterValidator(_require_unit(SiUnit.CUBIC_METRE_PER_SECOND))
]
# Rainfall intensity is a depth per time, dimensionally a speed, so it is stored in
# metre_per_second (100 mm/h is about 2.78e-5 m/s); see the data dictionary.
VelocityMeasurement = Annotated[
    Measurement, AfterValidator(_require_unit(SiUnit.METRE_PER_SECOND))
]

GlofMechanism = Literal[
    "moraine_dam_breach",
    "ice_dam_breach",
    "englacial_drainage",
    "overtopping",
    "unknown",
]
LandslideMovementType = Literal[
    "fall", "topple", "slide", "flow", "spread", "complex", "unknown"
]
LandslideMaterial = Literal["rock", "debris", "earth", "unknown"]
LandslideTrigger = Literal["rainfall", "earthquake", "snowmelt", "human", "unknown"]
DebrisFlowTrigger = Literal["rainfall", "glof", "snowmelt", "unknown"]
FlashFloodTrigger = Literal["cloudburst", "glof", "snowmelt", "dam_failure", "unknown"]
AvalancheType = Literal["slab", "loose_snow", "wet", "ice", "unknown"]
AvalancheTrigger = Literal["natural", "human", "unknown"]
AvalancheSizeClass = Annotated[int, Field(ge=1, le=5)]


class HazardAttributes(BaseModel):
    """Common base of every hazard attribute schema.

    Subclasses narrow ``hazard_type`` to a ``Literal`` so Pydantic can pick the schema
    from the payload alone.

    Implements: Strategy.

    Attributes:
        hazard_type: Discriminator naming the registry code of the schema.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    hazard_type: str


class GlofAttributes(HazardAttributes):
    """Attributes of a glacial lake outburst flood (proposed fields).

    Implements: Strategy.

    Attributes:
        hazard_type: Always ``"glof"``.
        source_lake: The lake that drained, if identified.
        source_glacier: The glacier damming or feeding the lake, if identified.
        mechanism: How the lake released its water.
        peak_discharge: Highest flow rate, in cubic metres per second.
        flood_volume: Total volume released, in cubic metres.
        lake_area_before: Lake surface area before the outburst, in square metres.
        lake_area_after: Lake surface area after the outburst, in square metres.
    """

    hazard_type: Literal["glof"] = "glof"
    source_lake: GlacialLakeRef | None = None
    source_glacier: GlacierRef | None = None
    mechanism: GlofMechanism = "unknown"
    peak_discharge: DischargeMeasurement | None = None
    flood_volume: VolumeMeasurement | None = None
    lake_area_before: AreaMeasurement | None = None
    lake_area_after: AreaMeasurement | None = None


class LandslideAttributes(HazardAttributes):
    """Attributes of a landslide (proposed fields; movement types after Varnes/Hungr).

    Implements: Strategy.

    Attributes:
        hazard_type: Always ``"landslide"``.
        movement_type: Type of movement.
        material: Main material moved.
        volume: Displaced volume, in cubic metres.
        runout_length: Distance travelled by the moved mass, in metres.
        trigger: What set the landslide off.
    """

    hazard_type: Literal["landslide"] = "landslide"
    movement_type: LandslideMovementType = "unknown"
    material: LandslideMaterial = "unknown"
    volume: VolumeMeasurement | None = None
    runout_length: LengthMeasurement | None = None
    trigger: LandslideTrigger = "unknown"


class DebrisFlowAttributes(HazardAttributes):
    """Attributes of a debris flow (proposed fields).

    Implements: Strategy.

    Attributes:
        hazard_type: Always ``"debris_flow"``.
        volume: Deposited volume, in cubic metres.
        runout_length: Distance travelled by the flow, in metres.
        trigger: What set the flow off.
        channel_blocked: Whether the flow blocked a river or stream channel; ``None``
            when unknown.
    """

    hazard_type: Literal["debris_flow"] = "debris_flow"
    volume: VolumeMeasurement | None = None
    runout_length: LengthMeasurement | None = None
    trigger: DebrisFlowTrigger = "unknown"
    channel_blocked: bool | None = None


class CloudburstAttributes(HazardAttributes):
    """Attributes of a cloudburst or extreme rainfall episode (proposed fields).

    Implements: Strategy.

    Attributes:
        hazard_type: Always ``"cloudburst"``.
        rainfall_total: Accumulated rainfall depth over the episode, in metres.
        duration: Length of the episode, in seconds.
        peak_intensity: Highest rainfall rate (depth per time), in metres per
            second; proposed, kept because it is not derivable from
            ``rainfall_total`` and ``duration``, which give only the mean rate.
    """

    hazard_type: Literal["cloudburst"] = "cloudburst"
    rainfall_total: LengthMeasurement | None = None
    duration: DurationMeasurement | None = None
    peak_intensity: VelocityMeasurement | None = None


class FlashFloodAttributes(HazardAttributes):
    """Attributes of a flash flood (proposed fields).

    Implements: Strategy.

    Attributes:
        hazard_type: Always ``"flash_flood"``.
        peak_discharge: Highest flow rate, in cubic metres per second.
        trigger: What caused the flood.
    """

    hazard_type: Literal["flash_flood"] = "flash_flood"
    peak_discharge: DischargeMeasurement | None = None
    trigger: FlashFloodTrigger = "unknown"


class AvalancheAttributes(HazardAttributes):
    """Attributes of a snow or ice avalanche (proposed fields).

    Implements: Strategy.

    Attributes:
        hazard_type: Always ``"avalanche"``.
        avalanche_type: Kind of avalanche. Named ``avalanche_type`` rather than
            ``type`` so it cannot be confused with the discriminator or the builtin.
        size_class: Destructive size class 1 to 5 (proposed: the international
            avalanche size scale, whole classes only).
        trigger: Whether the release was natural or human-triggered.
    """

    hazard_type: Literal["avalanche"] = "avalanche"
    avalanche_type: AvalancheType = "unknown"
    size_class: AvalancheSizeClass | None = None
    trigger: AvalancheTrigger = "unknown"


class GlacierSurgeAttributes(HazardAttributes):
    """Attributes of a glacier surge (proposed fields).

    Implements: Strategy.

    Attributes:
        hazard_type: Always ``"glacier_surge"``.
        glacier: The surging glacier, if identified.
        advance_distance: How far the terminus advanced, in metres.
        surge_start: When the surge began, with its precision.
        surge_end: When the surge ended, with its precision; never before
            ``surge_start``.
        river_blocked: Whether the advancing ice blocked a river; ``None`` when
            unknown.
    """

    hazard_type: Literal["glacier_surge"] = "glacier_surge"
    glacier: GlacierRef | None = None
    advance_distance: LengthMeasurement | None = None
    surge_start: DateWithPrecision | None = None
    surge_end: DateWithPrecision | None = None
    river_blocked: bool | None = None

    @model_validator(mode="after")
    def _check_surge_period(self) -> Self:
        if (
            self.surge_start is not None
            and self.surge_end is not None
            and self.surge_end.value < self.surge_start.value
        ):
            message = "surge_end must not be earlier than surge_start"
            raise ValueError(message)
        return self


HazardAttributesUnion = Annotated[
    GlofAttributes
    | LandslideAttributes
    | DebrisFlowAttributes
    | CloudburstAttributes
    | FlashFloodAttributes
    | AvalancheAttributes
    | GlacierSurgeAttributes,
    Field(discriminator="hazard_type"),
]
"""Every attribute schema, selected by the ``hazard_type`` value in the payload."""

HAZARD_ATTRIBUTES_ADAPTER: TypeAdapter[HazardAttributesUnion] = TypeAdapter(
    HazardAttributesUnion
)
"""Validates and serialises ``HazardAttributesUnion`` payloads, including JSON."""


class HazardAttributeRegistry:
    """Maps a registry code to its attribute schema.

    The registry is immutable: ``register`` returns a new registry, so the shared
    ``DEFAULT_REGISTRY`` cannot be changed by a caller at run time.

    Implements: Registry.
    """

    def __init__(self) -> None:
        """Create an empty registry."""
        self._schemas: Mapping[str, type[HazardAttributes]] = MappingProxyType({})

    def register(
        self, code: str, schema: type[HazardAttributes]
    ) -> "HazardAttributeRegistry":
        """Return a registry that also maps ``code`` to ``schema``.

        Args:
            code: Registry code; must equal the schema's ``hazard_type`` literal.
            schema: The attribute schema class.

        Returns:
            A new registry with the extra entry.

        Raises:
            HazardAttributesRegistrationError: If ``code`` is already registered or
                ``schema`` does not declare ``hazard_type: Literal[code]``.
        """
        if code in self._schemas:
            message = f"hazard attribute schema {code!r} is already registered"
            raise HazardAttributesRegistrationError(message)
        discriminator = schema.model_fields["hazard_type"]
        if get_args(discriminator.annotation) != (code,) or (
            discriminator.default != code
        ):
            message = (
                f"{schema.__name__} must declare hazard_type: "
                f"Literal[{code!r}] = {code!r}"
            )
            raise HazardAttributesRegistrationError(message)
        registry = HazardAttributeRegistry()
        registry._schemas = MappingProxyType({**self._schemas, code: schema})
        return registry

    def schema_for(self, code: str) -> type[HazardAttributes]:
        """Return the schema registered under ``code``.

        Args:
            code: Registry code, usually ``HazardType.attributes_schema``.

        Returns:
            The attribute schema class.

        Raises:
            UnknownHazardAttributesError: If nothing is registered under ``code``.
        """
        schema = self._schemas.get(code)
        if schema is None:
            raise UnknownHazardAttributesError(code)
        return schema

    def validate(self, code: str, payload: Mapping[str, object]) -> HazardAttributes:
        """Validate a raw attribute payload against the schema for ``code``.

        This is the JSONB boundary: persistence and API adapters hand over the
        undecoded ``Mapping`` they read, and only the validated, frozen model travels
        further. ``hazard_type`` may be omitted; if present it must equal ``code``.

        Args:
            code: Registry code of the schema to apply.
            payload: The raw attributes, for example a decoded JSONB object.

        Returns:
            The validated attributes.

        Raises:
            UnknownHazardAttributesError: If nothing is registered under ``code``.
            pydantic.ValidationError: If the payload does not match the schema.
        """
        return self.schema_for(code).model_validate(dict(payload))

    def codes(self) -> frozenset[str]:
        """Return every registered code.

        Returns:
            The registry codes.
        """
        return frozenset(self._schemas)


DEFAULT_REGISTRY: HazardAttributeRegistry = (
    HazardAttributeRegistry()
    .register("glof", GlofAttributes)
    .register("landslide", LandslideAttributes)
    .register("debris_flow", DebrisFlowAttributes)
    .register("cloudburst", CloudburstAttributes)
    .register("flash_flood", FlashFloodAttributes)
    .register("avalanche", AvalancheAttributes)
    .register("glacier_surge", GlacierSurgeAttributes)
)
"""The registry of every attribute schema in ``HazardAttributesUnion``."""
