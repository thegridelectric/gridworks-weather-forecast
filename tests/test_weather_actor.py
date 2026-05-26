"""Smoke tests for WeatherActor + Weather payload serialization."""

from __future__ import annotations

from gwbase.gridworks_actor import GridworksActor

from gwwf.sema.types import Weather
from gwwf.weather_actor import WeatherActor


def test_module_imports() -> None:
    assert WeatherActor is not None
    assert Weather is not None


def test_weather_actor_inherits_gridworks_actor() -> None:
    """WeatherForecastService is a GNode role; the actor must subclass
    GridworksActor (not bare ActorBase)."""
    assert issubclass(WeatherActor, GridworksActor)


def test_weather_payload_serializes_to_pascalcase() -> None:
    """Wire shape — matches the registered sema `weather` v000 type."""
    w = Weather(
        from_g_node_alias="hw1.isone.ws",
        weather_channel_name="weather.gov.kmlt",
        unix_time_s=1779999999,
        outside_air_temp_f=55.4,
        wind_speed_mph=3.44,
    )
    payload = w.to_dict()
    assert payload == {
        "TypeName": "weather",
        "Version": "000",
        "FromGNodeAlias": "hw1.isone.ws",
        "WeatherChannelName": "weather.gov.kmlt",
        "UnixTimeS": 1779999999,
        "OutsideAirTempF": 55.4,
        "WindSpeedMph": 3.44,
    }


def test_weather_payload_wind_optional() -> None:
    """`wind_speed_mph` is optional — legacy parity (some observations have
    no wind reading)."""
    w = Weather(
        from_g_node_alias="hw1.isone.ws",
        weather_channel_name="weather.gov.kmlt",
        unix_time_s=1779999999,
        outside_air_temp_f=55.4,
    )
    payload = w.to_dict()
    assert "WindSpeedMph" not in payload or payload.get("WindSpeedMph") is None


def test_weather_axiom_1_rejects_negative_wind() -> None:
    """Axiom 1 (WindSpeedNonNegative): if WindSpeedMph is present, it
    SHALL be >= 0."""
    import pytest

    with pytest.raises(Exception):  # Pydantic ValidationError wraps the axiom ValueError
        Weather(
            from_g_node_alias="hw1.isone.ws",
            weather_channel_name="weather.gov.kmlt",
            unix_time_s=1779999999,
            outside_air_temp_f=55.4,
            wind_speed_mph=-1.0,
        )
