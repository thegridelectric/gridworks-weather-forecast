"""WeatherActor — gwwf's emission actor.

Boots from the gridworks-weather DB: the bundle loads back as a
validated record reconstructed FROM the canonical channel rows (its
axioms fire on load — the service-side half of the bundle contract;
seeding is an explicit operator step, `gwwf seed`, and an unseeded DB
refuses to boot), scheduler state restores from the DB (last
observation, stored product), and emissions persist as they happen:
sent forecasts from the publish hook, the source product through a
caching fetch wrapper, last-observation state on publish. Fetchers
are injectable for harnesses; the defaults are the NWS adapters wired
from the records themselves (station from the location's IcaoId,
gridpoint from the forecast channel's SourceLocator). Broadcast radio
channels: observations ride the location alias, forecasts the bundle
Name; glitches ride un-channeled.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable

from gwbase.gridworks_actor import GridworksActor
from gwbase.transport_encoding import RoutingEnvelope, TransportClass

from gwwf.config import GwwfSettings
from gwwf.db.session import session_factory_from
from gwwf.db.store import (
    load_bundles,
    load_last_observation,
    load_locations,
    load_product,
    save_last_observation,
    save_product,
    store_forecast,
)
from gwwf.nws import (
    HourlyForecastProduct,
    fetch_hourly_product,
    fetch_latest_observation,
    gridpoint_from_locator,
)
from gwwf.scheduler import (
    EmissionScheduler,
    ForecastStream,
    ObservationStream,
    WeatherMessage,
)
from gwwf.sema.enums import Gw1Unit, LogLevel
from gwwf.sema.types import (
    Glitch,
    GwWeatherForecast,
    GwWeatherForecastBundleGt,
    GwWeatherLocationGt,
    GwWeatherObservation,
)

GLITCH_NODE = "scheduler"
TICK_S = 1.0

# Adapter-level mapping from a channel's Unit to the scale applied to
# the NWS product's natural units (°F, mph). Retires when a
# unit-metadata word drives conversion generically.
UNIT_SCALE: dict[Gw1Unit, int] = {
    Gw1Unit.FahrenheitX100: 100,
    Gw1Unit.MilesPerHourX1000: 1000,
}


class WeatherActor(GridworksActor):
    """Emits the weather streams on the DB records' schedule."""

    def __init__(
        self,
        settings: GwwfSettings,
        *,
        fetch_observation: Callable[[], GwWeatherObservation] | None = None,
        fetch_product: Callable[[int], HourlyForecastProduct] | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            transport_class=TransportClass.WeatherForecastService,
            my_super_alias=settings.my_super_alias,
            my_time_coordinator_alias=settings.my_time_coordinator_alias,
        )
        self.settings: GwwfSettings = settings
        self._sessions = session_factory_from(settings)

        with self._sessions() as session:
            bundles = load_bundles(session)
            if len(bundles) != 1:
                raise ValueError(
                    f"expected exactly one bundle in the gridworks-weather DB, "
                    f"got {len(bundles)}; seed first: gwwf seed"
                )
            bundle = bundles[0]
            locations = {loc.alias: loc for loc in load_locations(session)}
            location = locations[bundle.location_alias]
            self._method = bundle.temp_forecast_channel.method
            self._source_locator = bundle.temp_forecast_channel.source_locator
            stored_observation = load_last_observation(session, location.alias)
            stored_product = load_product(
                session, method=self._method, source_locator=self._source_locator or ""
            )

        observation_channels = [
            bundle.temp_observation_channel,
            bundle.wind_speed_observation_channel,
        ]
        if fetch_observation is None:
            fetch_observation = self._nws_observation_fetch(location, bundle)
        if fetch_product is None:
            fetch_product = self._nws_product_fetch()
        fetch_product = self._caching_product_fetch(fetch_product)

        self.scheduler = EmissionScheduler(
            observation_streams=[
                ObservationStream(
                    channels=observation_channels,
                    fetch=fetch_observation,
                    initial_observation=(
                        stored_observation.observation if stored_observation else None
                    ),
                    initial_published_slot=(
                        stored_observation.published_slot
                        if stored_observation
                        else None
                    ),
                )
            ],
            forecast_streams=[
                ForecastStream(
                    bundle=bundle,
                    fetch=fetch_product,
                    temp_scale=UNIT_SCALE[bundle.temp_observation_channel.unit],
                    wind_speed_scale=UNIT_SCALE[
                        bundle.wind_speed_observation_channel.unit
                    ],
                    initial_product=stored_product,
                )
            ],
            publish=self._publish_stream,
            raise_glitch=self._raise_glitch,
            now_s=int(time.time()),
            on_observation_published=self._observation_published,
        )
        self.main_thread = threading.Thread(target=self.main, daemon=True)

    # ------------------------------------------------------------------
    # Default NWS wiring (from the records themselves)
    # ------------------------------------------------------------------

    def _nws_observation_fetch(
        self,
        location: GwWeatherLocationGt,
        bundle: GwWeatherForecastBundleGt,
    ) -> Callable[[], GwWeatherObservation]:
        if location.icao_id is None:
            raise ValueError(
                f"{location.alias}: no IcaoId on the location record; "
                "cannot fetch NWS observations"
            )
        station = location.icao_id
        temperature = bundle.temp_observation_channel
        windspeed = bundle.wind_speed_observation_channel

        def fetch() -> GwWeatherObservation:
            return fetch_latest_observation(
                station=station,
                location_alias=location.alias,
                temperature_channel=temperature.name,
                windspeed_channel=windspeed.name,
            )

        return fetch

    def _nws_product_fetch(self) -> Callable[[int], HourlyForecastProduct]:
        if self._source_locator is None:
            raise ValueError(f"{self._method}: no SourceLocator on the record")
        gridpoint = gridpoint_from_locator(self._source_locator)

        def fetch(slices: int) -> HourlyForecastProduct:
            return fetch_hourly_product(gridpoint=gridpoint, slices=slices)

        return fetch

    def _caching_product_fetch(
        self, fetch: Callable[[int], HourlyForecastProduct]
    ) -> Callable[[int], HourlyForecastProduct]:
        def caching(slices: int) -> HourlyForecastProduct:
            product = fetch(slices)
            with self._sessions() as session:
                save_product(
                    session,
                    method=self._method,
                    source_locator=self._source_locator or "",
                    product=product,
                )
            return product

        return caching

    # ------------------------------------------------------------------
    # Actor lifecycle
    # ------------------------------------------------------------------

    def local_start(self) -> None:
        self._main_loop_running = True
        self.main_thread.start()

    def local_stop(self) -> None:
        self._main_loop_running = False
        self.main_thread.join()

    def process_message(self, *, envelope: RoutingEnvelope, body: bytes) -> None:
        # No inbound app traffic to handle today.
        self.logger.debug(
            "Ignored unexpected %s from %s", envelope.type_name, envelope.from_alias
        )

    def main(self) -> None:
        while self._main_loop_running:
            try:
                self.scheduler.run_pending(int(time.time()))
            except Exception:  # noqa: BLE001 -- the loop must survive anything
                self.logger.exception("scheduler tick failed")
            time.sleep(TICK_S)

    # ------------------------------------------------------------------
    # Scheduler hooks
    # ------------------------------------------------------------------

    def _publish_stream(self, message: WeatherMessage, radio_channel: str) -> None:
        envelope = self.broadcast_envelope(
            type_name=message.type_name, radio_channel=radio_channel
        )
        self.send(envelope=envelope, body=json.dumps(message.to_dict()).encode("utf-8"))
        self.logger.info(
            "published %s", envelope.routing_key, extra={"radio": radio_channel}
        )
        if isinstance(message, GwWeatherForecast):
            with self._sessions() as session:
                store_forecast(session, message)

    def _observation_published(
        self, location_alias: str, observation: GwWeatherObservation, slot_s: int
    ) -> None:
        with self._sessions() as session:
            save_last_observation(
                session,
                location_alias=location_alias,
                observation=observation,
                published_slot=slot_s,
            )

    def _raise_glitch(self, level: LogLevel, summary: str, details: str) -> None:
        glitch = Glitch(
            from_g_node_alias=self.alias,
            node=GLITCH_NODE,
            type=level,
            summary=summary,
            details=details,
            created_ms=int(time.time() * 1000),
        )
        envelope = self.broadcast_envelope(type_name=glitch.type_name)
        self.send(envelope=envelope, body=json.dumps(glitch.to_dict()).encode("utf-8"))
        self.logger.warning("glitch: %s — %s", summary, details)
