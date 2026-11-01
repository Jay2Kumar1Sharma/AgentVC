"""scripts/spikes/ignored_files.py (decision D4): aggregate sizes of git-ignored files."""

import json
import subprocess
from pathlib import Path

import pytest

from scripts.spikes import ignored_files

CANARY = "CANARYNAME7731"


def make_repo(root: Path) -> Path:
    repo = root / f"proj-{CANARY}"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(
        "node_modules/\n.env\n*.log\nbig.bin\nnotes/\n", encoding="utf-8"
    )
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (repo / "node_modules" / f"pkg-{CANARY}").mkdir(parents=True)
    (repo / "node_modules" / f"pkg-{CANARY}" / "index.js").write_bytes(b"x" * 5000)
    (repo / ".env").write_bytes(b"SECRET=1\n")
    (repo / f"run-{CANARY}.log").write_bytes(b"l" * 300)
    (repo / "big.bin").write_bytes(b"b" * (2 * 1024 * 1024))
    (repo / "notes").mkdir()
    (repo / "notes" / "a.md").write_bytes(b"n" * 100)
    return repo


@pytest.mark.slow
def test_scan_aggregates_by_category(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    result = ignored_files.scan_repo(repo)
    assert result["heavy"] == {"files": 1, "bytes": 5000}
    assert result["dotenv"] == {"files": 1, "bytes": 9}
    assert result["other"]["files"] == 3  # .log, big.bin, notes/a.md
    assert result["other"]["bytes"] == 300 + 2 * 1024 * 1024 + 100
    assert result["other_by_size"]["le_64k"] == {"files": 2, "bytes": 400}
    assert result["other_by_size"]["gt_1m_le_10m"] == {"files": 1, "bytes": 2 * 1024 * 1024}
    # The proposed default (D4): dotenv files plus "other" files up to 1 MB each.
    assert result["policy_default"] == {"files": 3, "bytes": 409}


@pytest.mark.slow
def test_output_holds_no_names_or_paths(tmp_path: Path) -> None:
    make_repo(tmp_path / "search")
    out = tmp_path / "out"
    assert ignored_files.main(["--search", str(tmp_path / "search"), "--out", str(out)]) == 0
    text = (out / "ignored_files.json").read_text(encoding="utf-8")
    assert CANARY not in text
    assert str(tmp_path) not in text
    data = json.loads(text)
    assert [r["repo"] for r in data["repos"]] == ["repo-1"]
