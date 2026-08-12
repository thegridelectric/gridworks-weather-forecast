"""gridworks-weather DB: seed round-trip, sent-forecast store, durable state.

Runs against an ephemeral migrated Postgres (see conftest) — every
read comes back as codec-validated instances equal to what went in.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gwwf import records
from gwwf.db.models import ForecastSql
from gwwf.db.store import (
    latest_forecast,
    load_bundles,
    load_forecast_channels,
    load_last_observation,
    load_locations,
    load_product,
    load_weather_channels,
    save_last_observation,
    save_product,
    seed_records,
    store_forecast,
)
from gwwf.nws import HourlyForecastProduct
from gwwf.sema.enums import GwWeatherForecastFidelity
from gwwf.sema.types import GwWeatherForecast, GwWeatherObservation

METHOD = "api.weather.gov.gridpoint.hourly"
LOCATOR = "car.60.114"
BUNDLE = records.MILLINOCKET_NWS_HOURLY_BUNDLE


def forecast_message(
    source_updated: str, first_slice: str, created_ms: int
) -> GwWeatherForecast:
    return GwWeatherForecast(
        bundle_name=BUNDLE.name,
        source_updated_time=source_updated,
        message_created_ms=created_ms,
        fidelity=GwWeatherForecastFidelity.Live,
        first_slice_start=first_slice,
        temp_channel_name=BUNDLE.temp_forecast_channel.name,
        temp_values=[7000] * 48,
        wind_speed_channel_name=BUNDLE.wind_speed_forecast_channel.name,
        wind_speed_values=[4000] * 48,
    )


def test_seed_round_trips_as_equal_records(db_session: Session) -> None:
    seed_records(db_session)
    seed_records(db_session)  # idempotent
    assert load_locations(db_session) == [records.MILLINOCKET_LOCATION]
    assert load_weather_channels(db_session) == records.OBSERVATION_CHANNELS
    assert load_forecast_channels(db_session) == records.FORECAST_CHANNELS
    # Bundles reconstruct FROM the canonical rows — axioms fire on load.
    assert load_bundles(db_session) == records.FORECAST_BUNDLES


def test_forecast_store_keeps_send_distinct_messages(db_session: Session) -> None:
    seed_records(db_session)
    same_window = forecast_message(
        "2026-08-12T09:09:03Z", "2026-08-12T14:00:00Z", 1786280400000
    )
    resend = forecast_message(
        "2026-08-12T09:09:03Z", "2026-08-12T14:00:00Z", 1786280460000
    )
    slid_window = forecast_message(
        "2026-08-12T09:09:03Z", "2026-08-12T15:00:00Z", 1786284000000
    )
    new_revision = forecast_message(
        "2026-08-12T16:33:26Z", "2026-08-12T15:00:00Z", 1786287600000
    )
    for message in (same_window, resend, slid_window, new_revision):
        store_forecast(db_session, message)
    count = db_session.scalar(
        select(func.count())
        .select_from(ForecastSql)
        .where(ForecastSql.bundle_name == BUNDLE.name)
    )
    # resend of an identical (revision, window) no-ops; the rest are rows
    assert count == 3
    assert latest_forecast(db_session, BUNDLE.name) == new_revision
    assert latest_forecast(db_session, "no.such.bundle") is None


def test_product_cache_round_trips(db_session: Session) -> None:
    product = HourlyForecastProduct(
        update_time="2026-08-11T16:33:26Z",
        generated_at="2026-08-11T16:57:20+00:00",
        first_slice_start="2026-08-11T16:00:00Z",
        period_s=3600,
        temperature_f=[70] * 72,
        wind_speed_mph=[5] * 72,
    )
    save_product(db_session, method=METHOD, source_locator=LOCATOR, product=product)
    assert load_product(db_session, method=METHOD, source_locator=LOCATOR) == product
    assert load_product(db_session, method=METHOD, source_locator="elsewhere") is None


def test_last_observation_round_trips(db_session: Session) -> None:
    seed_records(db_session)
    observation = GwWeatherObservation(
        location_alias=records.MILLINOCKET_LOCATION.alias,
        observation_time="2026-08-11T17:55:00Z",
        interpolated=False,
        temp_channel_name=records.TEMPERATURE_CHANNEL.name,
        temp_value=7268,
        wind_speed_channel_name=records.WINDSPEED_CHANNEL.name,
    )
    save_last_observation(
        db_session,
        location_alias=records.MILLINOCKET_LOCATION.alias,
        observation=observation,
        published_slot=1786000000,
    )
    stored = load_last_observation(db_session, records.MILLINOCKET_LOCATION.alias)
    assert stored is not None
    assert stored.observation == observation
    assert stored.observation.wind_speed_value is None
    assert stored.published_slot == 1786000000
    assert load_last_observation(db_session, "no.such.place") is None
