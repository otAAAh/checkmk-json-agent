# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the metric definitions and their consistency with the check."""


def _metric_names(graphing):
    return {getattr(graphing, name).name for name in dir(graphing) if name.startswith("metric_")}


def test_expected_metrics_are_defined(graphing):
    assert _metric_names(graphing) == {
        "json_api_value",
        "json_api_count",
        "json_api_bytes",
        "json_api_seconds",
        "json_api_percent",
    }


def test_every_unit_metric_has_a_definition(check, graphing):
    # The check maps units to metric names; each of those must be a real metric.
    assert set(check._UNIT_METRIC.values()) <= _metric_names(graphing)
