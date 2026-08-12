"""The read façade over a migrated Postgres — wire-form equality.

The done-when bar: a pull returns byte-identical wire form to the
broadcast (same `to_dict` rendering, served from the stored payload).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests import records
from gwwf.api import create_app
from gwwf.config import GwwfSettings
from gwwf.db.store import (
    save_last_observation,
    store_forecast,
)
from gwwf.sema.enums import WeatherForecastFidelity
from gwwf.sema.types import WeatherForecast, WeatherObservation

BUNDLE = records.MILLINOCKET_NWS_HOURLY_BUNDLE


def client_for(pg_url: str) -> TestClient:
    from pydantic import SecretStr

    return TestClient(create_app(GwwfSettings(db_url=SecretStr(pg_url))))


def test_record_listings_and_latest_pulls(db_session: Session, pg_url: str) -> None:
    records.seed(db_session)
    observation = WeatherObservation(
        location_alias=records.MILLINOCKET_LOCATION.alias,
        observation_time="2026-08-12T13:55:00Z",
        interpolated=False,
        temp_channel_name=records.TEMPERATURE_CHANNEL.name,
        temp_value=7268,
        wind_speed_channel_name=records.WINDSPEED_CHANNEL.name,
        wind_speed_value=4500,
    )
    save_last_observation(
        db_session,
        location_alias=records.MILLINOCKET_LOCATION.alias,
        observation=observation,
        published_slot=1786000000,
    )
    forecast = WeatherForecast(
        bundle_name=BUNDLE.name,
        source_updated_time="2026-08-12T09:09:03Z",
        message_created_ms=1786280400000,
        fidelity=WeatherForecastFidelity.Live,
        first_slice_start="2026-08-12T14:00:00Z",
        temp_channel_name=BUNDLE.temp_forecast_channel.name,
        temp_values=[7000] * 48,
        wind_speed_channel_name=BUNDLE.wind_speed_forecast_channel.name,
        wind_speed_values=[4000] * 48,
    )
    store_forecast(db_session, forecast)

    client = client_for(pg_url)

    assert client.get("/d1-weather/channels").json() == [
        c.to_dict() for c in records.OBSERVATION_CHANNELS
    ]
    assert client.get("/d1-weather/forecast-channels").json() == [
        c.to_dict() for c in records.FORECAST_CHANNELS
    ]
    assert client.get("/d1-weather/bundles").json() == [BUNDLE.to_dict()]
    assert client.get("/d1-weather/locations").json() == [
        records.MILLINOCKET_LOCATION.to_dict()
    ]

    # Byte-identical wire form to the broadcast.
    pulled = client.get(
        f"/d1-weather/latest-observation/{records.MILLINOCKET_LOCATION.alias}"
    )
    assert pulled.status_code == 200
    assert pulled.json() == observation.to_dict()

    pulled = client.get(f"/d1-weather/latest-forecast/{BUNDLE.name}")
    assert pulled.status_code == 200
    assert pulled.json() == forecast.to_dict()


def test_missing_pulls_404(db_session: Session, pg_url: str) -> None:
    records.seed(db_session)
    client = client_for(pg_url)
    assert client.get("/d1-weather/latest-observation/no.such.place").status_code == 404
    assert client.get("/d1-weather/latest-forecast/no.such.bundle").status_code == 404
