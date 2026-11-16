"""The actor is a GNode."""

from __future__ import annotations

from gwbase.gridworks_actor import GridworksActor

from gwwf.sema.types import WeatherStationGt
from gwwf.weather_actor import WeatherActor, is_model_gridpoint


def test_weather_actor_inherits_gridworks_actor() -> None:
    """WeatherForecastService is a GNode role; the actor must subclass
    GridworksActor (not bare ActorBase)."""
    assert issubclass(WeatherActor, GridworksActor)


def test_a_station_with_no_network_identifier_is_a_model_gridpoint() -> None:
    def station(**ids: str) -> WeatherStationGt:
        return WeatherStationGt(
            alias="s",
            display_name="s",
            latitude_microdegrees=45647800,
            longitude_microdegrees=-68685600,
            start="2024-10-01T00:00:00Z",
            id="2276984d-2842-434a-8a4e-da147d2853b8",
            **ids,
        )

    assert is_model_gridpoint(station())
    assert not is_model_gridpoint(station(icao_id="KMLT"))
    assert not is_model_gridpoint(station(wunderground_id="KMEMILLI18"))
    assert not is_model_gridpoint(station(ghcn_id="USW00014610"))
