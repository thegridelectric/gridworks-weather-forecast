"""gwwf console entry point — `gwwf {rabbit,api,seed}`.

The systemd units invoke these (`weather-rabbit.service` → `gwwf
rabbit`, `weather-api.service` → `gwwf api`); `seed` is the explicit
operator step that populates the record tables — never a boot side
effect.
"""

from __future__ import annotations

import argparse
import time

import dotenv
import uvicorn

from gwwf.config import GwwfSettings
from gwwf.db.session import SessionLocal
from gwwf.db.store import seed_records
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


def _run_seed() -> None:
    with SessionLocal() as session:
        seed_records(session)
    print("gridworks-weather DB seeded (standup records)")


def main(argv: list[str] | None = None) -> None:
    dotenv.load_dotenv(dotenv.find_dotenv())  # populate env BEFORE Settings()
    parser = argparse.ArgumentParser(prog="gwwf")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("rabbit", help="run the emission actor")
    subcommands.add_parser(
        "api", help="serve the read façade (loopback; TLS at the proxy)"
    )
    subcommands.add_parser(
        "seed", help="seed the record tables (explicit operator step)"
    )
    command = parser.parse_args(argv).command
    {"rabbit": _run_rabbit, "api": _run_api, "seed": _run_seed}[command]()
