PY ?= python3
VENV = .venv

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -e '.[dev]'

test:
	$(VENV)/bin/python -m pytest -q

lint:
	$(VENV)/bin/python -m compileall -q src

.PHONY: install test lint
