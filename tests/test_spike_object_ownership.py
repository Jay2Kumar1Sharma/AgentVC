"""Spike D3 (plan §7.1): snapshots that borrow objects through git alternates break when the user
prunes their repository; snapshots that own every object survive."""

import json
from pathlib import Path

import pytest

from scripts.spikes import object_ownership


@pytest.mark.slow
def test_borrowed_objects_are_lost_and_owned_objects_survive(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert object_ownership.main(["--out", str(out), "--workdir", str(tmp_path / "w")]) == 0
    result = json.loads((out / "object_ownership.json").read_text(encoding="utf-8"))
    scenarios = {s["name"]: s for s in result["scenarios"]}

    borrowed = scenarios["alternates"]
    assert borrowed["restore_ok_before_prune"] is True
    assert borrowed["user_repo_lost_objects"] is True
    assert borrowed["restore_ok_after_prune"] is False
    assert borrowed["missing_objects_after_prune"] > 0

    for name in ("own_every_object", "alternates_then_repack"):
        owned = scenarios[name]
        assert owned["restore_ok_before_prune"] is True
        assert owned["user_repo_lost_objects"] is True
        assert owned["restore_ok_after_prune"] is True, name
        assert owned["missing_objects_after_prune"] == 0, name
