"""Open-Meteo adapter — the irradiance product and the history series.

The forecast endpoint serves ``shortwave_radiation`` (global horizontal
irradiance, W/m²) as the mean of the hour PRECEDING each label, so the
value labelled 01:00 belongs to the slice that starts at 00:00: the
adapter shifts every label back one period. The model is pinned here
(``gfs_seamless``, Open-Meteo's own splice of HRRR, GFS 0.13° and GFS
0.25°). The splice has no run time of its own, so the series stamp is
the GFS 0.13° run read from the model metadata endpoint
(``last_run_initialisation_time``), the member that serves the
service's horizon. Free tier, non-commercial use; the data are
CC BY 4.0 Open-Meteo, and the record's Forecaster is the attribution.

The long pull reads the Historical Forecast API, the same request
shape on another host: the archive of past forecasts stitched from the
most recent run for each hour, so each value is the shortest-lead
forecast. Temperature and wind speed are instantaneous at the label;
shortwave radiation is the mean of the preceding hour and shifts back
one period as the live product does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Iterable
from typing import Any, NamedTuple

import requests

from gwwf.grid import next_slot, s_to_iso
from gwwf.sema.enums import Quantity
from gwwf.sema.property_format import UtcIso8601Seconds

OPEN_METEO_BASE = "https://api.open-meteo.com"
HISTORICAL_FORECAST_BASE = "https://historical-forecast-api.open-meteo.com"
MODEL = "gfs_seamless"
# The concrete member whose run stamps the series: GFS 0.13° serves the
# splice past HRRR's short range and across the whole horizon.
RUN_STAMP_MODEL = "ncep_gfs013"
VARIABLE = "shortwave_radiation"
TIMEOUT_S = 30
SECONDS_PER_SLICE = 3600
MAX_FORECAST_DAYS = 16
# The history request's variable per quantity, and the units asked for
# so the series comes back in the fleet's natural units (°F, mph, W/m²).
HISTORY_VARIABLES: dict[Quantity, str] = {
    Quantity.Temperature: "temperature_2m",
    Quantity.WindSpeed: "wind_speed_10m",
    Quantity.Irradiance: "shortwave_radiation",
}
HISTORY_UNIT_PARAMS = {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph"}
# Quantities whose label is the end of the hour it describes.
HOUR_MEAN_QUANTITIES = frozenset({Quantity.Irradiance})


class OpenMeteoProductError(Exception):
    """The Open-Meteo response did not have what the adapter requires."""


def coordinates_from_locator(locator: str) -> tuple[float, float]:
    """An Open-Meteo channel's SourceLocator is ``"<lat>,<lon>"`` in
    decimal degrees — the adapter's own encoding of its acquisition
    fact, not name-parsing."""
    lat_text, _, lon_text = locator.partition(",")
    try:
        return float(lat_text), float(lon_text)
    except ValueError as e:
        raise OpenMeteoProductError(f"locator {locator!r} is not '<lat>,<lon>'") from e


def _get(
    path: str, params: dict[str, Any] | None = None, *, base: str = OPEN_METEO_BASE
) -> dict[str, Any]:
    response = requests.get(f"{base}{path}", params=params, timeout=TIMEOUT_S)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise OpenMeteoProductError(f"{path}: non-object JSON response")
    return data


class IrradianceHourlyProduct(NamedTuple):
    """One parsed Open-Meteo hourly irradiance series.

    ``update_time`` is the model run time (binds
    IrradianceSourceUpdatedTime); ``generated_at`` is when that run
    became available at Open-Meteo, kept for freshness diagnostics
    only. ``irradiance_w_m2`` are whole W/m² per slice from
    ``first_slice_start``, verified contiguous at ``period_s``, the
    source's hour-end labels already shifted to slice starts.
    NOTE: interior record, no sema word — the emission scheduler
    consumes it and the forecast word is what reaches the wire.
    """

    update_time: UtcIso8601Seconds
    generated_at: UtcIso8601Seconds
    first_slice_start: UtcIso8601Seconds
    period_s: int
    irradiance_w_m2: list[int]


def _label_to_s(label: str) -> int:
    """Hourly labels come back as naive ISO minutes in the requested
    zone; the request pins GMT."""
    return int(datetime.fromisoformat(label).replace(tzinfo=UTC).timestamp())


def forecast_days_for(slices: int) -> int:
    """Days to request so that ``slices`` whole hours after the next
    hour boundary are inside the product, whatever the time of day
    (the product starts at 00:00 UTC today, up to 23 hours ago)."""
    # ceil((23 + 1 + slices) / 24) days, plus one day of margin.
    return min(MAX_FORECAST_DAYS, (slices + 47) // 24 + 1)


def parse_hourly_product(
    data: dict[str, Any],
    *,
    run_time: UtcIso8601Seconds,
    run_available_at: str,
    now_s: int,
    slices: int,
) -> IrradianceHourlyProduct:
    """The response body as a product covering ``slices`` hours from the
    next hour boundary after ``now_s``; raises ``OpenMeteoProductError``
    on a zone other than UTC, a hole, a null, or a short series."""
    if data.get("utc_offset_seconds") != 0:
        raise OpenMeteoProductError(
            f"expected a UTC series, got utc_offset_seconds={data.get('utc_offset_seconds')!r}"
        )
    hourly = data.get("hourly", {})
    labels = hourly.get("time", [])
    values = hourly.get(VARIABLE, [])
    if not labels or len(labels) != len(values):
        raise OpenMeteoProductError(
            f"{len(labels)} labels against {len(values)} {VARIABLE} values"
        )
    label_s = [_label_to_s(label) for label in labels]
    for i in range(1, len(label_s)):
        if label_s[i] - label_s[i - 1] != SECONDS_PER_SLICE:
            raise OpenMeteoProductError(f"labels not hour-contiguous at index {i}")
    series: list[int] = []
    for label, value in zip(labels, values, strict=True):
        if value is None:
            raise OpenMeteoProductError(f"{VARIABLE} is null at {label}")
        series.append(round(value))
    first_slice_start_s = label_s[0] - SECONDS_PER_SLICE
    needed_end_s = next_slot(now_s, SECONDS_PER_SLICE, 0) + slices * SECONDS_PER_SLICE
    covered_end_s = first_slice_start_s + len(series) * SECONDS_PER_SLICE
    if covered_end_s < needed_end_s:
        raise OpenMeteoProductError(
            f"series ends {s_to_iso(covered_end_s)}, before the "
            f"{slices} slices requested end {s_to_iso(needed_end_s)}"
        )
    return IrradianceHourlyProduct(
        update_time=run_time,
        generated_at=run_available_at,
        first_slice_start=s_to_iso(first_slice_start_s),
        period_s=SECONDS_PER_SLICE,
        irradiance_w_m2=series,
    )


def fetch_run_stamp() -> tuple[UtcIso8601Seconds, str]:
    """The run time and availability time of the stamping member."""
    meta = _get(f"/data/{RUN_STAMP_MODEL}/static/meta.json")
    initialised = meta.get("last_run_initialisation_time")
    available = meta.get("last_run_availability_time")
    if not isinstance(initialised, int) or not isinstance(available, int):
        raise OpenMeteoProductError(f"{RUN_STAMP_MODEL}: metadata missing run stamps")
    return s_to_iso(initialised), s_to_iso(available)


def fetch_irradiance_product(
    *, latitude: float, longitude: float, slices: int, now_s: int
) -> IrradianceHourlyProduct:
    """The pinned model's hourly irradiance at a point, verified to cover
    ``slices`` hours from the next hour boundary."""
    run_time, run_available_at = fetch_run_stamp()
    data = _get(
        "/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "hourly": VARIABLE,
            "models": MODEL,
            "forecast_days": forecast_days_for(slices),
            "timezone": "GMT",
        },
    )
    return parse_hourly_product(
        data,
        run_time=run_time,
        run_available_at=run_available_at,
        now_s=now_s,
        slices=slices,
    )


class HistorySeries(NamedTuple):
    """One quantity's hourly history in its natural unit.

    ``values`` run from ``first_time_s`` at ``period_s``; a None is an
    hour the source has no value for. The time of each value is the
    instant it describes: the label for an instantaneous quantity, the
    start of the hour for an hour-mean one.
    NOTE: interior record, no sema word — the archive executor turns it
    into gw.weather.observation messages, which are what reach the wire.
    """

    quantity: Quantity
    first_time_s: int
    period_s: int
    values: list[float | None]


def parse_history(
    data: dict[str, Any], quantities: Iterable[Quantity]
) -> dict[Quantity, HistorySeries]:
    """The response body as one series per quantity; raises
    ``OpenMeteoProductError`` on a zone other than UTC, a hole in the
    labels, or a missing variable."""
    if data.get("utc_offset_seconds") != 0:
        raise OpenMeteoProductError(
            f"expected a UTC series, got utc_offset_seconds={data.get('utc_offset_seconds')!r}"
        )
    hourly = data.get("hourly", {})
    labels = hourly.get("time", [])
    if not labels:
        raise OpenMeteoProductError("no hourly labels in the response")
    label_s = [_label_to_s(label) for label in labels]
    for i in range(1, len(label_s)):
        if label_s[i] - label_s[i - 1] != SECONDS_PER_SLICE:
            raise OpenMeteoProductError(f"labels not hour-contiguous at index {i}")
    series: dict[Quantity, HistorySeries] = {}
    for quantity in quantities:
        variable = HISTORY_VARIABLES[quantity]
        values = hourly.get(variable)
        if not isinstance(values, list) or len(values) != len(labels):
            raise OpenMeteoProductError(
                f"{variable}: {0 if values is None else len(values)} values "
                f"against {len(labels)} labels"
            )
        shift_s = SECONDS_PER_SLICE if quantity in HOUR_MEAN_QUANTITIES else 0
        series[quantity] = HistorySeries(
            quantity=quantity,
            first_time_s=label_s[0] - shift_s,
            period_s=SECONDS_PER_SLICE,
            values=[None if v is None else float(v) for v in values],
        )
    return series


def fetch_history(
    *,
    latitude: float,
    longitude: float,
    start_s: int,
    end_s: int,
    quantities: Iterable[Quantity],
) -> dict[Quantity, HistorySeries]:
    """The archived hourly series at a point over the days that cover
    ``[start_s, end_s)``, one per quantity, in natural units."""
    wanted = list(quantities)
    data = _get(
        "/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "start_date": _utc_date(start_s),
            "end_date": _utc_date(end_s),
            "hourly": ",".join(HISTORY_VARIABLES[q] for q in wanted),
            "timezone": "GMT",
            **HISTORY_UNIT_PARAMS,
        },
        base=HISTORICAL_FORECAST_BASE,
    )
    return parse_history(data, wanted)


def _utc_date(epoch_s: int) -> str:
    return datetime.fromtimestamp(epoch_s, tz=UTC).strftime("%Y-%m-%d")
