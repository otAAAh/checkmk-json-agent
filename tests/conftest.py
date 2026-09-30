# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Import the plugin modules for testing.

The plugin modules are imported under their real package names
(``cmk_addons.plugins.json_api.*``, a namespace package resolved from the repo
root), exactly as a site imports them - so the rulesets' relative ``..lib``
import works as it does at runtime. The ``cmk.*`` plugin APIs must be
importable - run the suite with a Checkmk dev venv or inside a site, e.g.::

    PYTHON=~/git/checkmk/.venv/bin/python make test
"""

import importlib
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# The Explorer's GUI half ships in the OTHER package, under a directory that is
# not an importable Python package here (in a site it lands in the
# ``cmk.gui.plugins.wato`` namespace), so it is mounted as one, see _gui_package.
GUI = REPO_ROOT / "gui" / "wato" / "json_explorer"

# ``cmk_addons`` is a namespace package: with the repo root on the path it
# resolves here the same way it resolves from ``local/lib/python3`` in a site.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _import(dotted: str):
    return importlib.import_module(f"cmk_addons.plugins.json_api.{dotted}")


@pytest.fixture(scope="session")
def agent():
    return _import("special_agent.agent_json_api")


@pytest.fixture(scope="session")
def check():
    return _import("agent_based.json_api")


@pytest.fixture(scope="session")
def ssc():
    return _import("server_side_calls.special_agent")


@pytest.fixture(scope="session")
def ruleset():
    return _import("rulesets.special_agent")


@pytest.fixture(scope="session")
def check_ruleset():
    return _import("rulesets.check_parameters")


@pytest.fixture(scope="session")
def graphing():
    return _import("graphing.json_api")


@pytest.fixture(scope="session")
def perfometers():
    return _import("graphing.perfometers")


def _gui_package() -> str:
    """Register the Explorer's GUI directory as a bare package and return its name.

    Its submodules import each other relatively (``from .page import ...``), so
    they need a parent package - but not the real ``__init__``, which imports
    every page and registers it into the live GUI registries.
    """
    name = "ja_explorer"
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(GUI)]
        sys.modules[name] = package
    return name


@pytest.fixture(scope="session")
def explorer_fetch():
    """The Explorer's preview fetch — 2.5+ only.

    The agent package supports 2.4, the Explorer does not (its GUI APIs, e.g.
    ``cmk.gui.pages.PageContext``, only exist from 2.5), so on the 2.4 leg of CI
    the module cannot even be imported. That is the package's documented
    requirement rather than a failure, hence a skip with the reason spelled out
    — the schema drift guards in the same file run on both lines regardless.
    """
    try:
        return importlib.import_module(f"{_gui_package()}.fetch")
    except ImportError as exc:
        pytest.skip(f"the Explorer package needs the Checkmk 2.5+ GUI APIs: {exc}")
