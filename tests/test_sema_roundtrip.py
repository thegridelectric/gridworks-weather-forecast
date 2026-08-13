"""Round-trip of the gw.weather message words through the vendored codec."""

from __future__ import annotations

import json
from pathlib import Path

from gwwf.sema.codec import SemaCodec
from gwwf.sema.types import WeatherForecast, WeatherObservation

SAMPLES = Path(__file__).parent.parent / "src" / "gwwf" / "sema" / "samples"


def test_observation_roundtrips_through_codec() -> None:
    codec = SemaCodec()
    sample = json.loads((SAMPLES / "gw.weather.observation.000.json").read_text())
    obs = codec.from_dict(sample, expect=WeatherObservation)
    assert obs.to_dict() == sample


def test_forecast_roundtrips_through_codec() -> None:
    codec = SemaCodec()
    sample = json.loads((SAMPLES / "gw.weather.forecast.000.json").read_text())
    forecast = codec.from_dict(sample, expect=WeatherForecast)
    assert forecast.to_dict() == sample
