"""Debug log and timing trace (plan §6.2): `AGENTVC_DEBUG=1` → `.agentvc/debug.log`.

One JSON line per record: what happened, the decision, and how long each phase took. Callers
pass short facts, never payload content; values are scrubbed and capped anyway. The log rotates
at `max_bytes`, keeping one old file. Logging can never break the caller: errors are swallowed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from agentvc.clock import Clock
from agentvc.io import IO
from agentvc.scrub import scrub_text

MAX_VALUE_CHARS = 200
Scalar = str | int | float | bool | None


def enabled_from_env(environ: Mapping[str, str]) -> bool:
    return environ.get("AGENTVC_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


class DebugLog:
    def __init__(
        self,
        io: IO,
        path: Path,
        *,
        enabled: bool,
        clock: Clock,
        max_bytes: int = 1024 * 1024,
    ) -> None:
        self._io = io
        self._path = path
        self.enabled = enabled
        self._clock = clock
        self._max_bytes = max_bytes

    def record(self, what: str, **fields: Scalar) -> None:
        if not self.enabled:
            return
        try:
            entry: dict[str, Scalar] = {"at": self._clock.now().isoformat(), "what": what}
            for key, value in fields.items():
                if isinstance(value, str):
                    value = scrub_text(value, home=None, user=None)[:MAX_VALUE_CHARS]
                entry[key] = value
            if self._io.size(self._path) >= self._max_bytes:
                self._io.replace(self._path, self._path.with_name(self._path.name + ".1"))
            self._io.append_line(self._path, json.dumps(entry, ensure_ascii=False), lock_timeout=1)
        except Exception:
            pass  # a debug log must never break a hook
