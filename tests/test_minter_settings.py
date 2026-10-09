"""The minter connects as its own principal when the settings name one."""

from pathlib import Path

from gwbase.config.rabbit_settings import RabbitBrokerClient, RabbitTls
from gwwf.config import GwwfSettings
from gwwf.create import minter_settings


def test_the_minter_uses_its_own_broker_client_when_set() -> None:
    minter = RabbitBrokerClient(
        url="amqps://hw1-1.electricity.works:5671/hw1__1",
        tls=RabbitTls(
            ca_cert_path=Path("/certs/ca.crt"),
            cert_path=Path("/certs/weather-minter.crt"),
            private_key_path=Path("/certs/weather-minter.pem"),
        ),
    )
    settings = GwwfSettings(service_alias="hw1.isone.weather", minter_rabbit=minter)
    got = minter_settings(settings)
    assert got.service_alias == "hw1.weatherminter"
    assert got.rabbit is minter


def test_the_minter_falls_back_to_the_actors_client() -> None:
    settings = GwwfSettings(service_alias="d1.weather")
    assert minter_settings(settings).rabbit is settings.rabbit
