"""Layer 2 — d1.weather over a real broker + real DB (gnr-style).

Boots the real `WeatherActor` as `d1.weather` on a vanilla
testcontainers RabbitMQ carrying the gwbase fabric, against a
migrated testcontainers Postgres seeded with FAST channel records
(5 s observation slots, 5 s forecast slots), driven by scripted
fetchers. A slug-bound tap witnesses live emissions over the wire;
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
    BundleSql,
    ForecastChannelSql,
    ForecastSql,
    LastObservationSql,
    LocationSql,
    SourceProductSql,
    WeatherChannelSql,
)
from gwwf.db.session import session_factory_from
from gwwf.db.store import latest_forecast, load_last_observation, seed_records
from gwwf.grid import s_to_iso
from gwwf.nws import HourlyForecastProduct
from gwwf.sema.codec import SemaCodec
from gwwf.sema.enums import Gw1Quantity, Gw1Unit
from gwwf.sema.types import (
    GwWeatherChannelGt,
    GwWeatherForecast,
    GwWeatherForecastBundleGt,
    GwWeatherForecastChannelGt,
    GwWeatherLocationGt,
    GwWeatherObservation,
)
from gwwf.weather_actor import WeatherActor

pytestmark = pytest.mark.integration

SERVICE_ALIAS = "d1.weather"
LOCATION = "d1.test.loc"
TEMP = "d1.test.loc.temperature"
WIND = "d1.test.loc.windspeed"
TEMP_FC = "d1.test.loc.temperature.forecast.fake.hourly"
WIND_FC = "d1.test.loc.windspeed.forecast.fake.hourly"
BUNDLE_NAME = "d1.test.loc.forecast.fake.hourly"

FAST_LOCATION = GwWeatherLocationGt(
    alias=LOCATION,
    latitude_microdegrees=0,
    longitude_microdegrees=0,
    timezone="America/New_York",
    id=str(uuid.uuid4()),
)
FAST_TEMP = GwWeatherChannelGt(
    name=TEMP,
    display_name="layer2 temperature",
    quantity=Gw1Quantity.Temperature,
    unit=Gw1Unit.FahrenheitX100,
    location_alias=LOCATION,
    emit_period_s=5,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id=str(uuid.uuid4()),
)
FAST_WIND = GwWeatherChannelGt(
    name=WIND,
    display_name="layer2 wind",
    quantity=Gw1Quantity.WindSpeed,
    unit=Gw1Unit.MilesPerHourX1000,
    location_alias=LOCATION,
    emit_period_s=5,
    emit_offset_s=0,
    start="2026-08-11T00:00:00Z",
    id=str(uuid.uuid4()),
)


def _fast_fc(name: str, target: str) -> GwWeatherForecastChannelGt:
    return GwWeatherForecastChannelGt(
        name=name,
        target_channel_name=target,
        forecaster="fake.model",
        method="fake.method",
        source_locator="fake.0.0",
        total_slices=3,
        slice_duration_s_list=[300, 600, 300],
        forecast_duration_minutes=20,
        start="2026-08-11T00:00:00Z",
        id=str(uuid.uuid4()),
    )


FAST_TEMP_FC = _fast_fc(TEMP_FC, TEMP)
FAST_WIND_FC = _fast_fc(WIND_FC, WIND)
FAST_BUNDLE = GwWeatherForecastBundleGt(
    name=BUNDLE_NAME,
    display_name="layer2 bundle",
    location_alias=LOCATION,
    temp_forecast_channel=FAST_TEMP_FC,
    temp_observation_channel=FAST_TEMP,
    wind_speed_forecast_channel=FAST_WIND_FC,
    wind_speed_observation_channel=FAST_WIND,
    emit_period_s=5,
    emit_offset_s=2,
    start="2026-08-12T00:00:00Z",
    id=str(uuid.uuid4()),
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
            "Version": "004",
            "GNodeId": str(uuid.uuid4()),
            "Alias": SERVICE_ALIAS,
            "BaseClass": "Logical",
            "GNodeClass": "weather",
            "Status": "Active",
        })
    )
    return path


def observation(epoch_s: int, temp: int, wind: int) -> GwWeatherObservation:
    return GwWeatherObservation(
        location_alias=LOCATION,
        observation_time=s_to_iso(epoch_s),
        interpolated=False,
        temp_channel_name=TEMP,
        temp_value=temp,
        wind_speed_channel_name=WIND,
        wind_speed_value=wind,
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


class Layer2Tap(ActorBase):
    def __init__(self, settings: ServiceSettings) -> None:
        super().__init__(settings=settings)
        self.codec = SemaCodec()
        self.observations: list[GwWeatherObservation] = []
        self.forecasts: list[GwWeatherForecast] = []

    def local_rabbit_startup(self) -> None:
        for type_name, radio in [
            (GwWeatherObservation.type_name_value(), LOCATION),
            (GwWeatherForecast.type_name_value(), BUNDLE_NAME),
        ]:
            self.subscribe_broadcast(
                from_alias=SERVICE_ALIAS,
                from_class=TransportClass.WeatherForecastService,
                type_name=type_name,
                radio_channel=radio,
            )

    def dispatch_message(self, *, envelope: RoutingEnvelope, body: bytes) -> None:
        decoded = self.codec.from_dict(json.loads(body))
        if isinstance(decoded, GwWeatherObservation):
            self.observations.append(decoded)
        elif isinstance(decoded, GwWeatherForecast):
            self.forecasts.append(decoded)


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
            BundleSql,
            LastObservationSql,
            SourceProductSql,
            ForecastChannelSql,
            WeatherChannelSql,
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
        seed_records(
            session,
            locations=[FAST_LOCATION],
            observation_channels=[FAST_TEMP, FAST_WIND],
            forecast_channels=[FAST_TEMP_FC, FAST_WIND_FC],
            bundles=[FAST_BUNDLE],
        )

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
            fetch_observation=lambda: first_observation,
            fetch_product=lambda slices: fake_product(now),
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
            stored = load_last_observation(session, LOCATION)
            assert stored is not None
            assert stored.observation == first_observation

        time.sleep(6)  # at least one slot passes while the service is down

        second_time = int(time.time()) - 1
        second_observation = observation(second_time, temp=6900, wind=4800)
        actor2 = WeatherActor(
            settings,
            fetch_observation=lambda: second_observation,
            fetch_product=lambda slices: fake_product(now),
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
