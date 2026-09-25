# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the metric definitions and their consistency with the check."""

import pytest
from cmk.rulesets.v1.form_specs.validators import ValidationError


def _metric_names(graphing):
    return {getattr(graphing, name).name for name in dir(graphing) if name.startswith("metric_")}


def test_expected_metrics_are_defined(graphing):
    assert _metric_names(graphing) == {
        "json_api_value",
        "json_api_count",
        "json_api_bytes",
        "json_api_seconds",
        "json_api_percent",
        # Per-second rates of a counter field: one per unit, so a rate never
        # writes into the absolute value's history.
        "json_api_rate",
        "json_api_count_rate",
        "json_api_bytes_rate",
        "json_api_seconds_rate",
        "json_api_percent_rate",
        # Measurements with a unit of their own. A value reported per second
        # already shares the rate metrics above.
        "json_api_bits_per_second",
        "json_api_celsius",
        "json_api_volts",
        "json_api_amperes",
        "json_api_watts",
        "json_api_hertz",
        # The age of a timestamp field.
        "json_api_age",
        # The endpoint's own service.
        "json_api_response_time",
        "json_api_response_size",
        # Remaining validity of the endpoint's TLS certificate, in days.
        "json_api_cert_expiry",
    }


def test_every_unit_metric_has_a_definition(check, graphing):
    # The check maps units to metric names; each of those must be a real metric.
    assert set(check._UNIT_METRIC.values()) <= _metric_names(graphing)


def test_every_rate_metric_has_a_definition(check, graphing):
    # Same for the rate metrics, and for the age / endpoint metrics the check
    # emits by name.
    names = _metric_names(graphing)
    assert set(check._UNIT_RATE_METRIC.values()) <= names
    assert {check._AGE_METRIC, "json_api_response_time", "json_api_response_size"} <= names


def test_the_countable_units_are_the_ones_setup_allows_on_a_counter(check, ruleset):
    # Every unit with a rate metric is a unit of its own, and every unit WITHOUT
    # one is refused on a counter by Setup - otherwise a counter in that unit
    # would silently fall back to the unit-less rate.
    assert set(check._UNIT_RATE_METRIC) <= set(check._UNIT_METRIC)
    for unit in set(check._UNIT_METRIC) - {None}:
        entry = {"unit": unit, "value_as": ("counter", None)}
        if unit in check._UNIT_RATE_METRIC:
            ruleset._validate_extraction(entry)  # must not raise
        else:
            with pytest.raises(ValidationError, match="counter"):
                ruleset._validate_extraction(entry)


def test_the_units_offered_are_the_units_the_check_knows(check, ruleset):
    offered = {
        element.name for element in ruleset._extraction().elements["unit"].parameter_form.elements
    }
    assert offered == set(check._UNIT_METRIC) - {None}
