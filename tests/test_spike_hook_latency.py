"""Smoke test for scripts/spikes/hook_latency.py (spike S1/D1) with tiny sizes."""

import json
from pathlib import Path

import pytest

from scripts.spikes import hook_latency


@pytest.mark.slow
def test_spike_writes_every_measurement(tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = hook_latency.main(
        ["--sizes", "30", "--runs", "3", "--out", str(out), "--workdir", str(tmp_path / "work")]
    )
    assert code == 0
    [result_file] = out.glob("hook_latency-*.json")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert {"os", "python", "git"} <= set(result["environment"])
    names = {m["name"] for m in result["measurements"]}
    assert {"python_noop", "hook_minimal", "hook_minimal_isolated", "snapshot_first"} <= names
    assert {"snapshot_unchanged", "snapshot_one_file_changed"} <= names
    for m in result["measurements"]:
        assert len(m["samples_ms"]) == m["runs"] > 0
        assert 0 < m["p50_ms"] <= m["p95_ms"] <= m["max_ms"]
    snapshot = [m for m in result["measurements"] if m["name"] == "snapshot_one_file_changed"]
    assert snapshot[0]["files"] == 30
