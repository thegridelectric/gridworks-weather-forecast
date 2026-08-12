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

from gwwf.sema.enums import Gw1Quantity, Gw1Unit, GwWeatherForecastFidelity


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
    icao_id: Mapped[str | None] = mapped_column(String, nullable=True)
    wban_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ghcn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    coop_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WeatherChannelSql(Base):
    """One gw.weather.channel.gt record — an observed series."""

    __tablename__ = "weather_channels"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String)
    quantity: Mapped[Gw1Quantity] = mapped_column(
        Enum(Gw1Quantity, name="gw1_quantity")
    )
    unit: Mapped[Gw1Unit] = mapped_column(Enum(Gw1Unit, name="gw1_unit"))
    location_alias: Mapped[str] = mapped_column(String, ForeignKey("locations.alias"))
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
    """One gw.weather.forecast.bundle.gt record, decomposed.

    Channel columns are FKs to the canonical channel rows; loading a
    bundle reconstructs the sema instance FROM those rows, so the
    word's axioms (and copy-agreement with the canonical records) fire
    on every load — the service-side half of the bundle contract.
    """

    __tablename__ = "bundles"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String)
    location_alias: Mapped[str] = mapped_column(String, ForeignKey("locations.alias"))
    temp_forecast_channel_name: Mapped[str] = mapped_column(
        String, ForeignKey("forecast_channels.name")
    )
    temp_observation_channel_name: Mapped[str] = mapped_column(
        String, ForeignKey("weather_channels.name")
    )
    wind_speed_forecast_channel_name: Mapped[str] = mapped_column(
        String, ForeignKey("forecast_channels.name")
    )
    wind_speed_observation_channel_name: Mapped[str] = mapped_column(
        String, ForeignKey("weather_channels.name")
    )
    emit_period_s: Mapped[int] = mapped_column(Integer)
    emit_offset_s: Mapped[int] = mapped_column(Integer)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ForecastSql(Base):
    """One sent gw.weather.forecast message — wire-form payload.

    Surrogate uuid key; uniqueness on (bundle, SourceUpdatedTime,
    FirstSliceStart) so re-sends of an identical window under a held
    source revision no-op while every send-distinct message is a row.
    """

    __tablename__ = "forecasts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    bundle_name: Mapped[str] = mapped_column(String, ForeignKey("bundles.name"))
    source_updated_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_slice_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message_created_ms: Mapped[int] = mapped_column(BigInteger)
    fidelity: Mapped[GwWeatherForecastFidelity] = mapped_column(
        Enum(GwWeatherForecastFidelity, name="gw_weather_forecast_fidelity")
    )
    # The full gw.weather.forecast message, wire form (to_dict).
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint(
            "bundle_name",
            "source_updated_time",
            "first_slice_start",
            name="uq_forecasts_bundle_revision_window",
        ),
    )


class SourceProductSql(Base):
    """Durable mirror of the scheduler's stored-rung source product.

    Keyed by acquisition identity (method + source locator). `series`
    holds the product's value lists keyed by series name (an interior
    structure with no sema word — `HourlyForecastProduct` in `nws.py`
    is its type; note carried there); `store.py` maps row ↔ product.
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
    """Per-location last real observation + the slot it published at.

    Makes latest-only and recovery interpolation survive a restart.
    `payload` is the wire-form gw.weather.observation message.
    """

    __tablename__ = "last_observations"

    location_alias: Mapped[str] = mapped_column(
        String, ForeignKey("locations.alias"), primary_key=True
    )
    observation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    published_slot: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
