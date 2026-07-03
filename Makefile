# SPDX-License-Identifier: GPL-2.0-only
RUFF   ?= ruff
PYTHON ?= python3

XGETTEXT ?= xgettext

.PHONY: help format lint typecheck test mkp pot clean

help:
	@echo "Targets: format | lint | typecheck | test | mkp | pot | clean"

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

# Regenerate the translation template from the localizable Setup/graphing strings.
# The .po files are then updated with `msgmerge`; see README (Translations).
pot:
	$(XGETTEXT) -L Python --from-code=UTF-8 \
		-k -kTitle:1 -kHelp:1 -kLabel:1 -kMessage:1 \
		--package-name="checkmk-json-agent" \
		--msgid-bugs-address="https://github.com/otAAAh/checkmk-json-agent/issues" \
		-o locales/json_api.pot \
		cmk_addons/plugins/json_api/rulesets/special_agent.py \
		cmk_addons/plugins/json_api/graphing/json_api.py

clean:
	rm -f *.mkp
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
