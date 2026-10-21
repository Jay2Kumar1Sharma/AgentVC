"""`.agentvc/config.toml`, minimal until session 4: `mode` and `record_prompts`.

A missing file means the defaults. A broken one is reported and the defaults apply: a bad
config never switches recording off (G8).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from agentvc.io import IO
from agentvc.wiring import MODES

DEFAULT_MODE = "protect"


@dataclass(frozen=True)
class Config:
    mode: str = DEFAULT_MODE
    record_prompts: bool = False
    problems: tuple[str, ...] = ()


def load_config(io: IO, path: Path) -> Config:
    try:
        text = io.read_text(path)
    except FileNotFoundError:
        return Config()
    except (OSError, UnicodeDecodeError) as exc:
        return Config(problems=(f"{path.name}: can't read it ({exc}); using defaults",))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return Config(problems=(f"{path.name}: not valid TOML ({exc}); using defaults",))

    problems = []
    mode = data.get("mode", DEFAULT_MODE)
    if mode not in MODES:
        problems.append(f"{path.name}: mode {mode!r} unknown (expected {', '.join(MODES)})")
        mode = DEFAULT_MODE
    record_prompts = data.get("record_prompts", False)
    if not isinstance(record_prompts, bool):
        problems.append(f"{path.name}: record_prompts must be true or false")
        record_prompts = False
    return Config(mode=mode, record_prompts=record_prompts, problems=tuple(problems))
