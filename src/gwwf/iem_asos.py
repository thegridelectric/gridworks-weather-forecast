"""The IEM ASOS archive: a station's own hourly METAR observations.

The Iowa Environmental Mesonet serves every ASOS station's METAR record
(https://mesonet.agron.iastate.edu/request/download.phtml) back
decades: public domain, attribution appreciated, one request per
second per address, and a request no larger than 1,000 station-years.
The routine observation comes once an hour a few minutes before the
top of the hour (KMLT reports at :53), so this adapter lays each
observation on the top of the hour nearest it: the channels' hourly
grid, with the reading taken as that hour's observation. Temperature
arrives in Fahrenheit and wind speed in knots; the series leave in the
fleet's natural units, °F and mph.

A channel's SourceLocator for this Method is the IEM station identifier
(``MLT`` for KMLT), the adapter's own encoding of its acquisition fact.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import UTC, datetime
from io import StringIO
from typing import Final

import requests

from gwwf.grid import s_to_iso
from gwwf.open_meteo import HistorySeries
from gwwf.sema.enums import Quantity

IEM_ASOS_URL: Final = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
TIMEOUT_S: Final = 120
SECONDS_PER_HOUR: Final = 3600
ROUTINE_REPORT_TYPE: Final = "3"
MISSING: Final = "M"
KNOTS_TO_MPH: Final = 1.150779
# The IEM column per quantity, and the factor from the column's unit
# onto the fleet's natural unit for that quantity (°F stays, knots → mph).
ASOS_COLUMNS: dict[Quantity, tuple[str, float]] = {
    Quantity.Temperature: ("tmpf", 1.0),
    Quantity.WindSpeed: ("sknt", KNOTS_TO_MPH),
}


class IemAsosProductError(Exception):
    """The IEM response did not have what the adapter requires."""


def fetch_asos_history(
    locator: str, start_s: int, end_s: int, quantities: Iterable[Quantity]
) -> dict[Quantity, HistorySeries]:
    """The station's hourly observations over ``[start_s, end_s)``, one
    series per quantity on the hourly grid, in natural units. The pull
    starts one hour early so the first grid point has its observation."""
    wanted = list(quantities)
    first_s = _floor_hour(start_s)
    response = requests.get(
        IEM_ASOS_URL,
        params={
            "station": locator,
            "data": ",".join(ASOS_COLUMNS[q][0] for q in wanted),
            "sts": s_to_iso(first_s - SECONDS_PER_HOUR),
            "ets": s_to_iso(end_s),
            "tz": "Etc/UTC",
            "format": "onlycomma",
            "latlon": "no",
            "elev": "no",
            "missing": MISSING,
            "trace": "T",
            "direct": "no",
            "report_type": ROUTINE_REPORT_TYPE,
        },
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    return parse_asos_history(
        response.text, start_s=start_s, end_s=end_s, quantities=wanted
    )


def parse_asos_history(
    text: str, *, start_s: int, end_s: int, quantities: Iterable[Quantity]
) -> dict[Quantity, HistorySeries]:
    """The CSV body as one series per quantity on the hourly grid that
    covers ``[start_s, end_s)``: each row goes to the top of the hour
    nearest its time, the row nearest the top winning an hour with
    several; an hour with no row, or a missing value, is None. Raises
    ``IemAsosProductError`` when a wanted column is absent."""
    wanted = list(quantities)
    first_s = _floor_hour(start_s)
    count = max(0, -(-(end_s - first_s) // SECONDS_PER_HOUR))
    reader = csv.DictReader(StringIO(text))
    columns = reader.fieldnames or []
    for quantity in wanted:
        column = ASOS_COLUMNS[quantity][0]
        if column not in columns:
            raise IemAsosProductError(f"{column}: column absent from the response")
    values: dict[Quantity, list[float | None]] = {q: [None] * count for q in wanted}
    distance: list[int | None] = [None] * count
    for row in reader:
        valid_s = _valid_to_s(row["valid"])
        hour_s = (
            (valid_s + SECONDS_PER_HOUR // 2) // SECONDS_PER_HOUR * SECONDS_PER_HOUR
        )
        index = (hour_s - first_s) // SECONDS_PER_HOUR
        if index < 0 or index >= count:
            continue
        gap = abs(valid_s - hour_s)
        held = distance[index]
        if held is not None and held <= gap:
            continue
        distance[index] = gap
        for quantity in wanted:
            column, factor = ASOS_COLUMNS[quantity]
            raw = row[column]
            values[quantity][index] = None if raw == MISSING else float(raw) * factor
    return {
        q: HistorySeries(
            quantity=q,
            first_time_s=first_s,
            period_s=SECONDS_PER_HOUR,
            values=values[q],
        )
        for q in wanted
    }


def _floor_hour(t_s: int) -> int:
    return t_s // SECONDS_PER_HOUR * SECONDS_PER_HOUR


def _valid_to_s(valid: str) -> int:
    """An IEM ``valid`` label (``YYYY-MM-DD HH:MM``, in the requested
    zone, UTC here) as epoch seconds."""
    return int(
        datetime.strptime(valid, "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp()
    )
