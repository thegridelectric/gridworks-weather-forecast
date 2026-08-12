"""Record-driven emission scheduler — framework-free.

Cadence comes entirely from the records (``EmitPeriodS`` /
``EmitOffsetS``); slice arithmetic walks the bundle channels' shared
``SliceDurationSList`` natively (per-slice lengths may differ). The
scheduler knows nothing of rabbit or NWS: fetchers and the publish
hook are injected, so ``WeatherActor`` wires production and tests
drive everything with fakes. A fast-record witness runs the same code
at second-scale periods.

Observation contract: latest-only, a stale observation is never
re-published, silence when nothing new; on recovery the missed grid
points are replayed as interpolated messages (``Interpolated: true``)
when the bracketing real observations are ≤ ``max_fill_s`` apart —
temperature always, wind only when both bracketing observations carry
it. Forecast contract: one ``gw.weather.forecast`` per BUNDLE per
slot, always emitted on schedule; fidelity ladder live → stored; the
seasonal-template rung is declared but unbuilt — reaching it glitches
and skips (Open until a template data source is chosen). In-memory
state only: the stored product and last-observation memory reset on
boot unless the actor restores them from the DB via the streams'
initial fields.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable, NamedTuple

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
from gwwf.nws import HourlyForecastProduct
from gwwf.sema.enums import GwWeatherForecastFidelity, LogLevel
from gwwf.sema.types import (
    GwWeatherChannelGt,
    GwWeatherForecast,
    GwWeatherForecastBundleGt,
    GwWeatherObservation,
)

WeatherMessage = GwWeatherObservation | GwWeatherForecast
Publisher = Callable[[WeatherMessage, str], None]  # (message, radio_channel)
GlitchRaiser = Callable[[LogLevel, str, str], None]  # (level, summary, details)

_FIDELITY_RANK = {
    GwWeatherForecastFidelity.Live: 0,
    GwWeatherForecastFidelity.Stored: 1,
    GwWeatherForecastFidelity.SeasonalTemplate: 2,
}


class ObservationStream(NamedTuple):
    """One station's observed channels + the fetch that serves them.

    Both channels share the location and emit schedule (validated at
    scheduler construction); ``fetch`` returns the newest station
    observation. ``initial_observation`` / ``initial_published_slot``
    restore the stream's last-real state across a restart (the actor
    loads them from the DB; the scheduler itself stays storage-free).
    """

    channels: list[GwWeatherChannelGt]
    fetch: Callable[[], GwWeatherObservation]
    initial_observation: GwWeatherObservation | None = None
    initial_published_slot: int | None = None


class ForecastStream(NamedTuple):
    """One bundle + the source-product fetch that serves it.

    The bundle's channels carry the slice grid and emission schedule
    (axiom-shared). ``temp_scale`` / ``wind_speed_scale`` map the
    product's natural units onto the channels' scaled-integer units —
    supplied by the actor from the observation channels' Units.
    ``initial_product`` restores the stored rung across a restart.
    """

    bundle: GwWeatherForecastBundleGt
    fetch: Callable[[int], HourlyForecastProduct]  # (slices) -> product
    temp_scale: int
    wind_speed_scale: int
    initial_product: HourlyForecastProduct | None = None


@dataclass
class _ObservationState:
    last_slot: int
    last_real: GwWeatherObservation | None = None
    # The slot at which last_real was published: replay fills only
    # slots after it (that slot itself carried the real message).
    published_slot: int | None = None


@dataclass
class _ForecastState:
    last_slot: int
    stored: HourlyForecastProduct | None = None
    last_fidelity: GwWeatherForecastFidelity | None = None


def _schedule(channels: Sequence[GwWeatherChannelGt]) -> tuple[int, int]:
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
        on_observation_published: Callable[[str, GwWeatherObservation, int], None]
        | None = None,
    ) -> None:
        for stream in observation_streams:
            locations = {c.location_alias for c in stream.channels}
            if len(locations) != 1:
                raise ValueError(f"observation stream spans locations: {locations}")
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
        self._forecasts = [
            (
                stream,
                (stream.bundle.emit_period_s, stream.bundle.emit_offset_s),
                _ForecastState(last_slot=now_s, stored=stream.initial_product),
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
        location = stream.channels[0].location_alias
        try:
            observation = stream.fetch()
        except Exception as e:  # noqa: BLE001 -- a failed fetch is a glitch, not a crash
            self._raise_glitch(
                LogLevel.Warning, f"{location}: observation fetch failed", repr(e)
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
                    f"{location}: no fresh observation at slot",
                    f"newest is {observation.observation_time} "
                    f"({slot_s - observed_s}s old at {s_to_iso(slot_s)})",
                )
            return

        if previous is not None and state.published_slot is not None:
            self._replay_gap(
                period_s, offset_s, previous, observation, state.published_slot
            )
        self._publish(observation, location)
        state.published_slot = slot_s
        if self._on_observation_published is not None:
            self._on_observation_published(location, observation, slot_s)
        if slot_s - observed_s > self._stale_after_s:
            self._raise_glitch(
                LogLevel.Warning,
                f"{location}: observation stale at publish",
                f"{observation.observation_time} at slot {s_to_iso(slot_s)}",
            )
        state.last_real = observation

    def _replay_gap(
        self,
        period_s: int,
        offset_s: int,
        previous: GwWeatherObservation,
        current: GwWeatherObservation,
        published_slot: int,
    ) -> None:
        """Interpolated messages for grid points missed between two reals.

        Temperature interpolates always (required on both sides); wind
        only when both bracketing observations carry it.
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
                f"{current.location_alias}: gap not filled",
                f"{previous.observation_time} → {current.observation_time} "
                f"exceeds {self._max_fill_s}s; archive backfill covers it",
            )
            return
        for slot in missed:
            wind_value = None
            if (
                previous.wind_speed_value is not None
                and current.wind_speed_value is not None
            ):
                wind_value = interpolate_at(
                    slot,
                    t0_s=previous_s,
                    v0=previous.wind_speed_value,
                    t1_s=current_s,
                    v1=current.wind_speed_value,
                )
            self._publish(
                GwWeatherObservation(
                    location_alias=current.location_alias,
                    observation_time=s_to_iso(slot),
                    interpolated=True,
                    temp_channel_name=current.temp_channel_name,
                    temp_value=interpolate_at(
                        slot,
                        t0_s=previous_s,
                        v0=previous.temp_value,
                        t1_s=current_s,
                        v1=current.temp_value,
                    ),
                    wind_speed_channel_name=current.wind_speed_channel_name,
                    wind_speed_value=wind_value,
                ),
                current.location_alias,
            )

    # ------------------------------------------------------------------
    # Forecasts
    # ------------------------------------------------------------------

    def _fire_forecast(
        self, stream: ForecastStream, state: _ForecastState, slot_s: int
    ) -> None:
        bundle = stream.bundle
        horizon = bundle.temp_forecast_channel.total_slices
        fidelity = GwWeatherForecastFidelity.Live
        try:
            product = stream.fetch(horizon + self._stored_horizon_slices)
            state.stored = product
        except Exception as e:  # noqa: BLE001 -- ladder rung, not a crash
            if state.stored is None:
                self._raise_glitch(
                    LogLevel.Error,
                    "no live product and no stored revision",
                    f"seasonal-template rung unbuilt; slot skipped: {e!r}",
                )
                return
            product = state.stored
            fidelity = GwWeatherForecastFidelity.Stored
        if (
            state.last_fidelity is not None
            and _FIDELITY_RANK[fidelity] > _FIDELITY_RANK[state.last_fidelity]
        ):
            self._raise_glitch(
                LogLevel.Warning,
                "forecast fidelity downgrade",
                f"{state.last_fidelity.value} → {fidelity.value}",
            )
        try:
            message = self._build_forecast(stream, product, fidelity, slot_s)
        except GridError as e:
            self._raise_glitch(
                LogLevel.Error,
                "stored horizon exhausted",
                f"seasonal-template rung unbuilt; slot skipped: {e!r}",
            )
            return
        self._publish(message, bundle.name)
        state.last_fidelity = fidelity

    def _build_forecast(
        self,
        stream: ForecastStream,
        product: HourlyForecastProduct,
        fidelity: GwWeatherForecastFidelity,
        slot_s: int,
    ) -> GwWeatherForecast:
        bundle = stream.bundle
        durations = bundle.temp_forecast_channel.slice_duration_s_list
        first_s = next_slot(slot_s, durations[0], 0)
        starts = slice_starts(first_s, durations)
        source_start_s = iso_to_s(product.first_slice_start)
        temp_values = lay_on_grid(
            starts,
            source_start_s=source_start_s,
            source_period_s=product.period_s,
            source_values=product.temperature_f,
        )
        wind_values = lay_on_grid(
            starts,
            source_start_s=source_start_s,
            source_period_s=product.period_s,
            source_values=product.wind_speed_mph,
        )
        return GwWeatherForecast(
            bundle_name=bundle.name,
            source_updated_time=product.update_time,
            message_created_ms=slot_s * 1000,
            fidelity=fidelity,
            first_slice_start=s_to_iso(first_s),
            temp_channel_name=bundle.temp_forecast_channel.name,
            temp_values=[v * stream.temp_scale for v in temp_values],
            wind_speed_channel_name=bundle.wind_speed_forecast_channel.name,
            wind_speed_values=[v * stream.wind_speed_scale for v in wind_values],
        )
