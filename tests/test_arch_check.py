"""scripts/arch_check.py: structure checks on architecture/graph.json (plan §5.2, §5.3).

Two directions: the real graph passes, and each kind of breakage is reported.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import arch_check

ROOT = Path(__file__).resolve().parent.parent


def minimal_graph() -> dict[str, Any]:
    return {
        "meta": {"updated": "2026-10-18", "version": "0.1.0.dev0", "changelog": ["x"]},
        "layers": [{"id": "engine", "label": "Engine"}],
        "nodes": [
            {
                "id": "a",
                "label": "A",
                "sub": "short",
                "layer": "engine",
                "path": None,
                "status": "planned",
                "desc": "d",
                "adr": [],
            },
            {
                "id": "b",
                "label": "B",
                "sub": "short",
                "layer": "engine",
                "path": None,
                "status": "planned",
                "desc": "d",
                "adr": [],
            },
        ],
        "edges": [{"from": "a", "to": "b", "kind": "calls"}],
        "views": {
            "dataflow": [{"from": "a", "to": "b", "label": "step"}],
            "lifecycle": {
                "states": ["S1", "S2"],
                "transitions": [{"from": "S1", "to": "S2", "on": "x"}],
            },
        },
        "roadmap": [
            {
                "id": "r1",
                "title": "T",
                "status": "planned",
                "goal": "g",
                "acceptance": ["a"],
                "first_contribution": "f",
                "links": ["docs/master-plan.md"],
            },
        ],
    }


def test_real_graph_passes() -> None:
    graph = json.loads((ROOT / "architecture" / "graph.json").read_text(encoding="utf-8"))
    assert arch_check.check(graph, ROOT) == []


def test_minimal_graph_passes() -> None:
    assert arch_check.check(minimal_graph(), ROOT) == []


def broken(change: str) -> dict[str, Any]:
    g = copy.deepcopy(minimal_graph())
    if change == "duplicate node id":
        g["nodes"][1]["id"] = "a"
    elif change == "dangling edge":
        g["edges"].append({"from": "a", "to": "nowhere", "kind": "calls"})
    elif change == "long sub":
        g["nodes"][0]["sub"] = "x" * 43
    elif change == "unknown layer":
        g["nodes"][0]["layer"] = "nope"
    elif change == "bad status":
        g["nodes"][0]["status"] = "done"
    elif change == "shipped without path":
        g["nodes"][0]["status"] = "shipped"
        g["nodes"][0]["path"] = "agentvc/does_not_exist.py"
    elif change == "roadmap field missing":
        del g["roadmap"][0]["acceptance"]
    elif change == "conditional without trigger":
        g["roadmap"][0]["status"] = "conditional"
    elif change == "shipped node on roadmap":
        g["nodes"][0]["status"] = "shipped"
        g["nodes"][0]["path"] = "agentvc/__init__.py"
        g["roadmap"][0]["id"] = "a"
    elif change == "dataflow references unknown node":
        g["views"]["dataflow"].append({"from": "a", "to": "ghost", "label": "x"})
    elif change == "lifecycle references unknown state":
        g["views"]["lifecycle"]["transitions"].append({"from": "S1", "to": "S9", "on": "x"})
    elif change == "meta incomplete":
        del g["meta"]["changelog"]
    return g


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("duplicate node id", "duplicate id"),
        ("dangling edge", "unknown node"),
        ("long sub", "longer than 42"),
        ("unknown layer", "unknown layer"),
        ("bad status", "status"),
        ("shipped without path", "path"),
        ("roadmap field missing", "acceptance"),
        ("conditional without trigger", "trigger"),
        ("shipped node on roadmap", "shipped"),
        ("dataflow references unknown node", "unknown node"),
        ("lifecycle references unknown state", "unknown state"),
        ("meta incomplete", "meta"),
    ],
)
def test_breakage_is_reported(change: str, message: str) -> None:
    problems = arch_check.check(broken(change), ROOT)
    assert any(message in p for p in problems), problems


def test_main_exit_codes(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps(minimal_graph()), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(broken("dangling edge")), encoding="utf-8")
    assert arch_check.main(["--graph", str(good)]) == 0
    assert arch_check.main(["--graph", str(bad)]) == 1
