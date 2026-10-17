"""Open-Meteo adapter — the irradiance product to a typed hourly series.

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
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NamedTuple

import requests

from gwwf.grid import next_slot, s_to_iso
from gwwf.sema.property_format import UtcIso8601Seconds

OPEN_METEO_BASE = "https://api.open-meteo.com"
MODEL = "gfs_seamless"
# The concrete member whose run stamps the series: GFS 0.13° serves the
# splice past HRRR's short range and across the whole horizon.
RUN_STAMP_MODEL = "ncep_gfs013"
VARIABLE = "shortwave_radiation"
TIMEOUT_S = 30
SECONDS_PER_SLICE = 3600
MAX_FORECAST_DAYS = 16


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


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        f"{OPEN_METEO_BASE}{path}", params=params, timeout=TIMEOUT_S
    )
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
