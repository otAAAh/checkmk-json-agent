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
import re
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


def test_header_paste_is_reduced_to_header_names(explorer_output: dict):
    """The Explorer cannot fetch, so headers are pasted - typically a whole
    'curl -sSi' dump. Only 'Name: value' lines may survive, or the picker offers
    '@header.' paths for a status line or a stray body line."""
    parsed = explorer_output["headers"]
    assert [h["name"] for h in parsed] == ["content-type", "X-RateLimit-Remaining", "set-cookie"]
    # The status line, the blank line and the JSON body contribute nothing.
    assert all(not h["name"].startswith("HTTP") for h in parsed)
    # A repeated header collapses to one pickable name, keeping the first value.
    assert [h["value"] for h in parsed if h["name"] == "set-cookie"] == ["a=1"]
    # The name carried into the extraction keeps the API's own spelling.
    assert parsed[1]["value"] == "4999"


def test_header_path_gets_a_sensible_default_service_name(explorer_output: dict):
    """'@header.' must not leak into the service name the picker proposes."""
    assert explorer_output["headerService"] == "X-RateLimit-Remaining"


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


def test_explorer_emits_oauth2_client_credentials_auth(rule_value: dict, explorer_output: dict):
    """The rule value carries the token URL / client id / secret reference; the
    CLI blob carries the same minus the secret, which travels as --secret_<i>."""
    endpoint = rule_value["endpoints"][3]
    kind, spec = endpoint["auth"]
    assert kind == "auth_oauth2"
    assert spec["token_url"] == "https://login.example.com/oauth2/v2.0/token"
    assert spec["client_id"] == "monitoring"
    assert spec["client_secret"] == ("cmk_postprocessed", "stored_password", ("pw-store-id", ""))
    assert spec["scope"] == "api://monitoring/.default"
    assert spec["client_auth"] == "post"
    # An empty optional is omitted rather than sent as "" - the ruleset treats a
    # missing key and an empty string differently.
    assert "audience" not in spec

    cli = explorer_output["cli"][3]
    assert cli["auth"] == "auth_oauth2"
    assert cli["oauth2"]["token_url"] == "https://login.example.com/oauth2/v2.0/token"
    assert cli["oauth2"]["client_id"] == "monitoring"
    assert cli["oauth2"]["audience"] is None
    # The secret never reaches the blob, which is loggable.
    assert "client_secret" not in cli["oauth2"]
    assert "pw-store-id" not in json.dumps(cli)


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
    # The fixture asks for 9 retries with a 99s backoff; both are clamped to the
    # ruleset's range so the generated rule value actually imports.
    endpoint = rule_value["endpoints"][0]
    assert endpoint["retry"] == {"attempts": 5, "backoff": 30.0}
    assert explorer_output["cli"][0]["retry"] == {"attempts": 5, "backoff": 30.0}


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


def test_explorer_clamps_the_retry_policy_to_the_rulesets_range(explorer_output: dict):
    # The Explorer's contract is that its output imports cleanly, so it must not
    # emit a retry policy the ruleset's NumberInRange would reject.
    for endpoint in explorer_output["cli"]:
        retry = endpoint.get("retry")
        if retry:
            assert 1 <= retry["attempts"] <= 5
            assert 0.0 <= retry["backoff"] <= 30.0


def test_explorer_emits_the_service_name_prefix(rule_value: dict, explorer_output: dict):
    # The named endpoint prefixes its field services, so both outputs must carry
    # the flag - otherwise the Explorer's rule and its CLI command produce
    # differently named services from the same configuration.
    assert rule_value["endpoints"][0]["service_prefix"] is True
    assert explorer_output["cli"][0]["service_prefix"] is True


def test_explorer_never_emits_a_prefix_without_an_endpoint_name(explorer_output: dict):
    # _validate_endpoint rejects that combination, and the Explorer's contract is
    # that what it prints imports cleanly.
    assert explorer_output["prefixWithName"] is True
    assert explorer_output["prefixWithoutName"] is False
    assert explorer_output["prefixOff"] is False


def test_explorer_emits_the_raw_response_report(rule_value: dict, explorer_output: dict):
    # Both outputs must agree, or the Explorer's rule and its CLI command report
    # different things about the same endpoint.
    assert rule_value["endpoints"][0]["show_response"] == {"max_bytes": 4096, "headers": False}
    assert explorer_output["cli"][0]["show_response"] == {"max_bytes": 4096, "headers": False}


def test_explorer_clamps_or_omits_the_raw_response_report(explorer_output: dict):
    # No byte budget = the option is off (an empty Dictionary would be rejected);
    # too large a one is clamped to the ruleset's maximum rather than rejected.
    assert explorer_output["reportOff"] is None
    assert explorer_output["reportClamped"] == {"max_bytes": 65536, "headers": True}


def test_explorer_emits_the_shared_service(rule_value: dict, explorer_output: dict):
    # A field reported into a shared service must carry it into both the rule and
    # the agent command line, or the Explorer's two outputs describe different
    # service layouts.
    by_service = {x["service"]: x for x in rule_value["endpoints"][0]["extractions"]}
    assert by_service["Requests"]["group"] == "Traffic"
    cli = {x["service"]: x for x in explorer_output["cli"][0]["extractions"]}
    assert cli["Requests"]["group"] == "Traffic"
    # A field of its own emits nothing, matching the ruleset's optional field.
    assert "group" not in by_service["Health"]
    assert "group" not in cli["Health"]


def test_explorer_emits_the_host_labels(rule_value: dict, explorer_output: dict):
    """The endpoint's host labels, including the filtered/literal classification.

    A blank row is dropped: an empty label spec is rejected by the ruleset, and
    the Explorer's contract is that what it prints imports cleanly.
    """
    endpoint = rule_value["endpoints"][0]
    assert endpoint["host_labels"] == [
        {"path": "version"},
        {
            "path": "services[*]",
            "key": "MyApp",
            "value": "yes",
            "filter": {"path": "name", "op": "regex", "value": "^MyApp.*"},
        },
    ]
    assert explorer_output["cli"][0]["host_labels"] == endpoint["host_labels"]


def test_explorer_emits_the_field_context_report(rule_value: dict, explorer_output: dict):
    endpoint = rule_value["endpoints"][0]
    # Clamped to the ruleset's own ceiling, or the rule would not import.
    assert endpoint["field_context"] == {"source": "element", "max_bytes": 65536}
    assert explorer_output["cli"][0]["field_context"] == endpoint["field_context"]


def test_explorer_omits_the_field_context_when_off(rule_value: dict, explorer_output: dict):
    # The Explorer says "off" the same way the ruleset does - by omitting the
    # optional Dictionary - so an empty byte budget must emit nothing at all.
    off = rule_value["endpoints"][3]
    assert off["name"] == "oauth2"
    assert "field_context" not in off
    assert explorer_output["cli"][3]["field_context"] is None


def test_explorer_emits_the_pagination_settings(rule_value: dict, explorer_output: dict):
    endpoint = rule_value["endpoints"][0]
    # 'next' is a CascadingSingleChoice, i.e. a tuple in the rule value, and the
    # page count is clamped to the ruleset's range (1-100) or the rule would not
    # import.
    assert endpoint["pagination"] == {
        "next": ("body", "links.next"),
        "items": "data.items",
        "max_pages": 100,
        "max_elements": 500,
    }
    # The CLI blob mirrors the server-side call's _endpoint_json, where the tuple
    # has been through JSON and the unset cap is an explicit null.
    assert explorer_output["cli"][0]["pagination"] == {
        "next": ["body", "links.next"],
        "items": "data.items",
        "max_pages": 100,
        "max_elements": 500,
    }


def test_explorer_emits_the_link_header_pagination_branch(rule_value: dict, explorer_output: dict):
    endpoint = rule_value["endpoints"][1]
    assert endpoint["name"] == "api-key-header"
    # A FixedValue branch: the tuple's second half is None, and no element cap
    # was given, so that key is omitted from the rule value.
    assert endpoint["pagination"] == {"next": ("link_header", None), "items": "$", "max_pages": 3}
    assert explorer_output["cli"][1]["pagination"]["next"] == ["link_header", None]
    assert explorer_output["cli"][1]["pagination"]["max_elements"] is None


def test_explorer_emits_the_counted_pagination_branches(explorer_output: dict):
    page, offset = (ast.literal_eval(py)["pagination"] for py in explorer_output["countedPy"])
    # A Dictionary branch: only what is set, plus the required first page.
    assert page["next"] == (
        "page_number",
        {
            "parameter": "p",
            "start": 0,
            "page_size": 50,
            "size_parameter": "per_page",
            "total": "meta.total",
        },
    )
    # Nothing but the mode: the ruleset's prefill for the parameter, no 'start'
    # (the offset branch has none), and a size parameter without a size is
    # dropped rather than emitted into a rule the ruleset would refuse.
    assert offset["next"] == ("offset", {"parameter": "offset"})
    assert offset["max_pages"] == 20


def test_explorer_counted_cli_mirrors_the_server_side_call(explorer_output: dict):
    page, offset = (cli["pagination"]["next"] for cli in explorer_output["countedCli"])
    # CountedPages.model_dump(): every key, null where unset, 'start' always.
    assert page == [
        "page_number",
        {
            "parameter": "p",
            "start": 0,
            "page_size": 50,
            "size_parameter": "per_page",
            "total": "meta.total",
        },
    ]
    assert offset == [
        "offset",
        {
            "parameter": "offset",
            "start": 1,
            "page_size": None,
            "size_parameter": None,
            "total": None,
        },
    ]


def test_explorer_omits_pagination_unless_it_can_be_followed(
    rule_value: dict, explorer_output: dict
):
    # A form filled in halfway must emit nothing rather than a rule the ruleset
    # would reject (both keys are required inside the Dictionary).
    off = rule_value["endpoints"][3]
    assert off["name"] == "oauth2"
    assert "pagination" not in off
    assert explorer_output["cli"][3]["pagination"] is None
    assert explorer_output["pageNoItems"] is None
    assert explorer_output["pageNoPath"] is None
    assert explorer_output["pageOff"] is None


def test_explorer_emits_a_value_range_the_ruleset_accepts(rule_value: dict, explorer_output: dict):
    """The range is optional, so nothing else in this file would notice it going
    missing — and a field that silently loses its range draws a graph that
    rescales itself every hour."""
    by_service = {e["service"]: e for e in rule_value["endpoints"][0]["extractions"]}

    assert by_service["Node"]["value_range"] == {"min": 0.0, "max": 1000.0}
    # Floats, like every other number the ruleset's Float fields hold.
    assert all(isinstance(end, float) for end in by_service["Node"]["value_range"].values())
    # A field with no range configured must not carry an empty one: the ruleset
    # rejects a range with neither end.
    assert "value_range" not in by_service["Health"]
    # The agent command line mirrors the rule.
    cli_extractions = {e["service"]: e for e in explorer_output["cli"][0]["extractions"]}
    assert cli_extractions["Node"]["value_range"] == {"min": 0, "max": 1000}


def _ruleset_assignment(name: str) -> ast.expr:
    """The value assigned to a module-level ``name`` in the ruleset.

    AST again, not an import: this file's whole point is running without
    ``cmk.*`` (the CI 'explorer' job is a plain Python with no Checkmk).
    """
    tree = ast.parse(_RULESET.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node.value
    raise AssertionError(f"the ruleset no longer defines {name}")


def test_explorers_metric_name_pattern_matches_the_rulesets():
    """The Explorer is standalone, so it mirrors the ruleset's metric-name rules
    by hand. A drift here means it hands you a rule Setup then refuses, which is
    the one thing this tool exists not to do."""
    call = _ruleset_assignment("_METRIC_NAME")  # re.compile(r"...")
    assert isinstance(call, ast.Call)
    (pattern,) = [a.value for a in call.args if isinstance(a, ast.Constant)]

    source = (_ROOT / "explorer" / "index.html").read_text()
    assert f"/{pattern}/" in source, f"the Explorer does not carry the ruleset's {pattern!r}"


def test_explorers_reserved_inventory_column_matches_the_rulesets():
    """The column a '[*]' wildcard's inventory table is keyed by, which an
    attribute name therefore cannot be. Reserved in the ruleset and mirrored by
    hand here; a drift means the Explorer offers a rule Setup refuses."""
    constant = _ruleset_assignment("_INVENTORY_ROW_KEY")
    assert isinstance(constant, ast.Constant)

    source = (_ROOT / "explorer" / "index.html").read_text()
    assert f'const INVENTORY_ROW_KEY = "{constant.value}"' in source


def test_explorers_reserved_metrics_match_the_rulesets():
    call = _ruleset_assignment("_DECLARED_METRICS")  # frozenset({...})
    assert isinstance(call, ast.Call)
    (literal,) = call.args
    declared = {e.value for e in literal.elts if isinstance(e, ast.Constant)}
    assert declared, "could not read the reserved names out of the ruleset"

    source = (_ROOT / "explorer" / "index.html").read_text()
    block = re.search(r"const DECLARED_METRICS = new Set\(\[(.*?)\]\)", source, re.S)
    assert block, "the Explorer no longer declares DECLARED_METRICS"
    # Compared as a set, not as text: order and wrapping are free to differ.
    assert set(re.findall(r'"([^"]+)"', block.group(1))) == declared


_LABEL_CASES = json.loads((_ROOT / "tests" / "fixtures" / "label_path_cases.json").read_text())


@pytest.mark.parametrize(
    "index", range(len(_LABEL_CASES["cases"])), ids=[c["name"] for c in _LABEL_CASES["cases"]]
)
def test_explorer_names_the_elements_as_the_agent_does(explorer_output: dict, index: int):
    """The 'Name suffix' warning is only worth showing if the Explorer names the
    '[*]' elements exactly as the agent will. The agent answers the same cases in
    tests/test_label_path_parity.py and the wizard in elementlabels.test.ts."""
    case = _LABEL_CASES["cases"][index]
    answer = explorer_output["labelCases"][index]

    assert answer["labels"] == case["labels"]
    assert answer["issues"] == case["issues"]


def test_explorer_warns_when_a_name_suffix_repeats(explorer_output: dict):
    """A repeated suffix is resolved by the agent with the elements' POSITIONS,
    which move when the API reorders its elements - so the warning has to say
    which value repeats, where, and what the services will be called."""
    plain = explorer_output["labelWarnings"]["plain"]
    assert len(plain) == 2
    assert plain[0].startswith("'web' names 2 elements (0, 2).")
    assert "'web [0]', 'web [2]'" in plain[0]
    assert "swap readings" in plain[0]
    assert plain[1] == (
        "The name field is missing in 1 element(s) (3): "
        "the site names those by their position instead."
    )


@pytest.mark.parametrize("setting", ["aggregated", "perHost", "noSample"])
def test_explorer_does_not_warn_where_no_element_name_reaches_a_service(
    explorer_output: dict, setting: str
):
    """An aggregation creates one service with no element names, a host per
    element keeps the plain service name, and without a sample there is nothing
    to judge - a warning in any of these would be noise."""
    assert explorer_output["labelWarnings"][setting] == []
