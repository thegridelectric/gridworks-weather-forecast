"""Live NWS adapter tests — env-gated so CI never depends on weather.gov.

Run with: GWWF_LIVE_NWS=1 uv run pytest tests/test_nws_live.py -v
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from tests.records import MILLINOCKET, MILLINOCKET_TEMPERATURE, MILLINOCKET_WINDSPEED
from gwwf.nws import (
    CAR_GRIDPOINT,
    KMLT_STATION,
    fetch_hourly_product,
    fetch_latest_observation,
)

pytestmark = pytest.mark.skipif(
    os.getenv("GWWF_LIVE_NWS") != "1",
    reason="live NWS API test; set GWWF_LIVE_NWS=1",
)


def _age_seconds(iso_z: str) -> float:
    return (datetime.now(UTC) - datetime.fromisoformat(iso_z)).total_seconds()


def test_latest_observation_live() -> None:
    obs = fetch_latest_observation(
        station=KMLT_STATION,
        location_alias=MILLINOCKET,
        temperature_channel=MILLINOCKET_TEMPERATURE,
        windspeed_channel=MILLINOCKET_WINDSPEED,
    )
    # KMLT is an AWOS station reporting every 5 minutes; an observation
    # older than 2 h means the adapter picked wrong (the legacy defect).
    assert _age_seconds(obs.observation_time) < 2 * 3600
    assert obs.interpolated is False
    assert obs.location_alias == MILLINOCKET
    assert obs.temp_channel_name == MILLINOCKET_TEMPERATURE
    assert isinstance(obs.temp_value, int)
    round_tripped = type(obs).model_validate(obs.to_dict())
    assert round_tripped == obs


def test_hourly_forecast_live() -> None:
    product = fetch_hourly_product(gridpoint=CAR_GRIDPOINT, slices=48)
    assert len(product.temperature_f) == 48
    assert len(product.wind_speed_mph) == 48
    assert product.period_s == 3600
    # updateTime is the underlying-data stamp; it never postdates the render.
    assert datetime.fromisoformat(product.update_time) <= datetime.fromisoformat(
        product.generated_at
    )
