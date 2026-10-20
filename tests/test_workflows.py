"""GitHub workflow files parse, and the fast lane covers the required matrix (plan §6.2)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} is not a YAML mapping"
    return data


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_workflow_parses(path: Path) -> None:
    data = load(path)
    # PyYAML reads the bare key `on` as boolean True.
    assert "on" in data or True in data, f"{path.name} has no trigger"
    assert data.get("jobs"), f"{path.name} has no jobs"


def test_fast_lane_matrix() -> None:
    job = load(WORKFLOWS / "fast.yml")["jobs"]["fast"]
    matrix = job["strategy"]["matrix"]
    assert set(matrix["os"]) == {"ubuntu-latest", "macos-latest", "windows-latest"}
    assert {"3.11", "3.13"} <= {str(v) for v in matrix["python-version"]}
    commands = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert "make fast" in commands
    assert "make.ps1 fast" in commands
