"""Run tests against deliberately broken builds of AgentVC (plan §15.2 rule 2, "seen to fail").

Each mutation in eval/broken_builds/<component>.json is one exact text replacement in one module
of `agentvc/`, with the test files expected to catch it. For each, the package is copied to a
temporary folder, the replacement applied (it must match exactly once), and the tests run with
that copy imported in place of the real one. A mutation no test catches is reported as
SURVIVED, and the script exits 1.

    python scripts/broken_build.py eval/broken_builds/snapshot.json [--only ID ...] [--markdown]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env(copy: Path) -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(copy), "PYTHONDONTWRITEBYTECODE": "1"}


def run_one(mutation: dict[str, object]) -> tuple[str, str]:
    """(verdict, detail) for one mutation: CAUGHT, SURVIVED or ERROR."""
    with tempfile.TemporaryDirectory(prefix="agentvc-broken-") as tmp:
        copy = Path(tmp)
        shutil.copytree(
            ROOT / "agentvc", copy / "agentvc", ignore=shutil.ignore_patterns("__pycache__")
        )
        target = copy / str(mutation["file"])
        source = target.read_bytes().decode("utf-8")
        find, replace = str(mutation["find"]), str(mutation["replace"])
        if source.count(find) != 1:
            return "ERROR", f"the text to replace occurs {source.count(find)} times, not once"
        target.write_bytes(source.replace(find, replace).encode("utf-8"))
        origin = subprocess.run(
            [sys.executable, "-P", "-c", "import agentvc; print(agentvc.__file__)"],
            env=_env(copy),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
        if not Path(origin).resolve().is_relative_to(copy.resolve()):
            return "ERROR", f"the broken copy wasn't imported (got {origin})"
        # pyproject's `pythonpath = ["."]` would put the real package first; the sentinel test
        # fails the run unless the broken copy is what the tests import.
        sentinel = copy / "test_broken_copy_is_imported.py"
        here = str(copy.resolve())
        sentinel.write_text(
            "import agentvc, pathlib\n\n\ndef test_broken_copy_is_imported():\n"
            f"    assert pathlib.Path(agentvc.__file__).resolve().is_relative_to({here!r})\n",
            encoding="utf-8",
        )
        tests = [str(ROOT / str(t)) for t in mutation["tests"]]  # type: ignore[attr-defined]
        done = subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "pytest",
                str(sentinel),
                *tests,
                "-q",
                "-x",
                "-rf",
                "-p",
                "no:cacheprovider",
                "--rootdir",
                str(ROOT),
                "-o",
                f"pythonpath={copy} {ROOT}",
            ],
            env=_env(copy),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", done.stdout, re.MULTILINE)
        if any("test_broken_copy_is_imported" in f for f in failed):
            return "ERROR", "the tests imported the real package, not the broken copy"
        if done.returncode == 0:
            return "SURVIVED", "every test passed"
        if failed:
            return "CAUGHT", failed[0].replace(str(ROOT) + os.sep, "").replace("\\", "/")
        return "ERROR", (done.stdout + done.stderr).strip().splitlines()[-1][:200]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("catalog", nargs="+", type=Path)
    parser.add_argument("--only", nargs="*", default=None, help="mutation ids to run")
    parser.add_argument(
        "--markdown", action="store_true", help="print a table for docs/adversarial"
    )
    args = parser.parse_args()
    results = []
    for catalog in args.catalog:
        for mutation in json.loads(catalog.read_text(encoding="utf-8")):
            if args.only is not None and mutation["id"] not in args.only:
                continue
            verdict, detail = run_one(mutation)
            results.append((mutation, verdict, detail))
            print(f"{verdict:<8} {mutation['id']}: {detail}", flush=True)
    if args.markdown:
        print("\n| Broken build | Verdict | First test that failed |\n|---|---|---|")
        for mutation, verdict, detail in results:
            print(f"| {mutation['id']}: {mutation['why']} | {verdict} | `{detail}` |")
    return 0 if all(v == "CAUGHT" for _, v, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
