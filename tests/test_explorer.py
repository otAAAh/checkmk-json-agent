# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Guard against the standalone Explorer drifting from the plugin's config schema.

``explorer/index.html`` is a hand-written mirror of the ruleset + server-side
call: it generates the ``value_raw`` for a rule and the agent ``--endpoint`` CLI.
When the ruleset gains a required field, the Explorer must emit it too, or the
rule it generates is rejected on import (this is exactly how ``follow_redirects``
broke once). These tests drive the Explorer's real generators (via a headless
Node harness) and check that its output still covers every ``required=True``
field in the ruleset.

The harness needs Node; the assertions are pure-stdlib (they AST-parse the
ruleset rather than importing ``cmk.*``), so this file runs outside a Checkmk
site and simply skips where Node is unavailable.
"""

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = Path(__file__).parent / "explorer_harness.mjs"
_RULESET = _ROOT / "cmk_addons" / "plugins" / "json_api" / "rulesets" / "special_agent.py"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is required to drive the Explorer"
)


def _required_keys(func_name: str) -> set[str]:
    """The keys of ``required=True`` DictElements in a ruleset builder function.

    AST-parses ``special_agent.py`` (no ``cmk`` import needed) and reads the
    single ``Dictionary(elements={...})`` returned by ``func_name`` (e.g.
    ``_endpoint`` / ``_extraction``).
    """
    tree = ast.parse(_RULESET.read_text())
    func = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == func_name
    )
    elements = next(
        kw.value
        for call in ast.walk(func)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "Dictionary"
        for kw in call.keywords
        if kw.arg == "elements"
    )
    assert isinstance(elements, ast.Dict)

    required = set()
    for key, value in zip(elements.keys, elements.values, strict=True):
        assert isinstance(key, ast.Constant)
        is_required = any(
            kw.arg == "required" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in value.keywords  # type: ignore[union-attr]
        )
        if is_required:
            required.add(key.value)
    return required


@pytest.fixture(scope="module")
def explorer_output() -> dict:
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, cwd=_ROOT)
    assert result.returncode == 0, f"harness failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def rule_value(explorer_output: dict) -> dict:
    # The Explorer emits value_raw as Python source; it must be a valid literal
    # in the {'endpoints': [...]} shape the ruleset expects after migration.
    value = ast.literal_eval(explorer_output["valuePy"])
    assert isinstance(value, dict) and isinstance(value.get("endpoints"), list)
    assert value["endpoints"], "Explorer produced no endpoints"
    return value


def test_ruleset_has_required_keys_to_check():
    # Guard the guard: if the AST parse found nothing, the checks below are
    # vacuous and the whole test is worthless.
    assert _required_keys("_endpoint") >= {"url", "method", "extractions"}
    assert _required_keys("_extraction") == {"service", "path"}


def test_explorer_value_raw_covers_required_endpoint_keys(rule_value: dict):
    endpoint = rule_value["endpoints"][0]
    missing = _required_keys("_endpoint") - set(endpoint)
    assert not missing, f"Explorer value_raw omits required endpoint keys: {missing}"


def test_explorer_value_raw_covers_required_extraction_keys(rule_value: dict):
    required = _required_keys("_extraction")
    for extraction in rule_value["endpoints"][0]["extractions"]:
        missing = required - set(extraction)
        assert not missing, f"Explorer omits required extraction keys: {missing}"


def test_explorer_cli_object_covers_required_endpoint_keys(explorer_output: dict):
    # The --endpoint CLI object mirrors the server-side call; the required
    # connection keys must be present there too (auth is a bare string in the
    # CLI form, so it is not compared by value here).
    cli = explorer_output["cli"][0]
    missing = _required_keys("_endpoint") - set(cli)
    assert not missing, f"Explorer CLI object omits required keys: {missing}"


def test_explorer_emits_the_aggregation(rule_value: dict, explorer_output: dict):
    # The 'count' boolean is gone; the aggregate choice replaces it and must be
    # emitted in the ruleset's own shape (a bare choice name).
    by_service = {x["service"]: x for x in rule_value["endpoints"][0]["extractions"]}
    assert "count" not in by_service["Health"]
    assert by_service["Node"]["aggregate"] == "avg"
    # A field without an aggregation must not carry the key at all (it is optional).
    assert "aggregate" not in by_service["Health"]

    cli = {x["service"]: x for x in explorer_output["cli"][0]["extractions"]}
    assert cli["Node"]["aggregate"] == "avg"


def test_explorer_choices_match_the_ruleset(explorer_output: dict):
    # Every aggregation the Explorer offers must exist in the ruleset, or it
    # generates a rule Checkmk rejects.
    source = (_ROOT / "explorer" / "index.html").read_text()
    ruleset = _RULESET.read_text()
    for choice in ("count", "sum", "avg", "min", "max"):
        assert f'SingleChoiceElement("{choice}"' in ruleset
        assert f'"{choice}"' in source
