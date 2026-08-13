"""gwwf console entry point — `gwwf {rabbit,api,create}`.

The systemd units invoke the services (`weather-rabbit.service` →
`gwwf rabbit`, `weather-api.service` → `gwwf api`); `create
<record.json>` is the human minting act — it publishes the record's
create command over the bus and reports the actor's verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import dotenv
import uvicorn

from gwwf.config import GwwfSettings
from gwwf.create import VERDICT_TIMEOUT_S, send_create
from gwwf.record_broadcast import RecordWord, record_name
from gwwf.sema.codec import default_codec
from gwwf.sema.types import WeatherCmdNack
from gwwf.weather_actor import WeatherActor


def _run_rabbit() -> None:
    actor = WeatherActor(GwwfSettings())
    actor.start()
    try:
        while actor.main_loop_running:
            time.sleep(1)
    except KeyboardInterrupt:
        actor.stop()


def _run_api() -> None:
    settings = GwwfSettings()
    uvicorn.run(
        "gwwf.api:app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
    )


def _run_create(path: str) -> None:
    payload = json.loads(Path(path).read_text())
    record = default_codec.from_dict(payload)
    if not isinstance(record, RecordWord):
        sys.exit(f"✗ {record.type_name} is not a weather record word")
    chash, verdict = send_create(GwwfSettings(), record)
    if verdict is None:
        sys.exit(
            f"✗ no verdict within {VERDICT_TIMEOUT_S:.0f}s (command "
            f"{chash[:12]}…) — is the weather actor running?"
        )
    if isinstance(verdict, WeatherCmdNack):
        sys.exit(f"✗ refused: {verdict.reason}")
    print(f"✓ applied (ack {chash[:12]}…) — {record.type_name} {record_name(record)}")


def main(argv: list[str] | None = None) -> None:
    dotenv.load_dotenv(dotenv.find_dotenv())  # populate env BEFORE Settings()
    parser = argparse.ArgumentParser(prog="gwwf")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("rabbit", help="run the emission actor")
    subcommands.add_parser(
        "api", help="serve the read façade (loopback; TLS at the proxy)"
    )
    creator = subcommands.add_parser(
        "create",
        help="mint one record: publish its create command and report the verdict",
    )
    creator.add_argument(
        "record_json", help="path to a JSON file holding one record word instance"
    )
    args = parser.parse_args(argv)
    if args.command == "create":
        _run_create(args.record_json)
        return
    {"rabbit": _run_rabbit, "api": _run_api}[args.command]()
