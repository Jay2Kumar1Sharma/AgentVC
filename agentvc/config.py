"""`.agentvc/config.toml`: mode, prompts, and snapshot rules (plan §14.1, ADR-0006, ADR-0010).

A missing file means the defaults. A broken file, or a bad setting, is reported and the default
applies for that setting: a bad config never switches recording off (G8).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentvc.io import IO
from agentvc.wiring import MODES

DEFAULT_MODE = "protect"
FORMAT_VERSION = 1

# ADR-0006: ignored folders that are never entered (listed as unprotected instead).
HEAVY_FOLDERS = (
    "node_modules", ".venv", "venv", "env", ".env.d", "target", "dist", "build", "out",
    ".next", ".nuxt", ".svelte-kit", ".gradle", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".cache", ".parcel-cache", ".turbo",
    "coverage", "htmlcov", ".terraform", "bin", "obj", ".idea", ".vs", ".hypothesis",
    "site-packages", ".eggs", "bower_components", "Pods", ".dart_tool", "DerivedData",
)  # fmt: skip


@dataclass(frozen=True)
class SnapshotConfig:
    time_budget_ms: int = 2000  # a full snapshot inside a hook (ADR-0010)
    max_file_mb: int = 100  # any single file (ADR-0010)
    ignored_file_max_kb: int = 1024  # each ignored file (ADR-0006)
    ignored_total_mb: int = 50  # ignored files per snapshot (ADR-0006)
    heavy_folders: tuple[str, ...] = HEAVY_FOLDERS
    always_include: tuple[str, ...] = ()
    never_include: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    mode: str = DEFAULT_MODE
    record_prompts: bool = False
    snapshot: SnapshotConfig = field(default_factory=SnapshotConfig)
    problems: tuple[str, ...] = ()


_TOP = {"version", "mode", "record_prompts", "snapshot"}
_INTS = ("time_budget_ms", "max_file_mb", "ignored_file_max_kb", "ignored_total_mb")
_LISTS = ("heavy_folders", "always_include", "never_include")


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

    problems: list[str] = []
    version = data.get("version", FORMAT_VERSION)
    newer = isinstance(version, int) and not isinstance(version, bool) and version > FORMAT_VERSION
    if newer:
        problems.append(
            f"{path.name}: written for a newer AgentVC (version {version}); "
            "settings this AgentVC doesn't know are ignored"
        )
    elif version != FORMAT_VERSION:
        problems.append(f"{path.name}: version must be {FORMAT_VERSION}")
    if not newer:
        problems += [f"{path.name}: unknown setting {k!r}" for k in sorted(set(data) - _TOP)]

    mode = data.get("mode", DEFAULT_MODE)
    if mode not in MODES:
        problems.append(f"{path.name}: mode {mode!r} unknown (expected {', '.join(MODES)})")
        mode = DEFAULT_MODE
    record_prompts = data.get("record_prompts", False)
    if not isinstance(record_prompts, bool):
        problems.append(f"{path.name}: record_prompts must be true or false")
        record_prompts = False
    snapshot = _snapshot(data.get("snapshot", {}), path.name, problems, newer)
    return Config(
        mode=mode, record_prompts=record_prompts, snapshot=snapshot, problems=tuple(problems)
    )


def _snapshot(table: Any, name: str, problems: list[str], newer: bool) -> SnapshotConfig:
    if not isinstance(table, dict):
        problems.append(f"{name}: [snapshot] must be a table")
        return SnapshotConfig()
    values: dict[str, Any] = {}
    for key, value in table.items():
        where = f"{name}: snapshot.{key}"
        if key in _INTS:
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                values[key] = value
            else:
                problems.append(f"{where} must be a positive whole number")
        elif key in _LISTS:
            if isinstance(value, list) and all(isinstance(v, str) and v for v in value):
                values[key] = tuple(value)
            else:
                problems.append(f"{where} must be a list of non-empty strings")
        elif not newer:
            problems.append(f"{name}: unknown setting 'snapshot.{key}'")
    return SnapshotConfig(**values)
