"""The actor's clock: wall, stepped by the coordinator, or moved by hand."""

from __future__ import annotations

import threading
import time

import pytest

from gwwf.clock import (
    ClockSource,
    ManualClock,
    TimestepClock,
    WallClock,
    build_clock,
)


def test_wall_clock_reads_the_wall() -> None:
    clock = WallClock()
    assert abs(clock.now() - time.time()) < 2


def test_timestep_clock_has_no_time_before_the_first_step() -> None:
    clock = TimestepClock()
    with pytest.raises(RuntimeError, match="no time before its first step"):
        clock.now()
    clock.step(1_786_000_000)
    assert clock.now() == 1_786_000_000


def test_wait_returns_once_a_step_reaches_the_target() -> None:
    clock = ManualClock(start_s=100)
    done = threading.Event()

    def waiter() -> None:
        clock.wait(10)
        done.set()

    threading.Thread(target=waiter, daemon=True).start()
    clock.advance(5)
    assert not done.wait(0.05)
    clock.advance(5)
    assert done.wait(1)


def test_wait_before_the_first_step_returns_on_that_step() -> None:
    clock = TimestepClock()
    done = threading.Event()

    def waiter() -> None:
        clock.wait(1)
        done.set()

    threading.Thread(target=waiter, daemon=True).start()
    assert not done.wait(0.05)
    clock.step(7)
    assert done.wait(1)


def test_interrupt_releases_a_pending_wait() -> None:
    clock = TimestepClock()
    done = threading.Event()

    def waiter() -> None:
        clock.wait(1)
        done.set()

    threading.Thread(target=waiter, daemon=True).start()
    clock.interrupt()
    assert done.wait(1)


def test_build_clock_follows_the_setting() -> None:
    assert isinstance(build_clock(ClockSource.Wall), WallClock)
    assert isinstance(build_clock(ClockSource.Timestep), TimestepClock)
