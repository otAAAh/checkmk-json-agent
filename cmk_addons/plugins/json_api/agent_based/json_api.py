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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from cmk.agent_based.v2 import (
    AgentSection,
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    HostLabel,
    HostLabelGenerator,
    Metric,
    Result,
    Service,
    ServiceLabel,
    State,
    StringTable,
    check_levels,
    render,
)

# Checkmk label keys we create are namespaced so they never collide with the
# built-in ``cmk/...`` labels or another plugin's.
_LABEL_NS = "json_api/"

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


# The unit chosen in the rule also decides how the value is *rendered* in the
# summary/details (and the levels line), so "1572864" with unit=bytes reads as
# "1.50 MiB" like the graph does - not just as a bare number. Units without a
# dedicated renderer ("count", or none) fall back to the plain number.
_UNIT_RENDER: dict[str, Callable[[float], str]] = {
    "bytes": render.bytes,
    "seconds": render.timespan,
    "percent": render.percent,
}


def _render_func(unit: object) -> Callable[[float], str] | None:
    return _UNIT_RENDER.get(unit) if isinstance(unit, str) else None


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
    render_func: Callable[[float], str] | None
    path: str
    url: str
    # Service labels (key, value) the agent resolved for this service, sans the
    # json_api/ namespace prefix (added when the ServiceLabel is emitted).
    service_labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Section:
    error: str | None
    items: Mapping[str, Item]
    # Host labels (key -> value) aggregated across all results on this host,
    # last value wins per key. Emitted by the section's host_label_function.
    host_labels: Mapping[str, str] = field(default_factory=dict)


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


def _coerce_calc(raw: object) -> str | None:
    """The arithmetic transform expression, or ``None`` for "no transform".

    A blank/whitespace-only value means no transform (the ruleset validator
    accepts it as such): normalize it to ``None`` so it never reaches
    ``ast.parse``, which would raise an uncaught ``SyntaxError`` on it.
    """
    if isinstance(raw, str) and raw.strip():
        return raw
    return None


def _service_labels(raw: object) -> tuple[tuple[str, str], ...]:
    """The (key, value) service labels the agent resolved for one result.

    Each entry is ``{"key", "value"}`` with string key/value; anything else is
    ignored defensively.
    """
    out: list[tuple[str, str]] = []
    for label in raw if isinstance(raw, list) else []:
        if not isinstance(label, dict):
            continue
        key, value = label.get("key"), label.get("value")
        if isinstance(key, str) and key and isinstance(value, str):
            out.append((key, value))
    return tuple(out)


def _coerce_host_labels(raw: object) -> dict[str, str]:
    """The endpoint-level ``{key: value}`` host labels, string-validated."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and k and isinstance(v, str)}


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
            calc=_coerce_calc(result.get("calc")),
            metric_name=_metric_name(result.get("unit")),
            render_func=_render_func(result.get("unit")),
            path=result.get("path", ""),
            url=result.get("url", ""),
            service_labels=_service_labels(result.get("labels")),
        )
    return Section(
        error=payload.get("error"),
        items=items,
        host_labels=_coerce_host_labels(payload.get("host_labels")),
    )


def host_label_json_api(section: Section) -> HostLabelGenerator:
    """Emit the host labels the agent resolved (namespaced, e.g. json_api/env).

    Host-scope labels are configured per extraction but attached to the host, so
    they are aggregated across every endpoint/extraction into section.host_labels
    (last value wins per key) before being yielded here.
    """
    for key, value in section.host_labels.items():
        yield HostLabel(f"{_LABEL_NS}{key}", value)


def discover_json_api(section: Section) -> DiscoveryResult:
    for name, entry in section.items.items():
        # Seed each service's discovered parameters with the thresholds / match
        # configured in the special-agent rule. These become the service's
        # defaults; a "Generic JSON API" check-parameters rule then overrides
        # them per folder/host/service (precedence: default < discovered < rule).
        # Only carry the keys that are actually set, so an unset level stays
        # absent (and the check falls back to the section for pre-upgrade
        # autochecks that have no discovered parameters at all).
        params: dict[str, object] = {}
        if entry.levels_upper is not None:
            params["levels_upper"] = entry.levels_upper
        if entry.levels_lower is not None:
            params["levels_lower"] = entry.levels_lower
        if entry.match is not None:
            params["match"] = entry.match
        # Service labels the agent resolved for this service, namespaced. Attached
        # at discovery, so they update when the service is re-discovered.
        labels = [ServiceLabel(f"{_LABEL_NS}{key}", value) for key, value in entry.service_labels]
        yield Service(item=name, parameters=params, labels=labels)


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


def _context(entry: Item, match: _Match) -> CheckResult:
    """Details-only lines describing where the value came from.

    Emitted as an OK result with ``notice``, so it never touches the summary
    line or the service state - it just enriches the Details view, which makes a
    misconfigured extraction (wrong path, wrong endpoint) far easier to debug.
    ``match`` is the *effective* matching (after any check-parameters override),
    so the details reflect what the check actually applied.
    """
    lines = []
    if entry.path:
        lines.append(f"JSON path: {entry.path}")
    if entry.url:
        lines.append(f"Source: {entry.url}")
    if match is not None:
        kind, cfg = match
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


def _value_results(
    entry: Item,
    levels_upper: _Levels,
    levels_lower: _Levels,
    match: _Match,
) -> CheckResult:
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

    has_levels = bool(levels_upper or levels_lower)

    if number is not None and has_levels:
        yield from check_levels(
            number,
            levels_upper=levels_upper,
            levels_lower=levels_lower,
            metric_name=entry.metric_name,
            label="Value",
            render_func=entry.render_func,
        )
        return

    # Levels configured on a value that is not numeric: a misconfiguration we
    # surface rather than silently pass - even when an 'expected' regex is also
    # set (otherwise it would be hidden behind the regex result).
    misconfigured_levels = has_levels and number is None
    misconfig_note = " (levels configured but value is not numeric)"

    if match is not None:
        text = _render_value(entry.value)
        try:
            state, description = _evaluate_match(match, text)
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

    # No levels, no match: surface the value, add a metric if numeric. A unit's
    # render func formats it like the graph (bytes -> "1.50 MiB"); otherwise show
    # the transformed number when a calculation ran, else the raw JSON value.
    if number is not None:
        if entry.render_func is not None:
            shown = entry.render_func(number)
        else:
            shown = _fmt_number(number) if entry.calc else _render_value(entry.value)
        yield Result(state=State.OK, summary=f"Value: {shown}")
        yield Metric(entry.metric_name, number)
    else:
        yield Result(state=State.OK, summary=f"Value: {_render_value(entry.value)}")


def check_json_api(item: str, params: Mapping[str, object], section: Section) -> CheckResult:
    if section.error:
        yield Result(state=State.CRIT, summary=f"API error: {section.error}")
        return
    entry = section.items.get(item)
    if entry is None:
        return

    # Effective parameters: a check-parameters rule (or the discovered defaults)
    # wins per key; where a key is absent we fall back to the value the agent
    # embedded in the section, so services discovered by a pre-parameters
    # version keep their thresholds until they are re-discovered.
    levels_upper = (
        _coerce_levels(params["levels_upper"]) if "levels_upper" in params else entry.levels_upper
    )
    levels_lower = (
        _coerce_levels(params["levels_lower"]) if "levels_lower" in params else entry.levels_lower
    )
    match = _coerce_match(params["match"]) if "match" in params else entry.match

    if not entry.found:
        yield Result(state=State.UNKNOWN, summary=entry.error or "not found")
        yield from _context(entry, match)
        return

    yield from _value_results(entry, levels_upper, levels_lower, match)
    yield from _context(entry, match)


agent_section_json_api = AgentSection(
    name="json_api",
    parse_function=parse_json_api,
    host_label_function=host_label_json_api,
)

check_plugin_json_api = CheckPlugin(
    name="json_api",
    service_name="JSON %s",
    discovery_function=discover_json_api,
    check_function=check_json_api,
    check_ruleset_name="json_api",
    check_default_parameters={},
)
