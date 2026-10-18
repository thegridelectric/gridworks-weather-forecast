"""initial schema

Revision ID: 327a9a28bf52
Revises:
Create Date: 2026-10-18 12:55:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "327a9a28bf52"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """One table per record word (locations, stations, weather_channels,
    forecast_channels, bundles + bundle_bindings, seasonal_templates),
    the sent-forecast archive, the source-product cache and the
    per-station last observation. Enums mirror gw1.quantity 003,
    gw1.unit 003 and gw.weather.forecast.fidelity."""
    op.create_table(
        "forecasts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("bundle_name", sa.String(), nullable=False),
        sa.Column("source_revision", sa.String(), nullable=False),
        sa.Column("first_slice_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_created_ms", sa.BigInteger(), nullable=False),
        sa.Column(
            "fidelity",
            sa.Enum(
                "Unknown",
                "Live",
                "Stored",
                "SeasonalTemplate",
                name="weather_forecast_fidelity",
            ),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bundle_name",
            "source_revision",
            "first_slice_start",
            name="uq_forecasts_bundle_revision_window",
        ),
    )
    op.create_index(
        op.f("ix_forecasts_bundle_name"), "forecasts", ["bundle_name"], unique=False
    )
    op.create_table(
        "locations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("alias", sa.String(), nullable=False),
        sa.Column("latitude_microdegrees", sa.Integer(), nullable=False),
        sa.Column("longitude_microdegrees", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_locations_alias"), "locations", ["alias"], unique=True)
    op.create_table(
        "source_products",
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("source_locator", sa.String(), nullable=False),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.String(), nullable=False),
        sa.Column("first_slice_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_s", sa.Integer(), nullable=False),
        sa.Column("series", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("method", "source_locator"),
    )
    op.create_table(
        "stations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("alias", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("latitude_microdegrees", sa.Integer(), nullable=False),
        sa.Column("longitude_microdegrees", sa.Integer(), nullable=False),
        sa.Column("icao_id", sa.String(), nullable=True),
        sa.Column("ghcn_id", sa.String(), nullable=True),
        sa.Column("wunderground_id", sa.String(), nullable=True),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_stations_alias"), "stations", ["alias"], unique=True)
    op.create_table(
        "bundles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("location_alias", sa.String(), nullable=False),
        sa.Column("emit_period_s", sa.Integer(), nullable=False),
        sa.Column("emit_offset_s", sa.Integer(), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["location_alias"],
            ["locations.alias"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bundles_name"), "bundles", ["name"], unique=True)
    op.create_table(
        "last_observations",
        sa.Column("station_alias", sa.String(), nullable=False),
        sa.Column("observation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_slot", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_alias"],
            ["stations.alias"],
        ),
        sa.PrimaryKeyConstraint("station_alias"),
    )
    op.create_table(
        "seasonal_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("location_alias", sa.String(), nullable=False),
        sa.Column(
            "temp_by_month", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["location_alias"],
            ["locations.alias"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_alias", "start"),
    )
    op.create_table(
        "weather_channels",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column(
            "quantity",
            sa.Enum(
                "Unknown",
                "Unitless",
                "Power",
                "Energy",
                "Temperature",
                "FlowRate",
                "Volume",
                "Voltage",
                "Current",
                "Percent",
                "Frequency",
                "Time",
                "WindSpeed",
                "Irradiance",
                "Currency",
                name="quantity",
            ),
            nullable=False,
        ),
        sa.Column(
            "unit",
            sa.Enum(
                "Unknown",
                "Unitless",
                "FahrenheitX100",
                "Watts",
                "WattHours",
                "Gallons",
                "GpmX100",
                "Seconds",
                "SecondsX10",
                "Milliseconds",
                "DollarsX1000",
                "MilesPerHourX1000",
                "KilowattHoursX1000",
                "WattsPerSquareMeter",
                "MilesPerHourX10",
                "FahrenheitX10",
                name="unit",
            ),
            nullable=False,
        ),
        sa.Column("station_alias", sa.String(), nullable=False),
        sa.Column("emit_period_s", sa.Integer(), nullable=False),
        sa.Column("emit_offset_s", sa.Integer(), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_alias"],
            ["stations.alias"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_weather_channels_name"), "weather_channels", ["name"], unique=True
    )
    op.create_table(
        "forecast_channels",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("target_channel_name", sa.String(), nullable=False),
        sa.Column("forecaster", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("source_locator", sa.String(), nullable=True),
        sa.Column("total_slices", sa.Integer(), nullable=False),
        sa.Column(
            "slice_duration_s_list",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("forecast_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_channel_name"],
            ["weather_channels.name"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_forecast_channels_name"), "forecast_channels", ["name"], unique=True
    )
    op.create_table(
        "bundle_bindings",
        sa.Column("bundle_name", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("forecast_channel_name", sa.String(), nullable=False),
        sa.Column("observation_channel_name", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["bundle_name"],
            ["bundles.name"],
        ),
        sa.ForeignKeyConstraint(
            ["forecast_channel_name"],
            ["forecast_channels.name"],
        ),
        sa.ForeignKeyConstraint(
            ["observation_channel_name"],
            ["weather_channels.name"],
        ),
        sa.PrimaryKeyConstraint("bundle_name", "position"),
        sa.UniqueConstraint(
            "bundle_name",
            "forecast_channel_name",
            name="uq_bundle_bindings_bundle_forecast_channel",
        ),
    )


def downgrade() -> None:
    op.drop_table("bundle_bindings")
    op.drop_index(op.f("ix_forecast_channels_name"), table_name="forecast_channels")
    op.drop_table("forecast_channels")
    op.drop_index(op.f("ix_weather_channels_name"), table_name="weather_channels")
    op.drop_table("weather_channels")
    op.drop_table("seasonal_templates")
    op.drop_table("last_observations")
    op.drop_index(op.f("ix_bundles_name"), table_name="bundles")
    op.drop_table("bundles")
    op.drop_index(op.f("ix_stations_alias"), table_name="stations")
    op.drop_table("stations")
    op.drop_table("source_products")
    op.drop_index(op.f("ix_locations_alias"), table_name="locations")
    op.drop_table("locations")
    op.drop_index(op.f("ix_forecasts_bundle_name"), table_name="forecasts")
    op.drop_table("forecasts")
    for enum in ("quantity", "unit", "weather_forecast_fidelity"):
        op.execute(f"DROP TYPE {enum}")
