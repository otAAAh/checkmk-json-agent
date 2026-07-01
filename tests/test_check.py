# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the check: parse, discovery, and check states/metrics."""

import json

from cmk.agent_based.v2 import Metric, Result, State


def _section(check, results):
    payload = {"url": "u", "error": None, "results": results}
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
    (ok,) = list(check.check_json_api("Up", section))
    assert ok.state == State.OK

    (crit,) = list(check.check_json_api("Down", section))
    assert crit.state == State.CRIT


def test_check_numeric_levels_and_metric(check):
    section = _section(check, [_entry("Conns", value=7, levels_upper=["fixed", [5.0, 10.0]])])
    results = list(check.check_json_api("Conns", section))
    states = [r.state for r in results if isinstance(r, Result)]
    metrics = [r for r in results if isinstance(r, Metric)]
    assert states == [State.WARN]
    assert metrics and metrics[0].name == "json_api_value"
    assert metrics[0].value == 7.0


def test_check_plain_numeric_value_emits_metric(check):
    section = _section(check, [_entry("Count", value=42)])
    results = list(check.check_json_api("Count", section))
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
    (metric,) = [r for r in check.check_json_api("Mem", section) if isinstance(r, Metric)]
    assert metric.name == "json_api_bytes"
    assert metric.value == 2048.0


def test_check_unit_names_the_metric_without_levels(check):
    section = _section(check, [_entry("Latency", value=0.5, unit="seconds")])
    (metric,) = [r for r in check.check_json_api("Latency", section) if isinstance(r, Metric)]
    assert metric.name == "json_api_seconds"
    assert metric.value == 0.5


def test_check_invalid_regex_is_unknown_not_crash(check):
    section = _section(check, [_entry("Bad", value="UP", expected="(unclosed")])
    (result,) = list(check.check_json_api("Bad", section))
    assert result.state == State.UNKNOWN
    assert "Invalid expected pattern" in result.summary


def test_check_levels_on_non_numeric_warns(check):
    section = _section(
        check, [_entry("Str", value="not-a-number", levels_upper=["fixed", [5.0, 10.0]])]
    )
    (result,) = list(check.check_json_api("Str", section))
    assert result.state == State.WARN
    assert "not numeric" in result.summary


def test_check_boolean_rendered_and_matched_as_json(check):
    # JSON true -> Python True must render/match as "true", not "True".
    section = _section(check, [_entry("Up", value=True, expected="true")])
    (result,) = list(check.check_json_api("Up", section))
    assert result.state == State.OK
    assert result.summary == "Value: true"


def test_check_null_value_rendered_as_json(check):
    section = _section(check, [_entry("Nothing", value=None)])
    (result,) = list(check.check_json_api("Nothing", section))
    assert result.summary == "Value: null"


def test_check_missing_path_is_unknown(check):
    section = _section(
        check, [_entry("Gone", found=False, value=None, error="path not found in response")]
    )
    (result,) = list(check.check_json_api("Gone", section))
    assert result.state == State.UNKNOWN


def test_parse_duplicate_service_names_kept_distinct(check):
    # Defensive backstop: identical service names must not collapse into one.
    section = _section(check, [_entry("Dup", value="a"), _entry("Dup", value="b")])
    assert len(section.items) == 2
    assert "Dup" in section.items
    assert "Dup (2)" in section.items


def test_check_api_error_is_crit(check):
    # A section-level error surfaces as CRIT on any discovered item.
    items = _section(check, [_entry("X", value="1")]).items
    section = check.Section(error="boom", items=items)
    (result,) = list(check.check_json_api("X", section))
    assert result.state == State.CRIT
    assert "boom" in result.summary
