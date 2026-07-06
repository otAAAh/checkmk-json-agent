#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Check for the generic JSON API special agent.

One service per configured extraction (keyed by its service name). Numeric
values get levels + a metric; string values get an optional regex match.
"""

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from cmk.agent_based.v2 import (
    AgentSection,
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    Metric,
    Result,
    Service,
    State,
    StringTable,
    check_levels,
)

# Level tuples come straight from the SimpleLevels form spec, JSON-encoded by
# the agent: ("fixed", (warn, crit)) or ("no_levels", None) or absent.
# Plain alias (not a PEP 695 `type` statement) to stay portable to Checkmk 2.4.
_Levels = tuple[str, tuple[float, float] | None] | None


# Maps the unit chosen in the rule to the metric defined in graphing/json_api.py.
# ``None`` (no unit chosen, incl. rules from before units existed) keeps the
# original unit-less "json_api_value" so existing metric history is preserved.
_UNIT_METRIC = {
    None: "json_api_value",
    "count": "json_api_count",
    "bytes": "json_api_bytes",
    "seconds": "json_api_seconds",
    "percent": "json_api_percent",
}


def _metric_name(unit: object) -> str:
    return _UNIT_METRIC.get(unit if isinstance(unit, str) else None, "json_api_value")


@dataclass(frozen=True)
class Item:
    found: bool
    value: object
    error: str | None
    levels_upper: _Levels
    levels_lower: _Levels
    expected: str | None
    metric_name: str
    path: str
    url: str


@dataclass(frozen=True)
class Section:
    error: str | None
    items: Mapping[str, Item]


def _coerce_levels(raw: object) -> _Levels:
    match raw:
        case ["fixed", [(int() | float()) as warn, (int() | float()) as crit]]:
            return ("fixed", (float(warn), float(crit)))
        case ["no_levels", _] | None:
            return None
    return None


def parse_json_api(string_table: StringTable) -> Section | None:
    if not string_table:
        return None
    payload = json.loads(string_table[0][0])
    items: dict[str, Item] = {}
    for result in payload["results"]:
        # The agent already makes wildcard labels unique; this is a defensive
        # backstop so a duplicate service name can never silently drop a service.
        name = result["service"]
        if name in items:
            suffix = 2
            while f"{name} ({suffix})" in items:
                suffix += 1
            name = f"{name} ({suffix})"
        items[name] = Item(
            found=result["found"],
            value=result["value"],
            error=result["error"],
            levels_upper=_coerce_levels(result.get("levels_upper")),
            levels_lower=_coerce_levels(result.get("levels_lower")),
            expected=result.get("expected"),
            metric_name=_metric_name(result.get("unit")),
            path=result.get("path", ""),
            url=result.get("url", ""),
        )
    return Section(error=payload.get("error"), items=items)


def discover_json_api(section: Section) -> DiscoveryResult:
    for service in section.items:
        yield Service(item=service)


def _render_value(value: object) -> str:
    """Render a value the way it appears in JSON, so 'expected' matches what
    users see (true/false/null, not Python's True/False/None)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    # Reject inf/nan: float("inf")/"nan" parse but make no sense as a metric or
    # a levels comparison, and would render confusingly.
    return number if math.isfinite(number) else None


def _context(entry: Item) -> CheckResult:
    """Details-only lines describing where the value came from.

    Emitted as an OK result with ``notice``, so it never touches the summary
    line or the service state - it just enriches the Details view, which makes a
    misconfigured extraction (wrong path, wrong endpoint) far easier to debug.
    """
    lines = []
    if entry.path:
        lines.append(f"JSON path: {entry.path}")
    if entry.url:
        lines.append(f"Source: {entry.url}")
    if entry.expected is not None:
        lines.append(f"Expected pattern: {entry.expected}")
    if lines:
        yield Result(state=State.OK, notice="\n".join(lines))


def _value_results(entry: Item) -> CheckResult:
    number = _as_number(entry.value)
    has_levels = bool(entry.levels_upper or entry.levels_lower)

    if number is not None and has_levels:
        yield from check_levels(
            number,
            levels_upper=entry.levels_upper,
            levels_lower=entry.levels_lower,
            metric_name=entry.metric_name,
            label="Value",
        )
        return

    # Levels configured on a value that is not numeric: a misconfiguration we
    # surface rather than silently pass - even when an 'expected' regex is also
    # set (otherwise it would be hidden behind the regex result).
    misconfigured_levels = has_levels and number is None
    misconfig_note = " (levels configured but value is not numeric)"

    if entry.expected is not None:
        text = _render_value(entry.value)
        try:
            ok = re.fullmatch(entry.expected, text) is not None
        except re.error as exc:
            yield Result(
                state=State.UNKNOWN,
                summary=f"Invalid expected pattern '{entry.expected}': {exc}",
            )
            return
        summary = f"Value: {text}" + ("" if ok else f" (expected to match '{entry.expected}')")
        if ok and misconfigured_levels:
            # The regex matched, but flag the meaningless levels config.
            yield Result(state=State.WARN, summary=f"Value: {text}{misconfig_note}")
        else:
            yield Result(state=State.OK if ok else State.CRIT, summary=summary)
        return

    if misconfigured_levels:
        yield Result(
            state=State.WARN, summary=f"Value: {_render_value(entry.value)}{misconfig_note}"
        )
        return

    # No levels, no expected pattern: surface the value, add a metric if numeric.
    yield Result(state=State.OK, summary=f"Value: {_render_value(entry.value)}")
    if number is not None:
        yield Metric(entry.metric_name, number)


def check_json_api(item: str, section: Section) -> CheckResult:
    if section.error:
        yield Result(state=State.CRIT, summary=f"API error: {section.error}")
        return
    entry = section.items.get(item)
    if entry is None:
        return
    if not entry.found:
        yield Result(state=State.UNKNOWN, summary=entry.error or "not found")
        yield from _context(entry)
        return

    yield from _value_results(entry)
    yield from _context(entry)


agent_section_json_api = AgentSection(
    name="json_api",
    parse_function=parse_json_api,
)

check_plugin_json_api = CheckPlugin(
    name="json_api",
    service_name="JSON %s",
    discovery_function=discover_json_api,
    check_function=check_json_api,
)
