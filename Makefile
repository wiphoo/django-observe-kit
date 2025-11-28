PYTHON ?= python
UV ?= $(shell command -v uv 2>/dev/null)
UV_FLAGS ?= --system

ifeq ($(UV),)
RUNNER ?= $(PYTHON) -m
INSTALL = $(PYTHON) -m pip install -e .[dev]
else
RUNNER ?= $(UV) run
INSTALL = $(UV) pip install $(UV_FLAGS) -e .[dev]
endif

.PHONY: install lint format test clean

install:
	$(INSTALL)

lint:
	$(RUNNER) ruff check src tests

format:
	$(RUNNER) ruff format src tests

test:
	$(RUNNER) pytest

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
