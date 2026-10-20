# Task runner for macOS and Linux. On Windows, make.ps1 offers the same targets:
#   powershell -ExecutionPolicy Bypass -File make.ps1 fast
# Run inside the project's virtual environment, or set PYTHON=/path/to/python.
PYTHON ?= python

.PHONY: help fast test arch fuzz chaos

help:
	@echo "make fast   lint, types and fast tests (< 2 min; run before every push)"
	@echo "make test   everything a pull request runs (not implemented yet)"
	@echo "make arch   architecture checks (not implemented yet)"
	@echo "make fuzz   fuzzers (not implemented yet)"
	@echo "make chaos  kill-at-random-point tests (not implemented yet)"

fast:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy
	$(PYTHON) -m pytest -m "not slow" -q

# Placeholders fail loudly instead of passing silently (plan §16.5, §5.3).
test:
	@echo "make test: not implemented yet (arrives with v0.1-alpha, plan §16.5)" >&2; exit 2

arch:
	@echo "make arch: not implemented yet (arrives in Discovery part 2, plan §5.2)" >&2; exit 2

fuzz:
	@echo "make fuzz: not implemented yet (arrives with v0.1-alpha, plan §15.5)" >&2; exit 2

chaos:
	@echo "make chaos: not implemented yet (arrives with v0.1-alpha, plan §15.5)" >&2; exit 2
