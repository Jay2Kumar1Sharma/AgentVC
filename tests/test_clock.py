"""Injectable clock, deadline and random source (plan §16.4)."""

from datetime import UTC, datetime

from agentvc.clock import Deadline, FakeClock, SeededRandom, SystemClock, SystemRandom


def test_fake_clock_moves_only_when_advanced() -> None:
    start = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    clock = FakeClock(start)
    assert clock.now() == start
    assert clock.now() == start
    m0 = clock.monotonic()
    clock.advance(2.5)
    assert (clock.now() - start).total_seconds() == 2.5
    assert clock.monotonic() - m0 == 2.5


def test_fake_clock_wall_time_can_jump_back_without_moving_monotonic() -> None:
    clock = FakeClock(datetime(2026, 10, 21, tzinfo=UTC))
    m0 = clock.monotonic()
    clock.set_wall(datetime(2020, 1, 1, tzinfo=UTC))
    assert clock.now().year == 2020
    assert clock.monotonic() == m0


def test_system_clock_is_utc_and_monotonic() -> None:
    clock = SystemClock()
    assert clock.now().tzinfo is UTC
    a = clock.monotonic()
    assert clock.monotonic() >= a


def test_deadline_counts_down_on_the_monotonic_clock() -> None:
    clock = FakeClock(datetime(2026, 10, 21, tzinfo=UTC))
    deadline = Deadline(clock, 5.0)
    assert deadline.remaining() == 5.0
    assert not deadline.expired()
    clock.advance(4.0)
    assert deadline.remaining() == 1.0
    clock.set_wall(datetime(1999, 1, 1, tzinfo=UTC))  # wall clock changes don't matter
    assert deadline.remaining() == 1.0
    clock.advance(1.5)
    assert deadline.remaining() == 0.0
    assert deadline.expired()


def test_seeded_random_repeats_and_system_random_varies() -> None:
    assert SeededRandom(7).token_hex(16) == SeededRandom(7).token_hex(16)
    assert len(SeededRandom(7).token_hex(16)) == 32
    system = SystemRandom()
    assert system.token_hex(16) != system.token_hex(16)


def test_system_clock_measures_short_durations_finely() -> None:
    # time.monotonic() ticks every 15.6 ms on Windows, which made every hook timing read 0 or
    # 16 ms (found in the U-20 run). Durations need a sub-millisecond clock.
    clock = SystemClock()
    steps: list[float] = []
    last = clock.monotonic()
    while len(steps) < 50:
        now = clock.monotonic()
        if now != last:
            steps.append(now - last)
            last = now
    assert min(steps) < 0.001
