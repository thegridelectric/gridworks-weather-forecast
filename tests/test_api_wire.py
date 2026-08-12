"""DB-free wire-form pin for the read façade (the gnr pattern).

A fake `WeatherReads` source stands in for the store; every response
must be byte-identical to the instance's `to_dict()` wire form, and
the OpenAPI schemas must link their sema definitions.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gwwf import records
from gwwf.api import SEMA_DEFINITION_URL, create_app
from gwwf.config import GwwfSettings
from gwwf.sema.enums import GwWeatherForecastFidelity
from gwwf.sema.types import (
    GwWeatherChannelGt,
    GwWeatherForecast,
    GwWeatherForecastBundleGt,
    GwWeatherForecastChannelGt,
    GwWeatherLocationGt,
    GwWeatherObservation,
)

BUNDLE = records.MILLINOCKET_NWS_HOURLY_BUNDLE

OBSERVATION = GwWeatherObservation(
    location_alias=records.MILLINOCKET_LOCATION.alias,
    observation_time="2026-08-12T14:00:00Z",
    interpolated=False,
    temp_channel_name=records.TEMPERATURE_CHANNEL.name,
    temp_value=7268,
    wind_speed_channel_name=records.WINDSPEED_CHANNEL.name,
)
FORECAST = GwWeatherForecast(
    bundle_name=BUNDLE.name,
    source_updated_time="2026-08-12T09:09:03Z",
    message_created_ms=1786280400000,
    fidelity=GwWeatherForecastFidelity.Live,
    first_slice_start="2026-08-12T15:00:00Z",
    temp_channel_name=BUNDLE.temp_forecast_channel.name,
    temp_values=[7000] * 48,
    wind_speed_channel_name=BUNDLE.wind_speed_forecast_channel.name,
    wind_speed_values=[4000] * 48,
)


class OneOfEachSource:
    def channels(self) -> list[GwWeatherChannelGt]:
        return records.OBSERVATION_CHANNELS

    def forecast_channels(self) -> list[GwWeatherForecastChannelGt]:
        return records.FORECAST_CHANNELS

    def bundles(self) -> list[GwWeatherForecastBundleGt]:
        return records.FORECAST_BUNDLES

    def locations(self) -> list[GwWeatherLocationGt]:
        return [records.MILLINOCKET_LOCATION]

    def latest_observation(self, location_alias: str) -> GwWeatherObservation | None:
        return (
            OBSERVATION
            if location_alias == records.MILLINOCKET_LOCATION.alias
            else None
        )

    def latest_forecast(self, bundle_name: str) -> GwWeatherForecast | None:
        return FORECAST if bundle_name == BUNDLE.name else None


client = TestClient(create_app(GwwfSettings(), source=OneOfEachSource()))
PARTY = "d1-weather"  # hyphenated GNodeAlias — gwwf is a GNode


def test_ping() -> None:
    assert client.get("/ping").json() == {"status": "ok"}


def test_listings_are_wire_form() -> None:
    assert client.get(f"/{PARTY}/channels").json() == [
        c.to_dict() for c in records.OBSERVATION_CHANNELS
    ]
    assert client.get(f"/{PARTY}/forecast-channels").json() == [
        c.to_dict() for c in records.FORECAST_CHANNELS
    ]
    assert client.get(f"/{PARTY}/bundles").json() == [BUNDLE.to_dict()]
    assert client.get(f"/{PARTY}/locations").json() == [
        records.MILLINOCKET_LOCATION.to_dict()
    ]


def test_point_lookups_are_wire_form_and_404() -> None:
    alias = records.MILLINOCKET_LOCATION.alias
    assert client.get(f"/{PARTY}/latest-observation/{alias}").json() == (
        OBSERVATION.to_dict()
    )
    assert client.get(f"/{PARTY}/latest-forecast/{BUNDLE.name}").json() == (
        FORECAST.to_dict()
    )
    assert client.get(f"/{PARTY}/latest-observation/no.such.place").status_code == 404
    assert client.get(f"/{PARTY}/latest-forecast/no.such.bundle").status_code == 404


def test_openapi_schemas_link_to_sema_definitions() -> None:
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    observation = components["GwWeatherObservation"]
    expected = SEMA_DEFINITION_URL.format(
        type_name="gw.weather.observation", version="000"
    )
    assert expected in observation["description"]
