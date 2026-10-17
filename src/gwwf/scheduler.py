"""Record-driven emission scheduler — framework-free.

Cadence comes entirely from the records (``EmitPeriodS`` /
``EmitOffsetS``); slice arithmetic walks the bundle channels' shared
``SliceDurationSList`` natively (per-slice lengths may differ). The
scheduler knows nothing of rabbit, NWS or Open-Meteo: fetchers and the
publish hook are injected, so ``WeatherActor`` wires production and
tests drive everything with fakes. A fast-record witness runs the same
code at second-scale periods.

Observation contract: one stream per station, latest-only, a stale
observation is never re-published, silence when nothing new; on
recovery the missed grid points are replayed as interpolated messages
(``Interpolated: true``) when the bracketing real observations are
≤ ``max_fill_s`` apart, each channel both brackets report. Forecast
contract: one ``gw.weather.forecast`` per BUNDLE per slot, always
emitted on schedule; every series is served by a source product and
walks its own fidelity ladder live → stored → seasonal template, and a
step down is a glitch. The template rung is the bundle's location
record of design-cold temperatures by month laid on the grid for a
temperature series, zeros for every other quantity; a temperature
series with no template glitches and skips the slot instead.
In-memory state only: the stored products and last-observation memory
reset on boot unless the actor restores them from the DB via the
streams' initial fields.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple, Protocol

from datetime import UTC, datetime

from gwwf.grid import (
    GridError,
    interpolate_at,
    iso_to_s,
    lay_on_grid,
    next_slot,
    s_to_iso,
    slice_starts,
    slots_between,
)
from gwwf.fidelity import FIDELITY_RANK
from gwwf.sema.enums import LogLevel, Quantity, WeatherForecastFidelity
from gwwf.sema.property_format import UtcIso8601Seconds
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecast,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherForecastSeries,
    WeatherObservation,
    WeatherObservationReading,
    WeatherSeasonalTemplateGt,
    WeatherStationGt,
)

WeatherMessage = WeatherObservation | WeatherForecast
Publisher = Callable[[WeatherMessage, str], None]  # (message, radio_channel)
GlitchRaiser = Callable[[LogLevel, str, str], None]  # (level, summary, details)


class HourlyProduct(Protocol):
    """What the scheduler needs of any adapter's product: a uniform
    series from ``first_slice_start`` at ``period_s``, stamped with the
    source's own revision time. The value lists are reached through
    the series bindings, so each adapter keeps its own field names."""

    @property
    def update_time(self) -> UtcIso8601Seconds: ...
    @property
    def first_slice_start(self) -> UtcIso8601Seconds: ...
    @property
    def period_s(self) -> int: ...


class ObservationStream(NamedTuple):
    """One station's observed channels + the fetch that serves them.

    The channels share the station and emit schedule (validated at
    scheduler construction); ``fetch`` returns the newest station
    observation. ``initial_observation`` / ``initial_published_slot``
    restore the stream's last-real state across a restart (the actor
    loads them from the DB; the scheduler itself stays storage-free).
    """

    station: WeatherStationGt
    channels: list[WeatherChannelGt]
    fetch: Callable[[], WeatherObservation]
    initial_observation: WeatherObservation | None = None
    initial_published_slot: int | None = None


class SeriesBinding(NamedTuple):
    """One bundle series served by a product: the forecast channel it
    goes out on, ``values`` picking the product's list for it (natural
    units), and the scale onto the channel's scaled-integer Unit."""

    forecast_channel: WeatherForecastChannelGt
    quantity: Quantity
    values: Callable[[Any], list[int]]
    scale: int


class ProductSource(NamedTuple):
    """One acquisition serving one or more of a bundle's series: the
    fetch, the series it serves, and the stored product that restores
    the stored rung across a restart. ``name`` labels it in glitches."""

    name: str
    fetch: Callable[[int], Any]  # (slices) -> product
    series: list[SeriesBinding]
    initial_product: Any = None


class ForecastStream(NamedTuple):
    """One bundle + the product sources that serve its series.

    The bundle's channels define the slice grid and emission schedule
    (axiom-shared); every binding of the bundle is served by exactly
    one source. ``template`` is the location's seasonal template, a
    temperature series' last rung, None when the location has none.
    """

    bundle: WeatherForecastBundleGt
    sources: list[ProductSource]
    template: WeatherSeasonalTemplateGt | None = None


@dataclass
class _ObservationState:
    last_slot: int
    last_real: WeatherObservation | None = None
    # The slot at which last_real was published: replay fills only
    # slots after it (that slot itself carried the real message).
    published_slot: int | None = None


@dataclass
class _ForecastState:
    last_slot: int
    stored: dict[str, Any] = field(default_factory=dict)  # by source name
    last_fidelity: dict[str, WeatherForecastFidelity] = field(default_factory=dict)


class _Series(NamedTuple):
    """One series resolved for a slot: the values on the bundle grid,
    scaled, the rung they came from, and the source stamp behind them.
    """

    values: list[int]
    fidelity: WeatherForecastFidelity
    source_updated_time: UtcIso8601Seconds


def _schedule(channels: Sequence[WeatherChannelGt]) -> tuple[int, int]:
    periods = {(c.emit_period_s, c.emit_offset_s) for c in channels}
    if len(periods) != 1:
        raise ValueError(f"channels disagree on emit schedule: {periods}")
    return periods.pop()


class EmissionScheduler:
    def __init__(
        self,
        *,
        observation_streams: list[ObservationStream],
        forecast_streams: list[ForecastStream],
        publish: Publisher,
        raise_glitch: GlitchRaiser,
        now_s: int,
        stale_after_s: int = 3600,
        max_fill_s: int = 3 * 3600,
        stored_horizon_slices: int = 24,
        on_observation_published: Callable[[WeatherObservation, int], None]
        | None = None,
    ) -> None:
        for stream in observation_streams:
            stations = {c.station_alias for c in stream.channels}
            if stations != {stream.station.alias}:
                raise ValueError(
                    f"{stream.station.alias}: observation stream channels are on "
                    f"{stations}"
                )
        self._observations = [
            (
                stream,
                _schedule(stream.channels),
                _ObservationState(
                    last_slot=now_s,
                    last_real=stream.initial_observation,
                    published_slot=stream.initial_published_slot,
                ),
            )
            for stream in observation_streams
        ]
        for forecast_stream in forecast_streams:
            bound = [
                b.forecast_channel.name for b in forecast_stream.bundle.binding_list
            ]
            served = [
                s.forecast_channel.name
                for source in forecast_stream.sources
                for s in source.series
            ]
            if sorted(bound) != sorted(served):
                raise ValueError(
                    f"{forecast_stream.bundle.name}: sources serve {sorted(served)}, "
                    f"the bundle binds {sorted(bound)}"
                )
        self._forecasts = [
            (
                stream,
                (stream.bundle.emit_period_s, stream.bundle.emit_offset_s),
                _ForecastState(
                    last_slot=now_s,
                    stored={
                        source.name: source.initial_product
                        for source in stream.sources
                        if source.initial_product is not None
                    },
                ),
            )
            for stream in forecast_streams
        ]
        self._publish = publish
        self._raise_glitch = raise_glitch
        self._stale_after_s = stale_after_s
        self._max_fill_s = max_fill_s
        self._stored_horizon_slices = stored_horizon_slices
        self._on_observation_published = on_observation_published

    def run_pending(self, now_s: int) -> None:
        """Fire every stream whose slot has passed; call every ~second."""
        for stream, (period_s, offset_s), state in self._observations:
            due = slots_between(state.last_slot, now_s, period_s, offset_s)
            if due:
                state.last_slot = due[-1]
                self._fire_observation(stream, period_s, offset_s, state, due[-1])
        for fc_stream, (fc_period_s, fc_offset_s), fc_state in self._forecasts:
            due = slots_between(fc_state.last_slot, now_s, fc_period_s, fc_offset_s)
            if due:
                fc_state.last_slot = due[-1]
                self._fire_forecast(fc_stream, fc_state, due[-1])

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def _fire_observation(
        self,
        stream: ObservationStream,
        period_s: int,
        offset_s: int,
        state: _ObservationState,
        slot_s: int,
    ) -> None:
        station = stream.station.alias
        try:
            observation = stream.fetch()
        except Exception as e:  # noqa: BLE001 -- a failed fetch is a glitch, not a crash
            self._raise_glitch(
                LogLevel.Warning, f"{station}: observation fetch failed", repr(e)
            )
            return
        observed_s = iso_to_s(observation.observation_time)

        previous = state.last_real
        if previous is not None and observed_s <= iso_to_s(previous.observation_time):
            # Nothing new — the slot stays silent; a stale observation
            # is never re-published.
            if slot_s - observed_s > self._stale_after_s:
                self._raise_glitch(
                    LogLevel.Warning,
                    f"{station}: no fresh observation at slot",
                    f"newest is {observation.observation_time} "
                    f"({slot_s - observed_s}s old at {s_to_iso(slot_s)})",
                )
            return

        if previous is not None and state.published_slot is not None:
            self._replay_gap(
                period_s, offset_s, previous, observation, state.published_slot
            )
        self._publish(observation, station)
        state.published_slot = slot_s
        if self._on_observation_published is not None:
            self._on_observation_published(observation, slot_s)
        if slot_s - observed_s > self._stale_after_s:
            self._raise_glitch(
                LogLevel.Warning,
                f"{station}: observation stale at publish",
                f"{observation.observation_time} at slot {s_to_iso(slot_s)}",
            )
        state.last_real = observation

    def _replay_gap(
        self,
        period_s: int,
        offset_s: int,
        previous: WeatherObservation,
        current: WeatherObservation,
        published_slot: int,
    ) -> None:
        """Interpolated messages for grid points missed between two reals.

        A channel interpolates when both bracketing observations report
        it; a slot with no such channel is left silent.
        """
        previous_s = iso_to_s(previous.observation_time)
        current_s = iso_to_s(current.observation_time)
        missed = [
            slot
            for slot in slots_between(published_slot, current_s, period_s, offset_s)
            if slot < current_s
        ]
        if not missed:
            return
        if current_s - previous_s > self._max_fill_s:
            self._raise_glitch(
                LogLevel.Info,
                f"{current.station_alias}: gap not filled",
                f"{previous.observation_time} → {current.observation_time} "
                f"exceeds {self._max_fill_s}s; archive backfill covers it",
            )
            return
        before = {r.channel_name: r for r in previous.reading_list}
        for slot in missed:
            readings = [
                WeatherObservationReading(
                    channel_name=reading.channel_name,
                    quantity=reading.quantity,
                    value=interpolate_at(
                        slot,
                        t0_s=previous_s,
                        v0=before[reading.channel_name].value,
                        t1_s=current_s,
                        v1=reading.value,
                    ),
                )
                for reading in current.reading_list
                if reading.channel_name in before
            ]
            if not readings:
                continue
            self._publish(
                WeatherObservation(
                    station_alias=current.station_alias,
                    observation_time=s_to_iso(slot),
                    interpolated=True,
                    reading_list=readings,
                ),
                current.station_alias,
            )

    # ------------------------------------------------------------------
    # Forecasts
    # ------------------------------------------------------------------

    def _fire_forecast(
        self, stream: ForecastStream, state: _ForecastState, slot_s: int
    ) -> None:
        bundle = stream.bundle
        grid_channel = bundle.binding_list[0].forecast_channel
        durations = grid_channel.slice_duration_s_list
        first_s = next_slot(slot_s, durations[0], 0)
        starts = slice_starts(first_s, durations)
        horizon = grid_channel.total_slices + self._stored_horizon_slices

        resolved: dict[str, _Series] = {}
        for source in stream.sources:
            series = self._source_series(stream, source, state, starts, horizon)
            if series is None:
                return  # no rung left for a temperature series: glitched, slot skipped
            resolved.update(series)
        for binding in bundle.binding_list:
            name = binding.forecast_channel.name
            self._note_rung(
                name, state.last_fidelity.get(name), resolved[name].fidelity
            )
            state.last_fidelity[name] = resolved[name].fidelity
        message = WeatherForecast(
            bundle_name=bundle.name,
            message_created_ms=slot_s * 1000,
            first_slice_start=s_to_iso(first_s),
            series_list=[
                WeatherForecastSeries(
                    channel_name=binding.forecast_channel.name,
                    quantity=binding.observation_channel.quantity,
                    values=resolved[binding.forecast_channel.name].values,
                    fidelity=resolved[binding.forecast_channel.name].fidelity,
                    source_updated_time=resolved[
                        binding.forecast_channel.name
                    ].source_updated_time,
                )
                for binding in bundle.binding_list
            ],
        )
        self._publish(message, bundle.name)

    def _note_rung(
        self,
        series: str,
        last: WeatherForecastFidelity | None,
        fidelity: WeatherForecastFidelity,
    ) -> None:
        """A step down the ladder since the last emission is a glitch."""
        if last is not None and FIDELITY_RANK[fidelity] > FIDELITY_RANK[last]:
            self._raise_glitch(
                LogLevel.Warning,
                "forecast fidelity downgrade",
                f"{series}: {last.value} → {fidelity.value}",
            )

    def _source_series(
        self,
        stream: ForecastStream,
        source: ProductSource,
        state: _ForecastState,
        starts: list[int],
        horizon: int,
    ) -> dict[str, _Series] | None:
        """Every series one source serves, off the product's ladder;
        None when a temperature series reaches the template rung and
        the location has no template (glitched here, the slot is
        skipped)."""
        fidelity = WeatherForecastFidelity.Live
        why = ""
        try:
            state.stored[source.name] = source.fetch(horizon)
        except Exception as e:  # noqa: BLE001 -- ladder rung, not a crash
            if source.name not in state.stored:
                fidelity = WeatherForecastFidelity.SeasonalTemplate
                why = f"no live product and no stored revision: {e!r}"
            else:
                fidelity = WeatherForecastFidelity.Stored
        resolved: dict[str, _Series] = {}
        product: HourlyProduct | None = None
        if fidelity is not WeatherForecastFidelity.SeasonalTemplate:
            product = state.stored[source.name]
            source_start_s = iso_to_s(product.first_slice_start)
            try:
                for binding in source.series:
                    values = lay_on_grid(
                        starts,
                        source_start_s=source_start_s,
                        source_period_s=product.period_s,
                        source_values=binding.values(product),
                    )
                    resolved[binding.forecast_channel.name] = _Series(
                        [v * binding.scale for v in values],
                        fidelity,
                        product.update_time,
                    )
            except GridError as e:
                fidelity = WeatherForecastFidelity.SeasonalTemplate
                why = f"stored horizon exhausted: {e!r}"
                resolved = {}
            else:
                return resolved
        for binding in source.series:
            series = self._template_series(stream, binding, starts, why)
            if series is None:
                return None
            resolved[binding.forecast_channel.name] = series
        return resolved

    def _template_series(
        self,
        stream: ForecastStream,
        binding: SeriesBinding,
        starts: list[int],
        why: str,
    ) -> _Series | None:
        """The template rung: each slice at its month's template
        temperature (the template is degrees Fahrenheit times 100; the
        channel's own scaling applies) for a temperature series, zeros
        stamped with the bundle's own Start for any other quantity."""
        if binding.quantity is not Quantity.Temperature:
            return _Series(
                [0] * len(starts),
                WeatherForecastFidelity.SeasonalTemplate,
                stream.bundle.start,
            )
        template = stream.template
        if template is None:
            self._raise_glitch(
                LogLevel.Error, "no seasonal template", f"slot skipped; {why}"
            )
            return None
        return _Series(
            [
                round(
                    template.temp_by_month[datetime.fromtimestamp(s, UTC).month - 1]
                    * binding.scale
                    / 100
                )
                for s in starts
            ],
            WeatherForecastFidelity.SeasonalTemplate,
            template.start,
        )
