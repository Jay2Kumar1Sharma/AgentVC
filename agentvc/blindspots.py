"""The blind-spot log, `.agentvc/blindspots.jsonl` (plan §6.1): what AgentVC couldn't record.

One JSON line per payload it couldn't parse or event it couldn't place, holding the reason and
the payload's shape, never its content. Counted and reported at the next prompt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentvc.clock import Clock
from agentvc.io import IO


def record(io: IO, path: Path, entry: dict[str, Any], clock: Clock) -> None:
    line = json.dumps({"at": clock.now().isoformat(), **entry}, ensure_ascii=False)
    io.append_line(path, line, lock_timeout=2)


def count(io: IO, path: Path) -> int:
    try:
        return io.read_bytes(path).count(b"\n")
    except FileNotFoundError:
        return 0
