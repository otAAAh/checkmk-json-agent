# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the ruleset's config-time validation."""

import pytest
from cmk.rulesets.v1.form_specs.validators import ValidationError


def test_valid_regex_passes(ruleset):
    ruleset._validate_regex("UP|ok")  # must not raise


def test_invalid_regex_rejected_at_config_time(ruleset):
    with pytest.raises(ValidationError):
        ruleset._validate_regex("(unclosed")


def test_valid_url_passes(ruleset):
    ruleset._validate_url("https://app.example.com/health")  # must not raise
    ruleset._validate_url("http://10.0.0.1:8080/status")  # must not raise


@pytest.mark.parametrize("url", ["HTTP://host/x", "HTTPS://host/x", "Https://host/x"])
def test_url_scheme_is_case_insensitive(ruleset, url):
    # Schemes are case-insensitive per RFC 3986; requests accepts these.
    ruleset._validate_url(url)  # must not raise


@pytest.mark.parametrize("bad", ["app.example.com/health", "ftp://x", "/relative", ""])
def test_url_without_http_scheme_rejected(ruleset, bad):
    with pytest.raises(ValidationError):
        ruleset._validate_url(bad)


@pytest.mark.parametrize(
    "expr",
    [
        "value / 1024 / 1024",
        "value * 1000",
        "(value - 32) * 5 / 9",
        "-value + 1",
        "value",
        "",
        "   ",
    ],
)
def test_valid_calc_passes(ruleset, expr):
    # An empty/blank expression is valid (= no transform); the check skips it.
    ruleset._validate_calc(expr)  # must not raise


@pytest.mark.parametrize(
    "expr",
    [
        "foo",  # unknown variable
        "__import__('os')",  # call + name
        "value.bit_length()",  # attribute + call
        "value ** 2",  # power operator not allowed
        "value +",  # syntax error
        "'str'",  # non-numeric constant
    ],
)
def test_invalid_calc_rejected(ruleset, expr):
    with pytest.raises(ValidationError):
        ruleset._validate_calc(expr)


def test_calc_accepts_the_second_operand(ruleset):
    ruleset._validate_calc("value / other * 100")  # must not raise


def test_calc_and_its_second_path_must_agree(ruleset):
    """Either half alone is a silent no-op, so both are rejected at config time."""
    ruleset._validate_extraction({"calc": "value / other", "calc_path": "total"})
    ruleset._validate_extraction({"calc": "value / 2"})  # neither half: fine

    with pytest.raises(ValidationError, match="must be set"):
        ruleset._validate_extraction({"calc": "value / other"})
    with pytest.raises(ValidationError, match="only used through"):
        ruleset._validate_extraction({"calc": "value / 2", "calc_path": "total"})
    # A blank path does not count as set.
    with pytest.raises(ValidationError, match="must be set"):
        ruleset._validate_extraction({"calc": "value / other", "calc_path": "  "})


@pytest.mark.parametrize(
    "expr",
    ["value / 2", "other_total", "'other'", "", None, "value +"],
)
def test_calc_uses_other_is_parsed_not_substring_matched(ruleset, expr):
    """A path or identifier that merely contains 'other' is not the variable."""
    assert ruleset._calc_uses_other(expr) is False


def test_parameter_form_builds(ruleset):
    # Smoke test: the form spec constructs without error.
    assert ruleset._parameter_form() is not None


def test_migrate_wraps_flat_rule_into_single_endpoint(ruleset):
    old = {"url": "http://x", "method": "GET", "verify_cert": True, "extractions": []}
    assert ruleset._migrate_to_endpoints(old) == {"endpoints": [old]}


def test_migrate_leaves_new_shape_untouched(ruleset):
    new = {"endpoints": [{"url": "http://x", "extractions": []}]}
    assert ruleset._migrate_to_endpoints(new) is new


def test_migrate_extraction_expected_becomes_must_match(ruleset):
    # A pre-'match' extraction (flat 'expected' regex) migrates to the
    # equivalent must_match, preserving the other fields.
    old = {"service": "Health", "path": "status", "unit": "count", "expected": "UP|ok"}
    migrated = ruleset._migrate_extraction(old)
    assert migrated["match"] == ("must_match", {"pattern": "UP|ok"})
    assert "expected" not in migrated
    assert migrated["service"] == "Health"
    assert migrated["unit"] == "count"


def test_migrate_extraction_leaves_match_untouched(ruleset):
    new = {"service": "S", "path": "p", "match": ("state_map", {"crit": "DOWN"})}
    migrated = ruleset._migrate_extraction(new)
    assert migrated["match"] == ("state_map", {"crit": "DOWN"})


def test_migrate_extraction_leaves_a_plain_extraction_alone(ruleset):
    plain = {"service": "S", "path": "p"}
    assert ruleset._migrate_extraction(plain) == {"service": "S", "path": "p"}


def test_migrate_extraction_count_becomes_aggregate(ruleset):
    # The 'count' boolean is now the 'count' choice of the aggregate dropdown.
    migrated = ruleset._migrate_extraction({"service": "S", "path": "p", "count": True})
    assert migrated["aggregate"] == "count"
    assert "count" not in migrated


def test_migrate_extraction_drops_a_disabled_count(ruleset):
    # count=False was the default for every rule saved while it was required, so
    # it must not turn into an aggregation.
    migrated = ruleset._migrate_extraction({"service": "S", "path": "p", "count": False})
    assert "aggregate" not in migrated
    assert "count" not in migrated


def test_migrate_extraction_keeps_an_explicit_aggregate(ruleset):
    # A rule that already has an aggregation wins over the legacy boolean.
    migrated = ruleset._migrate_extraction(
        {"service": "S", "path": "p", "count": True, "aggregate": "sum"}
    )
    assert migrated["aggregate"] == "sum"


def test_extraction_form_has_the_expected_keys(ruleset):
    # The 'count' boolean was replaced by the 'aggregate' choice; 'value_as'
    # carries the counter / timestamp interpretation.
    assert set(ruleset._extraction().elements) == {
        "service",
        "path",
        "label_path",
        "piggyback_host",
        "labels",
        "aggregate",
        "filter",
        "value_as",
        "unit",
        "levels_upper",
        "levels_lower",
        "calc",
        "calc_path",
        "match",
        "inventory",
        "summary",
    }
    assert "count" not in ruleset._extraction().elements


def test_aggregate_offers_every_function_the_agent_implements(ruleset, agent):
    choices = {
        element.name
        for element in ruleset._extraction().elements["aggregate"].parameter_form.elements
    }
    assert choices == {"count", "sum", "avg", "min", "max"}
    # Every offered function must actually reduce something in the agent.
    for mode in choices - {"count"}:
        found, value, error = agent._aggregate_numbers(mode, [1, 2])
        assert found, f"{mode}: {error}"
        assert value is not None


def test_value_as_offers_counter_and_timestamp(ruleset, check):
    choices = {
        element.name
        for element in ruleset._extraction().elements["value_as"].parameter_form.elements
    }
    assert choices == {"counter", "timestamp"}
    # Each choice must be one the check knows how to derive.
    for name in choices:
        assert check._coerce_value_as([name, {}]) is not None


def test_timestamp_formats_match_the_parser(ruleset, check):
    timestamp = next(
        element
        for element in ruleset._extraction().elements["value_as"].parameter_form.elements
        if element.name == "timestamp"
    )
    formats = {
        element.name
        for element in timestamp.parameter_form.elements["format"].parameter_form.elements
    }
    assert formats == {"auto", "epoch", "epoch_ms", "iso"}
    # Each offered format parses its canonical example.
    assert check._parse_timestamp(1700000000, "epoch")
    assert check._parse_timestamp(1700000000000, "epoch_ms")
    assert check._parse_timestamp("2023-11-14T22:13:20Z", "iso")
    assert check._parse_timestamp("2023-11-14T22:13:20Z", "auto")


def test_endpoint_form_has_an_optional_name(ruleset):
    name = ruleset._endpoint().elements["name"]
    assert name.required is False


def _endpoints(*specs):
    return [{"url": f"https://host{i}/health", **spec} for i, spec in enumerate(specs)]


def test_unique_endpoints_accepts_distinct_names(ruleset):
    ruleset._validate_unique_endpoints(  # must not raise
        _endpoints({"name": "frontend"}, {"name": "backend"})
    )


def test_unique_endpoints_accepts_unnamed_endpoints(ruleset):
    # A name is optional; several endpoints without one is the normal case and
    # each falls back to its own (already-unique) URL.
    ruleset._validate_unique_endpoints(_endpoints({}, {}))  # must not raise


def test_duplicate_endpoint_names_rejected(ruleset):
    # The name becomes the item of the endpoint's own service. A collision can
    # only be resolved positionally at runtime, so reordering the endpoints would
    # swap two services' histories - reject it at config time instead.
    with pytest.raises(ValidationError, match="frontend"):
        ruleset._validate_unique_endpoints(_endpoints({"name": "frontend"}, {"name": "frontend"}))


def test_duplicate_endpoint_names_compared_without_surrounding_whitespace(ruleset):
    # The agent strips the name before using it as the item, so ' api ' and 'api'
    # would collide at runtime; they must collide here too.
    with pytest.raises(ValidationError):
        ruleset._validate_unique_endpoints(_endpoints({"name": "api"}, {"name": " api "}))


def test_duplicate_endpoint_urls_still_rejected(ruleset):
    with pytest.raises(ValidationError, match="https://same/health"):
        ruleset._validate_unique_endpoints(
            [{"url": "https://same/health"}, {"url": "https://same/health"}]
        )


def test_unique_endpoints_ignores_a_non_list_value(ruleset):
    ruleset._validate_unique_endpoints(None)  # must not raise
    ruleset._validate_unique_endpoints("not a list")  # must not raise


@pytest.mark.parametrize("name", ["X-API-Key", "apikey", "PRIVATE-TOKEN", "X_Api_Key1"])
def test_valid_api_key_header_names_pass(ruleset, name):
    ruleset._validate_header_name(name)  # must not raise


@pytest.mark.parametrize("bad", ["", "X Api Key", "X-Api-Key:", "Ä-Key", "key\n"])
def test_invalid_api_key_header_names_rejected(ruleset, bad):
    # requests would fail deep inside the agent, where the user sees an
    # unreachable endpoint rather than the field that needs fixing.
    with pytest.raises(ValidationError):
        ruleset._validate_header_name(bad)


@pytest.mark.parametrize("name", ["api_key", "apikey", "token", "x.key"])
def test_valid_query_parameter_names_pass(ruleset, name):
    ruleset._validate_query_parameter(name)  # must not raise


@pytest.mark.parametrize("bad", ["", "api key", "a=b", "a&b", "a?b", "a#b"])
def test_invalid_query_parameter_names_rejected(ruleset, bad):
    with pytest.raises(ValidationError):
        ruleset._validate_query_parameter(bad)


def test_api_key_header_may_not_be_configured_twice(ruleset):
    # The clear-text copy this feature exists to remove must not be left behind
    # next to the password-store one - and which of the two wins is an
    # implementation detail either way.
    with pytest.raises(ValidationError, match="X-API-Key"):
        ruleset._validate_endpoint(
            {
                "url": "https://x/health",
                "auth": ("auth_header", {"header": "X-API-Key", "key": ("password", "s")}),
                "headers": [{"name": "x-api-key", "value": "leaked"}],
            }
        )


def test_api_key_header_alongside_an_unrelated_header_is_fine(ruleset):
    ruleset._validate_endpoint(  # must not raise
        {
            "url": "https://x/health",
            "auth": ("auth_header", {"header": "X-API-Key", "key": ("password", "s")}),
            "headers": [{"name": "Accept", "value": "application/json"}],
        }
    )


def test_endpoint_validation_ignores_other_auth_modes(ruleset):
    ruleset._validate_endpoint(  # must not raise
        {
            "url": "https://x/health",
            "auth": ("auth_token", {"token": ("password", "s")}),
            "headers": [{"name": "Authorization", "value": "whatever"}],
        }
    )
    ruleset._validate_endpoint(None)  # must not raise


@pytest.mark.parametrize(
    "template",
    ["{message}", "{message} (leader {leader})", "plain text", "", "{a.b['c.d']}"],
)
def test_valid_summary_templates_pass(ruleset, template):
    ruleset._validate_summary(template)  # must not raise


@pytest.mark.parametrize("bad", ["{unclosed", "closed}", "{}", "{a{b}}", "{ }"])
def test_invalid_summary_templates_rejected(ruleset, bad):
    # A stray brace would otherwise become a placeholder that silently never
    # renders.
    with pytest.raises(ValidationError):
        ruleset._validate_summary(bad)


@pytest.mark.parametrize(
    "node",
    ["software.applications.json_api", "hardware.system", "networking.interfaces.by_name"],
)
def test_valid_inventory_nodes_pass(ruleset, node):
    ruleset._validate_inventory_node(node)  # must not raise


@pytest.mark.parametrize(
    "bad",
    [
        "software",  # a single segment is not a node
        "Software.Apps",  # the tree is lower-case
        "software..apps",  # empty segment
        "software.apps-1",  # '-' is not allowed in a segment
        "custom.stuff",  # not one of the three tree roots
        "",
    ],
)
def test_invalid_inventory_nodes_rejected(ruleset, bad):
    # A typo produces a malformed tree node that is awkward to clean up per host,
    # so it has to fail in Setup.
    with pytest.raises(ValidationError):
        ruleset._validate_inventory_node(bad)


def test_inventory_key_is_optional_but_validated(ruleset):
    ruleset._validate_inventory_key("")  # must not raise - defaults from the path
    ruleset._validate_inventory_key("build_id")  # must not raise
    with pytest.raises(ValidationError):
        ruleset._validate_inventory_key("Build Id")


def test_inventory_form_has_the_expected_keys(ruleset):
    form = ruleset._extraction().elements["inventory"].parameter_form
    assert set(form.elements) == {"node", "key", "keep_service"}
