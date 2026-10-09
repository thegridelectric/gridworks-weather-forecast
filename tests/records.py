"""Standup record instances — test fixtures.

These were the in-code production seed until records began entering by
create command over the bus; production records now live in the DB and
the eventstore, and tests carry their own instances. Ids are fixed
uuid4s minted 2026-08-11. KMLT coordinates are from background
knowledge — verify against station metadata before treating as
canonical facts.
"""

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from gwwf.db.store import insert_record
from gwwf.sema.enums import Quantity, Unit
from gwwf.sema.property_format import LeftRightDot
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherLocationGt,
    WeatherSeasonalTemplateGt,
)

_lrd: TypeAdapter[LeftRightDot] = TypeAdapter(LeftRightDot)

MILLINOCKET: LeftRightDot = _lrd.validate_python("us.me.millinocket")
MILLINOCKET_TEMPERATURE: LeftRightDot = _lrd.validate_python(
    "us.me.millinocket.temperature"
)
MILLINOCKET_WINDSPEED: LeftRightDot = _lrd.validate_python(
    "us.me.millinocket.windspeed"
)
MILLINOCKET_TEMPERATURE_FORECAST_NWS_HOURLY: LeftRightDot = _lrd.validate_python(
    "us.me.millinocket.temperature.forecast.nws.hourly"
)
MILLINOCKET_WINDSPEED_FORECAST_NWS_HOURLY: LeftRightDot = _lrd.validate_python(
    "us.me.millinocket.windspeed.forecast.nws.hourly"
)
MILLINOCKET_FORECAST_NWS_HOURLY: LeftRightDot = _lrd.validate_python(
    "us.me.millinocket.forecast.nws.hourly"
)

MILLINOCKET_LOCATION = WeatherLocationGt(
    alias=MILLINOCKET,
    latitude_microdegrees=45_647_800,
    longitude_microdegrees=-68_685_600,
    timezone="America/New_York",
    icao_id="KMLT",
    id="822626e8-f4b2-4bc8-b815-afdc3cab5dfb",
)

TEMPERATURE_CHANNEL = WeatherChannelGt(
    name=MILLINOCKET_TEMPERATURE,
    display_name="Millinocket outdoor air temperature",
    quantity=Quantity.Temperature,
    unit=Unit.FahrenheitX100,
    location_alias=MILLINOCKET,
    emit_period_s=3600,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id="6ffaacf0-701d-49d8-a20b-cd89de9a21ea",
)

WINDSPEED_CHANNEL = WeatherChannelGt(
    name=MILLINOCKET_WINDSPEED,
    display_name="Millinocket wind speed",
    quantity=Quantity.WindSpeed,
    unit=Unit.MilesPerHourX1000,
    location_alias=MILLINOCKET,
    emit_period_s=3600,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id="2ee66a85-a869-4a34-98c2-3dd93718ce8a",
)

TEMPERATURE_FORECAST_NWS_HOURLY_CHANNEL = WeatherForecastChannelGt(
    name=MILLINOCKET_TEMPERATURE_FORECAST_NWS_HOURLY,
    target_channel_name=MILLINOCKET_TEMPERATURE,
    forecaster="us.nws.gridpoint",
    method="api.weather.gov.gridpoint.hourly",
    source_locator="car.60.114",
    total_slices=48,
    slice_duration_s_list=[3600] * 48,
    forecast_duration_minutes=2880,
    start="2026-08-11T00:00:00Z",
    id="dc3d3844-6c8c-466b-8140-eb06900b4bf8",
)

WINDSPEED_FORECAST_NWS_HOURLY_CHANNEL = WeatherForecastChannelGt(
    name=MILLINOCKET_WINDSPEED_FORECAST_NWS_HOURLY,
    target_channel_name=MILLINOCKET_WINDSPEED,
    forecaster="us.nws.gridpoint",
    method="api.weather.gov.gridpoint.hourly",
    source_locator="car.60.114",
    total_slices=48,
    slice_duration_s_list=[3600] * 48,
    forecast_duration_minutes=2880,
    start="2026-08-11T00:00:00Z",
    id="23cc841c-8e12-4d1e-9030-ed9ef8923592",
)

MILLINOCKET_NWS_HOURLY_BUNDLE = WeatherForecastBundleGt(
    name=MILLINOCKET_FORECAST_NWS_HOURLY,
    display_name="Millinocket NWS hourly forecast bundle",
    location_alias=MILLINOCKET,
    temp_forecast_channel=TEMPERATURE_FORECAST_NWS_HOURLY_CHANNEL,
    temp_observation_channel=TEMPERATURE_CHANNEL,
    wind_speed_forecast_channel=WINDSPEED_FORECAST_NWS_HOURLY_CHANNEL,
    wind_speed_observation_channel=WINDSPEED_CHANNEL,
    emit_period_s=3600,
    emit_offset_s=60,
    start="2026-08-12T00:00:00Z",
    id="309885e5-57eb-4afc-9269-5740fde5d27e",
)

MILLINOCKET_SEASONAL_TEMPLATE = WeatherSeasonalTemplateGt(
    location_alias=MILLINOCKET,
    temp_by_month=[-300, -700, 100, 2100, 3000, 3100, 4600, 4700, 2800, 2400, 1600, 0],
    start="2026-10-07T00:00:00Z",
    id="3b7c1e52-8d4a-4f6e-9c2b-5a1d8e7f0c93",
)

OBSERVATION_CHANNELS = [TEMPERATURE_CHANNEL, WINDSPEED_CHANNEL]
FORECAST_CHANNELS = [
    TEMPERATURE_FORECAST_NWS_HOURLY_CHANNEL,
    WINDSPEED_FORECAST_NWS_HOURLY_CHANNEL,
]
FORECAST_BUNDLES = [MILLINOCKET_NWS_HOURLY_BUNDLE]
SEASONAL_TEMPLATES = [MILLINOCKET_SEASONAL_TEMPLATE]


def seed(session: Session) -> None:
    """Insert the standup seven in referential order through the
    insert-only store path."""
    for record in [
        MILLINOCKET_LOCATION,
        *OBSERVATION_CHANNELS,
        *FORECAST_CHANNELS,
        *FORECAST_BUNDLES,
        *SEASONAL_TEMPLATES,
    ]:
        insert_record(session, record)
