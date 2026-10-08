from typing import Literal
from gwwf.sema.base import SemaType
from gwwf.sema.types.weather_channel_gt import WeatherChannelGt
from gwwf.sema.types.weather_forecast_bundle_gt import WeatherForecastBundleGt
from gwwf.sema.types.weather_forecast_channel_gt import WeatherForecastChannelGt
from gwwf.sema.types.weather_location_gt import WeatherLocationGt
from gwwf.sema.types.weather_seasonal_template_gt import WeatherSeasonalTemplateGt


class WeatherCreateCmd(SemaType):
    """Sema: https://schemas.electricity.works/types/gw.weather.create.cmd/001"""

    record: (
        WeatherChannelGt
        | WeatherForecastBundleGt
        | WeatherForecastChannelGt
        | WeatherLocationGt
        | WeatherSeasonalTemplateGt
    )
    proof: str | None = None
    type_name: Literal["gw.weather.create.cmd"] = "gw.weather.create.cmd"
    version: Literal["001"] = "001"
