"""The actor is a GNode; a model gridpoint's observation is the archive's
message for the same point."""

from __future__ import annotations

from gwbase.gridworks_actor import GridworksActor

from gwwf.open_meteo import HistorySeries
from gwwf.grid import iso_to_s
from gwwf.sema.enums import Quantity, Unit
from gwwf.sema.types import WeatherChannelGt, WeatherStationGt
from gwwf.weather_actor import (
    WeatherActor,
    gridpoint_observation,
    is_model_gridpoint,
)


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


def test_a_gridpoint_observation_reads_every_channel_at_the_slot() -> None:
    station = WeatherStationGt(
        alias="ommillinocket",
        display_name="Open-Meteo Millinocket gridpoint",
        latitude_microdegrees=45647800,
        longitude_microdegrees=-68685600,
        start="2024-10-01T00:00:00Z",
        id="2276984d-2842-434a-8a4e-da147d2853b8",
    )

    def channel(
        name: str, quantity: Quantity, unit: Unit, uid: str
    ) -> WeatherChannelGt:
        return WeatherChannelGt(
            name=f"ommillinocket.{name}",
            display_name=f"Open-Meteo Millinocket {name}",
            station_alias=station.alias,
            quantity=quantity,
            unit=unit,
            emit_period_s=3600,
            emit_offset_s=0,
            start="2024-10-01T00:00:00Z",
            id=uid,
        )

    channels = [
        channel(
            "irradiance",
            Quantity.Irradiance,
            Unit.WattsPerSquareMeter,
            "4d3f0e4e-1c3a-4e3f-9f2a-0b4a4e5f6a71",
        ),
        channel(
            "temperature",
            Quantity.Temperature,
            Unit.FahrenheitX10,
            "4d3f0e4e-1c3a-4e3f-9f2a-0b4a4e5f6a72",
        ),
        channel(
            "windspeed",
            Quantity.WindSpeed,
            Unit.MilesPerHourX10,
            "4d3f0e4e-1c3a-4e3f-9f2a-0b4a4e5f6a73",
        ),
    ]
    slot_s = iso_to_s("2026-11-23T20:00:00Z")
    series = {
        # Instantaneous at the label: the value at the slot.
        Quantity.Temperature: HistorySeries(
            Quantity.Temperature, slot_s - 3600, 3600, [58.0, 55.6, 51.6]
        ),
        Quantity.WindSpeed: HistorySeries(
            Quantity.WindSpeed, slot_s - 3600, 3600, [6.1, None, 11.0]
        ),
        # Hour-mean, already shifted to slice starts by parse_history.
        Quantity.Irradiance: HistorySeries(
            Quantity.Irradiance, slot_s - 3600, 3600, [84.0, 24.0, 0.0]
        ),
    }

    observation = gridpoint_observation(station, channels, series, slot_s)

    assert observation.station_alias == "ommillinocket"
    assert observation.observation_time == "2026-11-23T20:00:00Z"
    assert observation.interpolated is False
    # Wind has no value at the slot, so the message has no wind reading.
    assert [(r.channel_name, r.value) for r in observation.reading_list] == [
        ("ommillinocket.irradiance", 24),
        ("ommillinocket.temperature", 556),
    ]
