"""The archive command — a station's history replayed over the bus.

`gw.weather.archive.cmd` names a station, its channels, a Method with
its SourceLocator, and a window. The executor pulls the whole series
through the Method's long pull, lays each quantity on the channels'
shared grid, and builds one `gw.weather.observation` per grid point in
`[Start, End)` with a reading for every channel whose series has a
value at that point, Interpolated false. The actor publishes them on
the station's radio channel, so the history travels the bus as live
observations do.

The ack says the pull succeeded and the replay is under way; a failure
while publishing is a glitch. Every refusable fact is checked before
the pull: the station and channels are records on the station, the
Method has a long pull, and that pull serves each channel's Quantity.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import NamedTuple

from gwwf.grid import iso_to_s, s_to_iso
from gwwf.open_meteo import HistorySeries, coordinates_from_locator, fetch_history
from gwwf.sema.enums import Quantity
from gwwf.sema.types import (
    WeatherArchiveCmd,
    WeatherChannelGt,
    WeatherObservation,
    WeatherObservationReading,
    WeatherStationGt,
)
from gwwf.units import UNIT_SCALE

ARCHIVE_CMD = WeatherArchiveCmd.type_name_value()

HistoryFetch = Callable[
    [str, int, int, Iterable[Quantity]], dict[Quantity, HistorySeries]
]


class HistoryPull(NamedTuple):
    """How one Method pulls history: ``fetch`` takes the SourceLocator
    (the adapter's own encoding), the window and the quantities;
    ``serves`` is what its product can fill."""

    fetch: HistoryFetch
    serves: frozenset[Quantity]


def _open_meteo_history(
    locator: str, start_s: int, end_s: int, quantities: Iterable[Quantity]
) -> dict[Quantity, HistorySeries]:
    latitude, longitude = coordinates_from_locator(locator)
    return fetch_history(
        latitude=latitude,
        longitude=longitude,
        start_s=start_s,
        end_s=end_s,
        quantities=quantities,
    )


ARCHIVE_METHODS: dict[str, HistoryPull] = {
    "open.meteo.historical.forecast.hourly": HistoryPull(
        fetch=_open_meteo_history,
        serves=frozenset({
            Quantity.Temperature,
            Quantity.WindSpeed,
            Quantity.Irradiance,
        }),
    ),
}


class ArchivePlan(NamedTuple):
    """A checked command: the station, the channels in command order,
    their shared grid, and the pull that serves them."""

    station: WeatherStationGt
    channels: list[WeatherChannelGt]
    period_s: int
    offset_s: int
    pull: HistoryPull


def plan_archive(
    cmd: WeatherArchiveCmd,
    *,
    stations: Sequence[WeatherStationGt],
    channels: Sequence[WeatherChannelGt],
    methods: Mapping[str, HistoryPull],
) -> ArchivePlan:
    """Check the command against the records and the Method table;
    raise ``ValueError`` with the refusal reason."""
    by_alias = {s.alias: s for s in stations}
    station = by_alias.get(cmd.station_alias)
    if station is None:
        raise ValueError(f"no station record {cmd.station_alias!r}")
    pull = methods.get(cmd.method)
    if pull is None:
        raise ValueError(f"no long pull for Method {cmd.method!r}")
    by_name = {c.name: c for c in channels if c.station_alias == cmd.station_alias}
    chosen: list[WeatherChannelGt] = []
    for name in cmd.channel_name_list:
        channel = by_name.get(name)
        if channel is None:
            raise ValueError(f"no channel record {name!r} on station {station.alias!r}")
        if channel.quantity not in pull.serves:
            raise ValueError(
                f"Method {cmd.method!r} does not serve {channel.quantity.value} "
                f"({name!r})"
            )
        chosen.append(channel)
    schedules = {(c.emit_period_s, c.emit_offset_s) for c in chosen}
    if len(schedules) != 1:
        raise ValueError(f"channels disagree on emit schedule: {sorted(schedules)}")
    period_s, offset_s = schedules.pop()
    return ArchivePlan(
        station=station,
        channels=chosen,
        period_s=period_s,
        offset_s=offset_s,
        pull=pull,
    )


def grid_points(start_s: int, end_s: int, period_s: int, offset_s: int) -> list[int]:
    """The channels' grid points in ``[start_s, end_s)``, oldest first."""
    first = -((-(start_s - offset_s)) // period_s) * period_s + offset_s
    return list(range(first, end_s, period_s))


def observations_for(
    cmd: WeatherArchiveCmd,
    plan: ArchivePlan,
    series: Mapping[Quantity, HistorySeries],
) -> list[WeatherObservation]:
    """One observation per grid point that has at least one reading:
    each channel's value is the series value whose period holds the
    point, scaled onto the channel's Unit; a point outside a series or
    at a None has no reading for that channel."""
    observations: list[WeatherObservation] = []
    for point_s in grid_points(
        iso_to_s(cmd.start), iso_to_s(cmd.end), plan.period_s, plan.offset_s
    ):
        readings: list[WeatherObservationReading] = []
        for channel in plan.channels:
            value = value_at(series[channel.quantity], point_s)
            if value is None:
                continue
            readings.append(
                WeatherObservationReading(
                    channel_name=channel.name,
                    quantity=channel.quantity,
                    value=round(value * UNIT_SCALE[channel.unit]),
                )
            )
        if readings:
            observations.append(
                WeatherObservation(
                    station_alias=plan.station.alias,
                    observation_time=s_to_iso(point_s),
                    interpolated=False,
                    reading_list=readings,
                )
            )
    return observations


def value_at(series: HistorySeries, t_s: int) -> float | None:
    """The series value whose period holds ``t_s``; None outside the
    series or where the source has no value."""
    index = (t_s - series.first_time_s) // series.period_s
    if index < 0 or index >= len(series.values):
        return None
    return series.values[index]
