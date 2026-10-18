from typing import Literal
from pydantic import StrictInt
from gwwf.sema.base import SemaType
from gwwf.sema.enums import Quantity
from gwwf.sema.property_format import LeftRightDot


class WeatherObservationReading(SemaType):
    """Sema: https://schemas.electricity.works/types/gw.weather.observation.reading/000"""

    channel_name: LeftRightDot
    quantity: Quantity
    value: StrictInt
    type_name: Literal["gw.weather.observation.reading"] = (
        "gw.weather.observation.reading"
    )
    version: Literal["000"] = "000"
