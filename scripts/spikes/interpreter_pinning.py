"""Spike D6 (plan §7.1): which Python does a hook command run inside a project that has its own
virtual environment?

Builds a throwaway project with its own venv (made from a *different* interpreter, the
"foreign" Python), activates it the way a terminal would (PATH, VIRTUAL_ENV), sets PYTHONPATH to
the project, and plants a fake `agentvc` package in the project folder, as hostile or just
unlucky repo content might. Then runs `-m agentvc` four ways:

- bare_python              `python -m agentvc`, resolved through the activated PATH
- pinned                   AgentVC's own interpreter by absolute path
- pinned_isolated          the same with `-I` (ignore cwd, PYTHONPATH and user site)
- pinned_isolated_no_site  the same with `-I -S` (also skip site-packages)

Outcome per case: "real" (AgentVC's version line), "impostor" (the planted package ran), or
"not_found" (no agentvc module).

Usage:
    python scripts/spikes/interpreter_pinning.py --foreign-python C:/Python310/python.exe \\
        --out results/spikes/2026-10-18
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

IMPOSTOR = "IMPOSTOR agentvc from the project folder"


def _venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def _outcome(result: subprocess.CompletedProcess[str]) -> str:
    text = result.stdout + result.stderr
    if IMPOSTOR in text:
        return "impostor"
    if result.returncode == 0 and result.stdout.startswith("agentvc "):
        return "real"
    if "No module named agentvc" in text:
        return "not_found"
    return "other"


def run(foreign_python: str, workdir: Path) -> dict[str, Any]:
    project = workdir / "project"
    shutil.rmtree(project, ignore_errors=True)
    project.mkdir(parents=True)
    venv = project / ".venv"
    subprocess.run([foreign_python, "-m", "venv", "--without-pip", str(venv)], check=True,
                   timeout=300)  # fmt: skip
    fake = project / "agentvc"
    fake.mkdir()
    (fake / "__init__.py").write_text("", encoding="utf-8")
    (fake / "__main__.py").write_text(f"print({IMPOSTOR!r})\n", encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = str(_venv_bin(venv)) + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(venv)
    env["PYTHONPATH"] = str(project)
    # A shell resolves a bare `python` through PATH; on Windows, CreateProcess would not.
    bare = shutil.which("python", path=env["PATH"]) or "python"
    agentvc_python = sys.executable
    forms = {
        "bare_python": [bare, "-m", "agentvc"],
        "pinned": [agentvc_python, "-m", "agentvc"],
        "pinned_isolated": [agentvc_python, "-I", "-m", "agentvc"],
        "pinned_isolated_no_site": [agentvc_python, "-I", "-S", "-m", "agentvc"],
    }
    cases = []
    for name, command in forms.items():
        result = subprocess.run(
            command, cwd=project, env=env, capture_output=True, text=True, timeout=120,
            check=False,
        )  # fmt: skip
        cases.append(
            {
                "name": name,
                "outcome": _outcome(result),
                "exit_code": result.returncode,
                "interpreter_is_project_venv": Path(command[0]).resolve().is_relative_to(venv),
            }
        )
        print(f"{name:<26} {cases[-1]['outcome']}")
    foreign = subprocess.run([foreign_python, "--version"], capture_output=True, text=True)
    return {
        "environment": {
            "os": platform.system(),
            "agentvc_python": platform.python_version(),
            "foreign_python": (foreign.stdout or foreign.stderr).strip(),
        },
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--foreign-python", required=True, help="interpreter for the project venv")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args(argv)
    if args.workdir:
        result = run(args.foreign_python, args.workdir)
    else:
        with tempfile.TemporaryDirectory(prefix="agentvc-d6-") as tmp:
            result = run(args.foreign_python, Path(tmp))
    result["meta"] = {"script": "scripts/spikes/interpreter_pinning.py",
                      "date": date.today().isoformat()}  # fmt: skip
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "interpreter_pinning.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
