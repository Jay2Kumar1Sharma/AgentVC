"""Structure checks for architecture/graph.json (plan §5.2, §5.3).

Checks: meta fields; unique ids; every node's layer exists; node status is known; shipped nodes
point at an existing path; `sub` is at most 42 characters; edges and the Data flow view only name
existing nodes; the Lifecycle view only names its own states; roadmap cards have every field,
a known status, a trigger when conditional, and never describe a shipped node.

Later stages (plan §5.3) add: every module has a node, declared edges match real imports, docs
mention no missing paths, README diagrams match arch_export.py.

Usage:
    python scripts/arch_check.py            # check architecture/graph.json
    python scripts/arch_check.py --graph other.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAPH = REPO_ROOT / "architecture" / "graph.json"
SUB_MAX = 42
NODE_STATUSES = {"planned", "in progress", "shipped"}
ROADMAP_STATUSES = {"planned", "in progress", "idea", "conditional"}
ROADMAP_FIELDS = ("id", "title", "status", "goal", "acceptance", "first_contribution", "links")
META_FIELDS = ("updated", "version", "changelog")


def check(graph: dict[str, Any], root: Path = REPO_ROOT) -> list[str]:
    """Return a list of problems; empty when the graph is well formed."""
    problems: list[str] = []
    meta = graph.get("meta") or {}
    problems += [f"meta is missing {f}" for f in META_FIELDS if f not in meta]

    layers = {layer["id"] for layer in graph.get("layers", [])}
    nodes = graph.get("nodes", [])
    node_ids = {n.get("id") for n in nodes}
    roadmap = graph.get("roadmap", [])

    counts = Counter([*(n.get("id") for n in nodes), *(c.get("id") for c in roadmap)])
    counts.update(layer["id"] for layer in graph.get("layers", []))
    problems += [f"duplicate id {i!r}" for i, c in counts.items() if c > 1]

    for n in nodes:
        nid = n.get("id")
        if n.get("layer") not in layers:
            problems.append(f"node {nid!r}: unknown layer {n.get('layer')!r}")
        if n.get("status") not in NODE_STATUSES:
            problems.append(
                f"node {nid!r}: status {n.get('status')!r} not in {sorted(NODE_STATUSES)}"
            )
        if len(n.get("sub", "")) > SUB_MAX:
            problems.append(f"node {nid!r}: sub is longer than {SUB_MAX} characters")
        if n.get("status") == "shipped" and not (n.get("path") and (root / n["path"]).exists()):
            problems.append(f"node {nid!r}: shipped but its path {n.get('path')!r} doesn't exist")

    for e in graph.get("edges", []):
        for end in ("from", "to"):
            if e.get(end) not in node_ids:
                problems.append(f"edge {e.get('from')}->{e.get('to')}: unknown node {e.get(end)!r}")

    views = graph.get("views", {})
    for step in views.get("dataflow", []):
        for end in ("from", "to"):
            if step.get(end) not in node_ids:
                problems.append(
                    f"dataflow step {step.get('label')!r}: unknown node {step.get(end)!r}"
                )
    lifecycle = views.get("lifecycle", {})
    states = set(lifecycle.get("states", []))
    for t in lifecycle.get("transitions", []):
        for end in ("from", "to"):
            if t.get(end) not in states:
                problems.append(f"lifecycle transition: unknown state {t.get(end)!r}")

    shipped = {n.get("id") for n in nodes if n.get("status") == "shipped"}
    for card in roadmap:
        cid = card.get("id")
        problems += [f"roadmap {cid!r}: missing {f}" for f in ROADMAP_FIELDS if not card.get(f)]
        if card.get("status") not in ROADMAP_STATUSES:
            problems.append(f"roadmap {cid!r}: status {card.get('status')!r} unknown")
        if card.get("status") == "conditional" and not card.get("trigger"):
            problems.append(f"roadmap {cid!r}: conditional card needs a trigger")
        if cid in shipped:
            problems.append(f"roadmap {cid!r}: describes a shipped node")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--graph", type=Path, default=GRAPH)
    args = parser.parse_args(argv)
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    problems = check(graph)
    for problem in problems:
        print(f"arch_check: {problem}", file=sys.stderr)
    if not problems:
        print(f"arch_check: {args.graph.name} OK ({len(graph.get('nodes', []))} nodes)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
