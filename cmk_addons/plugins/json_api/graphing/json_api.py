#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Metric definitions for the generic JSON API checks.

Each service owns its item, so a metric is effectively per-service. A field may
declare a unit in the rule; the check then emits a distinctly named, correctly
formatted metric per unit. The unit-less ``json_api_value`` remains the default
so services configured before units existed keep their history.

A field read as a counter records the per-second *rate* - a different quantity,
hence its own metric per unit - and a field read as a timestamp records its
*age*.

The metric name for each unit is shared with the check (see ``_UNIT_METRIC`` /
``_UNIT_RATE_METRIC`` in ``agent_based/json_api.py``); keep the two in sync.
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

metric_json_api_rate = metrics.Metric(
    name="json_api_rate",
    title=Title("Rate"),
    unit=metrics.Unit(metrics.DecimalNotation("/s")),
    color=metrics.Color.BLUE,
)

metric_json_api_count_rate = metrics.Metric(
    name="json_api_count_rate",
    title=Title("Rate"),
    unit=metrics.Unit(metrics.DecimalNotation("/s")),
    color=metrics.Color.CYAN,
)

metric_json_api_bytes_rate = metrics.Metric(
    name="json_api_bytes_rate",
    title=Title("Throughput"),
    unit=metrics.Unit(metrics.IECNotation("B/s")),
    color=metrics.Color.GREEN,
)

metric_json_api_seconds_rate = metrics.Metric(
    name="json_api_seconds_rate",
    title=Title("Time per second"),
    unit=metrics.Unit(metrics.DecimalNotation("s/s")),
    color=metrics.Color.ORANGE,
)

metric_json_api_percent_rate = metrics.Metric(
    name="json_api_percent_rate",
    title=Title("Percentage per second"),
    unit=metrics.Unit(metrics.DecimalNotation("%/s")),
    color=metrics.Color.PURPLE,
)

metric_json_api_age = metrics.Metric(
    name="json_api_age",
    title=Title("Age"),
    unit=metrics.Unit(metrics.TimeNotation()),
    color=metrics.Color.YELLOW,
)
