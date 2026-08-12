"""EmissionScheduler behavior, driven entirely by fakes — no broker.

Channels here use second-scale emit periods and non-uniform slice
lists on purpose: cadence and slice arithmetic must come from the
records alone.
"""

from __future__ import annotations

from gwwf.grid import s_to_iso
from gwwf.nws import HourlyForecastProduct
from gwwf.scheduler import (
    EmissionScheduler,
    ForecastStream,
    ObservationStream,
)
from gwwf.sema.enums import (
    Quantity,
    Unit,
    WeatherForecastFidelity,
    LogLevel,
)
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherObservation,
)

LOCATION = "d1.test.loc"
TEMP = "d1.test.loc.temperature"
WIND = "d1.test.loc.windspeed"
TEMP_FC = "d1.test.loc.temperature.forecast.fake.hourly"
WIND_FC = "d1.test.loc.windspeed.forecast.fake.hourly"
BUNDLE_NAME = "d1.test.loc.forecast.fake.hourly"

TEMP_CH = WeatherChannelGt(
    name=TEMP,
    display_name="test temperature",
    quantity=Quantity.Temperature,
    unit=Unit.FahrenheitX100,
    location_alias=LOCATION,
    emit_period_s=60,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id="6ffaacf0-701d-49d8-a20b-cd89de9a21ea",
)
WIND_CH = WeatherChannelGt(
    name=WIND,
    display_name="test wind",
    quantity=Quantity.WindSpeed,
    unit=Unit.MilesPerHourX1000,
    location_alias=LOCATION,
    emit_period_s=60,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id="2ee66a85-a869-4a34-98c2-3dd93718ce8a",
)


def forecast_channel(name: str, target: str, cid: str) -> WeatherForecastChannelGt:
    return WeatherForecastChannelGt(
        name=name,
        target_channel_name=target,
        forecaster="fake.model",
        method="fake.method",
        total_slices=3,
        slice_duration_s_list=[300, 600, 300],  # non-uniform on purpose
        forecast_duration_minutes=20,
        start="2026-08-11T00:00:00Z",
        id=cid,
    )


BUNDLE = WeatherForecastBundleGt(
    name=BUNDLE_NAME,
    display_name="test bundle",
    location_alias=LOCATION,
    temp_forecast_channel=forecast_channel(
        TEMP_FC, TEMP, "dc3d3844-6c8c-466b-8140-eb06900b4bf8"
    ),
    temp_observation_channel=TEMP_CH,
    wind_speed_forecast_channel=forecast_channel(
        WIND_FC, WIND, "23cc841c-8e12-4d1e-9030-ed9ef8923592"
    ),
    wind_speed_observation_channel=WIND_CH,
    emit_period_s=60,
    emit_offset_s=30,
    start="2026-08-12T00:00:00Z",
    id="309885e5-57eb-4afc-9269-5740fde5d27e",
)


def obs_at(epoch_s: int, temp: int, wind: int | None = None) -> WeatherObservation:
    return WeatherObservation(
        location_alias=LOCATION,
        observation_time=s_to_iso(epoch_s),
        interpolated=False,
        temp_channel_name=TEMP,
        temp_value=temp,
        wind_speed_channel_name=WIND,
        wind_speed_value=wind,
    )


def product_from(
    first_start_s: int, temps: list[int], winds: list[int] | None = None
) -> HourlyForecastProduct:
    return HourlyForecastProduct(
        update_time=s_to_iso(first_start_s),
        generated_at=s_to_iso(first_start_s),
        first_slice_start=s_to_iso(first_start_s),
        period_s=3600,
        temperature_f=temps,
        wind_speed_mph=winds if winds is not None else [0] * len(temps),
    )


class Harness:
    def __init__(
        self,
        *,
        observations: list | None = None,
        products: list | None = None,
        max_fill_s: int = 3 * 3600,
        initial_observation: WeatherObservation | None = None,
        initial_published_slot: int | None = None,
        now_s: int = 0,
    ) -> None:
        self.published: list[tuple[object, str]] = []
        self.glitches: list[tuple[LogLevel, str, str]] = []
        self._observations = list(observations or [])
        self._products = list(products or [])
        streams = {}
        if observations is not None:
            streams["observation_streams"] = [
                ObservationStream(
                    channels=[TEMP_CH, WIND_CH],
                    fetch=self._next_obs,
                    initial_observation=initial_observation,
                    initial_published_slot=initial_published_slot,
                )
            ]
        else:
            streams["observation_streams"] = []
        if products is not None:
            streams["forecast_streams"] = [
                ForecastStream(
                    bundle=BUNDLE,
                    fetch=self._next_product,
                    temp_scale=100,
                    wind_speed_scale=1000,
                )
            ]
        else:
            streams["forecast_streams"] = []
        self.scheduler = EmissionScheduler(
            publish=lambda message, radio: self.published.append((message, radio)),
            raise_glitch=lambda level, summary, details: self.glitches.append((
                level,
                summary,
                details,
            )),
            now_s=now_s,
            max_fill_s=max_fill_s,
            **streams,
        )

    def _next_obs(self) -> WeatherObservation:
        item = self._observations.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _next_product(self, slices: int) -> HourlyForecastProduct:
        item = self._products.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_observation_published_on_slot_with_location_radio() -> None:
    h = Harness(observations=[obs_at(55, 7000, 4000)])
    h.scheduler.run_pending(60)
    assert len(h.published) == 1
    message, radio = h.published[0]
    assert radio == LOCATION
    assert message.observation_time == s_to_iso(55)
    assert message.interpolated is False
    assert message.temp_value == 7000
    assert message.wind_speed_value == 4000
    assert h.glitches == []


def test_same_observation_stays_silent_and_glitches_when_stale() -> None:
    h = Harness(observations=[obs_at(55, 7000), obs_at(55, 7000), obs_at(55, 7000)])
    h.scheduler.run_pending(60)
    h.scheduler.run_pending(120)  # same observation: silence, still fresh
    assert len(h.published) == 1
    assert h.glitches == []
    h.scheduler.run_pending(5000)  # same observation, now >1 h old at slot
    assert len(h.published) == 1
    assert len(h.glitches) == 1
    assert h.glitches[0][0] == LogLevel.Warning


def test_recovery_replays_interpolated_grid_points() -> None:
    # Real observations at 55 and 235; slots 120 and 180 missed.
    h = Harness(observations=[obs_at(55, 1000, 2000), obs_at(235, 1900, 5000)])
    h.scheduler.run_pending(60)
    h.scheduler.run_pending(240)
    kinds = [(m.interpolated, m.observation_time) for m, _ in h.published]
    assert kinds == [
        (False, s_to_iso(55)),
        (True, s_to_iso(120)),
        (True, s_to_iso(180)),
        (False, s_to_iso(235)),
    ]
    filled = h.published[1][0]
    # Linear between (55, 1000) and (235, 1900) at t=120: 1000 + 900*65/180
    assert filled.temp_value == 1325
    assert filled.wind_speed_value == 3083  # 2000 + 3000*65/180
    assert h.glitches == []


def test_recovery_omits_wind_when_a_bracket_lacks_it() -> None:
    h = Harness(observations=[obs_at(55, 1000), obs_at(235, 1900, 5000)])
    h.scheduler.run_pending(60)
    h.scheduler.run_pending(240)
    filled = [m for m, _ in h.published if m.interpolated]
    assert filled and all(m.wind_speed_value is None for m in filled)
    assert all(m.temp_value is not None for m in filled)


def test_long_gap_stays_a_gap() -> None:
    h = Harness(observations=[obs_at(55, 1000), obs_at(355, 1900)], max_fill_s=120)
    h.scheduler.run_pending(60)
    h.scheduler.run_pending(360)
    kinds = [(m.interpolated, m.observation_time) for m, _ in h.published]
    assert kinds == [(False, s_to_iso(55)), (False, s_to_iso(355))]
    assert len(h.glitches) == 1
    assert h.glitches[0][0] == LogLevel.Info


def test_restart_restores_latest_only_and_replay() -> None:
    # A fresh scheduler carrying restored state (the actor loads it
    # from the DB) behaves as if it never stopped.
    h = Harness(
        observations=[obs_at(55, 1000, 2000), obs_at(235, 1900, 5000)],
        initial_observation=obs_at(55, 1000, 2000),
        initial_published_slot=60,
    )
    h.scheduler.run_pending(120)  # same observation: latest-only holds
    assert h.published == []
    h.scheduler.run_pending(240)  # fresh observation: replay bridges the gap
    kinds = [(m.interpolated, m.observation_time) for m, _ in h.published]
    assert kinds == [
        (True, s_to_iso(120)),
        (True, s_to_iso(180)),
        (False, s_to_iso(235)),
    ]


B = 1_786_000_200  # Aug 2026, multiple of 300 — utc.milliseconds needs real epochs


def test_forecast_live_emits_one_bundle_message_on_channel_grid() -> None:
    h = Harness(products=[product_from(B, [10, 20], [5, 6])], now_s=B)
    h.scheduler.run_pending(B + 90)
    assert len(h.published) == 1
    message, radio = h.published[0]
    assert radio == BUNDLE_NAME
    assert message.bundle_name == BUNDLE_NAME
    assert message.fidelity == WeatherForecastFidelity.Live
    assert message.source_updated_time == s_to_iso(B)
    assert message.message_created_ms == (B + 90) * 1000
    # First slice: next 300-boundary after the slot → B+300; starts
    # B+300/B+600/B+1200, all inside source hour 0, scaled ×100 / ×1000.
    assert message.first_slice_start == s_to_iso(B + 300)
    assert message.temp_values == [1000, 1000, 1000]
    assert message.wind_speed_values == [5000, 5000, 5000]
    assert h.glitches == []


def test_forecast_falls_back_to_stored_with_downgrade_glitch() -> None:
    h = Harness(products=[product_from(B, [10, 20]), RuntimeError("nws down")], now_s=B)
    h.scheduler.run_pending(B + 90)
    h.scheduler.run_pending(B + 150)
    assert len(h.published) == 2
    assert h.published[1][0].fidelity == WeatherForecastFidelity.Stored
    assert len(h.glitches) == 1
    assert h.glitches[0][0] == LogLevel.Warning
    assert "downgrade" in h.glitches[0][1]


def test_forecast_with_no_product_at_all_glitches_and_skips() -> None:
    h = Harness(products=[RuntimeError("nws down")], now_s=B)
    h.scheduler.run_pending(B + 90)
    assert h.published == []
    assert len(h.glitches) == 1
    assert h.glitches[0][0] == LogLevel.Error


def test_stored_horizon_exhaustion_glitches_and_skips() -> None:
    h = Harness(
        products=[
            product_from(B, [10, 20]),
            RuntimeError("down"),
            RuntimeError("down"),
        ],
        now_s=B,
    )
    h.scheduler.run_pending(B + 90)  # live, stored covers B..B+7200
    h.scheduler.run_pending(B + 7500)  # next grid start B+7800 — beyond stored
    assert len(h.published) == 1
    errors = [g for g in h.glitches if g[0] == LogLevel.Error]
    assert len(errors) == 1
    assert "exhausted" in errors[0][1]
