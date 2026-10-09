# gridworks-weather-forecast

The GridWorks fleet's weather service (gwwf) — a cloud GNode that
publishes CURRENT weather observations and hourly weather forecasts
for the fleet's locations, and serves a public read-only HTTP API as
the single pull path.

**Broadcasts are the delivery.** gwwf publishes two message types over
RabbitMQ on the schedule declared by its channel records:

- `gw.weather.observation` — the newest station observation
  (temperature, wind speed) per location, latest-only; missed grid
  points are replayed as explicitly-marked interpolated messages when
  the gap is short (≤ 3 h), and left as gaps otherwise.
- `gw.weather.forecast` — one message per forecast channel per
  emission, stamped with the source's own data-revision time, with a
  fidelity marker (live / stored / seasonal-template) that keeps
  degraded emissions honest.

**HTTP is the pull path** — drop recovery, fallback, post-hoc reads:
record listings plus latest observation / latest forecast per channel,
every response a sema word byte-identical to its broadcast. Interactive
docs at `/docs` once running.

Cadence, slice structure, units, and locations all come from channel
records in the service's own Postgres (gwwf is the sole accessor);
NWS is the current source (station observations + gridpoint hourly
forecasts), held as one competing forecaster, not an identity.

Message types are governed by **Sema** — the versioned vocabulary of
JSON-Schema contracts for all GridWorks message boundaries, canonical at
[`thegridelectric/sema`](https://github.com/thegridelectric/sema) (schema
ids under `https://schemas.electricity.works`). This repo carries a vendored
snapshot at `src/gwwf/sema` — generated, never hand-edit; regenerate with
`scripts/regen_sema_snapshot.sh` from the seed
`src/gwwf/sema_seed_request.yaml`. See `src/gwwf/sema/README.md` for the
working rules.

## Quick start (dev)

Requires Python 3.12+, [`uv`](https://docs.astral.sh/uv/), Docker, and
a running RabbitMQ broker with the GridWorks fabric (for solo work,
start the dev broker from the sibling `gridworks-base` repo:
`./arm.sh` or `./x86.sh` there).

```sh
uv sync                       # install deps
cp template.env .env          # then fill in the values
docker compose up -d          # the service's own Postgres (host port 5436)
uv run alembic upgrade head   # create/migrate the schema
uv run gwwf rabbit            # run the emission actor
uv run gwwf api               # serve the read API (separate process)
```

The actor needs a `g.node.gt.json` identity file (`GWWF_G_NODE_PATH`);
it validates at boot and its `Alias` must equal `GWWF_SERVICE_ALIAS`.

## Records: how a location, channel, bundle or template enters

The schema starts empty and there is no seed. Every record enters by a
human act over the bus: `gwwf create <record.json>` publishes a
`gw.weather.create.cmd` direct to the running actor and prints the
typed verdict (ack, or a nack with its reason). Records are insert-only
identities; a change is a new record.

```sh
uv run gwwf create my-location.json
```

The file holds one record word instance. The five kinds, in the order
they reference one another:

1. `gw.weather.location.gt` — a station and its coordinates
2. `gw.weather.channel.gt` — an observation channel on a location
3. `gw.weather.forecast.channel.gt` — a forecast channel on a location
4. `gw.weather.forecast.bundle.gt` — the forecast channels one emission carries
5. `gw.weather.seasonal.template.gt` — a location's design-cold month table,
   the last-resort fill

`src/gwwf/sema/samples/` holds a valid instance of each as the shape to
copy. The actor validates every command through the vendored snapshot,
so a malformed or dangling record is nacked, never half-inserted.

**The minter identity.** The create command is sent as
`<universe>.weatherminter`, a Service principal of its own, never as the
actor (the actor addresses its verdict to the sender). Against a dev
broker the minter connects with the actor's broker client. Against a
gated broker it needs its own client cert, named by the
`GWWF_MINTER_RABBIT__*` lines (`template.env`); each `gwwf create` is a
fresh instance on that principal.

## Tests

```sh
./ci.sh
```

Mirrors CI: locked sync, ruff lint + format check, pytest. The DB and
layer-2 integration tests spin ephemeral Postgres/RabbitMQ via
testcontainers (Docker required; they self-skip without it). Live NWS
adapter tests are env-gated: `GWWF_LIVE_NWS=1 uv run pytest
tests/test_nws_live.py`.

## Deploy

`service/weather-rabbit.service` and `service/weather-api.service`
run the two halves under systemd from a repo checkout at
`/home/weather/gridworks-weather-forecast` (adjust user/paths to the
deploy host). The API binds loopback (`GWWF_API_HOST`/`GWWF_API_PORT`,
default `127.0.0.1:8531`); TLS belongs to the fronting proxy. Deploys
are land-in-git → push → pull on the box — never edit on the box.
