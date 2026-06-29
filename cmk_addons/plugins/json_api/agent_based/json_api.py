#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Check for the generic JSON API special agent.

One service per configured extraction (keyed by its service name). Numeric
values get levels + a metric; string values get an optional regex match.
"""

import json
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


@dataclass(frozen=True)
class Item:
    found: bool
    value: object
    error: str | None
    levels_upper: _Levels
    levels_lower: _Levels
    expected: str | None


@dataclass(frozen=True)
class Section:
    error: str | None
    items: Mapping[str, Item]


def _coerce_levels(raw: object) -> _Levels:
    match raw:
        case ["fixed", [warn, crit]]:
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
        )
    return Section(error=payload.get("error"), items=items)


def discover_json_api(section: Section) -> DiscoveryResult:
    for service in section.items:
        yield Service(item=service)


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def check_json_api(item: str, section: Section) -> CheckResult:
    if section.error:
        yield Result(state=State.CRIT, summary=f"API error: {section.error}")
        return
    entry = section.items.get(item)
    if entry is None:
        return
    if not entry.found:
        yield Result(state=State.UNKNOWN, summary=entry.error or "not found")
        return

    number = _as_number(entry.value)
    if number is not None and (entry.levels_upper or entry.levels_lower):
        yield from check_levels(
            number,
            levels_upper=entry.levels_upper,
            levels_lower=entry.levels_lower,
            metric_name="json_api_value",
            label="Value",
        )
        return

    if entry.expected is not None:
        text = str(entry.value)
        try:
            ok = re.fullmatch(entry.expected, text) is not None
        except re.error as exc:
            yield Result(
                state=State.UNKNOWN,
                summary=f"Invalid expected pattern '{entry.expected}': {exc}",
            )
            return
        yield Result(
            state=State.OK if ok else State.CRIT,
            summary=f"Value: {text}" + ("" if ok else f" (expected to match '{entry.expected}')"),
        )
        return

    if entry.levels_upper or entry.levels_lower:
        # Levels were configured but the value is not numeric - surface it
        # instead of silently passing.
        yield Result(
            state=State.WARN,
            summary=f"Value: {entry.value} (levels configured but value is not numeric)",
        )
        return

    # No levels, no expected pattern: surface the value, add a metric if numeric.
    yield Result(state=State.OK, summary=f"Value: {entry.value}")
    if number is not None:
        yield Metric("json_api_value", number)


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
