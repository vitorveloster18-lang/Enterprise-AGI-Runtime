VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
EGR := $(VENV)/bin/egr

.PHONY: setup install test lint fmt demo clean

setup:            ## create venv and install the runtime in editable mode
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

install:
	$(PIP) install -e ".[dev]"

test:             ## run the test suite
	$(VENV)/bin/pytest

lint:
	$(VENV)/bin/ruff check src tests

fmt:
	$(VENV)/bin/ruff format src tests

demo:             ## end-to-end demo: init -> status -> doctor -> task -> audit
	bash scripts/demo.sh

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__
