from typing import Literal
from gwwf.sema.base import SemaType


class WeatherCmdNack(SemaType):
    """Sema: https://schemas.electricity.works/types/gw.weather.cmd.nack/000"""

    command_hash: str
    reason: str
    type_name: Literal["gw.weather.cmd.nack"] = "gw.weather.cmd.nack"
    version: Literal["000"] = "000"
