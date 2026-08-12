"""Channel and location names for the standup fleet.

The canonical seed for these is the gridworks-weather DB channel
records; until those exist, callers source names from here. NOTE:
retire these constants into record lookups once the records land —
nothing should parse facts out of a name.
"""

from pydantic import TypeAdapter

from gwwf.sema.property_format import LeftRightDot

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
