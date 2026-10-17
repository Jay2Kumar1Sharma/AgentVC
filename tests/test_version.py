"""The package runs as a module and reports its version."""

import subprocess
import sys

import agentvc


def test_module_prints_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agentvc"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"agentvc {agentvc.__version__}"
