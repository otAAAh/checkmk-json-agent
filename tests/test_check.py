# Copyright (C) 2026 checkmk-json-agent contributors
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


def test_check_missing_path_is_unknown(check):
    section = _section(
        check, [_entry("Gone", found=False, value=None, error="path not found in response")]
    )
    (result,) = list(check.check_json_api("Gone", section))
    assert result.state == State.UNKNOWN


def test_check_api_error_is_crit(check):
    # A section-level error surfaces as CRIT on any discovered item.
    items = _section(check, [_entry("X", value="1")]).items
    section = check.Section(error="boom", items=items)
    (result,) = list(check.check_json_api("X", section))
    assert result.state == State.CRIT
    assert "boom" in result.summary
