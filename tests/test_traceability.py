"""tests/traceability.yml maps guarantees, hazards, FMEA rows and attacks to tests (plan §16.1).

A row may list "TODO" while its tests don't exist yet; those rows are reported as gaps
(a TraceabilityGap warning in the pytest summary). Any other entry must name a test that
exists, or this module fails.
"""

import ast
import re
import warnings
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
TRACE = ROOT / "tests" / "traceability.yml"
TESTING_DOCS = ROOT / "docs" / "testing"
SECTIONS = ("guarantees", "hazards", "fmea", "attacks")
TODO = "TODO"


class TraceabilityGap(UserWarning):
    """A traceability row with no test yet."""


def load_rows() -> dict[str, list[dict[str, Any]]]:
    data = yaml.safe_load(TRACE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "traceability.yml must be a mapping"
    return {section: data.get(section) or [] for section in SECTIONS}


def missing_reference(ref: str, root: Path = ROOT) -> str | None:
    """Return why ``ref`` (``path[::Class]::test[param]``) names no existing test, or None."""
    path_part, _, qualname = ref.partition("::")
    file = root / path_part
    if not file.is_file():
        return f"file not found: {path_part}"
    if not qualname:
        return None
    body: list[ast.stmt] = ast.parse(file.read_text(encoding="utf-8")).body
    for name in re.sub(r"\[.*\]$", "", qualname).split("::"):
        found = next(
            (
                node
                for node in body
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and node.name == name
            ),
            None,
        )
        if found is None:
            return f"{name} not defined in {path_part}"
        body = found.body
    return None


def doc_ids(filename: str, pattern: str) -> set[str]:
    text = (TESTING_DOCS / filename).read_text(encoding="utf-8")
    return set(re.findall(rf"^\| ({pattern}) \|", text, flags=re.MULTILINE))


# The resolver, in both directions: it accepts real tests and rejects missing ones.


def test_resolver_accepts_existing_test() -> None:
    assert missing_reference("tests/test_version.py::test_module_prints_version") is None
    assert missing_reference("tests/test_version.py") is None
    assert missing_reference("tests/test_workflows.py::test_workflow_parses[fast.yml]") is None


def test_resolver_rejects_missing_file_or_test() -> None:
    assert missing_reference("tests/test_nope.py::test_x") == "file not found: tests/test_nope.py"
    assert missing_reference("tests/test_version.py::test_nope") == (
        "test_nope not defined in tests/test_version.py"
    )


# The traceability file itself.


def test_rows_are_well_formed() -> None:
    seen: set[str] = set()
    for section, rows in load_rows().items():
        assert isinstance(rows, list), f"{section} must be a list"
        for row in rows:
            row_id = row.get("id")
            assert isinstance(row_id, str) and row_id, f"row without id in {section}: {row}"
            assert row_id not in seen, f"duplicate id {row_id}"
            seen.add(row_id)
            tests = row.get("tests")
            assert isinstance(tests, list) and tests, f"{row_id}: tests must be a non-empty list"
            assert all(isinstance(t, str) for t in tests), f"{row_id}: tests must be strings"


def test_every_listed_test_exists() -> None:
    problems = [
        f"{row['id']}: {ref}: {reason}"
        for rows in load_rows().values()
        for row in rows
        for ref in row["tests"]
        if ref != TODO and (reason := missing_reference(ref)) is not None
    ]
    assert not problems, "traceability rows name tests that don't exist:\n" + "\n".join(problems)


def test_all_guarantees_are_traced() -> None:
    ids = {row["id"] for row in load_rows()["guarantees"]}
    assert ids == {f"G{n}" for n in range(1, 9)}


def test_fmea_and_hazard_docs_match_traceability() -> None:
    rows = load_rows()
    assert doc_ids("fmea.md", r"FM-[A-Z]+-\d+") == {row["id"] for row in rows["fmea"]}
    assert doc_ids("hazards.md", r"HZ-G\d-\d+") == {row["id"] for row in rows["hazards"]}


def test_report_gaps() -> None:
    counts = []
    for section, rows in load_rows().items():
        gaps = sum(TODO in row["tests"] for row in rows)
        if gaps:
            counts.append(f"{section} {gaps}/{len(rows)}")
    if counts:
        warnings.warn(
            "traceability rows with no tests yet (TODO): " + ", ".join(counts),
            TraceabilityGap,
            stacklevel=1,
        )
