"""Scale from a channel's Unit onto a source's natural units."""

from __future__ import annotations

from gwwf.sema.enums import Unit

# Adapter-level mapping from a channel's Unit to the integer scale
# applied to a product's natural units (°F, mph, W/m²). Retires when a
# unit-metadata word drives conversion generically.
UNIT_SCALE: dict[Unit, int] = {
    Unit.FahrenheitX10: 10,
    Unit.FahrenheitX100: 100,
    Unit.MilesPerHourX10: 10,
    Unit.MilesPerHourX1000: 1000,
    Unit.WattsPerSquareMeter: 1,
}
