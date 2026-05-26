"""WeatherActor — like-for-like port of gjk/weather_service.py onto gwbase 0.4.0.

Polls NWS for the latest KMLT observation every 10 minutes and broadcasts a
`weather` v000 message. The broadcast envelope's routing class is `weather`
(long form, via `TransportClass.WeatherForecastService`); publish goes to
`amq.topic`. Consume exchange is `ws_tx` (matches prod broker fabric).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import pendulum
import requests
from gwbase.gridworks_actor import GridworksActor
from gwbase.transport_encoding import RoutingEnvelope
from result import Err, Ok, Result

from gwwf.config import GwwfSettings
from gwwf.sema.types import Weather

LOGGER = logging.getLogger(__name__)

KMLT_STATION = "KMLT"  # Millinocket, ME — ICAO
WEATHER_CHANNEL = "weather.gov.kmlt"
BASE_URL = "https://api.weather.gov"
CHECK_INTERVAL_SECONDS = 600  # 10 minutes


class WeatherResult:
    def __init__(
        self,
        success: bool,
        value: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.success = success
        self.value = value
        self.error = error


def safe_get_nested(d: dict[str, Any] | Any, *keys: Any) -> Any | None:
    curr: Any = d
    for key in keys:
        if isinstance(key, int):
            if not isinstance(curr, list) or not curr or abs(key) > len(curr):
                return None
            curr = curr[key]
        else:
            if not isinstance(curr, dict) or key not in curr:
                return None
            curr = curr[key]
    return curr


def c_to_f(temp_c: float | None) -> float | None:
    if temp_c is None:
        return None
    return round((temp_c * 9 / 5) + 32, 2)


def kmph_to_mph(speed_kmph: float | None) -> float | None:
    if speed_kmph is None:
        return None
    return round(speed_kmph * 0.621371, 2)


def get_latest_observation() -> Result[WeatherResult, Exception]:
    """True iff a reported observation with timestamp + temp arrived in the
    last 2 hours."""
    try:
        url = f"{BASE_URL}/stations/{KMLT_STATION}/observations"
        start = pendulum.now("UTC").subtract(hours=2)
        end = pendulum.now("UTC").add(minutes=5)
        params = {
            "start": start.to_iso8601_string(),
            "end": end.to_iso8601_string(),
        }
        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            return Ok(WeatherResult(False, error=f"NWS HTTP {response.status_code}"))
        data = response.json()
        if not safe_get_nested(data, "features"):
            return Ok(WeatherResult(False, error="no observations received"))
        latest = safe_get_nested(data, "features", -1, "properties")
        if not latest:
            return Ok(WeatherResult(False, error="no properties in latest"))
        if safe_get_nested(latest, "temperature", "value") is None:
            return Ok(WeatherResult(False, error="no temperature in latest"))
        if not safe_get_nested(latest, "timestamp"):
            return Ok(WeatherResult(False, error="no timestamp in latest"))
        return Ok(WeatherResult(True, value=latest))
    except Exception as e:
        return Err(e)


class WeatherActor(GridworksActor):
    """Polls NWS and broadcasts `weather` v000 every 10 minutes."""

    def __init__(self, settings: GwwfSettings) -> None:
        super().__init__(
            settings=settings,
            my_super_alias=settings.my_super_alias,
            my_time_coordinator_alias=settings.my_time_coordinator_alias,
        )
        self.settings: GwwfSettings = settings
        # Prod broker exchange uses the short form `ws_tx`, not `weather_tx`.
        # (Drift between gwbase.topology canonical names and the deployed
        # broker — same family as F-007.) Override before super's queue setup.
        self._consume_exchange = "ws_tx"
        self.main_thread = threading.Thread(target=self.main, daemon=True)

    def local_start(self) -> None:
        self._main_loop_running = True
        self.main_thread.start()

    def local_stop(self) -> None:
        self._main_loop_running = False
        self.main_thread.join()

    def process_message(
        self, *, envelope: RoutingEnvelope, body: bytes
    ) -> None:
        # No inbound app traffic to handle today.
        LOGGER.debug(
            "Ignored unexpected %s from %s", envelope.type_name, envelope.from_alias
        )

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    def _build_weather(self, observation: dict[str, Any]) -> Weather | None:
        try:
            timestamp_str = safe_get_nested(observation, "timestamp")
            if not timestamp_str:
                LOGGER.warning("no timestamp in observation; skipping")
                return None
            obs_time = pendulum.parse(timestamp_str)
            unix_s = int(obs_time.timestamp())

            temp_units = safe_get_nested(observation, "temperature", "unitCode")
            if temp_units != "wmoUnit:degC":
                LOGGER.warning("unexpected temp units %s; skipping", temp_units)
                return None
            temp_f = c_to_f(safe_get_nested(observation, "temperature", "value"))

            wind_units = safe_get_nested(observation, "windSpeed", "unitCode")
            if wind_units != "wmoUnit:km_h-1":
                LOGGER.warning("unexpected wind units %s; skipping", wind_units)
                return None
            wind_mph = kmph_to_mph(safe_get_nested(observation, "windSpeed", "value"))

            if temp_f is None:
                LOGGER.warning("temperature reading was None")
                return None

            return Weather(
                from_g_node_alias=self.alias,
                weather_channel_name=WEATHER_CHANNEL,
                outside_air_temp_f=temp_f,
                wind_speed_mph=wind_mph,
                unix_time_s=unix_s,
            )
        except Exception as e:
            LOGGER.error("failed to build Weather payload: %r", e)
            return None

    def _publish(self, weather: Weather) -> None:
        envelope = self.broadcast_envelope(type_name=weather.type_name)
        body = json.dumps(weather.to_dict()).encode("utf-8")
        self.send(envelope=envelope, body=body)

    def main(self) -> None:
        last_obs_time: pendulum.DateTime | None = None
        time.sleep(1)
        while self._main_loop_running:
            try:
                result = get_latest_observation()
                if isinstance(result, Err):
                    LOGGER.error("observation fetch failed: %r", result.err())
                    time.sleep(60)
                    continue
                wr = result.unwrap()
                if not wr.success:
                    LOGGER.info("no fresh observation: %s", wr.error)
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                obs = wr.value
                assert obs is not None  # success implies value present
                current_obs_time = pendulum.parse(obs["timestamp"])
                if last_obs_time is None or current_obs_time > last_obs_time:
                    last_obs_time = current_obs_time
                    weather = self._build_weather(obs)
                    if weather is None:
                        time.sleep(CHECK_INTERVAL_SECONDS)
                        continue
                    et = pendulum.from_timestamp(
                        weather.unix_time_s, tz="America/New_York"
                    )
                    LOGGER.info(
                        "[%s ET] %s: %.2f°F, %s mph",
                        et.format("YYYY-MM-DD HH:mm:ss"),
                        WEATHER_CHANNEL,
                        weather.outside_air_temp_f,
                        weather.wind_speed_mph,
                    )
                    self._publish(weather)

                time.sleep(CHECK_INTERVAL_SECONDS)
            except Exception as e:
                LOGGER.exception("main loop error: %r", e)
                time.sleep(60)
