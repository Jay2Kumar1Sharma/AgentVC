# Task runner for macOS and Linux. On Windows, make.ps1 offers the same targets:
#   powershell -ExecutionPolicy Bypass -File make.ps1 fast
# Run inside the project's virtual environment, or set PYTHON=/path/to/python.
PYTHON ?= python

.PHONY: help fast test arch fuzz chaos

help:
	@echo "make fast   lint, types and fast tests (< 2 min; run before every push)"
	@echo "make test   full local suite: lint, types, all tests incl. slow (end of every session)"
	@echo "make arch   architecture structure and render checks"
	@echo "make fuzz   fuzzers (not implemented yet)"
	@echo "make chaos  kill-at-random-point tests (not implemented yet)"

fast:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy
	$(PYTHON) -m pytest -m "not slow" -q

# Full local suite (AGENTS.md step 8): everything in fast, plus slow tests. Later checks
# (architecture, eval scenario and adversarial runners) join here as they arrive.
test:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy
	$(PYTHON) scripts/arch_check.py
	$(PYTHON) -m pytest -q

# Placeholders fail loudly instead of passing silently (plan §16.5, §5.3).

# Architecture page (plan §5.3): structure checks, then the Playwright render check.
arch:
	$(PYTHON) scripts/arch_check.py
	$(PYTHON) -m pytest -q tests/test_arch_render.py

fuzz:
	@echo "make fuzz: not implemented yet (arrives with v0.1-alpha, plan §15.5)" >&2; exit 2

chaos:
	@echo "make chaos: not implemented yet (arrives with v0.1-alpha, plan §15.5)" >&2; exit 2
