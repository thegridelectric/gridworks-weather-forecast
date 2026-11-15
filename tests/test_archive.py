"""The archive executor: a command checked against the records and the
Method table, then the window laid on the channels' grid as
observations, End exclusive, Interpolated false, a missing hour a
missing reading, a point with no reading no message."""

from __future__ import annotations

import pytest

from gwwf.archive import (
    ArchivePlan,
    HistoryPull,
    grid_points,
    observations_for,
    plan_archive,
)
from gwwf.grid import iso_to_s
from gwwf.open_meteo import HistorySeries
from gwwf.sema.enums import Quantity
from gwwf.sema.types import WeatherArchiveCmd
from tests.records import (
    IRRADIANCE_CHANNEL,
    KMEMILLI18_STATION,
    KMLT_STATION,
    TEMPERATURE_CHANNEL,
    WINDSPEED_CHANNEL,
)

START = "2024-10-01T00:00:00Z"
END = "2024-10-01T03:00:00Z"
STATIONS = [KMLT_STATION, KMEMILLI18_STATION]
CHANNELS = [TEMPERATURE_CHANNEL, WINDSPEED_CHANNEL, IRRADIANCE_CHANNEL]


def never_fetch(*_: object) -> dict[Quantity, HistorySeries]:
    raise AssertionError("the plan never pulls")


METHODS = {
    "test.hourly": HistoryPull(
        fetch=never_fetch,
        serves=frozenset({Quantity.Temperature, Quantity.WindSpeed}),
    )
}


def command(**overrides: object) -> WeatherArchiveCmd:
    fields: dict[str, object] = {
        "station_alias": "kmlt",
        "channel_name_list": ["kmlt.temperature", "kmlt.windspeed"],
        "method": "test.hourly",
        "source_locator": "45.6478,-68.6856",
        "start": START,
        "end": END,
        "created_ms": 1794754200000,
    }
    fields.update(overrides)
    return WeatherArchiveCmd(**fields)  # type: ignore[arg-type]


def plan(cmd: WeatherArchiveCmd) -> ArchivePlan:
    return plan_archive(cmd, stations=STATIONS, channels=CHANNELS, methods=METHODS)


def hourly(quantity: Quantity, first: str, values: list[float | None]) -> HistorySeries:
    return HistorySeries(
        quantity=quantity, first_time_s=iso_to_s(first), period_s=3600, values=values
    )


def test_plan_takes_the_channels_in_command_order() -> None:
    p = plan(command(channel_name_list=["kmlt.windspeed", "kmlt.temperature"]))
    assert [c.name for c in p.channels] == ["kmlt.windspeed", "kmlt.temperature"]
    assert p.station is KMLT_STATION
    assert (p.period_s, p.offset_s) == (
        TEMPERATURE_CHANNEL.emit_period_s,
        TEMPERATURE_CHANNEL.emit_offset_s,
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"station_alias": "nowhere", "channel_name_list": ["nowhere.temperature"]},
            "no station record 'nowhere'",
        ),
        ({"method": "no.such.pull"}, "no long pull for Method 'no.such.pull'"),
        (
            {"channel_name_list": ["kmlt.pressure"]},
            "no channel record 'kmlt.pressure' on station 'kmlt'",
        ),
        (
            {
                "station_alias": "kmemilli18",
                "channel_name_list": ["kmemilli18.irradiance"],
            },
            "does not serve Irradiance",
        ),
    ],
)
def test_plan_refuses_with_the_reason(
    overrides: dict[str, object], reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        plan(command(**overrides))


def test_grid_points_are_half_open_on_the_channel_grid() -> None:
    assert grid_points(iso_to_s(START), iso_to_s(END), 3600, 0) == [
        iso_to_s("2024-10-01T00:00:00Z"),
        iso_to_s("2024-10-01T01:00:00Z"),
        iso_to_s("2024-10-01T02:00:00Z"),
    ]
    # A Start off the grid rounds up to the first point on it.
    assert grid_points(iso_to_s("2024-10-01T00:30:00Z"), iso_to_s(END), 3600, 0) == [
        iso_to_s("2024-10-01T01:00:00Z"),
        iso_to_s("2024-10-01T02:00:00Z"),
    ]


def test_observations_scale_onto_the_channel_unit_and_skip_missing_hours() -> None:
    cmd = command()
    series = {
        Quantity.Temperature: hourly(
            Quantity.Temperature, START, [57.94, None, 54.4, 99.0]
        ),
        Quantity.WindSpeed: hourly(Quantity.WindSpeed, START, [4.9, 4.3, None, 99.0]),
    }
    observations = observations_for(cmd, plan(cmd), series)
    assert [o.observation_time for o in observations] == [
        "2024-10-01T00:00:00Z",
        "2024-10-01T01:00:00Z",
        "2024-10-01T02:00:00Z",
    ]
    assert all(o.interpolated is False for o in observations)
    assert all(o.station_alias == "kmlt" for o in observations)
    first, second, third = observations
    assert [(r.channel_name, r.value) for r in first.reading_list] == [
        ("kmlt.temperature", 579),
        ("kmlt.windspeed", 49),
    ]
    assert [(r.channel_name, r.value) for r in second.reading_list] == [
        ("kmlt.windspeed", 43)
    ]
    assert [(r.channel_name, r.value) for r in third.reading_list] == [
        ("kmlt.temperature", 544)
    ]


def test_a_point_with_no_reading_is_no_message() -> None:
    cmd = command(channel_name_list=["kmlt.temperature"])
    series = {
        Quantity.Temperature: hourly(
            Quantity.Temperature, "2024-10-01T02:00:00Z", [54.4]
        ),
    }
    observations = observations_for(cmd, plan(cmd), series)
    assert [o.observation_time for o in observations] == ["2024-10-01T02:00:00Z"]
