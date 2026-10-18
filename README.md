# gridworks-weather-forecast

The weather service is the fleet's one source of weather: what the air
is doing now at the stations near the houses, and what it will do over
the coming days. It publishes the newest observation from each station
it follows as the station reports, and on each forecast's own schedule
it fetches that forecast from the forecaster who serves it and publishes
it, one message per bundle of forecast channels that a consumer signs up
to. The Scada actors and LeafTransactiveNode actors read these messages,
so a missing forecast is a worse plan. Every forecast says, series by
series, whether it is live, a stored copy or a seasonal fallback, so a
reader knows how far to trust it.

What the service knows about the world, the places, the stations, which
quantities each station reports, which forecaster predicts which
quantity on what time grid, and which bundles exist, is a set of records
that enter once, by a person's act, and never change in place; a change
is a new record. The service starts empty and runs idle until records
arrive. Today the observations come from the National Weather Service's
station feeds, and the forecasts from its gridpoint model and from
Open-Meteo's solar model. Each forecaster is one competitor among
several, and a new one enters as a record with no code change. A read-only web
interface serves the records and the latest observation and forecast for
anyone who missed a message. The service keeps a database of its own
that nobody else reads.

## What it speaks

Every message in or out is a Sema word, the versioned vocabulary of
JSON-Schema contracts for GridWorks boundaries, canonical at
[`thegridelectric/sema`](https://github.com/thegridelectric/sema). The
words this service speaks, at the versions it speaks them, are listed in
`src/gwwf/sema_seed_request.yaml`.

Published on the broker:

- `gw.weather.observation`: the newest observation from one station, one
  reading per quantity the station reported, latest-only. A missed grid
  point is replayed as an explicitly marked interpolated message when the
  gap is three hours or less, and left as a gap otherwise.
- `gw.weather.forecast`: one message per bundle per emission, one series
  per quantity the bundle binds, each series stamped with its source's
  own revision time and a fidelity marker (live, stored,
  seasonal.template).
- Each record word, once, when the record is created, on a channel named
  for the record.
- `glitch`: a fault a person may want to know about (a source that would
  not answer, a gap that could not be filled).

Received on the broker:

- `gw.weather.create.cmd`: one record to insert, sent direct to the
  service by the minter. The verdict goes back to the sender as
  `gw.weather.cmd.ack` or `gw.weather.cmd.nack` with its reason.

Served over HTTP, every response a Sema word byte-identical to its
broadcast: a listing per record kind, the latest observation per
station, the latest forecast per bundle, and a ping. Interactive docs
at `/docs` once running.

## Processes

- **The actor** (`gwwf rabbit`): a GridWorks GNode on the broker. It
  fetches, publishes, and is the only writer of the service's database.
  It validates its identity file at boot and refuses to start if the
  file's alias and its configured alias differ.
- **The read API** (`gwwf api`): a separate process serving the HTTP
  surface from the same database. It needs no broker.
- **The minter** (`gwwf create <record.json>`): a command a person runs
  to insert one record. It connects as its own principal,
  `<universe>.weatherminter`, publishes the create command and prints
  the verdict the actor addresses to that sender.

## Records

The schema starts empty and there is no seed. Every record enters by a
human act over the bus. Records are insert-only identities; a change is
a new record. The actor validates every command through the vendored
snapshot, so a malformed or dangling record is refused and nothing is
inserted.

The six record kinds, in the order they reference one another:

1. `gw.weather.location.gt`: a place and its coordinates
2. `gw.weather.station.gt`: an observing instrument and its network ids
3. `gw.weather.channel.gt`: an observed quantity on a station
4. `gw.weather.forecast.channel.gt`: a named predictor of one channel,
   with its own time-slice grid; its Method names the adapter that
   serves it
5. `gw.weather.forecast.bundle.gt`: the bindings one emission serves;
   its embedded channel copies must agree with the canonical records
6. `gw.weather.seasonal.template.gt`: a location's design-cold month
   table, the last-resort fill

`dev-records/` holds the dev set in that order, and the same files mint
the production set by hand. `src/gwwf/sema/samples/` holds one valid
instance of each word.

## Operation

Where the service runs, who may log in, and how it is deployed,
restarted and re-minted are in `gridworks-infra/forecast/instance-README.md`.
The two systemd units are in `service/`. Deploys are commit, push, pull
on the box; nothing is edited there.

## Development

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
starts the actor and the read API under `scripts/dev.env` with the dev
identity `scripts/dev-g.node.gt.json`, and mints every record in
`dev-records/` over the bus in referential order. The actor's log is
`~/.local/state/gridworks/weather-forecast/log/d1.weather.log`; `.dev/`
has the pids. The pieces by hand, against a filled `.env` (copy
`template.env`; settings take the `GWWF_` prefix):

```sh
docker compose up -d && uv run alembic upgrade head
uv run gwwf rabbit            # the actor
uv run gwwf api               # the read API
uv run gwwf create my-location.json
```

Against a dev broker the minter uses the actor's broker client. Against
a gated broker it needs its own client cert, named by the
`GWWF_MINTER_RABBIT__*` lines in `template.env`.

The vendored Sema snapshot at `src/gwwf/sema` is generated; to change
it, edit the seed and run `scripts/regen_sema_snapshot.sh`. `src/gwwf/sema/README.md` has the working rules.

Tests:

```sh
./ci.sh
```

Mirrors CI: locked sync, ruff lint and format check, pytest. The DB and
broker integration tests spin ephemeral Postgres and RabbitMQ via
testcontainers (Docker required; they self-skip without it). Live NWS
adapter tests are env-gated: `GWWF_LIVE_NWS=1 uv run pytest
tests/test_nws_live.py`. Do not point `GWWF_TEST_RABBIT_URL` at the dev
broker while a dev actor runs on it: both answer to `d1.weather`, so the
running actor consumes the suite's create commands.
