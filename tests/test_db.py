"""gridworks-weather DB: seed round-trip, sent-forecast store, durable state.

Runs against an ephemeral migrated Postgres (see conftest) — every
read comes back as codec-validated instances equal to what went in.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import records
from gwwf.db.models import ForecastSql
from gwwf.db.store import (
    insert_record,
    latest_forecast,
    load_bundles,
    load_forecast_channels,
    load_last_observation,
    load_locations,
    load_product,
    load_weather_channels,
    save_last_observation,
    save_product,
    store_forecast,
)
from gwwf.nws import HourlyForecastProduct
from gwwf.sema.codec import default_codec
from gwwf.sema.enums import WeatherForecastFidelity
from gwwf.sema.types import (
    WeatherForecast,
    WeatherForecastBundleGt,
    WeatherObservation,
)

METHOD = "api.weather.gov.gridpoint.hourly"
LOCATOR = "car.60.114"
BUNDLE = records.MILLINOCKET_NWS_HOURLY_BUNDLE


def forecast_message(
    source_updated: str, first_slice: str, created_ms: int
) -> WeatherForecast:
    return WeatherForecast(
        bundle_name=BUNDLE.name,
        source_updated_time=source_updated,
        message_created_ms=created_ms,
        fidelity=WeatherForecastFidelity.Live,
        first_slice_start=first_slice,
        temp_channel_name=BUNDLE.temp_forecast_channel.name,
        temp_values=[7000] * 48,
        wind_speed_channel_name=BUNDLE.wind_speed_forecast_channel.name,
        wind_speed_values=[4000] * 48,
    )


def test_records_round_trip_as_equal_records(db_session: Session) -> None:
    records.seed(db_session)
    assert load_locations(db_session) == [records.MILLINOCKET_LOCATION]
    assert load_weather_channels(db_session) == records.OBSERVATION_CHANNELS
    assert load_forecast_channels(db_session) == records.FORECAST_CHANNELS
    # Bundles reconstruct FROM the canonical rows — axioms fire on load.
    assert load_bundles(db_session) == records.FORECAST_BUNDLES


def test_insert_record_is_insert_only(db_session: Session) -> None:
    records.seed(db_session)
    # Records are durable identities: a second create of the same
    # record refuses, never upserts.
    with pytest.raises(IntegrityError):
        insert_record(db_session, records.MILLINOCKET_LOCATION)
    # ... and the refusal leaves the session usable (rolled back).
    assert load_locations(db_session) == [records.MILLINOCKET_LOCATION]


def test_insert_record_enforces_referential_order(db_session: Session) -> None:
    # A channel before its location refuses on the FK.
    with pytest.raises(IntegrityError):
        insert_record(db_session, records.TEMPERATURE_CHANNEL)
    # A bundle whose channels are absent refuses with the reason.
    insert_record(db_session, records.MILLINOCKET_LOCATION)
    with pytest.raises(ValueError, match="no canonical record"):
        insert_record(db_session, records.MILLINOCKET_NWS_HOURLY_BUNDLE)


def test_insert_bundle_requires_embedded_agreement(db_session: Session) -> None:
    records.seed(db_session)
    payload = records.MILLINOCKET_NWS_HOURLY_BUNDLE.to_dict()
    payload["Name"] = "us.me.millinocket.forecast.nws.hourly2"
    payload["Id"] = "5b8f0f5e-6d0a-4b64-9c40-1c4c2f0a9e77"
    payload["TempForecastChannel"]["SourceLocator"] = "car.61.115"
    drifted = default_codec.from_dict(payload, expect=WeatherForecastBundleGt)
    with pytest.raises(ValueError, match="disagrees with the canonical record"):
        insert_record(db_session, drifted)


def test_forecast_store_keeps_send_distinct_messages(db_session: Session) -> None:
    records.seed(db_session)
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
    records.seed(db_session)
    observation = WeatherObservation(
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
