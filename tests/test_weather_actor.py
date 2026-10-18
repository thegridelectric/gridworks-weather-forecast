"""The actor is a GNode."""

from __future__ import annotations

from gwbase.gridworks_actor import GridworksActor

from gwwf.weather_actor import WeatherActor


def test_weather_actor_inherits_gridworks_actor() -> None:
    """WeatherForecastService is a GNode role; the actor must subclass
    GridworksActor (not bare ActorBase)."""
    assert issubclass(WeatherActor, GridworksActor)
