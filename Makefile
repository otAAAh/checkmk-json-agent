# SPDX-License-Identifier: GPL-2.0-only
RUFF   ?= ruff
PYTHON ?= python3

XGETTEXT ?= xgettext

.PHONY: help format lint typecheck test mkp pot check-po changelog release-notes clean

help:
	@echo "Targets: format | lint | typecheck | test | mkp | pot | check-po | changelog | release-notes | clean"

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

# Verify every shipped catalog is in sync with the current source strings.
# Regenerates the template, then checks each .po has exactly the template's
# msgids (catches a catalog that fell behind after a code change).
check-po: pot
	@rc=0; \
	for po in locales/*/LC_MESSAGES/multisite.po; do \
		echo "checking $$po"; \
		msgcmp "$$po" locales/json_api.pot || rc=1; \
	done; \
	if [ $$rc -ne 0 ]; then \
		echo "A catalog is out of sync — run 'make pot' then 'msgmerge --update <po> locales/json_api.pot' and translate the new strings." >&2; \
		exit 1; \
	fi

# Regenerate CHANGELOG.md from the git history (one section per version).
changelog:
	$(PYTHON) scripts/gen_changelog.py

# Print the release notes for the current pyproject version (release body).
release-notes:
	$(PYTHON) scripts/gen_changelog.py --version $$($(PYTHON) -c 'import tomllib,pathlib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')

clean:
	rm -f *.mkp
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
