"""The weather actor's one clock: wall time, the time coordinator's steps,
or a value a test moves.

Every plant-time read in the actor (the scheduler tick, the slot a
gridpoint observation is read at, the stamp on a glitch, the "now" an
irradiance fetch is cut from) goes through ``Clock.now``; IO durations
and retry pauses stay on the wall. The actor keeps one clock and the
settings pick it; a test injects a ``ManualClock`` in code, so no env
file can put a deployed box on one.

Under the Timestep clock the actor has no time until the coordinator's
first ``sim.timestep`` arrives through gwbase's ``on_simulated_time``
hook, and ``now`` raises before then. The actor's main loop waits on the
clock, so a step is what wakes a tick.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from enum import StrEnum


class ClockSource(StrEnum):
    """The settings value that picks the clock."""

    Wall = "Wall"
    Timestep = "Timestep"


class Clock(ABC):
    @abstractmethod
    def now(self) -> int:
        """Plant time as unix seconds."""

    @abstractmethod
    def wait(self, seconds: float) -> None:
        """Return once plant time has advanced by ``seconds`` from now, or
        once ``interrupt`` is called."""

    def interrupt(self) -> None:
        """Release a pending ``wait`` early. The default has nothing pending
        that outlives the call."""


class WallClock(Clock):
    def now(self) -> int:
        return int(time.time())

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)


class SteppedClock(Clock):
    """A clock that moves only when told: ``step`` sets the time and wakes
    every pending ``wait``. Safe to step from another thread than the one
    waiting."""

    def __init__(self, start_s: int | None = None) -> None:
        self.time_s: int | None = start_s
        self.condition = threading.Condition()
        self.interrupted = False

    def now(self) -> int:
        with self.condition:
            if self.time_s is None:
                raise RuntimeError(
                    f"{type(self).__name__} has no time before its first step"
                )
            return self.time_s

    def step(self, time_s: int) -> None:
        """Move to ``time_s``. The caller owns the direction: gwbase
        refuses a step behind the one it holds before this is reached."""
        with self.condition:
            self.time_s = time_s
            self.condition.notify_all()

    def wait(self, seconds: float) -> None:
        with self.condition:
            target = None if self.time_s is None else self.time_s + seconds
            while not self.interrupted:
                if (
                    self.time_s is not None
                    and target is not None
                    and self.time_s >= target
                ):
                    return
                if self.time_s is not None and target is None:
                    return  # the first step arrived
                self.condition.wait()
            self.interrupted = False

    def interrupt(self) -> None:
        with self.condition:
            self.interrupted = True
            self.condition.notify_all()


class TimestepClock(SteppedClock):
    """Fed by the coordinator: the actor's ``on_simulated_time`` calls
    ``step`` with each ``sim.timestep`` gwbase delivers."""


class ManualClock(SteppedClock):
    """A stored value a test moves with ``advance``."""

    def __init__(self, start_s: int) -> None:
        super().__init__(start_s)

    def advance(self, seconds: int) -> None:
        self.step(self.now() + seconds)


def build_clock(source: ClockSource) -> Clock:
    """The clock the settings ask for."""
    if source == ClockSource.Wall:
        return WallClock()
    if source == ClockSource.Timestep:
        return TimestepClock()
    raise ValueError(f"Unknown clock source {source}")
