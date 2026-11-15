"""gwwf console entry point — `gwwf {rabbit,api,create,archive}`.

The systemd units invoke the services (`weather-rabbit.service` →
`gwwf rabbit`, `weather-api.service` → `gwwf api`); `create
<record.json>` is the human minting act — it publishes the record's
create command over the bus and reports the actor's verdict; `archive`
fills a station's channels with history from an outside source over a
window, the same way.
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
from gwwf.create import VERDICT_TIMEOUT_S, send_command, send_create
from gwwf.record_broadcast import RecordWord, record_name
from gwwf.sema.codec import default_codec
from gwwf.sema.types import WeatherArchiveCmd, WeatherCmdNack
from gwwf.weather_actor import WeatherActor

# The actor pulls the whole window before it answers an archive command.
ARCHIVE_VERDICT_TIMEOUT_S = 90.0


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


def _run_archive(args: argparse.Namespace) -> None:
    cmd = WeatherArchiveCmd(
        station_alias=args.station,
        channel_name_list=args.channel,
        method=args.method,
        source_locator=args.locator,
        start=args.start,
        end=args.end,
        created_ms=int(time.time() * 1000),
    )
    chash, verdict = send_command(
        GwwfSettings(), cmd, timeout_s=ARCHIVE_VERDICT_TIMEOUT_S
    )
    if verdict is None:
        sys.exit(
            f"✗ no verdict within {ARCHIVE_VERDICT_TIMEOUT_S:.0f}s (command "
            f"{chash[:12]}…) — is the weather actor running?"
        )
    if isinstance(verdict, WeatherCmdNack):
        sys.exit(f"✗ refused: {verdict.reason}")
    print(
        f"✓ replaying (ack {chash[:12]}…) — {cmd.station_alias} "
        f"{cmd.start} to {cmd.end} via {cmd.method}"
    )


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
    archiver = subcommands.add_parser(
        "archive",
        help="fill a station's channels with history over a window and report the verdict",
    )
    archiver.add_argument("--station", required=True, help="station alias")
    archiver.add_argument(
        "--channel",
        action="append",
        required=True,
        help="channel name on the station; repeat for each",
    )
    archiver.add_argument(
        "--method", required=True, help="e.g. open.meteo.historical.forecast.hourly"
    )
    archiver.add_argument(
        "--locator",
        required=True,
        help="the Method's SourceLocator, e.g. '<lat>,<lon>'",
    )
    archiver.add_argument(
        "--start", required=True, help="first observation time, UTC ISO"
    )
    archiver.add_argument("--end", required=True, help="end of the window, exclusive")
    args = parser.parse_args(argv)
    if args.command == "create":
        _run_create(args.record_json)
        return
    if args.command == "archive":
        _run_archive(args)
        return
    {"rabbit": _run_rabbit, "api": _run_api}[args.command]()
