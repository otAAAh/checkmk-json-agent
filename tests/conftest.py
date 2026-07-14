# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Load the plugin modules by file path for testing.

Nothing is installed as ``cmk_addons.plugins.json_api`` during tests, and the
special agent has no ``.py`` extension, so each module is loaded directly from
its source file. The ``cmk.*`` plugin APIs must be importable - run the suite
with a Checkmk dev venv or inside a site, e.g.::

    PYTHON=~/git/checkmk/.venv/bin/python make test
"""

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FAMILY = REPO_ROOT / "cmk_addons" / "plugins" / "json_api"

# The rulesets import shared form specs as ``cmk_addons.plugins.json_api.lib``
# (an absolute import that resolves against the namespace package at runtime in
# a site). Put the repo root on the path so that same import resolves here too.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, relpath: str):
    loader = SourceFileLoader(name, str(FAMILY / relpath))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def agent():
    return _load("ja_agent", "libexec/agent_json_api")


@pytest.fixture(scope="session")
def check():
    return _load("ja_check", "agent_based/json_api.py")


@pytest.fixture(scope="session")
def ssc():
    return _load("ja_ssc", "server_side_calls/special_agent.py")


@pytest.fixture(scope="session")
def ruleset():
    return _load("ja_ruleset", "rulesets/special_agent.py")


@pytest.fixture(scope="session")
def check_ruleset():
    return _load("ja_check_ruleset", "rulesets/check_parameters.py")


@pytest.fixture(scope="session")
def graphing():
    return _load("ja_graphing", "graphing/json_api.py")
