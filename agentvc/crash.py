"""Named crash points for crash-consistency tests (plan §16.2, §16.4).

Every multi-step write path calls `hit(NAME)` between its steps, with each name declared once
at import (`NAME = declare("ledger.after_commit")`) so tests can enumerate them all. A point
does nothing unless a test arms it:

- in-process, `with armed(NAME):` makes `hit(NAME)` raise `CrashPointHit`;
- in a child process, the environment variable `AGENTVC_CRASH_AT=<name>` makes `hit` end the
  process at once with `EXIT_CODE`, as a kill would (no cleanup, no `finally`).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

EXIT_CODE = 86
_ENV = "AGENTVC_CRASH_AT"
_declared: set[str] = set()
_armed: set[str] = set()


class CrashPointHit(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"crash point {name}")
        self.name = name


def declare(name: str) -> str:
    _declared.add(name)
    return name


def declared() -> frozenset[str]:
    return frozenset(_declared)


def _check(name: str) -> None:
    if name not in _declared:
        raise ValueError(f"crash point {name!r} was never declared")


def hit(name: str) -> None:
    _check(name)
    if name in _armed:
        raise CrashPointHit(name)
    if os.environ.get(_ENV) == name:
        os._exit(EXIT_CODE)


@contextmanager
def armed(name: str) -> Iterator[None]:
    _check(name)
    _armed.add(name)
    try:
        yield
    finally:
        _armed.discard(name)
