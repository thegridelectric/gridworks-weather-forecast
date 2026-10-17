"""Record wire shape — the broadcast half of the minting act.

A record (location / station / channel / forecast-channel / bundle /
seasonal template) is broadcast exactly once, by the actor, when its
create command is applied; nothing tracks or repeats the send. Radio
channel = the record's own name, except the bundle broadcast, which
has NO radio tail — the TypeName segment already separates record
broadcasts from stream messages, and nothing binds records (they
exist for the audit tap).
"""

from __future__ import annotations

from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherLocationGt,
    WeatherSeasonalTemplateGt,
    WeatherStationGt,
)

RecordWord = (
    WeatherLocationGt
    | WeatherStationGt
    | WeatherChannelGt
    | WeatherForecastChannelGt
    | WeatherForecastBundleGt
    | WeatherSeasonalTemplateGt
)


def record_name(record: RecordWord) -> str:
    """The record's own name: the alias for the location, the station
    and the template that fills for a location; the Name for the rest."""
    if isinstance(record, WeatherLocationGt | WeatherStationGt):
        return record.alias
    if isinstance(record, WeatherSeasonalTemplateGt):
        return record.location_alias
    return record.name


def radio_channel_for(record: RecordWord) -> str | None:
    """The bundle broadcast is tail-less; every other record goes out on its name."""
    if isinstance(record, WeatherForecastBundleGt):
        return None
    return record_name(record)
