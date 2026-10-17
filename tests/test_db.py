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
    load_seasonal_templates,
    load_stations,
    load_weather_channels,
    save_last_observation,
    save_product,
    store_forecast,
)
from gwwf.nws import HourlyForecastProduct
from gwwf.open_meteo import IrradianceHourlyProduct
from gwwf.sema.codec import default_codec
from gwwf.sema.enums import Quantity, WeatherForecastFidelity
from gwwf.sema.types import (
    WeatherForecast,
    WeatherForecastBundleGt,
    WeatherForecastSeries,
    WeatherObservation,
    WeatherObservationReading,
)

METHOD = "api.weather.gov.gridpoint.hourly"
LOCATOR = "car.60.114"
BUNDLE = records.MILLINOCKET_RACCOON_BUNDLE


def forecast_message(
    source_updated: str, first_slice: str, created_ms: int
) -> WeatherForecast:
    temperature, wind, irradiance = BUNDLE.binding_list
    return WeatherForecast(
        bundle_name=BUNDLE.name,
        message_created_ms=created_ms,
        first_slice_start=first_slice,
        series_list=[
            WeatherForecastSeries(
                channel_name=temperature.forecast_channel.name,
                quantity=Quantity.Temperature,
                values=[700] * 48,
                fidelity=WeatherForecastFidelity.Live,
                source_updated_time=source_updated,
            ),
            WeatherForecastSeries(
                channel_name=wind.forecast_channel.name,
                quantity=Quantity.WindSpeed,
                values=[40] * 48,
                fidelity=WeatherForecastFidelity.Live,
                source_updated_time=source_updated,
            ),
            WeatherForecastSeries(
                channel_name=irradiance.forecast_channel.name,
                quantity=Quantity.Irradiance,
                values=[120] * 48,
                fidelity=WeatherForecastFidelity.Stored,
                source_updated_time="2026-10-18T06:00:00Z",
            ),
        ],
    )


def test_records_round_trip_as_equal_records(db_session: Session) -> None:
    records.seed(db_session)
    assert load_locations(db_session) == [records.MILLINOCKET_LOCATION]
    assert load_stations(db_session) == records.STATIONS
    assert load_weather_channels(db_session) == records.OBSERVATION_CHANNELS
    assert load_forecast_channels(db_session) == records.FORECAST_CHANNELS
    # Bundles reconstruct FROM the canonical rows — axioms fire on load.
    assert load_bundles(db_session) == records.FORECAST_BUNDLES
    assert load_seasonal_templates(db_session) == records.SEASONAL_TEMPLATES


def test_insert_record_is_insert_only(db_session: Session) -> None:
    records.seed(db_session)
    # Records are durable identities: a second create of the same
    # record refuses, never upserts.
    with pytest.raises(IntegrityError):
        insert_record(db_session, records.MILLINOCKET_LOCATION)
    # ... and the refusal leaves the session usable (rolled back).
    assert load_locations(db_session) == [records.MILLINOCKET_LOCATION]


def test_insert_record_enforces_referential_order(db_session: Session) -> None:
    # A channel before its station refuses on the FK.
    with pytest.raises(IntegrityError):
        insert_record(db_session, records.TEMPERATURE_CHANNEL)
    # So does the location's seasonal template.
    with pytest.raises(IntegrityError):
        insert_record(db_session, records.MILLINOCKET_SEASONAL_TEMPLATE)
    # A bundle whose channels are absent refuses with the reason.
    insert_record(db_session, records.MILLINOCKET_LOCATION)
    with pytest.raises(ValueError, match="no canonical record"):
        insert_record(db_session, BUNDLE)


def test_insert_bundle_requires_embedded_agreement(db_session: Session) -> None:
    records.seed(db_session)
    payload = BUNDLE.to_dict()
    payload["Name"] = "us.me.millinocket.raccoon2"
    payload["Id"] = "5b8f0f5e-6d0a-4b64-9c40-1c4c2f0a9e77"
    payload["BindingList"][2]["ForecastChannel"]["SourceLocator"] = "45.0,-68.0"
    drifted = default_codec.from_dict(payload, expect=WeatherForecastBundleGt)
    with pytest.raises(ValueError, match="disagrees with the canonical record"):
        insert_record(db_session, drifted)


def test_forecast_store_keeps_send_distinct_messages(db_session: Session) -> None:
    records.seed(db_session)
    same_window = forecast_message(
        "2026-10-18T09:09:03Z", "2026-10-18T14:00:00Z", 1792080000000
    )
    resend = forecast_message(
        "2026-10-18T09:09:03Z", "2026-10-18T14:00:00Z", 1792080060000
    )
    slid_window = forecast_message(
        "2026-10-18T09:09:03Z", "2026-10-18T15:00:00Z", 1792083600000
    )
    new_revision = forecast_message(
        "2026-10-18T16:33:26Z", "2026-10-18T15:00:00Z", 1792087200000
    )
    for message in (same_window, resend, slid_window, new_revision):
        store_forecast(db_session, message)
    count = db_session.scalar(
        select(func.count())
        .select_from(ForecastSql)
        .where(ForecastSql.bundle_name == BUNDLE.name)
    )
    # resend of an identical (revisions, window) no-ops; the rest are rows
    assert count == 3
    assert latest_forecast(db_session, BUNDLE.name) == new_revision
    assert latest_forecast(db_session, "no.such.bundle") is None
    # The fidelity column is the lowest rung among the series.
    assert {
        row.fidelity
        for row in db_session.scalars(
            select(ForecastSql).where(ForecastSql.bundle_name == BUNDLE.name)
        )
    } == {WeatherForecastFidelity.Stored}


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
    assert (
        load_product(
            db_session,
            method=METHOD,
            source_locator=LOCATOR,
            kind=HourlyForecastProduct,
        )
        == product
    )
    assert (
        load_product(
            db_session,
            method=METHOD,
            source_locator="elsewhere",
            kind=HourlyForecastProduct,
        )
        is None
    )
    irradiance = IrradianceHourlyProduct(
        update_time="2026-10-16T06:00:00Z",
        generated_at="2026-10-16T11:24:26Z",
        first_slice_start="2026-10-15T23:00:00Z",
        period_s=3600,
        irradiance_w_m2=[0] * 12 + [23, 41, 31] + [0] * 105,
    )
    channel = records.IRRADIANCE_FORECAST_OPENMETEO_HOURLY_CHANNEL
    save_product(
        db_session,
        method=channel.method,
        source_locator=channel.source_locator or "",
        product=irradiance,
    )
    assert (
        load_product(
            db_session,
            method=channel.method,
            source_locator=channel.source_locator or "",
            kind=IrradianceHourlyProduct,
        )
        == irradiance
    )


def test_last_observation_round_trips(db_session: Session) -> None:
    records.seed(db_session)
    observation = WeatherObservation(
        station_alias=records.KMLT,
        observation_time="2026-10-18T17:55:00Z",
        interpolated=False,
        reading_list=[
            WeatherObservationReading(
                channel_name=records.TEMPERATURE_CHANNEL.name,
                quantity=Quantity.Temperature,
                value=727,
            )
        ],
    )
    save_last_observation(
        db_session, observation=observation, published_slot=1792000000
    )
    stored = load_last_observation(db_session, records.KMLT)
    assert stored is not None
    assert stored.observation == observation
    assert stored.published_slot == 1792000000
    assert load_last_observation(db_session, "nosuchstation") is None
