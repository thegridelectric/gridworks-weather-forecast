"""Data access for the gridworks-weather DB — the sole-accessor layer.

Every read returns codec-validated sema instances (or the typed
interior records they mirror), never raw rows; every write starts
from a validated instance. Wire-form dicts appear only in payload
columns.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NamedTuple, Protocol, TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

import uuid

from gwwf.db.models import (
    BundleBindingSql,
    BundleSql,
    ForecastChannelSql,
    ForecastSql,
    LastObservationSql,
    LocationSql,
    SeasonalTemplateSql,
    SourceProductSql,
    StationSql,
    WeatherChannelSql,
)
from gwwf.fidelity import lowest_fidelity
from gwwf.record_broadcast import RecordWord
from gwwf.sema.codec import default_codec
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecast,
    WeatherForecastBinding,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherLocationGt,
    WeatherObservation,
    WeatherSeasonalTemplateGt,
    WeatherStationGt,
)


class CachedProduct(Protocol):
    """What the source-product cache keeps of an adapter's product: the
    three stamps as columns and every list field as a named series.
    The adapters' NamedTuple products satisfy this structurally."""

    @property
    def update_time(self) -> str: ...
    @property
    def generated_at(self) -> str: ...
    @property
    def first_slice_start(self) -> str: ...
    @property
    def period_s(self) -> int: ...
    def _asdict(self) -> dict[str, Any]: ...


P = TypeVar("P", bound=CachedProduct)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------
# Record creation + loads (the canonical .gt seed)
# ----------------------------------------------------------------------


def _record_rows(record: RecordWord) -> list[Any]:
    if isinstance(record, WeatherLocationGt):
        return [
            LocationSql(
                id=record.id,
                alias=record.alias,
                latitude_microdegrees=record.latitude_microdegrees,
                longitude_microdegrees=record.longitude_microdegrees,
                timezone=record.timezone,
            )
        ]
    if isinstance(record, WeatherStationGt):
        return [
            StationSql(
                id=record.id,
                alias=record.alias,
                display_name=record.display_name,
                latitude_microdegrees=record.latitude_microdegrees,
                longitude_microdegrees=record.longitude_microdegrees,
                icao_id=record.icao_id,
                ghcn_id=record.ghcn_id,
                wunderground_id=record.wunderground_id,
                start=_dt(record.start),
            )
        ]
    if isinstance(record, WeatherChannelGt):
        return [
            WeatherChannelSql(
                id=record.id,
                name=record.name,
                display_name=record.display_name,
                quantity=record.quantity,
                unit=record.unit,
                station_alias=record.station_alias,
                emit_period_s=record.emit_period_s,
                emit_offset_s=record.emit_offset_s,
                start=_dt(record.start),
            )
        ]
    if isinstance(record, WeatherForecastChannelGt):
        return [
            ForecastChannelSql(
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
        ]
    if isinstance(record, WeatherSeasonalTemplateGt):
        return [
            SeasonalTemplateSql(
                id=record.id,
                location_alias=record.location_alias,
                temp_by_month=record.temp_by_month,
                start=_dt(record.start),
            )
        ]
    rows: list[Any] = [
        BundleSql(
            id=record.id,
            name=record.name,
            display_name=record.display_name,
            location_alias=record.location_alias,
            emit_period_s=record.emit_period_s,
            emit_offset_s=record.emit_offset_s,
            start=_dt(record.start),
        )
    ]
    rows += [
        BundleBindingSql(
            bundle_name=record.name,
            position=position,
            forecast_channel_name=binding.forecast_channel.name,
            observation_channel_name=binding.observation_channel.name,
        )
        for position, binding in enumerate(record.binding_list)
    ]
    return rows


def _require_embedded_agreement(
    session: Session, bundle: WeatherForecastBundleGt
) -> None:
    """The service-side half of the bundle contract, at create time:
    the embedded channel copies must equal the canonical records."""
    canonical: dict[str, WeatherChannelGt | WeatherForecastChannelGt] = {
        c.name: c for c in load_weather_channels(session)
    }
    canonical.update({c.name: c for c in load_forecast_channels(session)})
    embedded_channels: list[WeatherChannelGt | WeatherForecastChannelGt] = []
    for binding in bundle.binding_list:
        embedded_channels += [binding.forecast_channel, binding.observation_channel]
    for embedded in embedded_channels:
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
    order: location, station → channels → bundle, location → template)
    raises IntegrityError; a bundle whose embedded channel copies
    disagree with the canonical rows raises ValueError before anything
    is written."""
    if isinstance(record, WeatherForecastBundleGt):
        _require_embedded_agreement(session, record)
    head, *rest = _record_rows(record)
    try:
        session.add(head)
        if rest:
            session.flush()  # the bundle row before the bindings that key it
            session.add_all(rest)
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
            id=row.id,
        )
        for row in session.scalars(select(LocationSql).order_by(LocationSql.alias))
    ]


def load_stations(session: Session) -> list[WeatherStationGt]:
    return [
        WeatherStationGt(
            alias=row.alias,
            display_name=row.display_name,
            latitude_microdegrees=row.latitude_microdegrees,
            longitude_microdegrees=row.longitude_microdegrees,
            icao_id=row.icao_id,
            ghcn_id=row.ghcn_id,
            wunderground_id=row.wunderground_id,
            start=_iso(row.start),
            id=row.id,
        )
        for row in session.scalars(select(StationSql).order_by(StationSql.alias))
    ]


def load_weather_channels(session: Session) -> list[WeatherChannelGt]:
    return [
        WeatherChannelGt(
            name=row.name,
            display_name=row.display_name,
            quantity=row.quantity,
            unit=row.unit,
            station_alias=row.station_alias,
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


def load_seasonal_templates(session: Session) -> list[WeatherSeasonalTemplateGt]:
    return [
        WeatherSeasonalTemplateGt(
            location_alias=row.location_alias,
            temp_by_month=row.temp_by_month,
            start=_iso(row.start),
            id=row.id,
        )
        for row in session.scalars(
            select(SeasonalTemplateSql).order_by(
                SeasonalTemplateSql.location_alias, SeasonalTemplateSql.start
            )
        )
    ]


def active_seasonal_template(
    session: Session, location_alias: str, now_s: int
) -> WeatherSeasonalTemplateGt | None:
    """The location's template with the latest Start at or before
    `now_s`, or None when the location has none yet."""
    active = [
        t
        for t in load_seasonal_templates(session)
        if t.location_alias == location_alias and _dt(t.start).timestamp() <= now_s
    ]
    return max(active, key=lambda t: t.start) if active else None


# ----------------------------------------------------------------------
# Bundle loads + the sent-forecast store
# ----------------------------------------------------------------------


def load_bundles(session: Session) -> list[WeatherForecastBundleGt]:
    """Reconstruct each bundle FROM the canonical channel rows — the
    word's axioms fire on construction, which IS the boot-time check
    that the decomposed record still holds together."""
    forecast_by_name = {c.name: c for c in load_forecast_channels(session)}
    observation_by_name = {c.name: c for c in load_weather_channels(session)}
    bindings_by_bundle: dict[str, list[WeatherForecastBinding]] = {}
    for binding_row in session.scalars(
        select(BundleBindingSql).order_by(
            BundleBindingSql.bundle_name, BundleBindingSql.position
        )
    ):
        bindings_by_bundle.setdefault(binding_row.bundle_name, []).append(
            WeatherForecastBinding(
                forecast_channel=forecast_by_name[binding_row.forecast_channel_name],
                observation_channel=observation_by_name[
                    binding_row.observation_channel_name
                ],
            )
        )
    return [
        WeatherForecastBundleGt(
            name=row.name,
            display_name=row.display_name,
            location_alias=row.location_alias,
            binding_list=bindings_by_bundle.get(row.name, []),
            emit_period_s=row.emit_period_s,
            emit_offset_s=row.emit_offset_s,
            start=_iso(row.start),
            id=row.id,
        )
        for row in session.scalars(select(BundleSql).order_by(BundleSql.name))
    ]


def source_revision(message: WeatherForecast) -> str:
    """The source revisions behind a message, one token per series in
    series order."""
    return "|".join(
        f"{series.channel_name}@{series.source_updated_time}"
        for series in message.series_list
    )


def store_forecast(session: Session, message: WeatherForecast) -> None:
    """One row per send-distinct message; re-sends of an identical
    window under held source revisions no-op."""
    statement = (
        pg_insert(ForecastSql)
        .values(
            id=str(uuid.uuid4()),
            bundle_name=message.bundle_name,
            source_revision=source_revision(message),
            first_slice_start=_dt(message.first_slice_start),
            message_created_ms=message.message_created_ms,
            fidelity=lowest_fidelity(message),
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
    product: CachedProduct,
) -> None:
    """The product's list fields are the row's series, keyed by their
    own names; the four stamps are columns."""
    values = dict(
        method=method,
        source_locator=source_locator,
        update_time=_dt(product.update_time),
        generated_at=product.generated_at,
        first_slice_start=_dt(product.first_slice_start),
        period_s=product.period_s,
        series={
            name: value
            for name, value in product._asdict().items()
            if isinstance(value, list)
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
    session: Session, *, method: str, source_locator: str, kind: Callable[..., P]
) -> P | None:
    """The cached product of one acquisition as ``kind`` (the product
    type the method's adapter produces)."""
    row = session.get(SourceProductSql, (method, source_locator))
    if row is None:
        return None
    return kind(
        update_time=_iso(row.update_time),
        generated_at=row.generated_at,
        first_slice_start=_iso(row.first_slice_start),
        period_s=row.period_s,
        **row.series,
    )


# ----------------------------------------------------------------------
# Last-observation state (latest-only + recovery survive restarts)
# ----------------------------------------------------------------------


class StoredObservation(NamedTuple):
    """The last real observation for a station + the slot it published
    at (the replay bound). NOTE: interior record, no sema word — the
    observation itself is the word; the pairing is scheduler state.
    """

    observation: WeatherObservation
    published_slot: int


def save_last_observation(
    session: Session,
    *,
    observation: WeatherObservation,
    published_slot: int,
) -> None:
    values = dict(
        station_alias=observation.station_alias,
        observation_time=_dt(observation.observation_time),
        payload=observation.to_dict(),
        published_slot=published_slot,
    )
    statement = pg_insert(LastObservationSql).values(**values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[LastObservationSql.station_alias], set_=values
        )
    )
    session.commit()


def load_last_observation(
    session: Session, station_alias: str
) -> StoredObservation | None:
    row = session.get(LastObservationSql, station_alias)
    if row is None:
        return None
    return StoredObservation(
        observation=default_codec.from_dict(row.payload, expect=WeatherObservation),
        published_slot=row.published_slot,
    )
