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
        # Per-second rates of a counter field: one per unit, so a rate never
        # writes into the absolute value's history.
        "json_api_rate",
        "json_api_count_rate",
        "json_api_bytes_rate",
        "json_api_seconds_rate",
        "json_api_percent_rate",
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


def test_unit_and_rate_metrics_cover_the_same_units(check):
    # A unit that has an absolute metric must have a rate metric too, otherwise a
    # counter field with that unit would silently fall back to the unit-less one.
    assert set(check._UNIT_METRIC) == set(check._UNIT_RATE_METRIC)
