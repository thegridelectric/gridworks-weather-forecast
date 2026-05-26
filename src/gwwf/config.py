"""Settings for gridworks-weather-forecast."""

from gwbase.config import GNodeSettings
from gwbase.transport_encoding import TransportClass
from pydantic_settings import SettingsConfigDict


class GwwfSettings(GNodeSettings):
    """Reads from env (GWWF_*) and/or a `.env` file at the repo root.

    Inherits `rabbit: RabbitBrokerClient`, `g_node_path: Path`,
    `transport_class: TransportClass`, `log_level: str` from `GNodeSettings`.

    Note: `transport_class` defaults to `TransportClass.Scada` in
    `GNodeSettings`; we override to `WeatherForecastService` here so the
    routing code on the wire is `"weather"` (long form) and not the prod
    short form `"ws"` (see F-007 in
    `wiki/gridworks-base/research/findings.md`).
    """

    transport_class: TransportClass = TransportClass.WeatherForecastService

    # Control-plane participants — required by GridworksActor. For a dev
    # instance these can be placeholders; in prod they're the alias of
    # the supervisor and the time coordinator the actor heartbeats with.
    my_super_alias: str = "d1.super"
    my_time_coordinator_alias: str = "d1.time"

    model_config = SettingsConfigDict(
        env_prefix="GWWF_",
        env_nested_delimiter="__",
        extra="ignore",
    )
