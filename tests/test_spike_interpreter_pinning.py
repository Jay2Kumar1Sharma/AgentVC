"""scripts/spikes/interpreter_pinning.py (decision D6): how a hook command picks its Python."""

import json
import sys
from pathlib import Path

import pytest

from scripts.spikes import interpreter_pinning


@pytest.mark.slow
def test_only_the_pinned_isolated_interpreter_runs_the_real_agentvc(tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = interpreter_pinning.main(
        ["--foreign-python", sys.executable, "--workdir", str(tmp_path / "w"), "--out", str(out)]
    )
    assert code == 0
    cases = {
        c["name"]: c
        for c in json.loads((out / "interpreter_pinning.json").read_text("utf-8"))["cases"]
    }
    # A bare `python` resolves to the project's own venv, and the project folder's planted
    # package runs (first run of this spike expected "not_found"; reality is worse).
    assert cases["bare_python"]["interpreter_is_project_venv"] is True
    assert cases["bare_python"]["outcome"] == "impostor"
    # The right interpreter, but the project folder (cwd) shadows the real package.
    assert cases["pinned"]["outcome"] == "impostor"
    # Isolated mode ignores cwd and PYTHONPATH: the real AgentVC runs.
    assert cases["pinned_isolated"]["outcome"] == "real"
    # -S drops site-packages, where AgentVC is installed: it needs a launcher on sys.path.
    assert cases["pinned_isolated_no_site"]["outcome"] == "not_found"
