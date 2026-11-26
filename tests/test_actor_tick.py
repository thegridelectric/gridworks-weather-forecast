"""The actor's tick on a real database, with the broker send captured:
activation by the bundle's own published forecast, a rebuild that fires
before it replaces, a failed build that is tried again."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from gwwf.clock import ManualClock
from gwwf.config import GwwfSettings
from gwwf.db.store import insert_record
from gwwf.sema.enums import WeatherForecastFidelity
from gwwf.sema.types import WeatherForecast, WeatherSeasonalTemplateGt
from gwwf.weather_actor import BUILD_RETRY_S, ProductAdapter, WeatherActor
from tests.test_layer2_weather import (
    BUNDLE_NAME,
    FAST_BUNDLE,
    FAST_RECORDS,
    LOCATION,
    SERVICE_ALIAS,
    fake_adapters,
    g_node_file,
)

# September 2026, three seconds past a multiple of 300. The fast bundle
# emits every 5 s at offset 2, so the latest slot at or before T0 is
# T0 - 1 and the next is T0 + 4.
T0 = 1_788_592_203
MOOSE_NAME = "d1.test.loc.moose"
# A second bundle on the fast bundle's own forecast channels, so on the
# same acquisitions.
MOOSE = FAST_BUNDLE.model_copy(
    update={"name": MOOSE_NAME, "display_name": "moose", "id": str(uuid.uuid4())}
)
TEMPLATE = WeatherSeasonalTemplateGt(
    location_alias=LOCATION,
    temp_by_month=[-300, -700, 100, 2100, 3000, 3100, 4600, 4700, 2800, 2400, 1600, 0],
    start="2026-08-01T00:00:00Z",
    id=str(uuid.uuid4()),
)


class Rig:
    """An actor that is never started: the test calls ``tick`` with the
    plant time it wants and reads what the actor would have sent."""

    def __init__(
        self,
        pg_url: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        adapters: dict[str, ProductAdapter],
    ) -> None:
        self.actor = WeatherActor(
            GwwfSettings(
                db_url=SecretStr(pg_url),
                g_node_path=g_node_file(tmp_path),
                service_alias=SERVICE_ALIAS,
                service_name="weather-forecast-tick",
            ),
            adapters=adapters,
            clock=ManualClock(T0),
        )
        self.sent: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            self.actor,
            "send",
            lambda *, envelope, body: self.sent.append((
                envelope.type_name,
                json.loads(body),
            )),
        )

    def forecasts(self) -> list[WeatherForecast]:
        return [
            WeatherForecast.from_dict(payload)
            for type_name, payload in self.sent
            if type_name == WeatherForecast.type_name_value()
        ]

    def glitches(self) -> list[dict[str, Any]]:
        return [
            payload
            for type_name, payload in self.sent
            if type_name != WeatherForecast.type_name_value()
        ]


def unreachable_source(slices: int) -> Any:
    raise RuntimeError("source unreachable")


def test_a_bundle_minted_on_an_acquisition_already_fetched_activates(
    pg_url: str,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for record in FAST_RECORDS:
        insert_record(db_session, record)
    rig = Rig(pg_url, tmp_path, monkeypatch, fake_adapters(T0))

    rig.actor.tick(T0)
    assert [f.bundle_name for f in rig.forecasts()] == [BUNDLE_NAME]

    # The first bundle's fetch has stored a product for both acquisitions.
    insert_record(db_session, MOOSE)
    rig.actor.scheduler_stale.set()
    rig.actor.tick(T0 + 1)
    assert [f.bundle_name for f in rig.forecasts()] == [BUNDLE_NAME, MOOSE_NAME]
    assert rig.forecasts()[1].message_created_ms == (T0 - 1) * 1000


def test_a_rebuild_on_a_slot_tick_still_fires_the_slot(
    pg_url: str,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for record in FAST_RECORDS:
        insert_record(db_session, record)
    rig = Rig(pg_url, tmp_path, monkeypatch, fake_adapters(T0))
    rig.actor.tick(T0)

    # T0 + 4 is the next slot. Its window and source revisions are the
    # activation's, so the archive answers with the row it already has,
    # and the test counts sends.
    rig.actor.scheduler_stale.set()
    rig.actor.tick(T0 + 4)
    assert len(rig.forecasts()) == 2
    rig.actor.tick(T0 + 5)
    assert len(rig.forecasts()) == 2


def test_a_rebuild_after_a_template_activation_sends_nothing_more(
    pg_url: str,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for record in (*FAST_RECORDS, TEMPLATE):
        insert_record(db_session, record)
    adapters = {
        method: adapter._replace(fetch_for=lambda channel, now: unreachable_source)
        for method, adapter in fake_adapters(T0).items()
    }
    rig = Rig(pg_url, tmp_path, monkeypatch, adapters)

    rig.actor.tick(T0)
    (activation,) = rig.forecasts()
    assert {s.fidelity for s in activation.series_list} == {
        WeatherForecastFidelity.SeasonalTemplate
    }

    rig.actor.scheduler_stale.set()
    rig.actor.tick(T0 + 1)
    assert len(rig.forecasts()) == 1


def test_a_failed_build_is_tried_again_with_no_create(
    pg_url: str,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for record in FAST_RECORDS:
        insert_record(db_session, record)
    rig = Rig(pg_url, tmp_path, monkeypatch, fake_adapters(T0))
    sessions = rig.actor._sessions
    reachable = False

    def flaky_sessions() -> Session:
        if not reachable:
            raise RuntimeError("database unreachable")
        return sessions()

    monkeypatch.setattr(rig.actor, "_sessions", flaky_sessions)

    rig.actor.tick(T0)
    assert rig.forecasts() == []
    assert [g["Summary"] for g in rig.glitches()] == ["scheduler build failed"]

    reachable = True
    rig.actor.tick(T0 + 1)
    assert rig.forecasts() == []
    rig.actor.tick(T0 + BUILD_RETRY_S)
    assert [f.bundle_name for f in rig.forecasts()] == [BUNDLE_NAME]
    assert len(rig.glitches()) == 1
