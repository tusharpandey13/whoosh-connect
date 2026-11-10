PY ?= python3
VENV = .venv

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -e '.[dev]'

test:
	$(VENV)/bin/python -m pytest -q

lint:
	$(VENV)/bin/python -m compileall -q src

# --- run the bridge -------------------------------------------------------

ride:
	$(VENV)/bin/whoosh-connect run

ride-bg:
	mkdir -p "$$HOME/Library/Logs/whoosh-connect"
	nohup $(VENV)/bin/whoosh-connect run > "$$HOME/Library/Logs/whoosh-connect/ride.log" 2>&1 & \
	echo "bridge started (log: ~/Library/Logs/whoosh-connect/ride.log)"; \
	echo "stop with: make stop"

stop:
	-pkill -f 'whoosh[-]connect'
	@echo "stopped (if a bridge was running)"

install-launchd:
	bash scripts/install_launchd.sh

uninstall-launchd:
	bash scripts/uninstall_launchd.sh

.PHONY: install test lint ride ride-bg stop install-launchd uninstall-launchd
