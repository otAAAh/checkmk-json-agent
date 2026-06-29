# SPDX-License-Identifier: GPL-2.0-only
RUFF   ?= ruff
PYTHON ?= python3

.PHONY: help format lint typecheck test mkp clean

help:
	@echo "Targets: format | lint | typecheck | test | mkp | clean"

format:
	$(RUFF) format cmk_addons scripts

lint:
	$(RUFF) check cmk_addons scripts

typecheck:
	mypy cmk_addons scripts

test:
	$(PYTHON) -m pytest tests

mkp:
	$(PYTHON) scripts/build_mkp.py

clean:
	rm -f *.mkp
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
