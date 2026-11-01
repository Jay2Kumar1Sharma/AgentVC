"""Named crash points (plan §16.4): no-ops until a test arms one."""

import subprocess
import sys

import pytest

from agentvc import crash

POINT = crash.declare("test.example_point")


def test_unarmed_point_does_nothing() -> None:
    crash.hit(POINT)


def test_armed_point_raises_and_disarms_after_the_block() -> None:
    with crash.armed(POINT), pytest.raises(crash.CrashPointHit) as info:
        crash.hit(POINT)
    assert info.value.name == POINT
    crash.hit(POINT)  # disarmed again


def test_only_the_armed_point_fires() -> None:
    other = crash.declare("test.other_point")
    with crash.armed(POINT):
        crash.hit(other)


def test_undeclared_names_are_rejected() -> None:
    with pytest.raises(ValueError):
        crash.hit("test.never_declared")
    with pytest.raises(ValueError), crash.armed("test.never_declared"):
        pass


def test_declared_points_can_be_enumerated() -> None:
    assert POINT in crash.declared()


def test_environment_variable_kills_a_child_process_at_the_point() -> None:
    code = (
        "from agentvc import crash\n"
        "p = crash.declare('test.child_point')\n"
        "print('before', flush=True)\n"
        "crash.hit(p)\n"
        "print('after', flush=True)\n"
    )
    env = {"AGENTVC_CRASH_AT": "test.child_point", "SYSTEMROOT": "C:\\Windows"}
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=30
    )
    assert done.returncode == crash.EXIT_CODE
    assert done.stdout.strip() == "before"
