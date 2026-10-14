#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The metric a field emits, as far as Setup can tell from the rule alone.

What Checkmk accepts as a metric name, which names this plugin already declares,
and the names the check derives from a field's unit. The check cannot import
the ruleset APIs, so the derivation is mirrored here and kept in step with
``agent_based/json_api.py`` by the parity tests.
"""

import re
from collections.abc import Mapping

from cmk.rulesets.v1 import Message
from cmk.rulesets.v1.form_specs import validators

# What Checkmk itself accepts as a metric name (cmk.gui.graphing MetricName):
# letters, digits and underscores, starting with a letter or a digit. Rejecting
# anything else here means the name an operator types into a Gauge / Single
# metric / Bar chart widget is the name the check actually emits.
_METRIC_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_]*$")

# The metrics this plugin DECLARES (graphing/json_api.py). Taking one of these
# names for a field does not just label it: the field inherits that metric's
# unit, colour and Perf-O-Meters, so a percentage named 'json_api_bytes' renders
# as '11.5 B'. Kept in step with the graphing module by
# test_the_reserved_names_are_exactly_the_declared_metrics.
_DECLARED_METRICS = frozenset(
    {
        "json_api_value",
        "json_api_count",
        "json_api_bytes",
        "json_api_seconds",
        "json_api_percent",
        "json_api_rate",
        "json_api_count_rate",
        "json_api_bytes_rate",
        "json_api_seconds_rate",
        "json_api_percent_rate",
        "json_api_bits_per_second",
        "json_api_celsius",
        "json_api_volts",
        "json_api_amperes",
        "json_api_watts",
        "json_api_hertz",
        "json_api_age",
        "json_api_response_time",
        "json_api_response_size",
        "json_api_cert_expiry",
    }
)


def _validate_metric_name(value: str) -> None:
    if not _METRIC_NAME.match(value):
        raise validators.ValidationError(
            Message(
                "A metric name must consist of letters, digits and underscores "
                "only, and start with a letter or a digit."
            )
        )
    if value in _DECLARED_METRICS:
        raise validators.ValidationError(
            Message(
                "'%s' is one of this plugin's own metrics. A field given that "
                "name inherits its unit, colour and Perf-O-Meter, which is "
                "almost never what is meant - a percentage named "
                "'json_api_bytes' is rendered as bytes. Choose a name of your "
                "own, or leave this empty to get the right one automatically."
            )
            % value
        )


# Mirrors ``_metric_slug`` in ``agent_based/json_api.py``: the suffix a shared
# service's per-line metric names are built from. Duplicated rather than shared
# because the check must not import the ruleset APIs; kept in step by
# test_the_rulesets_slug_matches_the_checks.
def _metric_slug(line: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", line.lower()).strip("_")
    return slug or "value"


# Mirrors ``_UNIT_METRIC`` / ``_UNIT_RATE_METRIC`` / ``_AGE_METRIC`` in the
# check, for the same reason and under the same parity test
# (test_the_rulesets_metric_base_matches_the_checks).
_UNIT_METRIC = {
    None: "json_api_value",
    "count": "json_api_count",
    "bytes": "json_api_bytes",
    "seconds": "json_api_seconds",
    "percent": "json_api_percent",
    "per_second": "json_api_count_rate",
    "bytes_per_second": "json_api_bytes_rate",
    "bits_per_second": "json_api_bits_per_second",
    "celsius": "json_api_celsius",
    "volts": "json_api_volts",
    "amperes": "json_api_amperes",
    "watts": "json_api_watts",
    "hertz": "json_api_hertz",
}
_UNIT_RATE_METRIC = {
    None: "json_api_rate",
    "count": "json_api_count_rate",
    "bytes": "json_api_bytes_rate",
    "seconds": "json_api_seconds_rate",
    "percent": "json_api_percent_rate",
}


def _field_metric_name(entry: Mapping[str, object]) -> str:
    """The metric name this extraction will emit, from the rule alone.

    Two fields of one shared service collide only when this whole name matches -
    a byte count and an element count that both slug to 'root_used' stay apart
    on their unit.
    """
    stated = entry.get("metric_name")
    if isinstance(stated, str) and stated:
        return stated
    unit = entry.get("unit")
    unit = unit if isinstance(unit, str) else None
    value_as = entry.get("value_as")
    kind = value_as[0] if isinstance(value_as, (list, tuple)) and value_as else None
    if kind == "counter":
        base = _UNIT_RATE_METRIC.get(unit, "json_api_rate")
    elif kind == "timestamp" and unit is None:
        base = "json_api_age"
    else:
        base = _UNIT_METRIC.get(unit, "json_api_value")
    line = entry.get("service")
    return f"{base}_{_metric_slug(line)}" if isinstance(line, str) and line else base


def _validate_unique_group_metrics(value: object) -> None:
    """Reject two fields of one shared service that would emit one metric name.

    A shared service names each line's metric after the line, and the slug folds
    every run of non-alphanumeric characters to '_' - so 'Root used' and
    'Root-used' with the same unit are one name. The check disambiguates at
    runtime ('_2', '_3', ...) so no value is ever dropped, but it can only do so
    POSITIONALLY: reorder the fields here and the suffix moves to the other one,
    silently trading two metrics' history, and with it their graphs and any
    dashboard widget bound to the name. Exactly the reasoning behind rejecting
    duplicate endpoint names above, one level down - and the reason to catch it
    at config time, where the two fields are in front of you.

    Only fields sharing a service can collide: a field of its own owns its
    service, and a duplicated SERVICE name is disambiguated as a service.
    """
    if not isinstance(value, (list, tuple)):
        return
    by_group: dict[str, dict[str, list[str]]] = {}
    for entry in value:
        if not isinstance(entry, dict):
            continue
        group = entry.get("group")
        if not isinstance(group, str) or not group.strip():
            continue
        line = entry.get("service")
        by_group.setdefault(group.strip(), {}).setdefault(_field_metric_name(entry), []).append(
            line if isinstance(line, str) else "?"
        )
    for group, metrics in sorted(by_group.items()):
        for metric, lines in sorted(metrics.items()):
            if len(lines) > 1:
                raise validators.ValidationError(
                    Message(
                        "The fields %s of the shared service '%s' would all be "
                        "recorded as the metric '%s'. Give them names that "
                        "differ by more than punctuation, or set 'Metric name' "
                        "on all but one."
                    )
                    % (", ".join(f"'{line}'" for line in lines), group, metric)
                )
