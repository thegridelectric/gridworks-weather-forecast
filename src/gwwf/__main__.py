"""Entry point — `uv run python -m gwwf`.

Boots the WeatherActor against the broker configured in `.env`
(GWWF_RABBIT__URL, GWWF_G_NODE_PATH).

Ctrl-C to stop; the actor's queue is auto-delete, so the consumer
evaporates from the broker on disconnect.
"""

from __future__ import annotations

import logging
import time

import dotenv

dotenv.load_dotenv(dotenv.find_dotenv())  # populate env BEFORE Settings()

from gwwf.config import GwwfSettings  # noqa: E402
from gwwf.weather_actor import WeatherActor  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = GwwfSettings()
    actor = WeatherActor(settings)
    actor.start()
    try:
        while actor.main_loop_running:
            time.sleep(1)
    except KeyboardInterrupt:
        actor.stop()


if __name__ == "__main__":
    main()
