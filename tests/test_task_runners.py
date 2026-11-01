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


def makefile_recipe(target: str) -> str:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(rf"^{target}:[^\n]*\n((?:\t[^\n]*\n)+)", text, flags=re.MULTILINE)
    assert match, f"Makefile has no recipe for {target}"
    return match.group(1)


def ps1_branch(target: str) -> str:
    text = (ROOT / "make.ps1").read_text(encoding="utf-8")
    match = re.search(rf"^    '{target}' \{{(.*?)^    \}}", text, flags=re.MULTILINE | re.DOTALL)
    assert match, f"make.ps1 has no multi-line branch for {target}"
    return match.group(1)


def assert_full_suite(recipe: str) -> None:
    flat = recipe.replace("'", "").replace(",", "")
    for step in ("ruff check", "ruff format --check", "mypy", "pytest"):
        assert step in flat, f"missing {step}"
    pytest_lines = [line for line in flat.splitlines() if "pytest" in line]
    pytest_args = [line.split("pytest", 1)[1] for line in pytest_lines]
    assert pytest_args and all(" -m " not in args for args in pytest_args), "pytest is filtered"
    assert "not implemented" not in recipe.lower()


def test_make_test_runs_the_full_suite() -> None:
    assert_full_suite(makefile_recipe("test"))


def test_make_ps1_test_runs_the_full_suite() -> None:
    assert_full_suite(ps1_branch("test"))


def test_make_arch_runs_structure_and_render_checks() -> None:
    for recipe in (makefile_recipe("arch"), ps1_branch("arch")):
        flat = recipe.replace("'", "").replace(",", "")
        assert "arch_check.py" in flat
        assert "test_arch_render.py" in flat
        assert "not implemented" not in recipe.lower()


def test_make_test_includes_the_architecture_check() -> None:
    for recipe in (makefile_recipe("test"), ps1_branch("test")):
        assert "arch_check.py" in recipe
