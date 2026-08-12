"""NWS adapters — live products to snapshot instances.

Observations come from ``/stations/<station>/observations``, which
serves newest-first; the adapter takes the newest usable feature.
Forecasts come from the gridpoint hourly product;
``WeatherForecast.source_updated_time`` binds the product's
``updateTime`` (the underlying data stamp — ``generatedAt`` refreshes
per render even when the data is hours older).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NamedTuple

import requests

from gwwf.sema.property_format import LeftRightDot, UtcIso8601Seconds
from gwwf.sema.types import WeatherObservation

NWS_BASE = "https://api.weather.gov"
# NWS asks that clients identify themselves with a contact in the UA.
USER_AGENT = "gridworks-weather-forecast (gridworks@gridworks-consulting.com)"
TIMEOUT_S = 30

# Acquisition facts for the standup station/gridpoint. The forecast
# channel record's SourceLocator owns the gridpoint once the step-4
# records land; nothing should parse facts out of these.
KMLT_STATION = "KMLT"
CAR_GRIDPOINT = "CAR/60,114"

SECONDS_PER_SLICE = 3600


class NwsProductError(Exception):
    """The NWS response did not carry what the adapter requires."""


def gridpoint_from_locator(locator: str) -> str:
    """Render an LRD SourceLocator (``car.60.114``) into the NWS path
    form (``CAR/60,114``) — the adapter's own encoding of its
    acquisition fact, not name-parsing."""
    office, grid_x, grid_y = locator.split(".")
    return f"{office.upper()}/{grid_x},{grid_y}"


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        f"{NWS_BASE}{path}",
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise NwsProductError(f"{path}: non-object JSON response")
    return data


def _iso_seconds_z(iso: str) -> str:
    """NWS ISO stamps (offset form) -> utc.iso8601.seconds ('...Z')."""
    return datetime.fromisoformat(iso).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _c_to_f_x100(temp_c: float) -> int:
    return round((temp_c * 9 / 5 + 32) * 100)


def _kmh_to_mph_x1000(speed_kmh: float) -> int:
    return round(speed_kmh * 0.621371 * 1000)


def fetch_latest_observation(
    *,
    station: str,
    location_alias: LeftRightDot,
    temperature_channel: LeftRightDot,
    windspeed_channel: LeftRightDot,
) -> WeatherObservation:
    """Newest usable station observation as a snapshot instance.

    Walks the newest-first feature list and returns the first feature
    carrying a timestamp and a temperature value. WindSpeedValue is
    omitted when the station reported no wind — absence is absence.
    Raises ``NwsProductError`` when nothing usable is in the response
    or a unit code is unexpected.
    """
    data = _get(f"/stations/{station}/observations", params={"limit": 12})
    for feature in data.get("features", []):
        properties = feature.get("properties", {})
        timestamp = properties.get("timestamp")
        temperature = properties.get("temperature", {})
        if not timestamp or temperature.get("value") is None:
            continue
        if temperature.get("unitCode") != "wmoUnit:degC":
            raise NwsProductError(
                f"unexpected temperature unit {temperature.get('unitCode')}"
            )
        wind = properties.get("windSpeed", {})
        wind_value = None
        if wind.get("value") is not None:
            if wind.get("unitCode") != "wmoUnit:km_h-1":
                raise NwsProductError(
                    f"unexpected windSpeed unit {wind.get('unitCode')}"
                )
            wind_value = _kmh_to_mph_x1000(wind["value"])
        return WeatherObservation(
            location_alias=location_alias,
            observation_time=_iso_seconds_z(timestamp),
            interpolated=False,
            temp_channel_name=temperature_channel,
            temp_value=_c_to_f_x100(temperature["value"]),
            wind_speed_channel_name=windspeed_channel,
            wind_speed_value=wind_value,
        )
    raise NwsProductError(f"{station}: no usable observation in the newest 12")


class HourlyForecastProduct(NamedTuple):
    """One parsed NWS gridpoint hourly product.

    ``update_time`` is the underlying-data stamp (binds
    SourceUpdatedTime); ``generated_at`` is the render stamp, kept for
    freshness diagnostics only. ``temperature_f`` / ``wind_speed_mph``
    are the leading values (natural units as served), verified
    contiguous at ``period_s`` from ``first_slice_start`` — the
    product's uniformity is declared HERE, at the adapter boundary;
    downstream slice arithmetic is list-driven. NOTE: interior record,
    no sema word — the emission scheduler consumes it and the message
    words are what reach the wire.
    """

    update_time: UtcIso8601Seconds
    generated_at: str
    first_slice_start: UtcIso8601Seconds
    period_s: int
    temperature_f: list[int]
    wind_speed_mph: list[int]


def _parse_mph(text: str) -> int:
    head, _, unit = text.partition(" ")
    if unit != "mph" or not head.isdigit():
        raise NwsProductError(f"unexpected windSpeed text {text!r}")
    return int(head)


def fetch_hourly_product(*, gridpoint: str, slices: int) -> HourlyForecastProduct:
    """The gridpoint hourly product, verified contiguous for ``slices`` hours."""
    data = _get(f"/gridpoints/{gridpoint}/forecast/hourly")
    properties = data.get("properties", {})
    update_time = properties.get("updateTime")
    generated_at = properties.get("generatedAt")
    periods = properties.get("periods", [])
    if not update_time or not generated_at:
        raise NwsProductError(f"{gridpoint}: product missing time stamps")
    if len(periods) < slices:
        raise NwsProductError(
            f"{gridpoint}: {len(periods)} periods < {slices} requested"
        )
    starts = [datetime.fromisoformat(p["startTime"]) for p in periods[:slices]]
    for i in range(1, slices):
        if (starts[i] - starts[i - 1]).total_seconds() != SECONDS_PER_SLICE:
            raise NwsProductError(
                f"{gridpoint}: periods not hour-contiguous at index {i}"
            )
    temperatures: list[int] = []
    winds: list[int] = []
    for period in periods[:slices]:
        if period.get("temperatureUnit") != "F":
            raise NwsProductError(
                f"unexpected temperatureUnit {period.get('temperatureUnit')}"
            )
        temperatures.append(int(period["temperature"]))
        winds.append(_parse_mph(period["windSpeed"]))
    return HourlyForecastProduct(
        update_time=_iso_seconds_z(update_time),
        generated_at=generated_at,
        first_slice_start=_iso_seconds_z(periods[0]["startTime"]),
        period_s=SECONDS_PER_SLICE,
        temperature_f=temperatures,
        wind_speed_mph=winds,
    )
