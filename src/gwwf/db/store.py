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
from gwwf.record_broadcast import RecordWord
from gwwf.sema.codec import default_codec
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecast,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherLocationGt,
    WeatherObservation,
)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------
# Record creation + loads (the canonical .gt seed)
# ----------------------------------------------------------------------


def _record_row(
    record: RecordWord,
) -> LocationSql | WeatherChannelSql | ForecastChannelSql | BundleSql:
    if isinstance(record, WeatherLocationGt):
        return LocationSql(
            id=record.id,
            alias=record.alias,
            latitude_microdegrees=record.latitude_microdegrees,
            longitude_microdegrees=record.longitude_microdegrees,
            timezone=record.timezone,
            icao_id=record.icao_id,
            wban_id=record.wban_id,
            ghcn_id=record.ghcn_id,
            coop_id=record.coop_id,
        )
    if isinstance(record, WeatherChannelGt):
        return WeatherChannelSql(
            id=record.id,
            name=record.name,
            display_name=record.display_name,
            quantity=record.quantity,
            unit=record.unit,
            location_alias=record.location_alias,
            emit_period_s=record.emit_period_s,
            emit_offset_s=record.emit_offset_s,
            start=_dt(record.start),
        )
    if isinstance(record, WeatherForecastChannelGt):
        return ForecastChannelSql(
            id=record.id,
            name=record.name,
            target_channel_name=record.target_channel_name,
            forecaster=record.forecaster,
            method=record.method,
            source_locator=record.source_locator,
            total_slices=record.total_slices,
            slice_duration_s_list=record.slice_duration_s_list,
            forecast_duration_minutes=record.forecast_duration_minutes,
            start=_dt(record.start),
        )
    return BundleSql(
        id=record.id,
        name=record.name,
        display_name=record.display_name,
        location_alias=record.location_alias,
        temp_forecast_channel_name=record.temp_forecast_channel.name,
        temp_observation_channel_name=record.temp_observation_channel.name,
        wind_speed_forecast_channel_name=record.wind_speed_forecast_channel.name,
        wind_speed_observation_channel_name=(
            record.wind_speed_observation_channel.name
        ),
        emit_period_s=record.emit_period_s,
        emit_offset_s=record.emit_offset_s,
        start=_dt(record.start),
    )


def _require_embedded_agreement(
    session: Session, bundle: WeatherForecastBundleGt
) -> None:
    """The service-side half of the bundle contract, at create time:
    the embedded channel copies must equal the canonical records."""
    canonical: dict[str, WeatherChannelGt | WeatherForecastChannelGt] = {
        c.name: c for c in load_weather_channels(session)
    }
    canonical.update({c.name: c for c in load_forecast_channels(session)})
    for embedded in (
        bundle.temp_forecast_channel,
        bundle.temp_observation_channel,
        bundle.wind_speed_forecast_channel,
        bundle.wind_speed_observation_channel,
    ):
        stored = canonical.get(embedded.name)
        if stored is None:
            raise ValueError(
                f"{embedded.name}: no canonical record — create the channel first"
            )
        if stored != embedded:
            raise ValueError(
                f"{embedded.name}: embedded copy disagrees with the canonical record"
            )


def insert_record(session: Session, record: RecordWord) -> None:
    """Insert-only create — records are durable identities, never
    upserted. A duplicate id/name or a missing reference (referential
    order: location → channels → bundle) raises IntegrityError; a
    bundle whose embedded channel copies disagree with the canonical
    rows raises ValueError before anything is written."""
    if isinstance(record, WeatherForecastBundleGt):
        _require_embedded_agreement(session, record)
    session.add(_record_row(record))
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def load_locations(session: Session) -> list[WeatherLocationGt]:
    return [
        WeatherLocationGt(
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


def load_weather_channels(session: Session) -> list[WeatherChannelGt]:
    return [
        WeatherChannelGt(
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


def load_forecast_channels(session: Session) -> list[WeatherForecastChannelGt]:
    return [
        WeatherForecastChannelGt(
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


def load_bundles(session: Session) -> list[WeatherForecastBundleGt]:
    """Reconstruct each bundle FROM the canonical channel rows — the
    word's axioms fire on construction, which IS the boot-time check
    that the decomposed record still holds together."""
    forecast_by_name = {c.name: c for c in load_forecast_channels(session)}
    observation_by_name = {c.name: c for c in load_weather_channels(session)}
    return [
        WeatherForecastBundleGt(
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


def store_forecast(session: Session, message: WeatherForecast) -> None:
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


def latest_forecast(session: Session, bundle_name: str) -> WeatherForecast | None:
    row = session.scalars(
        select(ForecastSql)
        .where(ForecastSql.bundle_name == bundle_name)
        .order_by(ForecastSql.message_created_ms.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return default_codec.from_dict(row.payload, expect=WeatherForecast)


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

    observation: WeatherObservation
    published_slot: int


def save_last_observation(
    session: Session,
    *,
    location_alias: str,
    observation: WeatherObservation,
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
        observation=default_codec.from_dict(row.payload, expect=WeatherObservation),
        published_slot=row.published_slot,
    )
