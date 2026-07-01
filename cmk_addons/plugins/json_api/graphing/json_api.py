#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Metric definitions for the generic JSON API check.

Each service owns its item, so a metric is effectively per-service. A field may
declare a unit in the rule; the check then emits a distinctly named, correctly
formatted metric per unit. The unit-less ``json_api_value`` remains the default
so services configured before units existed keep their history.

The metric name for each unit is shared with the check (see ``_UNIT_METRIC`` in
``agent_based/json_api.py``); keep the two in sync.
"""

from cmk.graphing.v1 import Title, metrics

metric_json_api_value = metrics.Metric(
    name="json_api_value",
    title=Title("Value"),
    unit=metrics.Unit(metrics.DecimalNotation("")),
    color=metrics.Color.BLUE,
)

metric_json_api_count = metrics.Metric(
    name="json_api_count",
    title=Title("Count"),
    unit=metrics.Unit(metrics.DecimalNotation(""), metrics.StrictPrecision(0)),
    color=metrics.Color.CYAN,
)

metric_json_api_bytes = metrics.Metric(
    name="json_api_bytes",
    title=Title("Size"),
    unit=metrics.Unit(metrics.IECNotation("B")),
    color=metrics.Color.GREEN,
)

metric_json_api_seconds = metrics.Metric(
    name="json_api_seconds",
    title=Title("Duration"),
    unit=metrics.Unit(metrics.TimeNotation()),
    color=metrics.Color.ORANGE,
)

metric_json_api_percent = metrics.Metric(
    name="json_api_percent",
    title=Title("Percentage"),
    unit=metrics.Unit(metrics.DecimalNotation("%")),
    color=metrics.Color.PURPLE,
)
