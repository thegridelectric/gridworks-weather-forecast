"""WeatherActor — gwwf's emission actor and the create command's consumer.

Boots from the gridworks-weather DB: each bundle loads back as a
validated record reconstructed FROM the canonical channel rows (its
axioms fire on load — the service-side half of the bundle contract),
scheduler state restores from the DB (last observation per station,
stored products), and emissions persist as they happen: sent forecasts
from the publish hook, the source products through a caching fetch
wrapper, last-observation state on publish. An EMPTY record set is a
legal boot state — the actor is the create command's consumer, so it
necessarily runs before any records exist; no records means an idle
scheduler, never a boot failure.

Records enter by `gw.weather.create.cmd` over the bus: the actor
validates through the snapshot, inserts (insert-only — records are
durable identities, never upserted), broadcasts the record once, and
replies `gw.weather.cmd.ack` / `gw.weather.cmd.nack` direct to the
sender, correlated by the hash of the command bytes as published. The
scheduler rebuilds from the DB after each applied create, so minted
records start emitting without a restart.

Each forecast channel's Method names its adapter (the `ADAPTERS`
table): the NWS gridpoint hourly product serves temperature and wind,
the Open-Meteo product irradiance; bindings sharing an acquisition
(method + SourceLocator) share one fetch. Observations come from the
NWS station feed, keyed by the station record's IcaoId; a station
without one has no observation stream. Fetchers and
adapters are injectable for harnesses. One forecast stream per
bundle, one observation stream per station the bundles' observed
channels name. Broadcast radio channels: observations go out on the
station alias, forecasts on the bundle Name, records on their own
name (the bundle tail-less); glitches un-channeled.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from typing import Any, Callable, NamedTuple

from gwbase.gridworks_actor import GridworksActor
from gwbase.transport_encoding import RoutingEnvelope, TransportClass
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gwwf.config import GwwfSettings
from gwwf.create import CREATE_CMD, command_hash
from gwwf.db.session import session_factory_from
from gwwf.db.store import (
    P,
    active_seasonal_template,
    insert_record,
    load_bundles,
    load_last_observation,
    load_product,
    load_stations,
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
from gwwf.open_meteo import (
    IrradianceHourlyProduct,
    coordinates_from_locator,
    fetch_irradiance_product,
)
from gwwf.record_broadcast import radio_channel_for, record_name
from gwwf.scheduler import (
    EmissionScheduler,
    ForecastStream,
    ObservationStream,
    ProductSource,
    SeriesBinding,
    WeatherMessage,
)
from gwwf.sema.codec import default_codec
from gwwf.sema.enums import LogLevel, Quantity
from gwwf.sema.types import (
    Glitch,
    WeatherChannelGt,
    WeatherCmdAck,
    WeatherCmdNack,
    WeatherCreateCmd,
    WeatherForecast,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherObservation,
    WeatherStationGt,
)
from gwwf.units import UNIT_SCALE

GLITCH_NODE = "scheduler"
TICK_S = 1.0


class ProductAdapter(NamedTuple):
    """How one Method is acquired: ``fetch_for`` builds the fetch from
    the forecast channel record (the SourceLocator is the adapter's
    own encoding), ``kind`` is the product type the cache restores,
    ``series`` picks each quantity's list out of the product."""

    fetch_for: Callable[[WeatherForecastChannelGt], Callable[[int], Any]]
    kind: Callable[..., Any]
    series: Mapping[Quantity, Callable[[Any], list[int]]]


def _nws_fetch_for(channel: WeatherForecastChannelGt) -> Callable[[int], Any]:
    if channel.source_locator is None:
        raise ValueError(f"{channel.name}: no SourceLocator on the record")
    gridpoint = gridpoint_from_locator(channel.source_locator)

    def fetch(slices: int) -> HourlyForecastProduct:
        return fetch_hourly_product(gridpoint=gridpoint, slices=slices)

    return fetch


def _open_meteo_fetch_for(channel: WeatherForecastChannelGt) -> Callable[[int], Any]:
    if channel.source_locator is None:
        raise ValueError(f"{channel.name}: no SourceLocator on the record")
    latitude, longitude = coordinates_from_locator(channel.source_locator)

    def fetch(slices: int) -> IrradianceHourlyProduct:
        return fetch_irradiance_product(
            latitude=latitude,
            longitude=longitude,
            slices=slices,
            now_s=int(time.time()),
        )

    return fetch


ADAPTERS: dict[str, ProductAdapter] = {
    "api.weather.gov.gridpoint.hourly": ProductAdapter(
        fetch_for=_nws_fetch_for,
        kind=HourlyForecastProduct,
        series={
            Quantity.Temperature: lambda p: p.temperature_f,
            Quantity.WindSpeed: lambda p: p.wind_speed_mph,
        },
    ),
    "open.meteo.gfs.seamless.hourly": ProductAdapter(
        fetch_for=_open_meteo_fetch_for,
        kind=IrradianceHourlyProduct,
        series={Quantity.Irradiance: lambda p: p.irradiance_w_m2},
    ),
}


class WeatherActor(GridworksActor):
    """Emits the weather streams on the DB records' schedule."""

    def __init__(
        self,
        settings: GwwfSettings,
        *,
        fetch_observation: Callable[[WeatherStationGt], WeatherObservation]
        | None = None,
        adapters: Mapping[str, ProductAdapter] | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            transport_class=TransportClass.WeatherForecastService,
            my_super_alias=settings.my_super_alias,
            my_time_coordinator_alias=settings.my_time_coordinator_alias,
        )
        self.settings: GwwfSettings = settings
        self._sessions = session_factory_from(settings)
        self._fetch_observation_override = fetch_observation
        self._adapters: dict[str, ProductAdapter] = {**ADAPTERS, **(adapters or {})}
        self.scheduler = self._build_scheduler()
        self.main_thread = threading.Thread(target=self.main, daemon=True)

    # ------------------------------------------------------------------
    # Scheduler construction (from the DB records)
    # ------------------------------------------------------------------

    def _build_scheduler(self) -> EmissionScheduler:
        now_s = int(time.time())
        observation_streams: list[ObservationStream] = []
        forecast_streams: list[ForecastStream] = []
        with self._sessions() as session:
            stations = {s.alias: s for s in load_stations(session)}
            observed: dict[str, dict[str, WeatherChannelGt]] = {}
            for bundle in load_bundles(session):
                for binding in bundle.binding_list:
                    channel = binding.observation_channel
                    observed.setdefault(channel.station_alias, {})[channel.name] = (
                        channel
                    )
                forecast_streams.append(self._forecast_stream(session, bundle, now_s))
            for station_alias, channels in observed.items():
                station = stations[station_alias]
                if self._fetch_observation_override is None and station.icao_id is None:
                    self.logger.warning(
                        "%s: no IcaoId, so no observation stream; its channels "
                        "are forecast only",
                        station_alias,
                    )
                    continue
                observation_streams.append(
                    self._observation_stream(session, station, list(channels.values()))
                )
        return EmissionScheduler(
            observation_streams=observation_streams,
            forecast_streams=forecast_streams,
            publish=self._publish_stream,
            raise_glitch=self._raise_glitch,
            now_s=now_s,
            on_observation_published=self._observation_published,
        )

    def _observation_stream(
        self,
        session: Session,
        station: WeatherStationGt,
        channels: list[WeatherChannelGt],
    ) -> ObservationStream:
        stored = load_last_observation(session, station.alias)
        override = self._fetch_observation_override
        if override is None:
            assert station.icao_id is not None  # _build_scheduler skips the rest
            fetch = self._nws_observation_fetch(station.icao_id, station, channels)
        else:

            def fetch() -> WeatherObservation:
                return override(station)

        return ObservationStream(
            station=station,
            channels=channels,
            fetch=fetch,
            initial_observation=stored.observation if stored else None,
            initial_published_slot=stored.published_slot if stored else None,
        )

    def _forecast_stream(
        self, session: Session, bundle: WeatherForecastBundleGt, now_s: int
    ) -> ForecastStream:
        """One product source per acquisition (method + locator) the
        bundle's bindings name, each serving every binding on it."""
        sources: dict[tuple[str, str], ProductSource] = {}
        for binding in bundle.binding_list:
            channel = binding.forecast_channel
            adapter = self._adapters.get(channel.method)
            if adapter is None:
                raise ValueError(
                    f"{channel.name}: no adapter for Method {channel.method}"
                )
            quantity = binding.observation_channel.quantity
            values = adapter.series.get(quantity)
            if values is None:
                raise ValueError(
                    f"{channel.name}: Method {channel.method} serves no "
                    f"{quantity.value}"
                )
            key = (channel.method, channel.source_locator or "")
            if key not in sources:
                sources[key] = ProductSource(
                    name=f"{channel.method} {channel.source_locator or ''}".strip(),
                    fetch=self._caching_product_fetch(
                        adapter.fetch_for(channel), channel
                    ),
                    series=[],
                    initial_product=load_product(
                        session,
                        method=channel.method,
                        source_locator=channel.source_locator or "",
                        kind=adapter.kind,
                    ),
                )
            sources[key].series.append(
                SeriesBinding(
                    forecast_channel=channel,
                    quantity=quantity,
                    values=values,
                    scale=UNIT_SCALE[binding.observation_channel.unit],
                )
            )
        return ForecastStream(
            bundle=bundle,
            sources=list(sources.values()),
            template=active_seasonal_template(session, bundle.location_alias, now_s),
        )

    # ------------------------------------------------------------------
    # Default source wiring (from the records themselves)
    # ------------------------------------------------------------------

    def _nws_observation_fetch(
        self, icao: str, station: WeatherStationGt, channels: list[WeatherChannelGt]
    ) -> Callable[[], WeatherObservation]:
        def fetch() -> WeatherObservation:
            return fetch_latest_observation(
                station=icao, station_alias=station.alias, channels=channels
            )

        return fetch

    def _caching_product_fetch(
        self, fetch: Callable[[int], P], channel: WeatherForecastChannelGt
    ) -> Callable[[int], P]:
        """Every fetched product is the durable stored rung for the
        channel's acquisition (method + locator)."""

        def caching(slices: int) -> P:
            product = fetch(slices)
            with self._sessions() as session:
                save_product(
                    session,
                    method=channel.method,
                    source_locator=channel.source_locator or "",
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
        if envelope.type_name == CREATE_CMD:
            self._process_create(envelope=envelope, body=body)
            return
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
    # The create round
    # ------------------------------------------------------------------

    def _process_create(self, *, envelope: RoutingEnvelope, body: bytes) -> None:
        # Correlation is the hash of the bytes AS PUBLISHED — the same
        # bytes the sender hashes on its side.
        chash = command_hash(body)
        try:
            cmd = default_codec.from_dict(json.loads(body), expect=WeatherCreateCmd)
            with self._sessions() as session:
                insert_record(session, cmd.record)
        except Exception as e:  # noqa: BLE001 -- a refusal is an answer, not
            # an exception to die on: the consume loop survives, the sender
            # gets the typed verdict with the reason, and — because the nack
            # goes over the bus — the ear captures the refusal as an audit record.
            self._reply(envelope, WeatherCmdNack(command_hash=chash, reason=_reason(e)))
            return
        record = cmd.record
        out = self.broadcast_envelope(
            type_name=record.type_name, radio_channel=radio_channel_for(record)
        )
        self.send(envelope=out, body=json.dumps(record.to_dict()).encode("utf-8"))
        self._reply(envelope, WeatherCmdAck(command_hash=chash))
        self.logger.info("created %s %s", record.type_name, record_name(record))
        try:
            self.scheduler = self._build_scheduler()
        except Exception as e:  # noqa: BLE001 -- the record is in; emission
            # wiring is what failed. Keep the old scheduler running and say so.
            self._raise_glitch(
                LogLevel.Warning, "scheduler rebuild failed after create", str(e)
            )

    def _reply(
        self, envelope: RoutingEnvelope, verdict: WeatherCmdAck | WeatherCmdNack
    ) -> None:
        """The typed verdict (ack/nack), direct to the command's sender."""
        from_class = envelope.from_class
        if from_class is None:
            return  # unknown sender class token — nowhere to address the reply
        self.send(
            envelope=self.direct_envelope(
                type_name=verdict.type_name,
                to_class=from_class,
                to_alias=envelope.from_alias,
            ),
            body=verdict.to_bytes(),
        )

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
        if isinstance(message, WeatherForecast):
            with self._sessions() as session:
                store_forecast(session, message)

    def _observation_published(
        self, observation: WeatherObservation, slot_s: int
    ) -> None:
        with self._sessions() as session:
            save_last_observation(
                session, observation=observation, published_slot=slot_s
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


def _reason(e: Exception) -> str:
    """The refusal reason, verbatim from the refusing check — the DB
    driver's message for integrity refusals, str(e) otherwise."""
    if isinstance(e, IntegrityError) and e.orig is not None:
        return str(e.orig).strip()
    return str(e)
