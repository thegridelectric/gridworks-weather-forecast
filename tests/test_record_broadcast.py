"""Radio-channel selection for record broadcasts (delivery spoke,
"Broadcast binding shape"): every record broadcast has the record's
own name as the radio tail, except the bundle, which is tail-less.
Instances come from the snapshot samples so the rule is checked
against real decoded words, not hand-built stand-ins.
"""

import json
from pathlib import Path

import gwwf.sema
from gwwf.record_broadcast import radio_channel_for, record_name
from gwwf.sema.codec import SemaCodec
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherLocationGt,
    WeatherSeasonalTemplateGt,
    WeatherStationGt,
)

SAMPLES = Path(gwwf.sema.__file__).resolve().parent / "samples"


def _sample(type_name: str, version: str, expect: type):
    payload = json.loads((SAMPLES / f"{type_name}.{version}.json").read_text())
    return SemaCodec().from_dict(payload, auto_upgrade=False, expect=expect)


def test_bundle_broadcast_is_tailless() -> None:
    bundle = _sample("gw.weather.forecast.bundle.gt", "002", WeatherForecastBundleGt)
    assert radio_channel_for(bundle) is None


def test_non_bundle_records_go_out_on_their_own_name() -> None:
    location = _sample("gw.weather.location.gt", "001", WeatherLocationGt)
    assert radio_channel_for(location) == location.alias == record_name(location)

    station = _sample("gw.weather.station.gt", "000", WeatherStationGt)
    assert radio_channel_for(station) == station.alias == record_name(station)

    channel = _sample("gw.weather.channel.gt", "002", WeatherChannelGt)
    assert radio_channel_for(channel) == channel.name == record_name(channel)

    forecast_channel = _sample(
        "gw.weather.forecast.channel.gt", "000", WeatherForecastChannelGt
    )
    assert (
        radio_channel_for(forecast_channel)
        == forecast_channel.name
        == record_name(forecast_channel)
    )

    template = _sample(
        "gw.weather.seasonal.template.gt", "000", WeatherSeasonalTemplateGt
    )
    assert (
        radio_channel_for(template) == template.location_alias == record_name(template)
    )
