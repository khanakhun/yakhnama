"""Coarsening reporter locations before they are published.

Reporter GPS positions are stored exactly and stay private; every public payload
shows them rounded to ``Settings.public_coordinate_decimals`` decimal places (spec
§10, Phase 2 security review). The rounding lives here, framework-free, so domain,
application and API code share one implementation.

Rounding rule: each axis is rounded independently with ``decimal.ROUND_HALF_UP`` on the
coordinate's shortest decimal representation (``repr`` of the float). Using the
decimal text rather than the binary float means ``36.125`` rounds to ``36.13`` as a
reader expects, not to ``36.12`` because the binary value is ``36.12499...``.
``ROUND_HALF_UP`` rounds ties away from zero, so the rule is symmetric for west and
south coordinates. Half-to-even was rejected because a published figure should round
the way a reader rounds by hand; the privacy effect of either rule is the same.

Bound on displacement: each axis moves by at most ``0.5 * 10**-decimals`` degrees. At
2 decimals that is 0.005°, about 0.56 km north-south anywhere and about 0.46 km
east-west at 34° N (less further north); the straight-line displacement is therefore
at most about 0.72 km at the latitudes of Gilgit-Baltistan. Because the WGS84 bounds
(±180, ±90) are whole degrees, rounding never leaves them.

Patterns: Policy.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field

from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.value_objects import Coordinates

MIN_PUBLIC_DECIMALS: Final = 0
MAX_PUBLIC_DECIMALS: Final = 6
"""Six decimals is about 0.11 m, beyond any phone GPS; more would publish noise."""

PublicDecimals = Annotated[
    int, Field(ge=MIN_PUBLIC_DECIMALS, le=MAX_PUBLIC_DECIMALS, strict=True)
]


def _round_degrees(degrees: float, decimals: int) -> float:
    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(degrees)).quantize(quantum, rounding=ROUND_HALF_UP))


def round_coordinates(coordinates: Coordinates, decimals: int) -> Coordinates:
    """Round both axes of a point to ``decimals`` decimal places, ties away from zero.

    Args:
        coordinates: The exact point.
        decimals: Decimal places to keep, 0 to 6.

    Returns:
        A new point; rounding it again with the same ``decimals`` returns an equal
        point.

    Raises:
        ValidationError: If ``decimals`` is outside 0 to 6.
    """
    if not MIN_PUBLIC_DECIMALS <= decimals <= MAX_PUBLIC_DECIMALS:
        message = (
            f"decimals must be between {MIN_PUBLIC_DECIMALS} and "
            f"{MAX_PUBLIC_DECIMALS}, got {decimals}"
        )
        raise ValidationError(message)
    return Coordinates(
        longitude=_round_degrees(coordinates.longitude, decimals),
        latitude=_round_degrees(coordinates.latitude, decimals),
    )


class PublicCoordinatePolicy(BaseModel):
    """Decides how precisely a private location may appear in a public payload.

    Built once in the composition root from ``Settings.public_coordinate_decimals``
    and applied wherever a reporter's position leaves the system.

    Implements: Policy.

    Attributes:
        decimals: Decimal places kept, 0 to 6.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decimals: PublicDecimals

    def apply(self, coordinates: Coordinates) -> Coordinates:
        """Return the publishable version of a point.

        Args:
            coordinates: The exact, private point.

        Returns:
            The point rounded to ``decimals`` places.
        """
        return round_coordinates(coordinates, self.decimals)
