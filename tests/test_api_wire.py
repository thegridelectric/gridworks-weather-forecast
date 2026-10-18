"""DB-free wire-form pin for the read façade (the gnr pattern).

A fake `WeatherReads` source stands in for the store; every response
must be byte-identical to the instance's `to_dict()` wire form, and
the OpenAPI schemas must link their sema definitions.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests import records
from gwwf.api import SEMA_DEFINITION_URL, create_app
from gwwf.config import GwwfSettings
from gwwf.sema.enums import Quantity, WeatherForecastFidelity
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecast,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherForecastSeries,
    WeatherLocationGt,
    WeatherObservation,
    WeatherObservationReading,
    WeatherSeasonalTemplateGt,
    WeatherStationGt,
)

BUNDLE = records.MILLINOCKET_RACCOON_BUNDLE

OBSERVATION = WeatherObservation(
    station_alias=records.KMLT,
    observation_time="2026-10-18T14:00:00Z",
    interpolated=False,
    reading_list=[
        WeatherObservationReading(
            channel_name=records.TEMPERATURE_CHANNEL.name,
            quantity=Quantity.Temperature,
            value=727,
        ),
        WeatherObservationReading(
            channel_name=records.WINDSPEED_CHANNEL.name,
            quantity=Quantity.WindSpeed,
            value=45,
        ),
    ],
)
FORECAST = WeatherForecast(
    bundle_name=BUNDLE.name,
    message_created_ms=1792080000000,
    first_slice_start="2026-10-18T15:00:00Z",
    series_list=[
        WeatherForecastSeries(
            channel_name=binding.forecast_channel.name,
            quantity=binding.observation_channel.quantity,
            values=[value] * 48,
            fidelity=fidelity,
            source_updated_time=stamp,
        )
        for binding, value, fidelity, stamp in zip(
            BUNDLE.binding_list,
            [700, 40, 120],
            [
                WeatherForecastFidelity.Live,
                WeatherForecastFidelity.Live,
                WeatherForecastFidelity.Stored,
            ],
            ["2026-10-18T09:09:03Z", "2026-10-18T09:09:03Z", "2026-10-18T06:00:00Z"],
            strict=True,
        )
    ],
)


class OneOfEachSource:
    def channels(self) -> list[WeatherChannelGt]:
        return records.OBSERVATION_CHANNELS

    def forecast_channels(self) -> list[WeatherForecastChannelGt]:
        return records.FORECAST_CHANNELS

    def bundles(self) -> list[WeatherForecastBundleGt]:
        return records.FORECAST_BUNDLES

    def locations(self) -> list[WeatherLocationGt]:
        return [records.MILLINOCKET_LOCATION]

    def stations(self) -> list[WeatherStationGt]:
        return records.STATIONS

    def seasonal_templates(self) -> list[WeatherSeasonalTemplateGt]:
        return records.SEASONAL_TEMPLATES

    def latest_observation(self, station_alias: str) -> WeatherObservation | None:
        return OBSERVATION if station_alias == records.KMLT else None

    def latest_forecast(self, bundle_name: str) -> WeatherForecast | None:
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
    assert client.get(f"/{PARTY}/bundles").json() == [
        b.to_dict() for b in records.FORECAST_BUNDLES
    ]
    assert client.get(f"/{PARTY}/locations").json() == [
        records.MILLINOCKET_LOCATION.to_dict()
    ]
    assert client.get(f"/{PARTY}/stations").json() == [
        s.to_dict() for s in records.STATIONS
    ]


def test_point_lookups_are_wire_form_and_404() -> None:
    assert client.get(f"/{PARTY}/latest-observation/{records.KMLT}").json() == (
        OBSERVATION.to_dict()
    )
    assert client.get(f"/{PARTY}/latest-forecast/{BUNDLE.name}").json() == (
        FORECAST.to_dict()
    )
    assert client.get(f"/{PARTY}/latest-observation/nosuchstation").status_code == 404
    assert client.get(f"/{PARTY}/latest-forecast/no.such.bundle").status_code == 404


def test_openapi_schemas_link_to_sema_definitions() -> None:
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    observation = components["WeatherObservation"]
    expected = SEMA_DEFINITION_URL.format(
        type_name="gw.weather.observation", version="001"
    )
    assert expected in observation["description"]
