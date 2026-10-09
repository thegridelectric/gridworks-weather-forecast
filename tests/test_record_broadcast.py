"""Radio-channel selection for record broadcasts (delivery spoke,
"Broadcast binding shape"): every record broadcast rides the record's
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
)

SAMPLES = Path(gwwf.sema.__file__).resolve().parent / "samples"


def _sample(type_name: str, expect: type):
    payload = json.loads((SAMPLES / f"{type_name}.000.json").read_text())
    return SemaCodec().from_dict(payload, expect=expect)


def test_bundle_broadcast_is_tailless() -> None:
    bundle = _sample("gw.weather.forecast.bundle.gt", WeatherForecastBundleGt)
    assert radio_channel_for(bundle) is None


def test_non_bundle_records_ride_their_own_name() -> None:
    location = _sample("gw.weather.location.gt", WeatherLocationGt)
    assert radio_channel_for(location) == location.alias == record_name(location)

    channel = _sample("gw.weather.channel.gt", WeatherChannelGt)
    assert radio_channel_for(channel) == channel.name == record_name(channel)

    forecast_channel = _sample(
        "gw.weather.forecast.channel.gt", WeatherForecastChannelGt
    )
    assert (
        radio_channel_for(forecast_channel)
        == forecast_channel.name
        == record_name(forecast_channel)
    )

    template = _sample("gw.weather.seasonal.template.gt", WeatherSeasonalTemplateGt)
    assert (
        radio_channel_for(template) == template.location_alias == record_name(template)
    )
