"""The IEM ASOS adapter: a station's routine METAR observations laid on
the hourly grid, knots onto mph, a missing value a None, parsed from a
captured month."""

from pathlib import Path

import pytest

from gwwf.grid import iso_to_s
from gwwf.iem_asos import KNOTS_TO_MPH, IemAsosProductError, parse_asos_history
from gwwf.sema.enums import Quantity

# October 2024 at KMLT from the IEM ASOS archive (routine reports,
# UTC, tmpf and sknt), fetched 2026-11-16: 743 rows, every one at :53.
FIXTURE = Path(__file__).parent / "fixtures" / "iem_asos_hourly_mlt_2024-10.csv"
QUANTITIES = [Quantity.Temperature, Quantity.WindSpeed]
START = iso_to_s("2024-10-01T00:00:00Z")
END = iso_to_s("2024-11-01T00:00:00Z")


def fixture() -> str:
    return FIXTURE.read_text()


def parse(text: str = "", *, start_s: int = START, end_s: int = END):
    return parse_asos_history(
        text or fixture(), start_s=start_s, end_s=end_s, quantities=QUANTITIES
    )


def test_each_observation_goes_to_the_nearest_top_of_hour() -> None:
    series = parse()
    temperature = series[Quantity.Temperature]
    assert temperature.first_time_s == START
    assert temperature.period_s == 3600
    assert len(temperature.values) == 744
    # 00:53 on 1 October is the 01:00 observation; the fixture starts
    # at 00:53, so 00:00 has none.
    assert temperature.values[0] is None
    assert temperature.values[1] == 58.0
    assert temperature.values[2] == 52.0


def test_knots_become_mph() -> None:
    wind = parse()[Quantity.WindSpeed]
    # 02:53 on 1 October reports 3 knots.
    assert wind.values[3] == pytest.approx(3 * KNOTS_TO_MPH)
    assert wind.values[1] == 0.0


def test_missing_is_none_and_the_rest_of_the_row_stands() -> None:
    text = "station,valid,tmpf,sknt\nMLT,2024-10-01 00:53,M,4.00\n"
    series = parse(text, end_s=START + 2 * 3600)
    assert series[Quantity.Temperature].values == [None, None]
    assert series[Quantity.WindSpeed].values == [None, pytest.approx(4 * KNOTS_TO_MPH)]


def test_the_row_nearest_the_top_wins_an_hour() -> None:
    text = (
        "station,valid,tmpf,sknt\n"
        "MLT,2024-10-01 00:40,50.00,0.00\n"
        "MLT,2024-10-01 00:53,51.00,0.00\n"
        "MLT,2024-10-01 01:10,52.00,0.00\n"
    )
    series = parse(text, end_s=START + 2 * 3600)
    assert series[Quantity.Temperature].values == [None, 51.0]


def test_rows_outside_the_window_are_dropped() -> None:
    text = (
        "station,valid,tmpf,sknt\n"
        "MLT,2024-09-30 22:53,40.00,0.00\n"
        "MLT,2024-09-30 23:53,41.00,0.00\n"
        "MLT,2024-10-01 00:53,42.00,0.00\n"
    )
    series = parse(text, end_s=START + 3600)
    assert series[Quantity.Temperature].values == [41.0]


def test_a_missing_column_refuses() -> None:
    with pytest.raises(IemAsosProductError, match="sknt"):
        parse("station,valid,tmpf\nMLT,2024-10-01 00:53,50.00\n")
