# gridworks-weather-forecast

The GridWorks fleet's weather service (gwwf) — a cloud GNode that
publishes CURRENT weather observations and hourly weather forecasts
for the fleet's locations, and serves a public read-only HTTP API as
the single pull path.

**Broadcasts are the delivery.** gwwf publishes two message types over
RabbitMQ on the schedule declared by its channel records:

- `gw.weather.observation` — the newest observation per station, one
  reading per quantity the station reported, latest-only; missed grid
  points are replayed as explicitly-marked interpolated messages when
  the gap is short (≤ 3 h), and left as gaps otherwise.
- `gw.weather.forecast` — one message per bundle per emission, one
  series per quantity the bundle binds, each stamped with its source's
  own data-revision time and a fidelity marker (live / stored /
  seasonal-template) that keeps degraded emissions honest.

**HTTP is the pull path** — drop recovery, fallback, post-hoc reads:
record listings plus latest observation per station and latest
forecast per bundle,
every response a sema word byte-identical to its broadcast. Interactive
docs at `/docs` once running.

Cadence, slice structure, units, stations and locations all come from
records in the service's own Postgres (gwwf is the sole accessor); a
forecast channel's Method names the adapter that serves it. Two
sources, each one competing forecaster and not an identity: NWS
(station observations, gridpoint hourly temperature and wind) and
Open-Meteo (hourly global horizontal irradiance from the pinned
`gfs_seamless` model, free tier, CC BY 4.0). Each series in a
forecast declares its own fidelity.

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
the local dev broker from the sibling `gridworks-base` repo (`./arm.sh`
or `./x86.sh` there).

```sh
uv sync                  # install deps
scripts/dev-up.sh        # empty DB, migrate, actor + API as d1.weather, mint dev-records/
scripts/dev-down.sh      # stop both and drop the DB volume
```

`dev-up.sh` is the whole dev loop: it tears down any previous dev run,
brings up the compose Postgres empty (host port 5436), migrates it,
starts the actor and the read API under `scripts/dev.env` with the
dev identity `scripts/dev-g.node.gt.json`, and mints every record in
`dev-records/` over the bus in referential order. The actor's log is
`~/.local/state/gridworks/weather-forecast/log/d1.weather.log`;
`.dev/` has the pids. The pieces by hand, against a filled `.env`:

```sh
docker compose up -d && uv run alembic upgrade head
uv run gwwf rabbit            # the emission actor
uv run gwwf api               # the read API (separate process)
```

The actor needs a `g.node.gt.json` identity file (`GWWF_G_NODE_PATH`);
it validates at boot and its `Alias` must equal `GWWF_SERVICE_ALIAS`.

## Records: how a location, station, channel, bundle or template enters

The schema starts empty and there is no seed. Every record enters by a
human act over the bus (`dev-records/` holds the dev set, which
`scripts/dev-up.sh` mints the same way): `gwwf create <record.json>` publishes a
`gw.weather.create.cmd` direct to the running actor and prints the
typed verdict (ack, or a nack with its reason). Records are insert-only
identities; a change is a new record.

```sh
uv run gwwf create my-location.json
```

The file holds one record word instance. The six kinds, in the order
they reference one another:

1. `gw.weather.location.gt` — a place and its coordinates
2. `gw.weather.station.gt` — an observing instrument and its network ids
3. `gw.weather.channel.gt` — an observed quantity on a station
4. `gw.weather.forecast.channel.gt` — a named predictor of one channel
5. `gw.weather.forecast.bundle.gt` — the bindings one emission serves
6. `gw.weather.seasonal.template.gt` — a location's design-cold month table,
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
tests/test_nws_live.py`. Do not point `GWWF_TEST_RABBIT_URL` at the
dev broker while a dev actor runs on it: both answer to `d1.weather`,
so the running actor consumes the suite's create commands.

## Deploy

`service/weather-rabbit.service` and `service/weather-api.service`
run the two halves under systemd from a repo checkout at
`/home/weather/gridworks-weather-forecast` (adjust user/paths to the
deploy host). The API binds loopback (`GWWF_API_HOST`/`GWWF_API_PORT`,
default `127.0.0.1:8531`); TLS belongs to the fronting proxy. Deploys
are commit → push → pull on the box — never edit on the box. The
schema has one migration; a box whose DB predates the station words
drops and recreates the database before `alembic upgrade head`, then
re-mints its records.
