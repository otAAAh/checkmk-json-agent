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


def test_explorer_emits_the_endpoint_name(rule_value: dict, explorer_output: dict):
    # The name becomes the item of the endpoint's own service, so it must reach
    # both the rule value and the agent command line.
    assert rule_value["endpoints"][0]["name"] == "frontend"
    assert explorer_output["cli"][0]["name"] == "frontend"


def test_explorer_emits_aggregate_and_value_as(rule_value: dict, explorer_output: dict):
    # The 'count' boolean is gone; aggregate / value_as replace it and must be
    # emitted in the ruleset's own shapes (a bare string / a cascading tuple).
    extractions = rule_value["endpoints"][0]["extractions"]
    by_service = {x["service"]: x for x in extractions}
    assert "count" not in by_service["Health"]
    assert by_service["Node"]["aggregate"] == "avg"
    assert by_service["Backup"]["value_as"] == ("timestamp", {"format": "iso"})
    assert by_service["Requests"]["value_as"] == ("counter", None)
    # A field with neither must carry neither (they are optional).
    assert "aggregate" not in by_service["Health"]
    assert "value_as" not in by_service["Health"]

    cli = {x["service"]: x for x in explorer_output["cli"][0]["extractions"]}
    assert cli["Node"]["aggregate"] == "avg"
    assert cli["Backup"]["value_as"] == ["timestamp", {"format": "iso"}]
    assert cli["Requests"]["value_as"] == ["counter", None]


def test_explorer_choices_match_the_ruleset(explorer_output: dict):
    # Every aggregate / value_as / timestamp-format choice the Explorer offers
    # must exist in the ruleset, or it generates a rule Checkmk rejects.
    source = (_ROOT / "explorer" / "index.html").read_text()
    ruleset = _RULESET.read_text()
    for choice in ("count", "sum", "avg", "min", "max"):
        assert f'SingleChoiceElement("{choice}"' in ruleset
    for choice in ("counter", "timestamp"):
        assert f'name="{choice}"' in ruleset
        assert f'"{choice}"' in source
    for fmt in ("auto", "epoch", "epoch_ms", "iso"):
        assert f'"{fmt}"' in ruleset


def test_explorer_emits_the_piggyback_host_field(rule_value: dict, explorer_output: dict):
    # It turns a '[*]' element into its own Checkmk host, so it must reach both
    # the rule value and the agent command line - a rule that silently drops it
    # would put every service back on the polling host.
    by_service = {x["service"]: x for x in rule_value["endpoints"][0]["extractions"]}
    assert by_service["Node"]["piggyback_host"] == "name"
    # Optional: a field without it must not carry the key at all.
    assert "piggyback_host" not in by_service["Health"]

    cli = {x["service"]: x for x in explorer_output["cli"][0]["extractions"]}
    assert cli["Node"]["piggyback_host"] == "name"
    assert "piggyback_host" not in cli["Health"]


def test_explorer_emits_the_cache_ttl(rule_value: dict, explorer_output: dict):
    # The agent owns the cache, so the TTL has to survive into both the rule value
    # and the '--endpoint' blob or caching is silently off.
    assert rule_value["endpoints"][0]["cache_ttl"] == 300.0
    assert explorer_output["cli"][0]["cache_ttl"] == 300.0


def test_explorer_emits_api_key_header_auth(rule_value: dict, explorer_output: dict):
    # The rule value carries the header NAME plus a password-store reference;
    # the CLI blob carries the name and the bare auth kind, never the key (which
    # travels as --secret_<i>-id).
    endpoint = rule_value["endpoints"][1]
    kind, spec = endpoint["auth"]
    assert kind == "auth_header"
    assert spec["header"] == "X-API-Key"
    assert spec["key"] == ("cmk_postprocessed", "stored_password", ("pw-store-id", ""))

    cli = explorer_output["cli"][1]
    assert cli["auth"] == "auth_header"
    assert cli["auth_header"] == "X-API-Key"
    assert "key" not in cli


def test_explorer_emits_api_key_query_auth(rule_value: dict, explorer_output: dict):
    endpoint = rule_value["endpoints"][2]
    kind, spec = endpoint["auth"]
    assert kind == "auth_query"
    assert spec["parameter"] == "api_key"
    assert spec["key"] == ("cmk_postprocessed", "stored_password", ("pw-store-id", ""))

    cli = explorer_output["cli"][2]
    assert cli["auth"] == "auth_query"
    assert cli["auth_query"] == "api_key"
    # The URL stays clean - the agent appends the parameter at request time.
    assert "api_key" not in cli["url"]


def test_explorer_emits_the_summary_template(rule_value: dict, explorer_output: dict):
    # Presentation-only extra summary text; it must reach both the rule value and
    # the agent command line, since the agent resolves its '{path}' placeholders.
    by_service = {x["service"]: x for x in rule_value["endpoints"][0]["extractions"]}
    assert by_service["Health"]["summary"] == "{message} (leader {leader})"
    assert "summary" not in by_service["Node"]

    cli = {x["service"]: x for x in explorer_output["cli"][0]["extractions"]}
    assert cli["Health"]["summary"] == "{message} (leader {leader})"


def test_explorer_emits_the_retry_policy(rule_value: dict, explorer_output: dict):
    endpoint = rule_value["endpoints"][0]
    assert endpoint["retry"] == {"attempts": 2, "backoff": 0.5}
    assert explorer_output["cli"][0]["retry"] == {"attempts": 2, "backoff": 0.5}


def test_explorer_emits_the_inventory_target(rule_value: dict, explorer_output: dict):
    by_service = {x["service"]: x for x in rule_value["endpoints"][0]["extractions"]}
    assert by_service["Backup"]["inventory"] == {
        "node": "software.applications.json_api",
        "keep_service": True,
    }
    # A field without one must not carry the key at all (it is optional).
    assert "inventory" not in by_service["Health"]

    cli = {x["service"]: x for x in explorer_output["cli"][0]["extractions"]}
    assert cli["Backup"]["inventory"]["node"] == "software.applications.json_api"
