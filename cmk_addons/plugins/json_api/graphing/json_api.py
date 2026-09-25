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
*age*. The endpoint services add the response time and the response size.

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

# Measurements with a unit of their own. A value the API reports per second
# already (requests/s, B/s) has no metric here: it is the same quantity as a
# counter's rate and records into json_api_count_rate / json_api_bytes_rate.

metric_json_api_bits_per_second = metrics.Metric(
    name="json_api_bits_per_second",
    title=Title("Bandwidth"),
    unit=metrics.Unit(metrics.SINotation("bit/s")),
    color=metrics.Color.DARK_GREEN,
)

metric_json_api_celsius = metrics.Metric(
    name="json_api_celsius",
    title=Title("Temperature"),
    unit=metrics.Unit(metrics.DecimalNotation("°C")),
    color=metrics.Color.RED,
)

metric_json_api_volts = metrics.Metric(
    name="json_api_volts",
    title=Title("Voltage"),
    unit=metrics.Unit(metrics.SINotation("V")),
    color=metrics.Color.DARK_YELLOW,
)

metric_json_api_amperes = metrics.Metric(
    name="json_api_amperes",
    title=Title("Electric current"),
    unit=metrics.Unit(metrics.SINotation("A")),
    color=metrics.Color.DARK_CYAN,
)

metric_json_api_watts = metrics.Metric(
    name="json_api_watts",
    title=Title("Power"),
    unit=metrics.Unit(metrics.SINotation("W")),
    color=metrics.Color.DARK_ORANGE,
)

metric_json_api_hertz = metrics.Metric(
    name="json_api_hertz",
    title=Title("Frequency"),
    unit=metrics.Unit(metrics.SINotation("Hz")),
    color=metrics.Color.DARK_PURPLE,
)

metric_json_api_age = metrics.Metric(
    name="json_api_age",
    title=Title("Age"),
    unit=metrics.Unit(metrics.TimeNotation()),
    color=metrics.Color.YELLOW,
)

metric_json_api_response_time = metrics.Metric(
    name="json_api_response_time",
    title=Title("Response time"),
    unit=metrics.Unit(metrics.TimeNotation()),
    color=metrics.Color.LIGHT_BLUE,
)

metric_json_api_response_size = metrics.Metric(
    name="json_api_response_size",
    title=Title("Response size"),
    unit=metrics.Unit(metrics.IECNotation("B")),
    color=metrics.Color.LIGHT_GREEN,
)

metric_json_api_cert_expiry = metrics.Metric(
    name="json_api_cert_expiry",
    title=Title("Certificate expires in"),
    # Days, not seconds: a certificate policy is written in days ("renew 30 days
    # out"), so that is the unit the levels and the graph speak.
    unit=metrics.Unit(metrics.DecimalNotation("days")),
    color=metrics.Color.LIGHT_ORANGE,
)
