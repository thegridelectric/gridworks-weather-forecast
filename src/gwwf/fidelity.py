"""The forecast fidelity ladder: live → stored → seasonal template."""

from __future__ import annotations

from gwwf.sema.enums import WeatherForecastFidelity
from gwwf.sema.types import WeatherForecast

# Rungs counted from the top: a larger rank is further down the ladder.
FIDELITY_RANK = {
    WeatherForecastFidelity.Live: 0,
    WeatherForecastFidelity.Stored: 1,
    WeatherForecastFidelity.SeasonalTemplate: 2,
}


def lowest_fidelity(message: WeatherForecast) -> WeatherForecastFidelity:
    """The rung furthest down the ladder among the message's series."""
    return max(
        (series.fidelity for series in message.series_list),
        key=lambda f: FIDELITY_RANK[f],
    )
