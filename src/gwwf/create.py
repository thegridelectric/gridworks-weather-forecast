"""The create-command round — records enter by a human act over the bus.

`gwwf create <record.json>` publishes `gw.weather.create.cmd` direct to
the weather actor and waits for the typed verdict (`gw.weather.cmd.ack`
/ `gw.weather.cmd.nack`), discriminated by TypeName and correlated by
the SHA-256 hash of the command bytes as published — the same bytes the
actor hashes on its side. The sender is its own weather-class operator
identity (`<universe>.weatherminter`), never the service alias: the
actor addresses the verdict to the command envelope's from_alias, so a
sender that shared the service identity would be answering itself. The
minter is a durable Service principal (its own cert, `minter_rabbit`
in the settings) and each invocation a fresh GNodeInstance on it.
Command and verdict both ride the fabric's (weather, weather)
self-edge — gwbase ≥ 0.5.9.
"""

from __future__ import annotations

import hashlib
import json
import time

from gwbase.config import ServiceSettings
from gwbase.orchestrator import Orchestrator
from gwbase.transport_encoding import RoutingEnvelope, TransportClass

from gwwf.config import GwwfSettings
from gwwf.record_broadcast import RecordWord
from gwwf.sema.codec import default_codec
from gwwf.sema.types import WeatherCmdAck, WeatherCmdNack, WeatherCreateCmd

CREATE_CMD = WeatherCreateCmd.type_name_value()
CMD_ACK = WeatherCmdAck.type_name_value()
CMD_NACK = WeatherCmdNack.type_name_value()

Verdict = WeatherCmdAck | WeatherCmdNack

_CONNECT_TIMEOUT_S = 20.0
VERDICT_TIMEOUT_S = 10.0


def command_hash(payload: bytes) -> str:
    """Content address of a create command — SHA-256 hex of its bytes
    as published."""
    return hashlib.sha256(payload).hexdigest()


class _MintPublisher(Orchestrator):
    """Operator publisher for create commands, itself a weather-class
    actor (the round rides the fabric's weather self-edge). The weather
    actor replies direct to this alias; verdicts land in
    `self.verdicts` keyed by the command's content hash."""

    def __init__(
        self,
        *,
        settings: ServiceSettings,
        my_super_alias: str,
        my_time_coordinator_alias: str,
    ) -> None:
        super().__init__(
            settings=settings,
            transport_class=TransportClass.WeatherForecastService,
            my_super_alias=my_super_alias,
            my_time_coordinator_alias=my_time_coordinator_alias,
        )
        self.verdicts: dict[str, Verdict] = {}

    def process_message(self, *, envelope: RoutingEnvelope, body: bytes) -> None:
        if envelope.type_name not in (CMD_ACK, CMD_NACK):
            return
        verdict = default_codec.from_dict(json.loads(body))
        if isinstance(verdict, Verdict):
            self.verdicts[verdict.command_hash] = verdict


def minter_settings(settings: GwwfSettings) -> ServiceSettings:
    """The minter's own service settings: the `<universe>.weatherminter`
    alias and the minter's broker client, the actor's when none is set."""
    universe = settings.service_alias.split(".")[0]
    return ServiceSettings(
        service_alias=f"{universe}.weatherminter",
        service_name="weather-minter",
        rabbit=settings.minter_rabbit or settings.rabbit,
    )


def send_create(
    settings: GwwfSettings,
    record: RecordWord,
    *,
    proof: str | None = None,
    timeout_s: float = VERDICT_TIMEOUT_S,
) -> tuple[str, Verdict | None]:
    """Publish the create command for `record`; return the command hash
    and the verdict (None when nothing answered within the timeout)."""
    publisher = _MintPublisher(
        settings=minter_settings(settings),
        my_super_alias=settings.my_super_alias,
        my_time_coordinator_alias=settings.my_time_coordinator_alias,
    )
    cmd = WeatherCreateCmd(record=record, proof=proof)
    payload = cmd.to_bytes()
    chash = command_hash(payload)
    publisher.start()
    try:
        deadline = time.monotonic() + _CONNECT_TIMEOUT_S
        while not publisher.consuming:
            if time.monotonic() > deadline:
                raise TimeoutError("broker connect timed out")
            time.sleep(0.05)
        publisher.send(
            envelope=publisher.direct_envelope(
                type_name=cmd.type_name,
                to_class=TransportClass.WeatherForecastService,
                to_alias=settings.service_alias,
            ),
            body=payload,
        )
        verdict: Verdict | None = None
        deadline = time.monotonic() + timeout_s
        while verdict is None and time.monotonic() < deadline:
            verdict = publisher.verdicts.get(chash)
            time.sleep(0.1)
    finally:
        publisher.stop()
    return chash, verdict
