#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Metric definition for the generic JSON API check.

A single, unit-less metric is shared by every numeric service (each service has
its own item, so the metric is per-service). Per-field units are a follow-up.
"""

from cmk.graphing.v1 import Title, metrics

metric_json_api_value = metrics.Metric(
    name="json_api_value",
    title=Title("Value"),
    unit=metrics.Unit(metrics.DecimalNotation("")),
    color=metrics.Color.BLUE,
)
