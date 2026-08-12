"""Slice-grid arithmetic — non-uniform slices are the primary case."""

from __future__ import annotations

import pytest

from gwwf.grid import (
    GridError,
    interpolate_at,
    lay_on_grid,
    next_slot,
    s_to_iso,
    slice_starts,
    slots_between,
)


def test_next_slot() -> None:
    assert next_slot(3600, 3600, 0) == 7200  # strictly after
    assert next_slot(3599, 3600, 0) == 3600
    assert next_slot(5399, 3600, 1800) == 5400
    assert next_slot(5400, 3600, 1800) == 9000
    assert next_slot(59, 60, 30) == 90


def test_slots_between() -> None:
    assert slots_between(0, 7200, 3600, 0) == [3600, 7200]
    assert slots_between(3600, 3600, 3600, 0) == []
    assert slots_between(0, 10800, 3600, 1800) == [1800, 5400, 9000]


def test_slice_starts_non_uniform() -> None:
    assert slice_starts(1000, [300, 600, 300]) == [1000, 1300, 1900]
    assert slice_starts(0, [3600]) == [0]


def test_lay_on_grid_non_uniform_slices_from_hourly_source() -> None:
    # Source: hourly values 10, 20, 30 from t=0. Slices: 30 min, 90 min,
    # 60 min starting t=1800 — starts at 1800, 3600, 9000.
    values = lay_on_grid(
        slice_starts(1800, [1800, 5400, 3600]),
        source_start_s=0,
        source_period_s=3600,
        source_values=[10, 20, 30],
    )
    assert values == [10, 20, 30]


def test_lay_on_grid_out_of_range() -> None:
    with pytest.raises(GridError):
        lay_on_grid(
            [7200],
            source_start_s=0,
            source_period_s=3600,
            source_values=[10, 20],
        )
    with pytest.raises(GridError):
        lay_on_grid(
            [-1],
            source_start_s=0,
            source_period_s=3600,
            source_values=[10],
        )


def test_interpolate_at() -> None:
    assert interpolate_at(1800, t0_s=0, v0=100, t1_s=3600, v1=300) == 200
    assert interpolate_at(900, t0_s=0, v0=0, t1_s=3600, v1=100) == 25
    with pytest.raises(GridError):
        interpolate_at(0, t0_s=0, v0=1, t1_s=10, v1=2)


def test_s_to_iso_round_trips() -> None:
    assert s_to_iso(1786000000).endswith("Z")
