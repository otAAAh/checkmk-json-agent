#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Check for the generic JSON API special agent.

One service per configured extraction (keyed by its service name). Numeric
values get levels + a metric; string values get an optional regex match.
"""

import ast
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

# The string-matching config from the CascadingSingleChoice form spec, JSON-encoded
# by the agent. After _coerce_match it is one of:
#   ("must_match", {"pattern": <regex>, "state_no_match": <0-3>})
#   ("state_map",  {"ok"/"warn"/"crit": <regex>, "state_no_match": <0-3>})
_Match = tuple[str, object] | None


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
    match: _Match
    calc: str | None
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


def _coerce_state(raw: object, default: int) -> int:
    """A ServiceState value (0=OK, 1=WARN, 2=CRIT, 3=UNKNOWN), else ``default``."""
    if not isinstance(raw, bool) and isinstance(raw, int) and 0 <= raw <= 3:
        return raw
    return default


def _coerce_match(raw: object, legacy_expected: object = None) -> _Match:
    """Normalize the string-match config (list from JSON -> tuple).

    ``must_match`` carries ``{"pattern", "state_no_match"}`` (a bare string is
    also accepted for a hand-written rule/CLI). ``state_map`` carries the
    OK/WARN/CRIT patterns plus a ``state_no_match`` fallback. ``legacy_expected``
    is the old flat ``expected`` regex; a section from a pre-'match' agent is
    read as the equivalent ``must_match`` (CRIT on mismatch) so old spool data
    keeps working alongside the migrated ruleset.
    """
    match raw:
        case ["must_match", dict() as cfg] | ("must_match", dict() as cfg):
            pattern = cfg.get("pattern")
            if isinstance(pattern, str):
                return (
                    "must_match",
                    {
                        "pattern": pattern,
                        "state_no_match": _coerce_state(cfg.get("state_no_match"), 2),
                    },
                )
        case ["must_match", str() as pattern] | ("must_match", str() as pattern):
            return ("must_match", {"pattern": pattern, "state_no_match": 2})
        case ["state_map", dict() as cfg] | ("state_map", dict() as cfg):
            cleaned: dict[str, object] = {
                key: cfg[key]
                for key in ("ok", "warn", "crit")
                if isinstance(cfg.get(key), str) and cfg[key]
            }
            cleaned["state_no_match"] = _coerce_state(cfg.get("state_no_match"), 0)
            return ("state_map", cleaned)
    if isinstance(legacy_expected, str):
        return ("must_match", {"pattern": legacy_expected, "state_no_match": 2})
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
            match=_coerce_match(result.get("match"), result.get("expected")),
            calc=result.get("calc") if isinstance(result.get("calc"), str) else None,
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
    if entry.match is not None:
        kind, cfg = entry.match
        if kind == "must_match" and isinstance(cfg, dict) and isinstance(cfg.get("pattern"), str):
            lines.append(f"Expected pattern: {cfg['pattern']}")
        elif kind == "state_map" and isinstance(cfg, dict):
            parts = [
                f"{key.upper()} /{cfg[key]}/" for key in ("ok", "warn", "crit") if cfg.get(key)
            ]
            if parts:
                lines.append("State map: " + ", ".join(parts))
    if lines:
        yield Result(state=State.OK, notice="\n".join(lines))


_STATE_MAP_ORDER = (("ok", State.OK), ("warn", State.WARN), ("crit", State.CRIT))


def _evaluate_match(match: tuple[str, object], text: str) -> tuple[State, str]:
    """Return ``(state, description)`` for a string value's configured matching.

    ``must_match`` is OK on a full match, else CRIT. ``state_map`` tries the OK,
    WARN then CRIT pattern and the first full match wins; no match stays OK.
    Raises ``re.error`` on a bad pattern so the caller surfaces it as UNKNOWN.
    """
    kind, cfg = match
    if not isinstance(cfg, dict):
        return State.OK, ""
    if kind == "must_match":
        pattern = cfg.get("pattern")
        pattern = pattern if isinstance(pattern, str) else ""
        if re.fullmatch(pattern, text) is not None:
            return State.OK, ""
        return State(_coerce_state(cfg.get("state_no_match"), 2)), f"expected to match '{pattern}'"
    for key, state in _STATE_MAP_ORDER:
        pattern = cfg.get(key)
        if isinstance(pattern, str) and pattern and re.fullmatch(pattern, text) is not None:
            return state, f"matched {key.upper()}"
    no_match = _coerce_state(cfg.get("state_no_match"), 0)
    return State(no_match), ("no pattern matched" if no_match != 0 else "")


def _apply_calc(value: float, expr: str) -> float:
    """Evaluate a small arithmetic expression over the variable ``value``.

    Only numeric literals, ``value``, parentheses and + - * / (incl. unary +/-)
    are supported; anything else raises ``ValueError``. This walks the AST and
    never uses ``eval``, so a rule cannot smuggle in code execution. Operators
    are dispatched with explicit ``isinstance`` branches (rather than a lookup
    table of ``operator`` callables) so the arithmetic stays statically typed.
    """

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.BinOp):
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            raise ValueError("unsupported operator")
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("unsupported operator")
        if (
            isinstance(node, ast.Constant)
            and not isinstance(node.value, bool)
            and isinstance(node.value, (int, float))
        ):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id == "value":
            return value
        raise ValueError("unsupported expression")

    return float(_eval(ast.parse(expr, mode="eval")))


def _fmt_number(number: float) -> str:
    """Render a (calculated) number without a spurious trailing ``.0``."""
    return str(int(number)) if number.is_integer() else str(number)


def _value_results(entry: Item) -> CheckResult:
    number = _as_number(entry.value)

    # A numeric value may be transformed by a small arithmetic expression before
    # levels and the metric see it (e.g. bytes -> MiB). A broken calculation
    # (bad expression, divide-by-zero, non-finite result) is surfaced, never
    # silently ignored.
    if number is not None and entry.calc:
        try:
            number = _apply_calc(number, entry.calc)
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            yield Result(state=State.UNKNOWN, summary=f"Calculation '{entry.calc}' failed: {exc}")
            return
        if not math.isfinite(number):
            yield Result(
                state=State.UNKNOWN,
                summary=f"Calculation '{entry.calc}' produced a non-finite result",
            )
            return

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

    if entry.match is not None:
        text = _render_value(entry.value)
        try:
            state, description = _evaluate_match(entry.match, text)
        except re.error as exc:
            yield Result(state=State.UNKNOWN, summary=f"Invalid match pattern: {exc}")
            return
        # The matched state stands; a meaningless levels config only ever
        # escalates it (to at least WARN) and appends its note, so the misconfig
        # is surfaced without ever hiding behind - or weakening - the match.
        note = misconfig_note if misconfigured_levels else ""
        if misconfigured_levels:
            state = State.worst(state, State.WARN)
        summary = f"Value: {text}"
        if description:
            summary += f" ({description})"
        yield Result(state=state, summary=summary + note)
        return

    if misconfigured_levels:
        yield Result(
            state=State.WARN, summary=f"Value: {_render_value(entry.value)}{misconfig_note}"
        )
        return

    # No levels, no match: surface the value, add a metric if numeric. When a
    # calculation transformed the number, show the transformed value (what the
    # metric records), not the raw JSON.
    if number is not None:
        shown = _fmt_number(number) if entry.calc else _render_value(entry.value)
        yield Result(state=State.OK, summary=f"Value: {shown}")
        yield Metric(entry.metric_name, number)
    else:
        yield Result(state=State.OK, summary=f"Value: {_render_value(entry.value)}")


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
