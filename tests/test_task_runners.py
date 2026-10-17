"""The Makefile and its Windows equivalent (make.ps1) must offer the same targets."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = {"fast", "test", "arch", "fuzz", "chaos"}


def makefile_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    return set(re.findall(r"^([a-z][a-z0-9-]*):", text, flags=re.MULTILINE))


def ps1_targets() -> set[str]:
    text = (ROOT / "make.ps1").read_text(encoding="utf-8")
    match = re.search(r"\[ValidateSet\(([^)]*)\)\]", text)
    assert match, "make.ps1 must declare its targets with [ValidateSet(...)]"
    return set(re.findall(r"'([a-z][a-z0-9-]*)'", match.group(1)))


def test_makefile_has_required_targets() -> None:
    assert makefile_targets() >= REQUIRED


def test_make_ps1_matches_makefile() -> None:
    assert ps1_targets() == makefile_targets()
