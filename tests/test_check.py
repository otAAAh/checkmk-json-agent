# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the check: parse, discovery, and check states/metrics."""

import json

from cmk.agent_based.v2 import Metric, Result, State


def _section(check, results, host_labels=None):
    payload = {"url": "u", "error": None, "results": results, "host_labels": host_labels or {}}
    return check.parse_json_api([[json.dumps(payload)]])


def _entry(service, **kw):
    base = {
        "service": service,
        "path": "p",
        "found": True,
        "value": None,
        "error": None,
        "levels_upper": None,
        "levels_lower": None,
        "expected": None,
    }
    base.update(kw)
    return base


def test_coerce_levels(check):
    assert check._coerce_levels(["fixed", [5.0, 10.0]]) == ("fixed", (5.0, 10.0))
    assert check._coerce_levels(["no_levels", None]) is None
    assert check._coerce_levels(None) is None


def test_discovery_lists_every_service(check):
    section = _section(check, [_entry("A"), _entry("B")])
    assert sorted(s.item for s in check.discover_json_api(section)) == ["A", "B"]


def test_check_string_expected_ok_and_crit(check):
    section = _section(
        check,
        [
            _entry("Up", value="UP", expected="UP"),
            _entry("Down", value="DOWN", expected="UP"),
        ],
    )
    ok = list(check.check_json_api("Up", {}, section))[0]
    assert ok.state == State.OK

    crit = list(check.check_json_api("Down", {}, section))[0]
    assert crit.state == State.CRIT


def test_check_numeric_levels_and_metric(check):
    section = _section(check, [_entry("Conns", value=7, levels_upper=["fixed", [5.0, 10.0]])])
    results = list(check.check_json_api("Conns", {}, section))
    # Exclude the details-only context result (empty summary) from the state check.
    states = [r.state for r in results if isinstance(r, Result) and r.summary]
    metrics = [r for r in results if isinstance(r, Metric)]
    assert states == [State.WARN]
    assert metrics and metrics[0].name == "json_api_value"
    assert metrics[0].value == 7.0


def test_check_plain_numeric_value_emits_metric(check):
    section = _section(check, [_entry("Count", value=42)])
    results = list(check.check_json_api("Count", {}, section))
    assert any(isinstance(r, Metric) and r.value == 42.0 for r in results)


def test_metric_name_maps_unit_to_metric(check):
    assert check._metric_name(None) == "json_api_value"
    assert check._metric_name("count") == "json_api_count"
    assert check._metric_name("bytes") == "json_api_bytes"
    assert check._metric_name("seconds") == "json_api_seconds"
    assert check._metric_name("percent") == "json_api_percent"
    # Unknown/garbage units fall back to the default metric.
    assert check._metric_name("nonsense") == "json_api_value"


def test_check_unit_names_the_metric_with_levels(check):
    section = _section(
        check, [_entry("Mem", value=2048, unit="bytes", levels_upper=["fixed", [5.0, 10.0]])]
    )
    (metric,) = [r for r in check.check_json_api("Mem", {}, section) if isinstance(r, Metric)]
    assert metric.name == "json_api_bytes"
    assert metric.value == 2048.0


def test_check_unit_names_the_metric_without_levels(check):
    section = _section(check, [_entry("Latency", value=0.5, unit="seconds")])
    (metric,) = [r for r in check.check_json_api("Latency", {}, section) if isinstance(r, Metric)]
    assert metric.name == "json_api_seconds"
    assert metric.value == 0.5


def _value_summary(check, name, section):
    """The single 'Value: ...' summary line (skipping the details-only context)."""
    return next(
        r.summary
        for r in check.check_json_api(name, {}, section)
        if isinstance(r, Result) and r.summary.startswith("Value:")
    )


def test_check_unit_renders_value_in_summary_without_levels(check):
    # 1.5 MiB should read as "1.50 MiB", not the raw byte count.
    section = _section(check, [_entry("Mem", value=1572864, unit="bytes")])
    assert "MiB" in _value_summary(check, "Mem", section)


def test_check_unit_renders_value_in_summary_with_levels(check):
    # The levels line's value is rendered with the unit too (via check_levels).
    section = _section(
        check,
        [_entry("Mem", value=1572864, unit="bytes", levels_upper=["fixed", [5.0, 10.0]])],
    )
    assert "MiB" in _value_summary(check, "Mem", section)


def test_check_percent_unit_renders_with_symbol(check):
    section = _section(check, [_entry("Load", value=42.5, unit="percent")])
    assert "%" in _value_summary(check, "Load", section)


def test_check_without_unit_shows_raw_number(check):
    # No unit: the summary keeps the plain number (no renderer applied).
    section = _section(check, [_entry("Raw", value=1572864)])
    assert _value_summary(check, "Raw", section) == "Value: 1572864"


def test_discovery_attaches_namespaced_service_labels(check):
    section = _section(
        check,
        [_entry("Node alpha", labels=[{"key": "name", "value": "alpha"}])],
    )
    (service,) = list(check.discover_json_api(section))
    assert [(lab.name, lab.value) for lab in service.labels] == [("json_api/name", "alpha")]


def test_discovery_without_labels_yields_no_labels(check):
    section = _section(check, [_entry("Plain", value="ok")])
    (service,) = list(check.discover_json_api(section))
    assert list(service.labels) == []


def test_host_labels_from_payload_are_namespaced(check):
    # Host labels ride on the payload, not on any service.
    section = _section(check, [_entry("A")], host_labels={"env": "prod", "region": "eu"})
    assert {lab.name: lab.value for lab in check.host_label_json_api(section)} == {
        "json_api/env": "prod",
        "json_api/region": "eu",
    }


def test_host_labels_independent_of_services(check):
    # No services at all, but host labels still emitted (decoupled).
    section = _section(check, [], host_labels={"version": "2.4.0"})
    assert list(check.discover_json_api(section)) == []
    assert {lab.name: lab.value for lab in check.host_label_json_api(section)} == {
        "json_api/version": "2.4.0"
    }


def test_check_invalid_regex_is_unknown_not_crash(check):
    section = _section(check, [_entry("Bad", value="UP", expected="(unclosed")])
    result = list(check.check_json_api("Bad", {}, section))[0]
    assert result.state == State.UNKNOWN
    assert "Invalid match pattern" in result.summary


def test_check_levels_on_non_numeric_warns(check):
    section = _section(
        check, [_entry("Str", value="not-a-number", levels_upper=["fixed", [5.0, 10.0]])]
    )
    result = list(check.check_json_api("Str", {}, section))[0]
    assert result.state == State.WARN
    assert "not numeric" in result.summary


def test_check_levels_on_non_numeric_warns_even_with_expected(check):
    # The expected regex matches, but the meaningless levels config is still
    # surfaced (previously it was hidden behind the OK regex result).
    section = _section(
        check,
        [_entry("Str", value="UP", expected="UP", levels_upper=["fixed", [5.0, 10.0]])],
    )
    result = list(check.check_json_api("Str", {}, section))[0]
    assert result.state == State.WARN
    assert "not numeric" in result.summary


def test_check_failed_match_still_surfaces_misconfigured_levels(check):
    # The regex fails (CRIT) and levels are also misconfigured: the value stays
    # CRIT but the levels-misconfig note is appended, not hidden behind it.
    section = _section(
        check,
        [_entry("Str", value="DOWN", expected="UP", levels_upper=["fixed", [5.0, 10.0]])],
    )
    result = list(check.check_json_api("Str", {}, section))[0]
    assert result.state == State.CRIT
    assert "expected to match 'UP'" in result.summary
    assert "not numeric" in result.summary


def test_as_number_rejects_non_finite(check):
    assert check._as_number("inf") is None
    assert check._as_number("nan") is None
    assert check._as_number("-inf") is None
    assert check._as_number(3) == 3.0
    assert check._as_number("2.5") == 2.5


def test_check_non_finite_value_is_not_treated_as_metric(check):
    # "inf" is not a usable number: no metric, and levels don't apply to it.
    section = _section(check, [_entry("Inf", value="inf")])
    results = list(check.check_json_api("Inf", {}, section))
    assert not any(isinstance(r, Metric) for r in results)


def test_check_boolean_rendered_and_matched_as_json(check):
    # JSON true -> Python True must render/match as "true", not "True".
    section = _section(check, [_entry("Up", value=True, expected="true")])
    result = list(check.check_json_api("Up", {}, section))[0]
    assert result.state == State.OK
    assert result.summary == "Value: true"


def test_check_null_value_rendered_as_json(check):
    section = _section(check, [_entry("Nothing", value=None)])
    result = list(check.check_json_api("Nothing", {}, section))[0]
    assert result.summary == "Value: null"


def test_check_missing_path_is_unknown(check):
    section = _section(
        check, [_entry("Gone", found=False, value=None, error="path not found in response")]
    )
    result = list(check.check_json_api("Gone", {}, section))[0]
    assert result.state == State.UNKNOWN


def _details(results):
    return "\n".join(r.details for r in results if isinstance(r, Result) and r.details)


def test_check_details_include_path_and_source(check):
    section = _section(
        check,
        [_entry("Up", value="UP", expected="UP", path="components.db.status", url="http://x/h")],
    )
    results = list(check.check_json_api("Up", {}, section))
    details = _details(results)
    assert "JSON path: components.db.status" in details
    assert "Source: http://x/h" in details
    assert "Expected pattern: UP" in details
    # The context is details-only: it must not add to the summary line or worsen state.
    assert all(
        r.summary == "" for r in results if isinstance(r, Result) and "JSON path" in r.details
    )


def test_check_details_present_on_missing_path(check):
    section = _section(
        check,
        [
            _entry(
                "Gone",
                found=False,
                value=None,
                error="path not found in response",
                path="a.b.c",
                url="http://x/h",
            )
        ],
    )
    details = _details(check.check_json_api("Gone", {}, section))
    assert "JSON path: a.b.c" in details
    assert "Source: http://x/h" in details


def test_check_no_context_result_when_path_and_url_empty(check):
    # A section built without path/url (e.g. an old agent) must not emit an empty context.
    section = _section(check, [_entry("Plain", value="ok", path="", url="")])
    results = list(check.check_json_api("Plain", {}, section))
    assert len(results) == 1
    assert results[0].summary == "Value: ok"


def test_parse_duplicate_service_names_kept_distinct(check):
    # Defensive backstop: identical service names must not collapse into one.
    section = _section(check, [_entry("Dup", value="a"), _entry("Dup", value="b")])
    assert len(section.items) == 2
    assert "Dup" in section.items
    assert "Dup (2)" in section.items


def test_coerce_match_normalizes_shapes(check):
    # Canonical must_match: dict with pattern + no-match state (defaults CRIT=2).
    assert check._coerce_match(["must_match", {"pattern": "UP"}]) == (
        "must_match",
        {"pattern": "UP", "state_no_match": 2},
    )
    assert check._coerce_match(["must_match", {"pattern": "UP", "state_no_match": 1}]) == (
        "must_match",
        {"pattern": "UP", "state_no_match": 1},
    )
    # A bare string is accepted for hand-written rules/CLI.
    assert check._coerce_match(["must_match", "UP"]) == (
        "must_match",
        {"pattern": "UP", "state_no_match": 2},
    )
    # state_map keeps only real patterns and always carries a no-match state.
    assert check._coerce_match(["state_map", {"ok": "UP", "crit": "DOWN"}]) == (
        "state_map",
        {"ok": "UP", "crit": "DOWN", "state_no_match": 0},
    )
    assert check._coerce_match(["state_map", {"ok": "", "warn": "W", "x": "y"}]) == (
        "state_map",
        {"warn": "W", "state_no_match": 0},
    )
    assert check._coerce_match(None) is None
    # Back-compat: an old section with only the flat 'expected' regex -> CRIT.
    assert check._coerce_match(None, "UP|ok") == (
        "must_match",
        {"pattern": "UP|ok", "state_no_match": 2},
    )


def test_check_must_match_explicit_shape(check):
    section = _section(
        check,
        [
            _entry("Up", value="UP", match=["must_match", {"pattern": "UP"}]),
            _entry("Down", value="DOWN", match=["must_match", {"pattern": "UP"}]),
        ],
    )
    assert list(check.check_json_api("Up", {}, section))[0].state == State.OK
    crit = list(check.check_json_api("Down", {}, section))[0]
    assert crit.state == State.CRIT
    assert "expected to match 'UP'" in crit.summary


def test_check_must_match_no_match_state_is_configurable(check):
    # The ARD-style "not-match -> WARN" instead of the CRIT default.
    section = _section(
        check,
        [
            _entry(
                "Down", value="DOWN", match=["must_match", {"pattern": "UP", "state_no_match": 1}]
            )
        ],
    )
    result = list(check.check_json_api("Down", {}, section))[0]
    assert result.state == State.WARN
    assert "expected to match 'UP'" in result.summary


def test_check_state_map_first_match_wins(check):
    # OK -> WARN -> CRIT order; 'degraded' matches WARN.
    match = ["state_map", {"ok": "ready", "warn": "degraded", "crit": "failed"}]
    section = _section(
        check,
        [
            _entry("Ready", value="ready", match=match),
            _entry("Degraded", value="degraded", match=match),
            _entry("Failed", value="failed", match=match),
        ],
    )
    assert list(check.check_json_api("Ready", {}, section))[0].state == State.OK
    warn = list(check.check_json_api("Degraded", {}, section))[0]
    assert warn.state == State.WARN
    assert "matched WARN" in warn.summary
    assert list(check.check_json_api("Failed", {}, section))[0].state == State.CRIT


def test_check_state_map_no_match_defaults_ok(check):
    section = _section(
        check,
        [_entry("Mode", value="maintenance", match=["state_map", {"crit": "^failed$"}])],
    )
    result = list(check.check_json_api("Mode", {}, section))[0]
    assert result.state == State.OK
    assert result.summary == "Value: maintenance"


def test_check_state_map_no_match_state_is_configurable(check):
    section = _section(
        check,
        [_entry("Mode", value="weird", match=["state_map", {"ok": "good", "state_no_match": 2}])],
    )
    result = list(check.check_json_api("Mode", {}, section))[0]
    assert result.state == State.CRIT
    assert "no pattern matched" in result.summary


def test_check_state_map_details_list_patterns(check):
    section = _section(
        check,
        [_entry("Mode", value="ok", match=["state_map", {"ok": "ok", "crit": "bad"}], path="m")],
    )
    details = _details(check.check_json_api("Mode", {}, section))
    assert "State map: OK /ok/, CRIT /bad/" in details


def test_check_legacy_expected_section_still_evaluated(check):
    # A section produced by a pre-'match' agent (only 'expected') must still work.
    section = _section(check, [_entry("Up", value="DOWN", expected="UP")])
    result = list(check.check_json_api("Up", {}, section))[0]
    assert result.state == State.CRIT
    assert "expected to match 'UP'" in result.summary


def test_apply_calc_arithmetic(check):
    assert check._apply_calc(1048576.0, "value / 1024 / 1024") == 1.0
    assert check._apply_calc(0.5, "value * 1000") == 500.0
    assert check._apply_calc(212.0, "(value - 32) * 5 / 9") == 100.0
    assert check._apply_calc(3.0, "-value") == -3.0


def test_apply_calc_rejects_non_arithmetic(check):
    # No names other than 'value', no calls, no attribute access, no power op.
    import pytest

    for expr in ("foo", "__import__('os')", "value.bit_length()", "value ** 2", "abs(value)"):
        with pytest.raises((ValueError, ZeroDivisionError)):
            check._apply_calc(2.0, expr)


def test_check_calc_transforms_value_and_metric(check):
    section = _section(
        check, [_entry("Mem", value=1048576, unit="bytes", calc="value / 1024 / 1024")]
    )
    results = list(check.check_json_api("Mem", {}, section))
    (metric,) = [r for r in results if isinstance(r, Metric)]
    assert metric.value == 1.0
    summary = next(r.summary for r in results if isinstance(r, Result) and r.summary)
    # The unit renders the *transformed* number (what the metric records), so the
    # summary reads the same as the graph: render.bytes(1.0) -> "1 B".
    assert summary == "Value: 1 B"


def test_check_calc_applies_before_levels(check):
    # 90000 ms -> 90 s, which trips the upper levels set in seconds.
    section = _section(
        check,
        [_entry("Lat", value=90000, calc="value / 1000", levels_upper=["fixed", [60.0, 120.0]])],
    )
    result = next(
        r for r in check.check_json_api("Lat", {}, section) if isinstance(r, Result) and r.summary
    )
    assert result.state == State.WARN


def test_check_calc_divide_by_zero_is_unknown(check):
    section = _section(check, [_entry("Bad", value=5, calc="value / 0")])
    result = list(check.check_json_api("Bad", {}, section))[0]
    assert result.state == State.UNKNOWN
    assert "Calculation" in result.summary


def test_check_calc_ignored_on_non_numeric(check):
    # A calc on a string value simply does not apply; the value shows as-is.
    section = _section(check, [_entry("S", value="hello", calc="value * 2")])
    result = list(check.check_json_api("S", {}, section))[0]
    assert result.summary == "Value: hello"


def test_check_blank_calc_is_no_transform_not_crash(check):
    # The ruleset accepts a blank/whitespace calc as "no transform". A truthy
    # whitespace string must not reach ast.parse (which would raise an uncaught
    # SyntaxError and crash the check) - it is normalized away at parse time.
    section = _section(check, [_entry("N", value=5, calc="   ")])
    results = list(check.check_json_api("N", {}, section))
    (metric,) = [r for r in results if isinstance(r, Metric)]
    assert metric.value == 5.0
    summary = next(r.summary for r in results if isinstance(r, Result) and r.summary)
    assert summary == "Value: 5"


def test_check_api_error_is_crit(check):
    # A section-level error surfaces as CRIT on any discovered item.
    items = _section(check, [_entry("X", value="1")]).items
    section = check.Section(error="boom", items=items)
    (result,) = list(check.check_json_api("X", {}, section))
    assert result.state == State.CRIT
    assert "boom" in result.summary


# --- check-parameters ruleset: discovery seeds defaults, rule overrides them ---


def test_plugin_wires_check_ruleset(check):
    # The plugin must advertise the ruleset so the rule's values reach the check.
    assert check.check_plugin_json_api.check_ruleset_name == "json_api"


def test_discovery_seeds_parameters_from_section(check):
    # The thresholds / match configured in the agent rule become the service's
    # discovered defaults; a field with neither carries no parameters.
    section = _section(
        check,
        [
            _entry("Conns", value=7, levels_upper=["fixed", [5.0, 10.0]]),
            _entry("Up", value="UP", match=["must_match", {"pattern": "UP"}]),
            _entry("Plain", value="x"),
        ],
    )
    services = {s.item: s.parameters for s in check.discover_json_api(section)}
    assert services["Conns"] == {"levels_upper": ("fixed", (5.0, 10.0))}
    assert services["Up"]["match"][0] == "must_match"
    assert services["Plain"] == {}


def test_check_levels_param_overrides_section(check):
    # Section carries 5/10 (so 7 would WARN); the rule raises them to 100/200.
    section = _section(check, [_entry("Conns", value=7, levels_upper=["fixed", [5.0, 10.0]])])
    params = {"levels_upper": ("fixed", (100.0, 200.0))}
    result = next(
        r
        for r in check.check_json_api("Conns", params, section)
        if isinstance(r, Result) and r.summary
    )
    assert result.state == State.OK


def test_check_params_no_levels_removes_section_levels(check):
    # An explicit "no_levels" in the rule wins over the section's levels: the key
    # is present, so the check does NOT fall back to the section value.
    section = _section(check, [_entry("Conns", value=7, levels_upper=["fixed", [5.0, 10.0]])])
    params = {"levels_upper": ("no_levels", None)}
    result = next(
        r
        for r in check.check_json_api("Conns", params, section)
        if isinstance(r, Result) and r.summary
    )
    assert result.state == State.OK


def test_check_absent_param_key_falls_back_to_section(check):
    # A rule that only sets 'match' must NOT wipe the section's levels: the
    # levels_upper key is absent from params, so it falls back to the section.
    section = _section(check, [_entry("Conns", value=7, levels_upper=["fixed", [5.0, 10.0]])])
    params = {"match": ("must_match", {"pattern": "x"})}
    result = next(
        r
        for r in check.check_json_api("Conns", params, section)
        if isinstance(r, Result) and r.summary
    )
    assert result.state == State.WARN  # 7 still trips the section's 5/10


def test_check_match_param_overrides_section(check):
    # Section expects 'UP' (DOWN -> CRIT); the rule flips it to expect 'DOWN'.
    section = _section(check, [_entry("Mode", value="DOWN", expected="UP")])
    params = {"match": ("must_match", {"pattern": "DOWN"})}
    result = list(check.check_json_api("Mode", params, section))[0]
    assert result.state == State.OK


def test_check_details_reflect_effective_match_override(check):
    # The details show the effective (overridden) pattern, not the agent's.
    section = _section(check, [_entry("Mode", value="DOWN", expected="UP", path="m")])
    params = {"match": ("must_match", {"pattern": "DOWN"})}
    details = _details(check.check_json_api("Mode", params, section))
    assert "Expected pattern: DOWN" in details
