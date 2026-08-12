"""Settings for gridworks-weather-forecast."""

from gwbase.config import GNodeSettings
from gwbase.transport_format import LeftRightDot
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict


class GwwfSettings(GNodeSettings):
    """Reads from env (GWWF_*) and/or a `.env` file at the repo root.

    Inherits `rabbit: RabbitBrokerClient`, `g_node_path`, `log_level`
    (and the rest of `ServiceSettings`) from `GNodeSettings`.
    `service_alias` MUST match the g.node.gt.json Alias — `GridworksActor`
    enforces the binding at boot. No `transport_class` here: it is intrinsic
    to the actor's role, not deployment config; `WeatherActor` passes
    `TransportClass.WeatherForecastService` up itself.
    """

    service_alias: LeftRightDot = "d1.weather"
    service_name: str = "weather-forecast"  # XDG path segment for logs/state

    # gwwf's OWN database; gwwf is the sole accessor. Default = the
    # compose-managed dev postgres (docker-compose.yaml, dev-only creds).
    db_url: SecretStr = SecretStr(
        "postgresql+psycopg://gwwf:gwwfpass@localhost:5436/gwwf"
    )
    db_echo: bool = False

    # The read façade binds loopback; TLS is the fronting proxy's job.
    api_host: str = "127.0.0.1"
    api_port: int = 8531

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
