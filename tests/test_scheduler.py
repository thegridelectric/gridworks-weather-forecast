"""EmissionScheduler behavior, driven entirely by fakes — no broker.

Channels here use second-scale emit periods and non-uniform slice
lists on purpose: cadence and slice arithmetic must come from the
records alone.
"""

from __future__ import annotations

import pytest

from gwwf.grid import s_to_iso
from gwwf.nws import HourlyForecastProduct
from gwwf.open_meteo import IrradianceHourlyProduct
from gwwf.scheduler import (
    EmissionScheduler,
    ForecastStream,
    ObservationStream,
    ProductSource,
    SeriesBinding,
)
from gwwf.sema.enums import (
    Quantity,
    Unit,
    WeatherForecastFidelity,
    LogLevel,
)
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecastBinding,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherObservation,
    WeatherObservationReading,
    WeatherSeasonalTemplateGt,
    WeatherStationGt,
)

LOCATION = "d1.test.loc"
STATION = "fake"
TEMP = "fake.temperature"
WIND = "fake.windspeed"
IRR = "fake.irradiance"
TEMP_FC = "fake.temperature.forecast.fake.hourly"
WIND_FC = "fake.windspeed.forecast.fake.hourly"
IRR_FC = "fake.irradiance.forecast.fake.hourly"
BUNDLE_NAME = "d1.test.loc.raccoon"
SMALL_BUNDLE_NAME = "d1.test.loc.bobcat"

STATION_RECORD = WeatherStationGt(
    alias=STATION,
    display_name="test station",
    latitude_microdegrees=0,
    longitude_microdegrees=0,
    start="2026-08-11T00:00:00Z",
    id="5d2a7c4e-9b1f-4e6a-8c3d-7f0b2e9a4c61",
)


def observed_channel(
    name: str, quantity: Quantity, unit: Unit, cid: str
) -> WeatherChannelGt:
    return WeatherChannelGt(
        name=name,
        display_name=f"test {quantity.value}",
        quantity=quantity,
        unit=unit,
        station_alias=STATION,
        emit_period_s=60,
        emit_offset_s=0,
        start="2026-08-11T00:00:00Z",
        id=cid,
    )


TEMP_CH = observed_channel(
    TEMP,
    Quantity.Temperature,
    Unit.FahrenheitX100,
    "6ffaacf0-701d-49d8-a20b-cd89de9a21ea",
)
WIND_CH = observed_channel(
    WIND,
    Quantity.WindSpeed,
    Unit.MilesPerHourX1000,
    "2ee66a85-a869-4a34-98c2-3dd93718ce8a",
)
IRR_CH = observed_channel(
    IRR,
    Quantity.Irradiance,
    Unit.WattsPerSquareMeter,
    "3b5e23e0-c7d4-4298-9eeb-862b88594e12",
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


TEMP_FC_CH = forecast_channel(TEMP_FC, TEMP, "dc3d3844-6c8c-466b-8140-eb06900b4bf8")
WIND_FC_CH = forecast_channel(WIND_FC, WIND, "23cc841c-8e12-4d1e-9030-ed9ef8923592")
IRR_FC_CH = forecast_channel(IRR_FC, IRR, "10fba70e-8dce-452f-b996-365d3f61a8e8")

TEMP_BINDING = WeatherForecastBinding(
    forecast_channel=TEMP_FC_CH, observation_channel=TEMP_CH
)
WIND_BINDING = WeatherForecastBinding(
    forecast_channel=WIND_FC_CH, observation_channel=WIND_CH
)
IRR_BINDING = WeatherForecastBinding(
    forecast_channel=IRR_FC_CH, observation_channel=IRR_CH
)

BUNDLE = WeatherForecastBundleGt(
    name=BUNDLE_NAME,
    display_name="test bundle",
    location_alias=LOCATION,
    binding_list=[TEMP_BINDING, WIND_BINDING, IRR_BINDING],
    emit_period_s=60,
    emit_offset_s=30,
    start="2026-08-12T00:00:00Z",
    id="309885e5-57eb-4afc-9269-5740fde5d27e",
)

# A temperature-only bundle.
SMALL_BUNDLE = WeatherForecastBundleGt(
    name=SMALL_BUNDLE_NAME,
    display_name="test bundle, temperature only",
    location_alias=LOCATION,
    binding_list=[TEMP_BINDING],
    emit_period_s=60,
    emit_offset_s=30,
    start="2026-08-12T00:00:00Z",
    id="01430d5b-4a61-4228-bdcc-da1addf7310d",
)


# January -3 F, a degree a month after, so a slice's month shows in its value.
TEMPLATE = WeatherSeasonalTemplateGt(
    location_alias=LOCATION,
    temp_by_month=[-300 + 100 * m for m in range(12)],
    start="2026-01-01T00:00:00Z",
    id="3b7c1e52-8d4a-4f6e-9c2b-5a1d8e7f0c93",
)


def obs_at(epoch_s: int, temp: int, wind: int | None = None) -> WeatherObservation:
    readings = [
        WeatherObservationReading(
            channel_name=TEMP, quantity=Quantity.Temperature, value=temp
        )
    ]
    if wind is not None:
        readings.append(
            WeatherObservationReading(
                channel_name=WIND, quantity=Quantity.WindSpeed, value=wind
            )
        )
    return WeatherObservation(
        station_alias=STATION,
        observation_time=s_to_iso(epoch_s),
        interpolated=False,
        reading_list=readings,
    )


def value_of(message: WeatherObservation, channel: str) -> int | None:
    for reading in message.reading_list:
        if reading.channel_name == channel:
            return reading.value
    return None


def series_of(message, channel: str):
    return next(s for s in message.series_list if s.channel_name == channel)


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


def irradiance_from(first_start_s: int, values: list[int]) -> IrradianceHourlyProduct:
    return IrradianceHourlyProduct(
        update_time=s_to_iso(first_start_s),
        generated_at=s_to_iso(first_start_s),
        first_slice_start=s_to_iso(first_start_s),
        period_s=3600,
        irradiance_w_m2=values,
    )


TEMP_SERIES = SeriesBinding(
    forecast_channel=TEMP_FC_CH,
    quantity=Quantity.Temperature,
    values=lambda p: p.temperature_f,
    scale=100,
)
WIND_SERIES = SeriesBinding(
    forecast_channel=WIND_FC_CH,
    quantity=Quantity.WindSpeed,
    values=lambda p: p.wind_speed_mph,
    scale=1000,
)
IRR_SERIES = SeriesBinding(
    forecast_channel=IRR_FC_CH,
    quantity=Quantity.Irradiance,
    values=lambda p: p.irradiance_w_m2,
    scale=1,
)


class Harness:
    """``irradiance_products`` scripts the irradiance fetch like
    ``products`` scripts the temperature-and-wind one; left None, every
    irradiance fetch succeeds with a flat 100 W/m² product from
    ``now_s`` that outlasts any test window."""

    def __init__(
        self,
        *,
        observations: list | None = None,
        products: list | None = None,
        irradiance_products: list | None = None,
        bundle: WeatherForecastBundleGt = BUNDLE,
        max_fill_s: int = 3 * 3600,
        initial_observation: WeatherObservation | None = None,
        initial_published_slot: int | None = None,
        now_s: int = 0,
        template: WeatherSeasonalTemplateGt | None = None,
    ) -> None:
        self.published: list[tuple[object, str]] = []
        self.glitches: list[tuple[LogLevel, str, str]] = []
        self._observations = list(observations or [])
        self._products = list(products or [])
        self._irradiance = (
            list(irradiance_products) if irradiance_products is not None else None
        )
        self._now_s = now_s
        streams = {}
        if observations is not None:
            streams["observation_streams"] = [
                ObservationStream(
                    station=STATION_RECORD,
                    channels=[TEMP_CH, WIND_CH],
                    fetch=self._next_obs,
                    initial_observation=initial_observation,
                    initial_published_slot=initial_published_slot,
                )
            ]
        else:
            streams["observation_streams"] = []
        if products is not None:
            sources = [
                ProductSource(
                    name="nws",
                    fetch=self._next_product,
                    series=[
                        s
                        for s in (TEMP_SERIES, WIND_SERIES)
                        if s.forecast_channel.name
                        in {b.forecast_channel.name for b in bundle.binding_list}
                    ],
                )
            ]
            if IRR_BINDING in bundle.binding_list:
                sources.append(
                    ProductSource(
                        name="open-meteo",
                        fetch=self._next_irradiance,
                        series=[IRR_SERIES],
                    )
                )
            streams["forecast_streams"] = [
                ForecastStream(bundle=bundle, sources=sources, template=template)
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

    def _next_irradiance(self, slices: int) -> IrradianceHourlyProduct:
        if self._irradiance is None:
            return irradiance_from(self._now_s, [100] * 48)
        item = self._irradiance.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_observation_published_on_slot_with_station_radio() -> None:
    h = Harness(observations=[obs_at(55, 7000, 4000)])
    h.scheduler.run_pending(60)
    assert len(h.published) == 1
    message, radio = h.published[0]
    assert radio == STATION
    assert message.observation_time == s_to_iso(55)
    assert message.interpolated is False
    assert value_of(message, TEMP) == 7000
    assert value_of(message, WIND) == 4000
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
    assert value_of(filled, TEMP) == 1325
    assert value_of(filled, WIND) == 3083  # 2000 + 3000*65/180
    assert h.glitches == []


def test_recovery_omits_a_channel_a_bracket_lacks() -> None:
    h = Harness(observations=[obs_at(55, 1000), obs_at(235, 1900, 5000)])
    h.scheduler.run_pending(60)
    h.scheduler.run_pending(240)
    filled = [m for m, _ in h.published if m.interpolated]
    assert filled and all(value_of(m, WIND) is None for m in filled)
    assert all(value_of(m, TEMP) is not None for m in filled)


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


def test_observation_stream_channels_must_be_on_its_station() -> None:
    elsewhere = TEMP_CH.model_copy(update={"station_alias": "other"})
    with pytest.raises(ValueError, match="observation stream channels"):
        EmissionScheduler(
            observation_streams=[
                ObservationStream(
                    station=STATION_RECORD,
                    channels=[elsewhere],
                    fetch=lambda: obs_at(1, 1),
                )
            ],
            forecast_streams=[],
            publish=lambda m, r: None,
            raise_glitch=lambda *a: None,
            now_s=0,
        )


B = 1_786_000_200  # Aug 2026, multiple of 300 — utc.milliseconds needs real epochs


def test_forecast_live_emits_one_bundle_message_on_channel_grid() -> None:
    h = Harness(products=[product_from(B, [10, 20], [5, 6])], now_s=B)
    h.scheduler.run_pending(B + 90)
    assert len(h.published) == 1
    message, radio = h.published[0]
    assert radio == BUNDLE_NAME
    assert message.bundle_name == BUNDLE_NAME
    assert [s.channel_name for s in message.series_list] == [TEMP_FC, WIND_FC, IRR_FC]
    assert [s.quantity for s in message.series_list] == [
        Quantity.Temperature,
        Quantity.WindSpeed,
        Quantity.Irradiance,
    ]
    assert all(s.fidelity == WeatherForecastFidelity.Live for s in message.series_list)
    assert all(s.source_updated_time == s_to_iso(B) for s in message.series_list)
    assert message.message_created_ms == (B + 90) * 1000
    # First slice: next 300-boundary after the slot → B+300; starts
    # B+300/B+600/B+1200, all inside source hour 0, scaled ×100 / ×1000
    # / ×1.
    assert message.first_slice_start == s_to_iso(B + 300)
    assert series_of(message, TEMP_FC).values == [1000, 1000, 1000]
    assert series_of(message, WIND_FC).values == [5000, 5000, 5000]
    assert series_of(message, IRR_FC).values == [100, 100, 100]
    assert h.glitches == []


def test_temperature_only_bundle_emits_one_series() -> None:
    h = Harness(
        products=[product_from(B, [10, 20], [5, 6])], now_s=B, bundle=SMALL_BUNDLE
    )
    h.scheduler.run_pending(B + 90)
    message, radio = h.published[0]
    assert radio == SMALL_BUNDLE_NAME
    assert [s.channel_name for s in message.series_list] == [TEMP_FC]
    assert series_of(message, TEMP_FC).values == [1000, 1000, 1000]


def test_sources_must_serve_exactly_the_bundle_bindings() -> None:
    def never(slices: int) -> HourlyForecastProduct:
        raise AssertionError("not fetched")

    with pytest.raises(ValueError, match="the bundle binds"):
        EmissionScheduler(
            observation_streams=[],
            forecast_streams=[
                ForecastStream(
                    bundle=BUNDLE,
                    sources=[
                        ProductSource(name="nws", fetch=never, series=[TEMP_SERIES])
                    ],
                )
            ],
            publish=lambda m, r: None,
            raise_glitch=lambda *a: None,
            now_s=B,
        )


def test_irradiance_walks_its_own_ladder() -> None:
    """Irradiance source down with nothing stored: zeros at the
    template rung stamped with the bundle's Start while temperature and
    wind stay Live; recovery is silent; a later outage falls to Stored
    with an irradiance-only downgrade glitch."""
    nws = [product_from(B, [10] * 4, [5] * 4) for _ in range(4)]
    h = Harness(
        products=nws,
        irradiance_products=[
            RuntimeError("open-meteo down"),
            irradiance_from(B, [100, 200]),
            RuntimeError("open-meteo down"),
            RuntimeError("open-meteo down"),
        ],
        now_s=B,
    )
    h.scheduler.run_pending(B + 90)
    h.scheduler.run_pending(B + 150)
    h.scheduler.run_pending(B + 210)
    h.scheduler.run_pending(B + 7500)  # past the stored horizon
    rungs = [
        (
            series_of(m, TEMP_FC).fidelity,
            series_of(m, IRR_FC).fidelity,
            series_of(m, IRR_FC).values,
        )
        for m, _ in h.published
    ]
    assert rungs == [
        (
            WeatherForecastFidelity.Live,
            WeatherForecastFidelity.SeasonalTemplate,
            [0, 0, 0],
        ),
        (WeatherForecastFidelity.Live, WeatherForecastFidelity.Live, [100, 100, 100]),
        (WeatherForecastFidelity.Live, WeatherForecastFidelity.Stored, [100, 100, 100]),
        (
            WeatherForecastFidelity.Live,
            WeatherForecastFidelity.SeasonalTemplate,
            [0, 0, 0],
        ),
    ]
    assert series_of(h.published[0][0], IRR_FC).source_updated_time == BUNDLE.start
    assert series_of(h.published[1][0], IRR_FC).source_updated_time == s_to_iso(B)
    assert [(g[0], g[2]) for g in h.glitches] == [
        (LogLevel.Warning, f"{IRR_FC}: Live → Stored"),
        (LogLevel.Warning, f"{IRR_FC}: Stored → SeasonalTemplate"),
    ]


def test_no_template_skips_the_slot_whatever_irradiance_says() -> None:
    h = Harness(products=[RuntimeError("nws down")], now_s=B)
    h.scheduler.run_pending(B + 90)
    assert h.published == []
    assert [g[0] for g in h.glitches] == [LogLevel.Error]


def test_forecast_falls_back_to_stored_with_downgrade_glitch() -> None:
    h = Harness(products=[product_from(B, [10, 20]), RuntimeError("nws down")], now_s=B)
    h.scheduler.run_pending(B + 90)
    h.scheduler.run_pending(B + 150)
    assert len(h.published) == 2
    second = h.published[1][0]
    assert series_of(second, TEMP_FC).fidelity == WeatherForecastFidelity.Stored
    assert series_of(second, WIND_FC).fidelity == WeatherForecastFidelity.Stored
    assert series_of(second, IRR_FC).fidelity == WeatherForecastFidelity.Live
    assert [(g[0], g[1], g[2]) for g in h.glitches] == [
        (LogLevel.Warning, "forecast fidelity downgrade", f"{TEMP_FC}: Live → Stored"),
        (LogLevel.Warning, "forecast fidelity downgrade", f"{WIND_FC}: Live → Stored"),
    ]


def test_forecast_with_no_product_at_all_glitches_and_skips() -> None:
    h = Harness(products=[RuntimeError("nws down")], now_s=B)
    h.scheduler.run_pending(B + 90)
    assert h.published == []
    assert len(h.glitches) == 1
    assert h.glitches[0][0] == LogLevel.Error


def test_no_product_at_all_fills_from_the_seasonal_template() -> None:
    # B falls in August 2026, so every slice takes the template's eighth value.
    h = Harness(products=[RuntimeError("nws down")], now_s=B, template=TEMPLATE)
    h.scheduler.run_pending(B + 90)
    assert len(h.published) == 1
    message, radio = h.published[0]
    assert radio == BUNDLE_NAME
    temperature = series_of(message, TEMP_FC)
    wind = series_of(message, WIND_FC)
    assert temperature.fidelity == WeatherForecastFidelity.SeasonalTemplate
    assert wind.fidelity == WeatherForecastFidelity.SeasonalTemplate
    assert temperature.source_updated_time == TEMPLATE.start
    assert wind.source_updated_time == BUNDLE.start
    assert message.first_slice_start == s_to_iso(B + 300)
    assert temperature.values == [400, 400, 400]
    assert wind.values == [0, 0, 0]
    # Irradiance is on its own ladder: Live while the NWS product is out.
    assert series_of(message, IRR_FC).fidelity == WeatherForecastFidelity.Live
    assert series_of(message, IRR_FC).values == [100, 100, 100]
    assert h.glitches == []


def test_stored_horizon_exhaustion_fills_from_the_seasonal_template() -> None:
    h = Harness(
        products=[
            product_from(B, [10, 20]),
            RuntimeError("down"),
            RuntimeError("down"),
        ],
        now_s=B,
        template=TEMPLATE,
    )
    h.scheduler.run_pending(B + 90)  # live
    h.scheduler.run_pending(B + 7500)  # beyond the stored horizon
    assert [series_of(m, TEMP_FC).fidelity for m, _ in h.published] == [
        WeatherForecastFidelity.Live,
        WeatherForecastFidelity.SeasonalTemplate,
    ]
    assert series_of(h.published[1][0], TEMP_FC).values == [400, 400, 400]
    assert [g[0] for g in h.glitches] == [LogLevel.Warning, LogLevel.Warning]
    assert all("downgrade" in g[1] for g in h.glitches)


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
    assert "no seasonal template" in errors[0][1]
    assert "exhausted" in errors[0][2]
