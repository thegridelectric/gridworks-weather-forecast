"""Standup record instances — test fixtures.

Production records live in the DB and the eventstore (they enter by
create command over the bus); tests have their own instances. Ids are
fixed uuid4s. KMLT coordinates are from background knowledge — verify
against station metadata before treating as canonical facts.

One location, two stations and two bundles: `kmlt` (the airport ASOS)
observes temperature and wind at X10, `kmemilli18` (a personal
station in town) observes irradiance; the bobcat bundle binds
temperature only, the raccoon bundle temperature, wind and
irradiance.
"""

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from gwwf.db.store import insert_record
from gwwf.sema.enums import Quantity, Unit
from gwwf.sema.property_format import LeftRightDot, LowercaseToken
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecastBinding,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherLocationGt,
    WeatherSeasonalTemplateGt,
    WeatherStationGt,
)

_lrd: TypeAdapter[LeftRightDot] = TypeAdapter(LeftRightDot)
_token: TypeAdapter[LowercaseToken] = TypeAdapter(LowercaseToken)

MILLINOCKET: LeftRightDot = _lrd.validate_python("us.me.millinocket")
KMLT: LowercaseToken = _token.validate_python("kmlt")
KMEMILLI18: LowercaseToken = _token.validate_python("kmemilli18")
KMLT_TEMPERATURE: LeftRightDot = _lrd.validate_python("kmlt.temperature")
KMLT_WINDSPEED: LeftRightDot = _lrd.validate_python("kmlt.windspeed")
KMEMILLI18_IRRADIANCE: LeftRightDot = _lrd.validate_python("kmemilli18.irradiance")
KMLT_TEMPERATURE_FORECAST_NWS_HOURLY: LeftRightDot = _lrd.validate_python(
    "kmlt.temperature.forecast.nws.hourly"
)
KMLT_WINDSPEED_FORECAST_NWS_HOURLY: LeftRightDot = _lrd.validate_python(
    "kmlt.windspeed.forecast.nws.hourly"
)
KMEMILLI18_IRRADIANCE_FORECAST_OPENMETEO_HOURLY: LeftRightDot = _lrd.validate_python(
    "kmemilli18.irradiance.forecast.openmeteo.hourly"
)
MILLINOCKET_BOBCAT: LeftRightDot = _lrd.validate_python("us.me.millinocket.bobcat")
MILLINOCKET_RACCOON: LeftRightDot = _lrd.validate_python("us.me.millinocket.raccoon")

MILLINOCKET_LOCATION = WeatherLocationGt(
    alias=MILLINOCKET,
    latitude_microdegrees=45_647_800,
    longitude_microdegrees=-68_685_600,
    timezone="America/New_York",
    id="822626e8-f4b2-4bc8-b815-afdc3cab5dfb",
)

KMLT_STATION = WeatherStationGt(
    alias=KMLT,
    display_name="Millinocket Municipal Airport ASOS (KMLT)",
    latitude_microdegrees=45_647_800,
    longitude_microdegrees=-68_685_800,
    icao_id="KMLT",
    ghcn_id="USW00014610",
    start="2026-10-18T00:00:00Z",
    id="5d2a7c4e-9b1f-4e6a-8c3d-7f0b2e9a4c61",
)

KMEMILLI18_STATION = WeatherStationGt(
    alias=KMEMILLI18,
    display_name="Millinocket personal weather station (KMEMILLI18)",
    latitude_microdegrees=45_657_300,
    longitude_microdegrees=-68_709_800,
    wunderground_id="KMEMILLI18",
    start="2026-10-18T00:00:00Z",
    id="9a1c3e5f-7b2d-4f8a-9c6e-1d3b5a7f9c2e",
)

TEMPERATURE_CHANNEL = WeatherChannelGt(
    name=KMLT_TEMPERATURE,
    display_name="Millinocket outdoor air temperature",
    quantity=Quantity.Temperature,
    unit=Unit.FahrenheitX10,
    station_alias=KMLT,
    emit_period_s=3600,
    emit_offset_s=0,
    start="2026-10-18T00:00:00Z",
    id="6ffaacf0-701d-49d8-a20b-cd89de9a21ea",
)

WINDSPEED_CHANNEL = WeatherChannelGt(
    name=KMLT_WINDSPEED,
    display_name="Millinocket wind speed",
    quantity=Quantity.WindSpeed,
    unit=Unit.MilesPerHourX10,
    station_alias=KMLT,
    emit_period_s=3600,
    emit_offset_s=0,
    start="2026-10-18T00:00:00Z",
    id="2ee66a85-a869-4a34-98c2-3dd93718ce8a",
)

IRRADIANCE_CHANNEL = WeatherChannelGt(
    name=KMEMILLI18_IRRADIANCE,
    display_name="Millinocket global horizontal irradiance",
    quantity=Quantity.Irradiance,
    unit=Unit.WattsPerSquareMeter,
    station_alias=KMEMILLI18,
    emit_period_s=3600,
    emit_offset_s=0,
    start="2026-10-18T00:00:00Z",
    id="3b5e23e0-c7d4-4298-9eeb-862b88594e12",
)

TEMPERATURE_FORECAST_NWS_HOURLY_CHANNEL = WeatherForecastChannelGt(
    name=KMLT_TEMPERATURE_FORECAST_NWS_HOURLY,
    target_channel_name=KMLT_TEMPERATURE,
    forecaster="us.nws.gridpoint",
    method="api.weather.gov.gridpoint.hourly",
    source_locator="car.60.114",
    total_slices=48,
    slice_duration_s_list=[3600] * 48,
    forecast_duration_minutes=2880,
    start="2026-10-18T00:00:00Z",
    id="dc3d3844-6c8c-466b-8140-eb06900b4bf8",
)

WINDSPEED_FORECAST_NWS_HOURLY_CHANNEL = WeatherForecastChannelGt(
    name=KMLT_WINDSPEED_FORECAST_NWS_HOURLY,
    target_channel_name=KMLT_WINDSPEED,
    forecaster="us.nws.gridpoint",
    method="api.weather.gov.gridpoint.hourly",
    source_locator="car.60.114",
    total_slices=48,
    slice_duration_s_list=[3600] * 48,
    forecast_duration_minutes=2880,
    start="2026-10-18T00:00:00Z",
    id="23cc841c-8e12-4d1e-9030-ed9ef8923592",
)

IRRADIANCE_FORECAST_OPENMETEO_HOURLY_CHANNEL = WeatherForecastChannelGt(
    name=KMEMILLI18_IRRADIANCE_FORECAST_OPENMETEO_HOURLY,
    target_channel_name=KMEMILLI18_IRRADIANCE,
    forecaster="open.meteo",
    method="open.meteo.gfs.seamless.hourly",
    source_locator="45.6478,-68.6856",
    total_slices=48,
    slice_duration_s_list=[3600] * 48,
    forecast_duration_minutes=2880,
    start="2026-10-18T00:00:00Z",
    id="10fba70e-8dce-452f-b996-365d3f61a8e8",
)

TEMPERATURE_BINDING = WeatherForecastBinding(
    forecast_channel=TEMPERATURE_FORECAST_NWS_HOURLY_CHANNEL,
    observation_channel=TEMPERATURE_CHANNEL,
)
WINDSPEED_BINDING = WeatherForecastBinding(
    forecast_channel=WINDSPEED_FORECAST_NWS_HOURLY_CHANNEL,
    observation_channel=WINDSPEED_CHANNEL,
)
IRRADIANCE_BINDING = WeatherForecastBinding(
    forecast_channel=IRRADIANCE_FORECAST_OPENMETEO_HOURLY_CHANNEL,
    observation_channel=IRRADIANCE_CHANNEL,
)

MILLINOCKET_BOBCAT_BUNDLE = WeatherForecastBundleGt(
    name=MILLINOCKET_BOBCAT,
    display_name="Millinocket bobcat bundle: NWS temperature",
    location_alias=MILLINOCKET,
    binding_list=[TEMPERATURE_BINDING],
    emit_period_s=3600,
    emit_offset_s=60,
    start="2026-10-18T00:00:00Z",
    id="309885e5-57eb-4afc-9269-5740fde5d27e",
)

MILLINOCKET_RACCOON_BUNDLE = WeatherForecastBundleGt(
    name=MILLINOCKET_RACCOON,
    display_name=(
        "Millinocket raccoon bundle: NWS temperature and wind, Open-Meteo irradiance"
    ),
    location_alias=MILLINOCKET,
    binding_list=[TEMPERATURE_BINDING, WINDSPEED_BINDING, IRRADIANCE_BINDING],
    emit_period_s=3600,
    emit_offset_s=60,
    start="2026-10-18T00:00:00Z",
    id="01430d5b-4a61-4228-bdcc-da1addf7310d",
)

MILLINOCKET_SEASONAL_TEMPLATE = WeatherSeasonalTemplateGt(
    location_alias=MILLINOCKET,
    temp_by_month=[-300, -700, 100, 2100, 3000, 3100, 4600, 4700, 2800, 2400, 1600, 0],
    start="2026-10-07T00:00:00Z",
    id="3b7c1e52-8d4a-4f6e-9c2b-5a1d8e7f0c93",
)

# Listed in name order, the order the store loads them in.
STATIONS = [KMEMILLI18_STATION, KMLT_STATION]
OBSERVATION_CHANNELS = [IRRADIANCE_CHANNEL, TEMPERATURE_CHANNEL, WINDSPEED_CHANNEL]
FORECAST_CHANNELS = [
    IRRADIANCE_FORECAST_OPENMETEO_HOURLY_CHANNEL,
    TEMPERATURE_FORECAST_NWS_HOURLY_CHANNEL,
    WINDSPEED_FORECAST_NWS_HOURLY_CHANNEL,
]
FORECAST_BUNDLES = [MILLINOCKET_BOBCAT_BUNDLE, MILLINOCKET_RACCOON_BUNDLE]
SEASONAL_TEMPLATES = [MILLINOCKET_SEASONAL_TEMPLATE]


def seed(session: Session) -> None:
    """Insert the standup records in referential order through the
    insert-only store path."""
    for record in [
        MILLINOCKET_LOCATION,
        *STATIONS,
        *OBSERVATION_CHANNELS,
        *FORECAST_CHANNELS,
        *FORECAST_BUNDLES,
        *SEASONAL_TEMPLATES,
    ]:
        insert_record(session, record)
