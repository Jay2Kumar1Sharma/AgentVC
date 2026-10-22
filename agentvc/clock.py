"""Injectable time and randomness (plan §16.4).

Engine code never calls `time.time()`, `datetime.now()` or `random` directly: it takes a `Clock`
and a `RandomSource`, so tests and simulations can replay any run exactly.
"""

from __future__ import annotations

import random
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Wall-clock time, UTC. For display and records; never for ordering or timeouts."""
        ...

    def monotonic(self) -> float:
        """Seconds on a clock that never goes back. For deadlines and durations."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        # perf_counter, not time.monotonic: both never go back, but on Windows time.monotonic
        # advances in 15.6 ms ticks, too coarse to time a hook.
        return time.perf_counter()


class FakeClock:
    """A clock that moves only when told to."""

    def __init__(self, start: datetime) -> None:
        self._wall = start
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._wall

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._wall += timedelta(seconds=seconds)
        self._mono += seconds

    def set_wall(self, when: datetime) -> None:
        """Jump the wall clock (as a user or NTP might); monotonic time is unaffected."""
        self._wall = when


class Deadline:
    """A point in monotonic time after which work must stop."""

    def __init__(self, clock: Clock, seconds: float) -> None:
        self._clock = clock
        self._end = clock.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self._end - self._clock.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0.0


class RandomSource(Protocol):
    def token_hex(self, nbytes: int) -> str: ...


class SystemRandom:
    """Cryptographic randomness, for tokens."""

    def token_hex(self, nbytes: int) -> str:
        return secrets.token_hex(nbytes)


class SeededRandom:
    """Repeatable randomness for tests and simulations. Never for real tokens."""

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def token_hex(self, nbytes: int) -> str:
        return self._random.randbytes(nbytes).hex()
