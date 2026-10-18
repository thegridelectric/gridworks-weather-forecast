"""SQLAlchemy models for the gridworks-weather DB.

gwwf's OWN database — gwwf is the sole accessor; every other consumer
goes through the broadcasts or the API. Record rows mirror their sema
GT record's fields column-for-column (enums as SQL enums); rows load
back AS codec-validated record instances in `store.py`, never as raw
tuples. Message payload columns hold wire-form dicts (PascalCase, via
`to_dict`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from gwwf.sema.enums import Quantity, Unit, WeatherForecastFidelity


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class LocationSql(Base):
    """One gw.weather.location.gt record — the place anchor."""

    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    alias: Mapped[str] = mapped_column(String, unique=True, index=True)
    latitude_microdegrees: Mapped[int] = mapped_column(Integer)
    longitude_microdegrees: Mapped[int] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class StationSql(Base):
    """One gw.weather.station.gt record — an observing instrument."""

    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    alias: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String)
    latitude_microdegrees: Mapped[int] = mapped_column(Integer)
    longitude_microdegrees: Mapped[int] = mapped_column(Integer)
    icao_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ghcn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    wunderground_id: Mapped[str | None] = mapped_column(String, nullable=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WeatherChannelSql(Base):
    """One gw.weather.channel.gt record — an observed series on a station."""

    __tablename__ = "weather_channels"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String)
    quantity: Mapped[Quantity] = mapped_column(Enum(Quantity, name="quantity"))
    unit: Mapped[Unit] = mapped_column(Enum(Unit, name="unit"))
    station_alias: Mapped[str] = mapped_column(String, ForeignKey("stations.alias"))
    emit_period_s: Mapped[int] = mapped_column(Integer)
    emit_offset_s: Mapped[int] = mapped_column(Integer)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ForecastChannelSql(Base):
    """One gw.weather.forecast.channel.gt record — a named predictor+shape."""

    __tablename__ = "forecast_channels"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    target_channel_name: Mapped[str] = mapped_column(
        String, ForeignKey("weather_channels.name")
    )
    forecaster: Mapped[str] = mapped_column(String)
    method: Mapped[str] = mapped_column(String)
    source_locator: Mapped[str | None] = mapped_column(String, nullable=True)
    total_slices: Mapped[int] = mapped_column(Integer)
    slice_duration_s_list: Mapped[list[int]] = mapped_column(JSONB)
    forecast_duration_minutes: Mapped[int] = mapped_column(Integer)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BundleSql(Base):
    """One gw.weather.forecast.bundle.gt record, decomposed: the bundle's
    own fields here, its bindings in `bundle_bindings`. Loading a bundle
    reconstructs the sema instance FROM the canonical channel rows, so
    the word's axioms (and copy-agreement with the canonical records)
    fire on every load — the service-side half of the bundle contract.
    """

    __tablename__ = "bundles"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String)
    location_alias: Mapped[str] = mapped_column(String, ForeignKey("locations.alias"))
    emit_period_s: Mapped[int] = mapped_column(Integer)
    emit_offset_s: Mapped[int] = mapped_column(Integer)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BundleBindingSql(Base):
    """One gw.weather.forecast.binding of a bundle: the forecast channel
    serving one series and the observed channel it targets, in the
    bundle's binding order."""

    __tablename__ = "bundle_bindings"

    bundle_name: Mapped[str] = mapped_column(
        String, ForeignKey("bundles.name"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    forecast_channel_name: Mapped[str] = mapped_column(
        String, ForeignKey("forecast_channels.name")
    )
    observation_channel_name: Mapped[str] = mapped_column(
        String, ForeignKey("weather_channels.name")
    )

    __table_args__ = (
        UniqueConstraint(
            "bundle_name",
            "forecast_channel_name",
            name="uq_bundle_bindings_bundle_forecast_channel",
        ),
    )


class SeasonalTemplateSql(Base):
    """One gw.weather.seasonal.template.gt record: the design-cold
    temperature of each month at a location, the forecast ladder's
    last rung. A later record for the same location supersedes by
    Start."""

    __tablename__ = "seasonal_templates"
    __table_args__ = (UniqueConstraint("location_alias", "start"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    location_alias: Mapped[str] = mapped_column(String, ForeignKey("locations.alias"))
    temp_by_month: Mapped[list[int]] = mapped_column(JSONB)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ForecastSql(Base):
    """One sent gw.weather.forecast message — wire-form payload.

    The sent-forecast archive is keyed by the bundle name a message
    went out under, a plain column: the archive is history, not a
    reference into the record set. Surrogate
    uuid key; uniqueness on (bundle, source revision, FirstSliceStart)
    so re-sends of an identical window under held source revisions
    no-op while every send-distinct message is a row. `source_revision`
    joins every series' channel and source stamp in series order; the
    stamps themselves are in the payload. `fidelity` is the lowest rung
    among the message's series.
    """

    __tablename__ = "forecasts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    bundle_name: Mapped[str] = mapped_column(String, index=True)
    source_revision: Mapped[str] = mapped_column(String)
    first_slice_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message_created_ms: Mapped[int] = mapped_column(BigInteger)
    fidelity: Mapped[WeatherForecastFidelity] = mapped_column(
        Enum(WeatherForecastFidelity, name="weather_forecast_fidelity")
    )
    # The full gw.weather.forecast message, wire form (to_dict).
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint(
            "bundle_name",
            "source_revision",
            "first_slice_start",
            name="uq_forecasts_bundle_revision_window",
        ),
    )


class SourceProductSql(Base):
    """Durable mirror of the scheduler's stored-rung source product.

    Keyed by acquisition identity (method + source locator). `series`
    holds the product's value lists keyed by series name (an interior
    structure with no sema word — `HourlyForecastProduct` in `nws.py`
    and `IrradianceHourlyProduct` in `open_meteo.py` are its types,
    told apart by the method); `store.py` maps row ↔ product.
    The extended horizon (broadcast + stored margin) lives here, in
    source terms, because a non-uniform channel grid has no defined
    extension of its own.
    """

    __tablename__ = "source_products"

    method: Mapped[str] = mapped_column(String, primary_key=True)
    source_locator: Mapped[str] = mapped_column(String, primary_key=True)
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[str] = mapped_column(String)
    first_slice_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_s: Mapped[int] = mapped_column(Integer)
    series: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LastObservationSql(Base):
    """Per-station last real observation + the slot it published at.

    Makes latest-only and recovery interpolation survive a restart.
    `payload` is the wire-form gw.weather.observation message.
    """

    __tablename__ = "last_observations"

    station_alias: Mapped[str] = mapped_column(
        String, ForeignKey("stations.alias"), primary_key=True
    )
    observation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    published_slot: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
