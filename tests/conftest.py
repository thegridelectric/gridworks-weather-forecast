"""Shared fixtures.

DB tests ride an ephemeral testcontainers Postgres (override with
`GWWF_TEST_PG_URL` to reuse a standing one); the schema arrives via
the real alembic migration chain, so migrations are exercised — not
`create_all`. No Docker ⇒ the DB tests self-skip.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    override = os.environ.get("GWWF_TEST_PG_URL")
    if override:
        yield override
        return
    # The ryuk reaper can't mount the docker socket on Docker Desktop
    # mac setups; the fixture stops the container itself, so run
    # without the reaper (env-overridable).
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:
        pytest.skip("no GWWF_TEST_PG_URL and testcontainers not installed")
    try:
        pg = PostgresContainer("postgres:16", driver="psycopg")
        pg.start()
    except Exception as e:  # noqa: BLE001 -- Docker unavailable / pull failed
        pytest.skip(f"could not start a testcontainers Postgres: {e}")
    # Yield outside the start try/except so a test failure thrown back in
    # here is never mistaken for an infra-unavailable skip.
    try:
        yield pg.get_connection_url()
    finally:
        pg.stop()


@pytest.fixture(scope="session")
def migrated_engine(pg_url: str) -> Iterator[Engine]:
    from alembic import command
    from alembic.config import Config

    saved = os.environ.get("GWWF_DB_URL")
    os.environ["GWWF_DB_URL"] = pg_url  # alembic env.py reads GwwfSettings
    try:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        command.upgrade(config, "head")
    finally:
        if saved is None:
            del os.environ["GWWF_DB_URL"]
        else:
            os.environ["GWWF_DB_URL"] = saved
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """A session on the migrated harness DB, cleared first — tests
    share one session-scoped container, so each starts from empty."""
    from sqlalchemy import delete

    from gwwf.db.models import (
        BundleSql,
        ForecastChannelSql,
        ForecastSql,
        LastObservationSql,
        LocationSql,
        SourceProductSql,
        WeatherChannelSql,
    )

    factory = sessionmaker(bind=migrated_engine, class_=Session, expire_on_commit=False)
    with factory() as session:
        for model in (
            ForecastSql,
            BundleSql,
            LastObservationSql,
            SourceProductSql,
            ForecastChannelSql,
            WeatherChannelSql,
            LocationSql,
        ):
            session.execute(delete(model))
        session.commit()
        yield session


@pytest.fixture(scope="session")
def rabbit_url() -> Iterator[str]:
    """An AMQP URL for the layer-2 tests: override with
    `GWWF_TEST_RABBIT_URL` (e.g. the shared gw-dev-rabbit `d1__1`
    vhost) for a fast local loop; else ephemeral testcontainers."""
    from urllib.parse import quote

    override = os.environ.get("GWWF_TEST_RABBIT_URL")
    if override:
        yield override
        return
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    try:
        from testcontainers.community.rabbitmq import RabbitMqContainer
    except ImportError:
        pytest.skip("no GWWF_TEST_RABBIT_URL and testcontainers not installed")
    try:
        rabbit = RabbitMqContainer("rabbitmq:3.13", vhost="d1__1")
        rabbit.start()
    except Exception as e:  # noqa: BLE001 -- Docker unavailable / pull failed
        pytest.skip(f"could not start a testcontainers RabbitMQ: {e}")
    try:
        params = rabbit.get_connection_params()
        vhost = quote(rabbit.vhost or "/", safe="")
        yield (
            f"amqp://{rabbit.username}:{rabbit.password}"
            f"@{params.host}:{params.port}/{vhost}"
        )
    finally:
        rabbit.stop()
