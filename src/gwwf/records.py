"""Standup seed records — validated instances of the .gt words.

The gridworks-weather DB is the canonical seed for these records;
this module is the interim in-code seed and retires into DB lookups
when the DB lands. Ids are fixed uuid4s minted 2026-08-11: records
are durable identities, never per-boot state. KMLT coordinates are
from background knowledge — verify against station metadata before
canonizing into the DB seed.
"""

from gwwf.names import (
    MILLINOCKET,
    MILLINOCKET_FORECAST_NWS_HOURLY,
    MILLINOCKET_TEMPERATURE,
    MILLINOCKET_TEMPERATURE_FORECAST_NWS_HOURLY,
    MILLINOCKET_WINDSPEED,
    MILLINOCKET_WINDSPEED_FORECAST_NWS_HOURLY,
)
from gwwf.sema.enums import Gw1Quantity, Gw1Unit
from gwwf.sema.types import (
    GwWeatherChannelGt,
    GwWeatherForecastBundleGt,
    GwWeatherForecastChannelGt,
    GwWeatherLocationGt,
)

MILLINOCKET_LOCATION = GwWeatherLocationGt(
    alias=MILLINOCKET,
    latitude_microdegrees=45_647_800,
    longitude_microdegrees=-68_685_600,
    # Hand-validated IANA name; a sema timezone format word would
    # retire the bare string (noted in the vocabulary).
    timezone="America/New_York",
    icao_id="KMLT",
    id="822626e8-f4b2-4bc8-b815-afdc3cab5dfb",
)

TEMPERATURE_CHANNEL = GwWeatherChannelGt(
    name=MILLINOCKET_TEMPERATURE,
    display_name="Millinocket outdoor air temperature",
    quantity=Gw1Quantity.Temperature,
    unit=Gw1Unit.FahrenheitX100,
    location_alias=MILLINOCKET,
    emit_period_s=3600,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id="6ffaacf0-701d-49d8-a20b-cd89de9a21ea",
)

WINDSPEED_CHANNEL = GwWeatherChannelGt(
    name=MILLINOCKET_WINDSPEED,
    display_name="Millinocket wind speed",
    quantity=Gw1Quantity.WindSpeed,
    unit=Gw1Unit.MilesPerHourX1000,
    location_alias=MILLINOCKET,
    emit_period_s=3600,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id="2ee66a85-a869-4a34-98c2-3dd93718ce8a",
)

TEMPERATURE_FORECAST_NWS_HOURLY_CHANNEL = GwWeatherForecastChannelGt(
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

WINDSPEED_FORECAST_NWS_HOURLY_CHANNEL = GwWeatherForecastChannelGt(
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

MILLINOCKET_NWS_HOURLY_BUNDLE = GwWeatherForecastBundleGt(
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

OBSERVATION_CHANNELS = [TEMPERATURE_CHANNEL, WINDSPEED_CHANNEL]
FORECAST_CHANNELS = [
    TEMPERATURE_FORECAST_NWS_HOURLY_CHANNEL,
    WINDSPEED_FORECAST_NWS_HOURLY_CHANNEL,
]
FORECAST_BUNDLES = [MILLINOCKET_NWS_HOURLY_BUNDLE]
