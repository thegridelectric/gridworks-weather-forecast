"""The read façade over a migrated Postgres — wire-form equality.

The done-when bar: a pull returns byte-identical wire form to the
broadcast (same `to_dict` rendering, served from the stored payload).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests import records
from tests.test_api_wire import FORECAST, OBSERVATION
from gwwf.api import create_app
from gwwf.config import GwwfSettings
from gwwf.db.store import save_last_observation, store_forecast


def client_for(pg_url: str) -> TestClient:
    from pydantic import SecretStr

    return TestClient(create_app(GwwfSettings(db_url=SecretStr(pg_url))))


def test_record_listings_and_latest_pulls(db_session: Session, pg_url: str) -> None:
    records.seed(db_session)
    save_last_observation(
        db_session, observation=OBSERVATION, published_slot=1792000000
    )
    store_forecast(db_session, FORECAST)

    client = client_for(pg_url)

    assert client.get("/d1-weather/channels").json() == [
        c.to_dict() for c in records.OBSERVATION_CHANNELS
    ]
    assert client.get("/d1-weather/forecast-channels").json() == [
        c.to_dict() for c in records.FORECAST_CHANNELS
    ]
    assert client.get("/d1-weather/bundles").json() == [
        b.to_dict() for b in records.FORECAST_BUNDLES
    ]
    assert client.get("/d1-weather/locations").json() == [
        records.MILLINOCKET_LOCATION.to_dict()
    ]
    assert client.get("/d1-weather/stations").json() == [
        s.to_dict() for s in records.STATIONS
    ]
    assert client.get("/d1-weather/seasonal-templates").json() == [
        t.to_dict() for t in records.SEASONAL_TEMPLATES
    ]

    # Byte-identical wire form to the broadcast.
    pulled = client.get(f"/d1-weather/latest-observation/{records.KMLT}")
    assert pulled.status_code == 200
    assert pulled.json() == OBSERVATION.to_dict()

    pulled = client.get(f"/d1-weather/latest-forecast/{FORECAST.bundle_name}")
    assert pulled.status_code == 200
    assert pulled.json() == FORECAST.to_dict()


def test_missing_pulls_404(db_session: Session, pg_url: str) -> None:
    records.seed(db_session)
    client = client_for(pg_url)
    assert client.get("/d1-weather/latest-observation/nosuchstation").status_code == 404
    assert client.get("/d1-weather/latest-forecast/no.such.bundle").status_code == 404
