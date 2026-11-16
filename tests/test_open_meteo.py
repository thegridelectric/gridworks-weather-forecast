"""Open-Meteo adapter against captured responses. The live product
(gfs_seamless, Millinocket, fetched 2026-10-16): the hour-end label
shifts to the slice start, holes and nulls refuse, and the coverage
check is against the next hour boundary after now. The history
series: each quantity timed as the instant it describes, a null a
missing hour, a hole refused.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from gwwf.grid import iso_to_s
from gwwf.open_meteo import (
    OpenMeteoProductError,
    coordinates_from_locator,
    forecast_days_for,
    HISTORY_VARIABLES,
    parse_history,
    parse_hourly_product,
)
from gwwf.sema.enums import Quantity

FIXTURE = Path(__file__).parent / "fixtures" / "open_meteo_gfs_seamless_hourly.json"
RUN_TIME = "2026-10-16T06:00:00Z"
AVAILABLE_AT = "2026-10-16T11:24:26Z"
# Inside the fixture's first day; the series runs 2026-10-16T00:00 to
# 2026-10-20T23:00 (120 labels).
NOW_S = iso_to_s("2026-10-16T13:20:00Z")


def fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_labels_shift_to_slice_starts() -> None:
    data = fixture()
    product = parse_hourly_product(
        data, run_time=RUN_TIME, run_available_at=AVAILABLE_AT, now_s=NOW_S, slices=72
    )
    # The value labelled 00:00 is the mean of the hour before it.
    assert product.first_slice_start == "2026-10-15T23:00:00Z"
    assert product.period_s == 3600
    assert len(product.irradiance_w_m2) == 120
    assert product.irradiance_w_m2[:14] == [0] * 12 + [23, 41]
    assert product.update_time == RUN_TIME
    assert product.generated_at == AVAILABLE_AT


def test_short_series_refuses_against_the_next_hour() -> None:
    data = fixture()
    # 120 labels from 00:00 cover 119 slice starts after 23:00 the day
    # before; from 13:20 the next boundary is 14:00 and 106 slices fit.
    parse_hourly_product(
        data, run_time=RUN_TIME, run_available_at=AVAILABLE_AT, now_s=NOW_S, slices=105
    )
    with pytest.raises(OpenMeteoProductError, match="before the 106 slices"):
        parse_hourly_product(
            data,
            run_time=RUN_TIME,
            run_available_at=AVAILABLE_AT,
            now_s=NOW_S,
            slices=106,
        )


def test_hole_and_null_refuse() -> None:
    holed = copy.deepcopy(fixture())
    del holed["hourly"]["time"][5]
    del holed["hourly"]["shortwave_radiation"][5]
    with pytest.raises(OpenMeteoProductError, match="not hour-contiguous"):
        parse_hourly_product(
            holed,
            run_time=RUN_TIME,
            run_available_at=AVAILABLE_AT,
            now_s=NOW_S,
            slices=3,
        )
    nulled = copy.deepcopy(fixture())
    nulled["hourly"]["shortwave_radiation"][40] = None
    with pytest.raises(OpenMeteoProductError, match="null at 2026-10-17T16:00"):
        parse_hourly_product(
            nulled,
            run_time=RUN_TIME,
            run_available_at=AVAILABLE_AT,
            now_s=NOW_S,
            slices=3,
        )


def test_non_utc_series_refuses() -> None:
    shifted = fixture()
    shifted["utc_offset_seconds"] = -14400
    with pytest.raises(OpenMeteoProductError, match="UTC"):
        parse_hourly_product(
            shifted,
            run_time=RUN_TIME,
            run_available_at=AVAILABLE_AT,
            now_s=NOW_S,
            slices=3,
        )


def test_locator_is_lat_lon() -> None:
    assert coordinates_from_locator("45.6478,-68.6856") == (45.6478, -68.6856)
    with pytest.raises(OpenMeteoProductError):
        coordinates_from_locator("car.60.114")


def test_forecast_days_cover_the_horizon_at_any_hour() -> None:
    # 72 slices: 23 h of today already past + 1 + 72 = 96 h = 4 days, + 1 margin.
    assert forecast_days_for(72) == 5
    assert forecast_days_for(3) == 3
    assert forecast_days_for(24 * 30) == 16


# The history fixture: one day (2024-10-01) at Millinocket from the
# Historical Forecast API, fetched 2026-11-15, 24 labels, °F / mph / W/m².
HISTORY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "open_meteo_historical_forecast_hourly.json"
)


def history_fixture() -> dict:
    return json.loads(HISTORY_FIXTURE.read_text())


def test_history_series_time_their_values() -> None:
    series = parse_history(history_fixture(), HISTORY_VARIABLES)
    temperature = series[Quantity.Temperature]
    irradiance = series[Quantity.Irradiance]
    # Temperature is instantaneous at the label; irradiance is the
    # mean of the hour before it, so its series starts one hour earlier.
    assert temperature.first_time_s == iso_to_s("2024-10-01T00:00:00Z")
    assert irradiance.first_time_s == iso_to_s("2024-09-30T23:00:00Z")
    assert len(temperature.values) == 24
    assert temperature.values[:3] == [57.9, 56.4, 54.4]
    assert series[Quantity.WindSpeed].values[:3] == [4.9, 4.3, 4.9]


def test_history_null_is_a_missing_hour() -> None:
    data = history_fixture()
    data["hourly"]["temperature_2m"][5] = None
    series = parse_history(data, [Quantity.Temperature])
    assert series[Quantity.Temperature].values[5] is None


def test_history_refuses_a_hole_in_the_labels() -> None:
    data = history_fixture()
    del data["hourly"]["time"][3]
    for variable in ("temperature_2m", "wind_speed_10m", "shortwave_radiation"):
        del data["hourly"][variable][3]
    with pytest.raises(OpenMeteoProductError, match="not hour-contiguous"):
        parse_history(data, [Quantity.Temperature])


def test_history_refuses_a_missing_variable() -> None:
    data = history_fixture()
    del data["hourly"]["wind_speed_10m"]
    with pytest.raises(OpenMeteoProductError, match="wind_speed_10m"):
        parse_history(data, [Quantity.WindSpeed])
