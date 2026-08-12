"""Data access for the gridworks-weather DB — the sole-accessor layer.

Every read returns codec-validated sema instances (or the typed
interior records they mirror), never raw rows; every write starts
from a validated instance. Wire-form dicts appear only in payload
columns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

import uuid

from gwwf import records
from gwwf.db.models import (
    BundleSql,
    ForecastChannelSql,
    ForecastSql,
    LastObservationSql,
    LocationSql,
    SourceProductSql,
    WeatherChannelSql,
)
from gwwf.nws import HourlyForecastProduct
from gwwf.sema.codec import default_codec
from gwwf.sema.types import (
    GwWeatherChannelGt,
    GwWeatherForecast,
    GwWeatherForecastBundleGt,
    GwWeatherForecastChannelGt,
    GwWeatherLocationGt,
    GwWeatherObservation,
)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------
# Record seed + loads (the canonical .gt seed)
# ----------------------------------------------------------------------


def seed_records(
    session: Session,
    *,
    locations: list[GwWeatherLocationGt] | None = None,
    observation_channels: list[GwWeatherChannelGt] | None = None,
    forecast_channels: list[GwWeatherForecastChannelGt] | None = None,
    bundles: list[GwWeatherForecastBundleGt] | None = None,
) -> None:
    """Upsert record instances into the record tables (default: the
    in-code standup seed, `gwwf.records`).

    Idempotent by id; the given seed is authoritative until a
    registration surface exists, so an existing row is overwritten.
    """
    if locations is None:
        locations = [records.MILLINOCKET_LOCATION]
    if observation_channels is None:
        observation_channels = records.OBSERVATION_CHANNELS
    if forecast_channels is None:
        forecast_channels = records.FORECAST_CHANNELS
    if bundles is None:
        bundles = records.FORECAST_BUNDLES
    for location in locations:
        values = dict(
            id=location.id,
            alias=location.alias,
            latitude_microdegrees=location.latitude_microdegrees,
            longitude_microdegrees=location.longitude_microdegrees,
            timezone=location.timezone,
            icao_id=location.icao_id,
            wban_id=location.wban_id,
            ghcn_id=location.ghcn_id,
            coop_id=location.coop_id,
        )
        statement = pg_insert(LocationSql).values(**values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[LocationSql.id], set_=values
            )
        )
    for channel in observation_channels:
        values = dict(
            id=channel.id,
            name=channel.name,
            display_name=channel.display_name,
            quantity=channel.quantity,
            unit=channel.unit,
            location_alias=channel.location_alias,
            emit_period_s=channel.emit_period_s,
            emit_offset_s=channel.emit_offset_s,
            start=_dt(channel.start),
        )
        statement = pg_insert(WeatherChannelSql).values(**values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[WeatherChannelSql.id], set_=values
            )
        )
    for forecast_channel in forecast_channels:
        values = dict(
            id=forecast_channel.id,
            name=forecast_channel.name,
            target_channel_name=forecast_channel.target_channel_name,
            forecaster=forecast_channel.forecaster,
            method=forecast_channel.method,
            source_locator=forecast_channel.source_locator,
            total_slices=forecast_channel.total_slices,
            slice_duration_s_list=forecast_channel.slice_duration_s_list,
            forecast_duration_minutes=forecast_channel.forecast_duration_minutes,
            start=_dt(forecast_channel.start),
        )
        statement = pg_insert(ForecastChannelSql).values(**values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[ForecastChannelSql.id], set_=values
            )
        )
    for bundle in bundles:
        values = dict(
            id=bundle.id,
            name=bundle.name,
            display_name=bundle.display_name,
            location_alias=bundle.location_alias,
            temp_forecast_channel_name=bundle.temp_forecast_channel.name,
            temp_observation_channel_name=bundle.temp_observation_channel.name,
            wind_speed_forecast_channel_name=bundle.wind_speed_forecast_channel.name,
            wind_speed_observation_channel_name=(
                bundle.wind_speed_observation_channel.name
            ),
            emit_period_s=bundle.emit_period_s,
            emit_offset_s=bundle.emit_offset_s,
            start=_dt(bundle.start),
        )
        statement = pg_insert(BundleSql).values(**values)
        session.execute(
            statement.on_conflict_do_update(index_elements=[BundleSql.id], set_=values)
        )
    session.commit()


def load_locations(session: Session) -> list[GwWeatherLocationGt]:
    return [
        GwWeatherLocationGt(
            alias=row.alias,
            latitude_microdegrees=row.latitude_microdegrees,
            longitude_microdegrees=row.longitude_microdegrees,
            timezone=row.timezone,
            icao_id=row.icao_id,
            wban_id=row.wban_id,
            ghcn_id=row.ghcn_id,
            coop_id=row.coop_id,
            id=row.id,
        )
        for row in session.scalars(select(LocationSql).order_by(LocationSql.alias))
    ]


def load_weather_channels(session: Session) -> list[GwWeatherChannelGt]:
    return [
        GwWeatherChannelGt(
            name=row.name,
            display_name=row.display_name,
            quantity=row.quantity,
            unit=row.unit,
            location_alias=row.location_alias,
            emit_period_s=row.emit_period_s,
            emit_offset_s=row.emit_offset_s,
            start=_iso(row.start),
            id=row.id,
        )
        for row in session.scalars(
            select(WeatherChannelSql).order_by(WeatherChannelSql.name)
        )
    ]


def load_forecast_channels(session: Session) -> list[GwWeatherForecastChannelGt]:
    return [
        GwWeatherForecastChannelGt(
            name=row.name,
            target_channel_name=row.target_channel_name,
            forecaster=row.forecaster,
            method=row.method,
            source_locator=row.source_locator,
            total_slices=row.total_slices,
            slice_duration_s_list=row.slice_duration_s_list,
            forecast_duration_minutes=row.forecast_duration_minutes,
            start=_iso(row.start),
            id=row.id,
        )
        for row in session.scalars(
            select(ForecastChannelSql).order_by(ForecastChannelSql.name)
        )
    ]


# ----------------------------------------------------------------------
# Bundle loads + the sent-forecast store
# ----------------------------------------------------------------------


def load_bundles(session: Session) -> list[GwWeatherForecastBundleGt]:
    """Reconstruct each bundle FROM the canonical channel rows — the
    word's axioms fire on construction, which IS the boot-time check
    that the decomposed record still holds together."""
    forecast_by_name = {c.name: c for c in load_forecast_channels(session)}
    observation_by_name = {c.name: c for c in load_weather_channels(session)}
    return [
        GwWeatherForecastBundleGt(
            name=row.name,
            display_name=row.display_name,
            location_alias=row.location_alias,
            temp_forecast_channel=forecast_by_name[row.temp_forecast_channel_name],
            temp_observation_channel=observation_by_name[
                row.temp_observation_channel_name
            ],
            wind_speed_forecast_channel=forecast_by_name[
                row.wind_speed_forecast_channel_name
            ],
            wind_speed_observation_channel=observation_by_name[
                row.wind_speed_observation_channel_name
            ],
            emit_period_s=row.emit_period_s,
            emit_offset_s=row.emit_offset_s,
            start=_iso(row.start),
            id=row.id,
        )
        for row in session.scalars(select(BundleSql).order_by(BundleSql.name))
    ]


def store_forecast(session: Session, message: GwWeatherForecast) -> None:
    """One row per send-distinct message; re-sends of an identical
    window under a held source revision no-op."""
    statement = (
        pg_insert(ForecastSql)
        .values(
            id=str(uuid.uuid4()),
            bundle_name=message.bundle_name,
            source_updated_time=_dt(message.source_updated_time),
            first_slice_start=_dt(message.first_slice_start),
            message_created_ms=message.message_created_ms,
            fidelity=message.fidelity,
            payload=message.to_dict(),
        )
        .on_conflict_do_nothing(constraint="uq_forecasts_bundle_revision_window")
    )
    session.execute(statement)
    session.commit()


def latest_forecast(session: Session, bundle_name: str) -> GwWeatherForecast | None:
    row = session.scalars(
        select(ForecastSql)
        .where(ForecastSql.bundle_name == bundle_name)
        .order_by(ForecastSql.message_created_ms.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return default_codec.from_dict(row.payload, expect=GwWeatherForecast)


# ----------------------------------------------------------------------
# Source-product cache (the stored rung, durable)
# ----------------------------------------------------------------------


def save_product(
    session: Session,
    *,
    method: str,
    source_locator: str,
    product: HourlyForecastProduct,
) -> None:
    values = dict(
        method=method,
        source_locator=source_locator,
        update_time=_dt(product.update_time),
        generated_at=product.generated_at,
        first_slice_start=_dt(product.first_slice_start),
        period_s=product.period_s,
        series={
            "temperature_f": product.temperature_f,
            "wind_speed_mph": product.wind_speed_mph,
        },
    )
    statement = pg_insert(SourceProductSql).values(**values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[SourceProductSql.method, SourceProductSql.source_locator],
            set_=values,
        )
    )
    session.commit()


def load_product(
    session: Session, *, method: str, source_locator: str
) -> HourlyForecastProduct | None:
    row = session.get(SourceProductSql, (method, source_locator))
    if row is None:
        return None
    return HourlyForecastProduct(
        update_time=_iso(row.update_time),
        generated_at=row.generated_at,
        first_slice_start=_iso(row.first_slice_start),
        period_s=row.period_s,
        temperature_f=row.series["temperature_f"],
        wind_speed_mph=row.series["wind_speed_mph"],
    )


# ----------------------------------------------------------------------
# Last-observation state (latest-only + recovery survive restarts)
# ----------------------------------------------------------------------


class StoredObservation(NamedTuple):
    """The last real observation for a location + the slot it published
    at (the replay bound). NOTE: interior record, no sema word — the
    observation itself is the word; the pairing is scheduler state.
    """

    observation: GwWeatherObservation
    published_slot: int


def save_last_observation(
    session: Session,
    *,
    location_alias: str,
    observation: GwWeatherObservation,
    published_slot: int,
) -> None:
    values = dict(
        location_alias=location_alias,
        observation_time=_dt(observation.observation_time),
        payload=observation.to_dict(),
        published_slot=published_slot,
    )
    statement = pg_insert(LastObservationSql).values(**values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[LastObservationSql.location_alias], set_=values
        )
    )
    session.commit()


def load_last_observation(
    session: Session, location_alias: str
) -> StoredObservation | None:
    row = session.get(LastObservationSql, location_alias)
    if row is None:
        return None
    return StoredObservation(
        observation=default_codec.from_dict(row.payload, expect=GwWeatherObservation),
        published_slot=row.published_slot,
    )
