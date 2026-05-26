# gridworks-weather-forecast

Weather service for the GridWorks fleet.

**What it does today:** publishes `weather` (v000) realtime observations
on a fixed cadence — a like-for-like replacement for the legacy
`weather_service.py` that lived inside `gridworks-journalkeeper`. Same
type, same cadence, same channel (`weather.gov.kmlt`); only the host
and framework change.

**What it grows into:** forecasts (`weather.forecast`) for the LTN
forward-looking optimizers + observations under a renamed
(`gw.weather`-ish) type — see the design intent in the wiki for the
trajectory.

## Quick start

Requires Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and a running
RabbitMQ broker for the actor to publish into.

```sh
# 1. install deps
uv sync

# 2. set local config
cp template.env .env
$EDITOR .env   # fill in GWWF_RABBIT__URL etc.

# 3. run the actor
uv run python -m gwwf
```

## Solo dev — start the dev RabbitMQ broker

If you're working on this repo in isolation (not as part of a larger
simulation that brings its own broker), spin up a local dev broker from
the sibling `gridworks-base` repo:

```sh
cd ../gridworks-base
./x86.sh        # on Apple silicon: ./arm.sh
```

That starts the `gw-dev-rabbit` container (the GHCR-baked image with the
canonical exchange/binding topology baked in) on the standard ports
(`5672` AMQP, `1885` MQTT, `15672` mgmt UI). Then point this repo at it
in `.env`:

```
GWWF_RABBIT__URL=amqp://smqPublic:smqPublic@localhost:5672/d1__1
```

The same image runs in CI (see `.github/workflows/tests.yml`), so what
you test locally matches what CI tests.

## Layout

```
src/gwwf/        # the service package
  __init__.py
  __main__.py    # entry point
template.env     # env-var template; copy to .env (gitignored)
```

## License

MIT — see `LICENSE`.
