"""Layer 2 — d1.weather over a real broker + real DB (gnr-style).

Boots the real `WeatherActor` as `d1.weather` on a vanilla
testcontainers RabbitMQ carrying the gwbase fabric, against a
migrated testcontainers Postgres seeded with FAST channel records
(5 s observation slots, 5 s forecast slots), driven by scripted
adapters. A slug-bound tap witnesses live emissions over the wire;
then the actor RESTARTS and the recovery replay bridges the outage
with interpolated messages — latest-only and replay surviving the
process boundary via the DB.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

import pika
import pytest
from gwbase import topology
from gwbase.actor_base import ActorBase
from gwbase.config import ServiceSettings
from gwbase.config.rabbit_settings import RabbitBrokerClient
from gwbase.transport_encoding import RoutingEnvelope, TransportClass
from pydantic import SecretStr
from sqlalchemy import delete

from gwwf.config import GwwfSettings
from gwwf.db.models import (
    BundleBindingSql,
    BundleSql,
    ForecastChannelSql,
    ForecastSql,
    LastObservationSql,
    LocationSql,
    SeasonalTemplateSql,
    SourceProductSql,
    StationSql,
    WeatherChannelSql,
)
from gwwf.create import send_create
from gwwf.db.session import session_factory_from
from gwwf.db.store import (
    insert_record,
    latest_forecast,
    load_bundles,
    load_last_observation,
)
from gwwf.grid import s_to_iso
from gwwf.nws import HourlyForecastProduct
from gwwf.open_meteo import IrradianceHourlyProduct
from gwwf.record_broadcast import RecordWord
from gwwf.sema.codec import SemaCodec
from gwwf.sema.enums import Quantity, Unit
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherCmdAck,
    WeatherCmdNack,
    WeatherForecast,
    WeatherForecastBinding,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherLocationGt,
    WeatherObservation,
    WeatherObservationReading,
    WeatherStationGt,
)
from gwwf.weather_actor import ProductAdapter, WeatherActor

pytestmark = pytest.mark.integration

SERVICE_ALIAS = "d1.weather"
LOCATION = "d1.test.loc"
STATION = "fake"
TEMP = "fake.temperature"
WIND = "fake.windspeed"
IRR = "fake.irradiance"
TEMP_FC = "fake.temperature.forecast.fake.hourly"
WIND_FC = "fake.windspeed.forecast.fake.hourly"
IRR_FC = "fake.irradiance.forecast.fake.hourly"
BUNDLE_NAME = "d1.test.loc.raccoon"
NWS_METHOD = "fake.method"
IRRADIANCE_METHOD = "fake.irradiance.method"

FAST_LOCATION = WeatherLocationGt(
    alias=LOCATION,
    latitude_microdegrees=0,
    longitude_microdegrees=0,
    timezone="America/New_York",
    id=str(uuid.uuid4()),
)
FAST_STATION = WeatherStationGt(
    alias=STATION,
    display_name="layer2 station",
    latitude_microdegrees=0,
    longitude_microdegrees=0,
    start="2026-08-11T00:00:00Z",
    id=str(uuid.uuid4()),
)
FAST_TEMP = WeatherChannelGt(
    name=TEMP,
    display_name="layer2 temperature",
    quantity=Quantity.Temperature,
    unit=Unit.FahrenheitX100,
    station_alias=STATION,
    emit_period_s=5,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id=str(uuid.uuid4()),
)
FAST_WIND = WeatherChannelGt(
    name=WIND,
    display_name="layer2 wind",
    quantity=Quantity.WindSpeed,
    unit=Unit.MilesPerHourX1000,
    station_alias=STATION,
    emit_period_s=5,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id=str(uuid.uuid4()),
)
FAST_IRR = WeatherChannelGt(
    name=IRR,
    display_name="layer2 irradiance",
    quantity=Quantity.Irradiance,
    unit=Unit.WattsPerSquareMeter,
    station_alias=STATION,
    emit_period_s=5,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id=str(uuid.uuid4()),
)


def _fast_fc(
    name: str, target: str, method: str = NWS_METHOD
) -> WeatherForecastChannelGt:
    """A method names an adapter's product, so the irradiance channel
    (a different product) has its own."""
    return WeatherForecastChannelGt(
        name=name,
        target_channel_name=target,
        forecaster="fake.model",
        method=method,
        source_locator="fake.0.0",
        total_slices=3,
        slice_duration_s_list=[300, 600, 300],
        forecast_duration_minutes=20,
        start="2026-08-11T00:00:00Z",
        id=str(uuid.uuid4()),
    )


FAST_TEMP_FC = _fast_fc(TEMP_FC, TEMP)
FAST_WIND_FC = _fast_fc(WIND_FC, WIND)
FAST_IRR_FC = _fast_fc(IRR_FC, IRR, method=IRRADIANCE_METHOD)
# Referential order: location, station → channels → bundle (defined below).
FAST_BUNDLE = WeatherForecastBundleGt(
    name=BUNDLE_NAME,
    display_name="layer2 bundle",
    location_alias=LOCATION,
    binding_list=[
        WeatherForecastBinding(
            forecast_channel=FAST_TEMP_FC, observation_channel=FAST_TEMP
        ),
        WeatherForecastBinding(
            forecast_channel=FAST_WIND_FC, observation_channel=FAST_WIND
        ),
        WeatherForecastBinding(
            forecast_channel=FAST_IRR_FC, observation_channel=FAST_IRR
        ),
    ],
    emit_period_s=5,
    emit_offset_s=2,
    start="2026-08-12T00:00:00Z",
    id=str(uuid.uuid4()),
)
FAST_RECORDS = (
    FAST_LOCATION,
    FAST_STATION,
    FAST_TEMP,
    FAST_WIND,
    FAST_IRR,
    FAST_TEMP_FC,
    FAST_WIND_FC,
    FAST_IRR_FC,
    FAST_BUNDLE,
)


def provision_topology(url: str) -> None:
    """Declare the gwbase fabric before any actor starts — actors only
    passively assert their consume exchange exists."""
    connection = pika.BlockingConnection(pika.URLParameters(url))
    try:
        channel = connection.channel()
        for exchange in topology.exchanges():
            channel.exchange_declare(
                exchange=exchange.name,
                exchange_type=exchange.exchange_type,
                durable=exchange.durable,
                internal=exchange.internal,
            )
        for binding in topology.exchange_bindings():
            channel.exchange_bind(
                destination=binding.destination,
                source=binding.source,
                routing_key=binding.routing_key,
            )
    finally:
        connection.close()


def g_node_file(tmp_path: Path) -> Path:
    path = tmp_path / "g.node.gt.json"
    path.write_text(
        json.dumps({
            "TypeName": "g.node.gt",
            "Version": "006",
            "GNodeId": str(uuid.uuid4()),
            "Alias": SERVICE_ALIAS,
            "BaseClass": "Logical",
            "GNodeClass": "weather",
            "Status": "Active",
        })
    )
    return path


def observation(epoch_s: int, temp: int, wind: int) -> WeatherObservation:
    return WeatherObservation(
        station_alias=STATION,
        observation_time=s_to_iso(epoch_s),
        interpolated=False,
        reading_list=[
            WeatherObservationReading(
                channel_name=TEMP, quantity=Quantity.Temperature, value=temp
            ),
            WeatherObservationReading(
                channel_name=WIND, quantity=Quantity.WindSpeed, value=wind
            ),
        ],
    )


def fake_product(now_s: int) -> HourlyForecastProduct:
    start = (now_s // 300) * 300
    return HourlyForecastProduct(
        update_time=s_to_iso(start),
        generated_at=s_to_iso(start),
        first_slice_start=s_to_iso(start),
        period_s=300,
        temperature_f=[50 + i for i in range(40)],
        wind_speed_mph=[5 + i for i in range(40)],
    )


def fake_irradiance(now_s: int) -> IrradianceHourlyProduct:
    start = (now_s // 300) * 300
    return IrradianceHourlyProduct(
        update_time=s_to_iso(start),
        generated_at=s_to_iso(start),
        first_slice_start=s_to_iso(start),
        period_s=300,
        irradiance_w_m2=[10 * i for i in range(40)],
    )


def fake_adapters(now_s: int) -> dict[str, ProductAdapter]:
    """The two fake Methods' adapters: scripted products, no network."""
    return {
        NWS_METHOD: ProductAdapter(
            fetch_for=lambda channel: lambda slices: fake_product(now_s),
            kind=HourlyForecastProduct,
            series={
                Quantity.Temperature: lambda p: p.temperature_f,
                Quantity.WindSpeed: lambda p: p.wind_speed_mph,
            },
        ),
        IRRADIANCE_METHOD: ProductAdapter(
            fetch_for=lambda channel: lambda slices: fake_irradiance(now_s),
            kind=IrradianceHourlyProduct,
            series={Quantity.Irradiance: lambda p: p.irradiance_w_m2},
        ),
    }


class Layer2Tap(ActorBase):
    """Slug-bound witness for the streams AND the record broadcasts
    (records ride their own name; the bundle broadcast is tail-less)."""

    def __init__(self, settings: ServiceSettings) -> None:
        super().__init__(settings=settings)
        self.codec = SemaCodec()
        self.observations: list[WeatherObservation] = []
        self.forecasts: list[WeatherForecast] = []
        self.records: list[RecordWord] = []

    def local_rabbit_startup(self) -> None:
        for type_name, radio in [
            (WeatherObservation.type_name_value(), STATION),
            (WeatherForecast.type_name_value(), BUNDLE_NAME),
            (WeatherLocationGt.type_name_value(), LOCATION),
            (WeatherStationGt.type_name_value(), STATION),
            (WeatherChannelGt.type_name_value(), TEMP),
            (WeatherChannelGt.type_name_value(), WIND),
            (WeatherChannelGt.type_name_value(), IRR),
            (WeatherForecastChannelGt.type_name_value(), TEMP_FC),
            (WeatherForecastChannelGt.type_name_value(), WIND_FC),
            (WeatherForecastChannelGt.type_name_value(), IRR_FC),
            (WeatherForecastBundleGt.type_name_value(), None),
        ]:
            self.subscribe_broadcast(
                from_alias=SERVICE_ALIAS,
                from_class=TransportClass.WeatherForecastService,
                type_name=type_name,
                radio_channel=radio,
            )

    def dispatch_message(self, *, envelope: RoutingEnvelope, body: bytes) -> None:
        decoded = self.codec.from_dict(json.loads(body))
        if isinstance(decoded, WeatherObservation):
            self.observations.append(decoded)
        elif isinstance(decoded, WeatherForecast):
            self.forecasts.append(decoded)
        elif isinstance(decoded, RecordWord):
            self.records.append(decoded)


def wait_for(predicate, seconds: float, desc: str) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise TimeoutError(desc)


def _clear_weather_tables(sessions) -> None:
    with sessions() as session:
        for model in (
            ForecastSql,
            BundleBindingSql,
            BundleSql,
            LastObservationSql,
            SourceProductSql,
            ForecastChannelSql,
            WeatherChannelSql,
            SeasonalTemplateSql,
            StationSql,
            LocationSql,
        ):
            session.execute(delete(model))
        session.commit()


def test_d1_weather_boots_emits_and_recovers_across_restart(
    rabbit_url, pg_url, migrated_engine, tmp_path
) -> None:
    provision_topology(rabbit_url)
    settings = GwwfSettings(
        rabbit=RabbitBrokerClient(url=SecretStr(rabbit_url)),
        db_url=SecretStr(pg_url),
        g_node_path=g_node_file(tmp_path),
        service_alias=SERVICE_ALIAS,
        service_name="weather-forecast-l2",
    )
    sessions = session_factory_from(settings)
    _clear_weather_tables(sessions)
    with sessions() as session:
        for record in FAST_RECORDS:
            insert_record(session, record)

    tap = Layer2Tap(
        ServiceSettings(
            rabbit=settings.rabbit,
            service_alias="d1.wx.l2tap",
            service_name="gwwf-l2-tap",
        )
    )
    tap.start()
    try:
        wait_for(lambda: tap.consuming, 10, "tap consuming")
        time.sleep(0.5)  # slug binds land as consuming starts

        now = int(time.time())
        first_observation = observation(now - 2, temp=6000, wind=3000)
        actor1 = WeatherActor(
            settings,
            fetch_observation=lambda station: first_observation,
            adapters=fake_adapters(now),
        )
        actor1.start()
        try:
            wait_for(lambda: actor1.consuming, 10, "actor1 consuming")
            wait_for(
                lambda: tap.observations and tap.forecasts,
                30,
                "first observation + forecast on the wire",
            )
        finally:
            actor1.stop()

        with sessions() as session:
            assert latest_forecast(session, BUNDLE_NAME) is not None
            stored = load_last_observation(session, STATION)
            assert stored is not None
            assert stored.observation == first_observation

        time.sleep(6)  # at least one slot passes while the service is down

        second_time = int(time.time()) - 1
        second_observation = observation(second_time, temp=6900, wind=4800)
        actor2 = WeatherActor(
            settings,
            fetch_observation=lambda station: second_observation,
            adapters=fake_adapters(now),
        )
        actor2.start()
        try:
            wait_for(lambda: actor2.consuming, 10, "actor2 consuming")
            wait_for(
                lambda: (
                    any(o.interpolated for o in tap.observations)
                    and any(
                        o.observation_time == second_observation.observation_time
                        for o in tap.observations
                    )
                ),
                30,
                "interpolated replay + fresh observation after restart",
            )
        finally:
            actor2.stop()

        reals = [o for o in tap.observations if not o.interpolated]
        filled = [o for o in tap.observations if o.interpolated]
        # Latest-only across the restart: the pre-restart observation
        # published exactly once; the replay bridges the downtime.
        assert [o.observation_time for o in reals] == [
            first_observation.observation_time,
            second_observation.observation_time,
        ]
        assert filled, "no interpolated replay witnessed"
        for fill in filled:
            fill_s = int(datetime.fromisoformat(fill.observation_time).timestamp())
            assert now - 2 < fill_s < second_time
    finally:
        tap.stop()
        _clear_weather_tables(sessions)


def test_create_round_mints_records_onto_empty_service(
    rabbit_url, pg_url, migrated_engine, tmp_path
) -> None:
    """The step-7 contract over a real broker: the actor boots on an
    EMPTY record set (it is the create command's consumer, so it must
    run before any records exist), records enter one by one through
    `send_create` — verdicts typed, refusals answered — their
    broadcasts cross the wire, and the rebuilt scheduler starts
    emitting without a restart."""
    provision_topology(rabbit_url)
    settings = GwwfSettings(
        rabbit=RabbitBrokerClient(url=SecretStr(rabbit_url)),
        db_url=SecretStr(pg_url),
        g_node_path=g_node_file(tmp_path),
        service_alias=SERVICE_ALIAS,
        service_name="weather-forecast-l2",
    )
    sessions = session_factory_from(settings)
    _clear_weather_tables(sessions)

    tap = Layer2Tap(
        ServiceSettings(
            rabbit=settings.rabbit,
            service_alias="d1.wx.l2tap",
            service_name="gwwf-l2-tap",
        )
    )
    tap.start()
    now = int(time.time())
    live_observation = observation(now - 2, temp=6100, wind=3100)
    actor = WeatherActor(  # empty DB: boots idle, never refuses
        settings,
        fetch_observation=lambda station: live_observation,
        adapters=fake_adapters(now),
    )
    actor.start()
    try:
        wait_for(lambda: tap.consuming and actor.consuming, 10, "consuming")
        time.sleep(0.5)  # slug binds land as consuming starts

        # Out of referential order: a typed refusal with the reason.
        _, verdict = send_create(settings, FAST_TEMP, timeout_s=20)
        assert isinstance(verdict, WeatherCmdNack)

        for record in FAST_RECORDS:
            _, verdict = send_create(settings, record, timeout_s=20)
            assert isinstance(verdict, WeatherCmdAck), f"refused: {verdict}"

        # A duplicate mint refuses: records are durable identities.
        _, verdict = send_create(settings, FAST_LOCATION, timeout_s=20)
        assert isinstance(verdict, WeatherCmdNack)

        with sessions() as session:
            assert [b.name for b in load_bundles(session)] == [BUNDLE_NAME]

        wait_for(
            lambda: len(tap.records) >= len(FAST_RECORDS),
            10,
            "record broadcasts on the wire",
        )
        assert {record_key(r) for r in tap.records} == {
            record_key(r) for r in FAST_RECORDS
        }

        wait_for(
            lambda: tap.observations and tap.forecasts,
            30,
            "emissions begin after the bundle mint, no restart",
        )
    finally:
        actor.stop()
        tap.stop()
        _clear_weather_tables(sessions)


def record_key(record: RecordWord) -> str:
    return record.id


def test_station_without_observation_path_still_forecasts(
    rabbit_url, pg_url, migrated_engine, tmp_path
) -> None:
    """A station with no IcaoId has no NWS observation path (the
    personal irradiance station), so it gets no observation stream;
    the bundles that bind its channels still forecast."""
    provision_topology(rabbit_url)
    settings = GwwfSettings(
        rabbit=RabbitBrokerClient(url=SecretStr(rabbit_url)),
        db_url=SecretStr(pg_url),
        g_node_path=g_node_file(tmp_path),
        service_alias=SERVICE_ALIAS,
        service_name="weather-forecast-l2",
    )
    sessions = session_factory_from(settings)
    _clear_weather_tables(sessions)
    with sessions() as session:
        for record in FAST_RECORDS:
            insert_record(session, record)

    tap = Layer2Tap(
        ServiceSettings(
            rabbit=settings.rabbit,
            service_alias="d1.wx.l2tap",
            service_name="gwwf-l2-tap",
        )
    )
    tap.start()
    try:
        wait_for(lambda: tap.consuming, 10, "tap consuming")
        time.sleep(0.5)
        # No fetch_observation override: the default NWS wiring applies.
        actor = WeatherActor(settings, adapters=fake_adapters(int(time.time())))
        actor.start()
        try:
            wait_for(lambda: actor.consuming, 10, "actor consuming")
            wait_for(lambda: tap.forecasts, 30, "forecast with no observation path")
            assert tap.observations == []
        finally:
            actor.stop()
    finally:
        tap.stop()
        _clear_weather_tables(sessions)
