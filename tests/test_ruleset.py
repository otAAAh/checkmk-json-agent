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


@pytest.mark.parametrize("name", ["used", "json_api_bytes_root", "x", "0disk", "A_b_9"])
def test_valid_metric_names_pass(ruleset, name):
    # 'json_api_bytes_root' is fine: it is not one of the DECLARED metrics, only
    # a name in the same shape. Only the declared set is reserved.
    ruleset._validate_metric_name(name)  # must not raise


@pytest.mark.parametrize("name", ["json_api_value", "json_api_bytes", "json_api_age"])
def test_the_plugins_own_metrics_are_reserved(ruleset, name):
    # Taking a declared name does not just label the field, it inherits that
    # metric's unit, colour and Perf-O-Meter - a percentage named
    # 'json_api_bytes' renders as bytes.
    with pytest.raises(ValidationError, match="own metrics"):
        ruleset._validate_metric_name(name)


def test_the_reserved_names_are_exactly_the_declared_metrics(ruleset, graphing):
    declared = {getattr(graphing, n).name for n in dir(graphing) if n.startswith("metric_")}
    assert declared == ruleset._DECLARED_METRICS


def test_the_rulesets_slug_matches_the_checks(ruleset, check):
    for line in ["Root used", "Root-used", "CPU / core 0", "  ...  ", "Größe", "a"]:
        assert ruleset._metric_slug(line) == check._metric_slug(line), line


@pytest.mark.parametrize(
    "entry",
    [
        {"service": "Root used"},
        {"service": "Root used", "unit": "bytes"},
        {"service": "Root used", "unit": "percent"},
        {"service": "Root used", "unit": "count", "value_as": ["counter", None]},
        {"service": "Root used", "value_as": ["counter", None]},
        {"service": "Root used", "value_as": ["timestamp", {"format": "auto"}]},
        {"service": "Root used", "unit": "seconds", "value_as": ["timestamp", {"format": "auto"}]},
    ],
)
def test_the_rulesets_metric_base_matches_the_checks(ruleset, check, entry):
    # The ruleset predicts the metric name from the rule so it can reject a
    # collision at config time; the check derives the same name at runtime. If
    # the two drift, Setup accepts a pair the check then has to disambiguate
    # positionally - the very thing the validator exists to prevent.
    section = _check_section(check, entry)
    (item,) = section.items["Cap"]
    expected = check._line_metric_name(check._emitted_metric_name(item), item.label)
    assert ruleset._field_metric_name({**entry, "group": "Cap"}) == expected


def _check_section(check, entry):
    import json

    payload = {
        "url": "u",
        "error": None,
        "host_labels": {},
        "endpoints": [],
        "results": [
            {
                "service": "Cap",
                "label": entry["service"],
                "path": "p",
                "found": True,
                "value": 1,
                "error": None,
                "levels_upper": None,
                "levels_lower": None,
                "expected": None,
                "unit": entry.get("unit"),
                "value_as": entry.get("value_as"),
                "metric_name": entry.get("metric_name"),
            }
        ],
    }
    return check.parse_json_api([[json.dumps(payload)]])


def _extractions(*entries):
    return list(entries)


def test_two_fields_of_one_group_that_slug_alike_are_rejected(ruleset):
    with pytest.raises(ValidationError, match="differ by more than punctuation"):
        ruleset._validate_unique_group_metrics(
            _extractions(
                {"service": "Root used", "group": "Disks", "unit": "bytes", "path": "a"},
                {"service": "Root-used", "group": "Disks", "unit": "bytes", "path": "b"},
            )
        )


def test_the_same_slug_with_a_different_unit_is_fine(ruleset):
    # 'json_api_bytes_root_used' and 'json_api_count_root_used' are two names;
    # rejecting this pair would break configurations that work today.
    ruleset._validate_unique_group_metrics(
        _extractions(
            {"service": "Root used", "group": "Disks", "unit": "bytes", "path": "a"},
            {"service": "Root-used", "group": "Disks", "unit": "count", "path": "b"},
        )
    )


def test_the_same_slug_in_different_groups_is_fine(ruleset):
    # Metric names only have to be unique WITHIN a service.
    ruleset._validate_unique_group_metrics(
        _extractions(
            {"service": "Root used", "group": "Disks", "unit": "bytes", "path": "a"},
            {"service": "Root-used", "group": "Volumes", "unit": "bytes", "path": "b"},
        )
    )


def test_ungrouped_fields_never_collide(ruleset):
    # Each owns its service, and a duplicated service name is disambiguated as
    # a service, not as a metric.
    ruleset._validate_unique_group_metrics(
        _extractions(
            {"service": "Root used", "unit": "bytes", "path": "a"},
            {"service": "Root-used", "unit": "bytes", "path": "b"},
        )
    )


def test_an_explicit_metric_name_resolves_the_collision(ruleset):
    ruleset._validate_unique_group_metrics(
        _extractions(
            {"service": "Root used", "group": "Disks", "unit": "bytes", "path": "a"},
            {
                "service": "Root-used",
                "group": "Disks",
                "unit": "bytes",
                "metric_name": "root_used_data",
                "path": "b",
            },
        )
    )


def test_two_fields_stating_the_same_metric_name_are_rejected(ruleset):
    with pytest.raises(ValidationError, match="differ by more than punctuation"):
        ruleset._validate_unique_group_metrics(
            _extractions(
                {"service": "A", "group": "Disks", "metric_name": "used", "path": "a"},
                {"service": "B", "group": "Disks", "metric_name": "used", "path": "b"},
            )
        )


def test_the_rejection_names_the_fields_and_the_metric(ruleset):
    with pytest.raises(ValidationError) as exc:
        ruleset._validate_unique_group_metrics(
            _extractions(
                {"service": "Root used", "group": "Disks", "unit": "bytes", "path": "a"},
                {"service": "Root-used", "group": "Disks", "unit": "bytes", "path": "b"},
            )
        )
    text = str(exc.value)
    assert "Root used" in text and "Root-used" in text
    assert "Disks" in text and "json_api_bytes_root_used" in text


@pytest.mark.parametrize(
    "name",
    [
        "",
        "_leading",  # must start with a letter or a digit
        "root used",  # no spaces
        "root-used",  # no dashes
        "root.used",
        "größe",  # Checkmk's own pattern is ASCII
    ],
)
def test_invalid_metric_names_rejected(ruleset, name):
    # The pattern is Checkmk's own (cmk.gui.graphing MetricName): anything it
    # would refuse must be refused here, or the name an operator types into a
    # Gauge widget could never match the one the check emits.
    with pytest.raises(ValidationError):
        ruleset._validate_metric_name(name)


def test_piggyback_labels_need_a_piggyback_host(ruleset):
    ruleset._validate_extraction(
        {"piggyback_host": "name", "piggyback_labels": [{"path": "region"}]}
    )
    with pytest.raises(ValidationError, match="Create one host per element"):
        ruleset._validate_extraction({"piggyback_labels": [{"path": "region"}]})
    with pytest.raises(ValidationError, match="Create one host per element"):
        ruleset._validate_extraction(
            {"piggyback_host": "  ", "piggyback_labels": [{"path": "region"}]}
        )


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


def test_migrate_extraction_degrades_instead_of_crashing_the_form(ruleset):
    """Regression for #161: emptying a required field and saving hands the
    migrate a None on the RE-RENDER. Raising there takes out the whole Setup
    page (TypeError: Unexpected extraction value: None) instead of highlighting
    the offending box, so a non-dict must degrade to an empty extraction."""
    for broken in (None, "", [], 0, "service"):
        assert ruleset._migrate_extraction(broken) == {}


def test_migrate_to_endpoints_degrades_instead_of_crashing_the_form(ruleset):
    """The top-level migrate runs on the same render path, so it degrades too."""
    for broken in (None, "", [], 0):
        assert ruleset._migrate_to_endpoints(broken) == {"endpoints": []}


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
        "group",
        "path",
        "label_path",
        "piggyback_host",
        "piggyback_labels",
        "labels",
        "aggregate",
        "filter",
        "value_as",
        "unit",
        "metric_name",
        "value_range",
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


def test_endpoint_form_offers_the_service_prefix(ruleset):
    element = ruleset._endpoint().elements["service_prefix"]
    # Required with a False prefill, like the other endpoint toggles: the form
    # always shows it (that is how it gets discovered) and an existing rule that
    # predates it keeps its plain service names.
    assert element.required is True
    assert element.parameter_form.prefill.value is False


def test_service_prefix_without_an_endpoint_name_is_rejected(ruleset):
    # It would be a silent no-op: the agent never falls back to the URL.
    with pytest.raises(ValidationError):
        ruleset._validate_endpoint({"url": "http://x", "service_prefix": True})
    with pytest.raises(ValidationError):
        ruleset._validate_endpoint({"url": "http://x", "service_prefix": True, "name": "  "})


def test_service_prefix_with_a_name_passes(ruleset):
    ruleset._validate_endpoint({"url": "http://x", "service_prefix": True, "name": "app1"})
    # And an endpoint that does not prefix needs no name at all.
    ruleset._validate_endpoint({"url": "http://x", "service_prefix": False})


def test_report_raw_response_form_has_the_expected_keys(ruleset):
    form = ruleset._endpoint().elements["show_response"].parameter_form
    assert set(form.elements) == {"max_bytes", "headers"}
    # Reporting the body is the point of the option, so a size is required; the
    # headers come along by default because they are small and informative.
    assert form.elements["max_bytes"].parameter_form.prefill.value == 2048
    assert form.elements["headers"].parameter_form.prefill.value is True


def test_reporting_the_raw_response_is_optional(ruleset):
    assert ruleset._endpoint().elements["show_response"].required is False


def test_extraction_offers_a_shared_service(ruleset):
    element = ruleset._extraction().elements["group"]
    # Optional: the default stays one service per field.
    assert element.required is False


def test_an_inventory_only_field_cannot_join_a_shared_service(ruleset):
    # It creates no service at all, so there is no line to contribute - silently
    # ignoring one half of that would be worse than rejecting it.
    with pytest.raises(ValidationError):
        ruleset._validate_extraction(
            {
                "service": "Version",
                "path": "version",
                "group": "Health",
                "inventory": {"node": "software.applications.x"},
            }
        )


def test_an_inventory_field_that_keeps_its_service_may_join_one(ruleset):
    ruleset._validate_extraction(
        {
            "service": "Version",
            "path": "version",
            "group": "Health",
            "inventory": {"node": "software.applications.x", "keep_service": True},
        }
    )
    # ... and so may an ordinary field.
    ruleset._validate_extraction({"service": "Status", "path": "status", "group": "Health"})


def test_host_label_form_offers_a_filter_and_a_literal_value(ruleset):
    form = ruleset._endpoint().elements["host_labels"].parameter_form.element_template
    assert set(form.elements) == {"path", "key", "value_field", "value", "filter"}
    # The predicate is the same three fields an extraction's filter uses.
    assert set(form.elements["filter"].parameter_form.elements) == {"path", "op", "value"}
    # A path is no longer required: a condition plus a literal value describes a
    # label on its own.
    assert form.elements["path"].required is False


def test_piggyback_label_form_offers_a_filter_and_a_literal_value(ruleset):
    form = ruleset._extraction().elements["piggyback_labels"].parameter_form.element_template
    assert set(form.elements) == {"path", "key", "value", "filter"}


def test_a_label_without_a_path_needs_a_key_and_a_literal_value(ruleset):
    with pytest.raises(ValidationError):
        ruleset._validate_label_spec({"value": "yes"})  # no key
    with pytest.raises(ValidationError):
        ruleset._validate_label_spec({"key": "MyApp"})  # no value
    ruleset._validate_label_spec({"key": "MyApp", "value": "yes"})
    # With a path both halves can be derived, so nothing else is needed.
    ruleset._validate_label_spec({"path": "services[*]"})


def test_a_host_label_takes_its_value_from_one_place_only(ruleset):
    with pytest.raises(ValidationError):
        ruleset._validate_host_label({"path": "c[*]", "value": "yes", "value_field": "status"})
    ruleset._validate_host_label({"path": "c[*]", "value_field": "status"})
    ruleset._validate_host_label({"path": "c[*]", "value": "yes"})


def test_field_context_form_has_the_expected_keys(ruleset):
    form = ruleset._endpoint().elements["field_context"].parameter_form
    assert set(form.elements) == {"source", "max_bytes"}
    # The targeted form is the default: the element the value came from, not the
    # whole body repeated on every field service.
    assert form.elements["source"].parameter_form.prefill.value == "element"
    assert [e.name for e in form.elements["source"].parameter_form.elements] == [
        "element",
        "response",
    ]
    # Smaller than the raw response's 2048: this text is stored once per FIELD.
    assert form.elements["max_bytes"].parameter_form.prefill.value == 1024


def test_reporting_the_field_context_is_optional(ruleset):
    assert ruleset._endpoint().elements["field_context"].required is False


def test_pagination_form_has_the_expected_keys(ruleset):
    form = ruleset._endpoint().elements["pagination"].parameter_form
    assert set(form.elements) == {"next", "items", "max_pages", "max_elements"}
    # Both halves are needed to follow anything, so both are required; the caps
    # have a default (the page one) and are optional (the element one).
    assert [key for key, el in form.elements.items() if el.required] == [
        "next",
        "items",
        "max_pages",
    ]
    assert [e.name for e in form.elements["next"].parameter_form.elements] == [
        "body",
        "link_header",
    ]
    assert form.elements["next"].parameter_form.prefill.value == "body"
    assert form.elements["max_pages"].parameter_form.prefill.value == 10


def test_following_pagination_is_optional(ruleset):
    assert ruleset._endpoint().elements["pagination"].required is False


def test_the_paths_of_a_pagination_setting_name_single_places(ruleset):
    # The collection to merge is ONE container and the next link is ONE URL, so a
    # '[*]' in either cannot mean anything - rejected in Setup rather than
    # becoming an endpoint that reports "no collection at ..." at runtime.
    with pytest.raises(ValidationError, match="without a '\\[\\*\\]' wildcard"):
        ruleset._validate_pagination({"next": ("body", "links.next"), "items": "data.items[*]"})
    with pytest.raises(ValidationError, match="must not contain a"):
        ruleset._validate_pagination({"next": ("body", "pages[*].next"), "items": "items"})
    ruleset._validate_pagination({"next": ("body", "links.next"), "items": "data.items"})
    ruleset._validate_pagination({"next": ("link_header", None), "items": "$"})


def test_a_value_range_needs_at_least_one_end(ruleset):
    ruleset._validate_value_range({"min": 0.0, "max": 100.0})  # must not raise
    ruleset._validate_value_range({"max": 100.0})  # must not raise

    with pytest.raises(ValidationError):
        ruleset._validate_value_range({})
    with pytest.raises(ValidationError):
        ruleset._validate_value_range({"min": None, "max": None})


def test_a_value_range_must_run_upwards(ruleset):
    """A reversed range would be accepted by the metric and then draw a graph
    nobody can read, so it is refused where the operator can still see why."""
    with pytest.raises(ValidationError):
        ruleset._validate_value_range({"min": 100.0, "max": 0.0})
    with pytest.raises(ValidationError):
        ruleset._validate_value_range({"min": 5.0, "max": 5.0})
