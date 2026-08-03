# SPDX-License-Identifier: GPL-2.0-only
RUFF   ?= ruff
PYTHON ?= python3

XGETTEXT ?= xgettext

NPM ?= npm
# Checkmk checkout supplying the BUILT-IN cmk-frontend-vue source (build-time
# bridge — see frontend/vite.config.ts). Locally $CMK_REPO; in CI a clone of
# the public github.com/Checkmk/checkmk at a pinned ref.
CMK_REPO ?= $(HOME)/git/checkmk

.PHONY: help format lint typecheck test mkp frontend pot check-po changelog release-notes clean

help:
	@echo "Targets: format | lint | typecheck | test | mkp | frontend | pot | check-po | changelog | release-notes | clean"

# Build the Explorer's own Vue app, importing the REAL CmkWizard from
# cmk-frontend-vue (no vendoring) via the build-time bridge. Emits index.html +
# assets/ (the real components pull theme image assets that can't be inlined),
# placed under web/htdocs/json_api/wizard/ which the json_api_explorer MKP ships
# via the 'web' part. build_mkp.py stays stdlib and just packages web/.
frontend:
	cd frontend && $(NPM) ci && CMK_REPO="$(CMK_REPO)" $(NPM) run build
	rm -rf web/htdocs/json_api/wizard
	mkdir -p web/htdocs/json_api/wizard
	cp -r frontend/dist/. web/htdocs/json_api/wizard/
	@echo "Wrote web/htdocs/json_api/wizard/ (index.html + assets/)"

# Keep these in lock-step with the lint-and-build job in .github/workflows/ci.yml:
# the local targets must be a SUPERSET of what CI enforces, never a subset, or
# `make format lint` passes and the pipeline still fails. 'gui' ships in the
# Explorer MKP, so it is production code and is checked like the rest.
RUFF_SOURCES = cmk_addons scripts tests gui
# mypy skips 'tests': disallow_untyped_defs is on globally and the suite is
# deliberately untyped, so including it would mean annotating every test.
MYPY_SOURCES = cmk_addons scripts gui

format:
	$(RUFF) format $(RUFF_SOURCES)

lint:
	$(RUFF) check $(RUFF_SOURCES)

typecheck:
	mypy $(MYPY_SOURCES)

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
		cmk_addons/plugins/json_api/lib.py \
		cmk_addons/plugins/json_api/rulesets/special_agent.py \
		cmk_addons/plugins/json_api/rulesets/check_parameters.py \
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
