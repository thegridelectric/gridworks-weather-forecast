"""Slice-grid arithmetic for record-driven emission.

Every function walks explicit per-slice durations — nothing here
assumes uniform slices. The NWS product's hourly uniformity is an
adapter-side fact that stops at the adapter boundary; laying source
values onto a channel's own grid happens here, piecewise-constant.
Times are epoch seconds internally; ISO renders only at the message
boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime


class GridError(Exception):
    """A grid computation was asked for points the source cannot cover."""


def iso_to_s(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def s_to_iso(epoch_s: int) -> str:
    return datetime.fromtimestamp(epoch_s, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_slot(now_s: int, period_s: int, offset_s: int) -> int:
    """The first emission slot strictly after ``now_s``."""
    return ((now_s - offset_s) // period_s + 1) * period_s + offset_s


def slots_between(
    after_s: int, until_s: int, period_s: int, offset_s: int
) -> list[int]:
    """Emission slots in ``(after_s, until_s]``, oldest first."""
    slots = []
    slot = next_slot(after_s, period_s, offset_s)
    while slot <= until_s:
        slots.append(slot)
        slot += period_s
    return slots


def slice_starts(first_start_s: int, durations: Sequence[int]) -> list[int]:
    """Start times of every slice; walks the durations element-wise."""
    starts = [first_start_s]
    for duration in durations[:-1]:
        starts.append(starts[-1] + duration)
    return starts


def lay_on_grid(
    starts_s: Sequence[int],
    *,
    source_start_s: int,
    source_period_s: int,
    source_values: Sequence[int],
) -> list[int]:
    """Value for each slice start, piecewise-constant from a uniform source.

    Each slice takes the source value whose period contains the slice
    start. Raises ``GridError`` when a slice start falls outside the
    source series.
    """
    values = []
    for start_s in starts_s:
        index = (start_s - source_start_s) // source_period_s
        if index < 0 or index >= len(source_values):
            raise GridError(
                f"slice start {s_to_iso(start_s)} outside source series "
                f"({s_to_iso(source_start_s)} + {len(source_values)} × "
                f"{source_period_s}s)"
            )
        values.append(source_values[index])
    return values


def interpolate_at(t_s: int, *, t0_s: int, v0: int, t1_s: int, v1: int) -> int:
    """Linear interpolation at ``t_s`` between two readings, rounded."""
    if not t0_s < t_s < t1_s:
        raise GridError(f"{t_s} not strictly between {t0_s} and {t1_s}")
    return round(v0 + (v1 - v0) * (t_s - t0_s) / (t1_s - t0_s))
