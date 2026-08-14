#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Checks for the generic JSON API special agent.

Two plugins read the one ``json_api`` section:

* ``json_api`` - one service per configured extraction (keyed by its service
  name). Numeric values get levels + a metric; string values get an optional
  regex match. A value may also be read as a counter (monitor its per-second
  rate) or as a timestamp (monitor its age).
* ``json_api_endpoint`` - one service per configured endpoint, reporting the
  outcome of the request itself: HTTP status, response time and body size.
"""

import ast
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cmk.agent_based.v2 import (
    AgentSection,
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    GetRateError,
    HostLabel,
    HostLabelGenerator,
    IgnoreResults,
    Metric,
    Result,
    Service,
    ServiceLabel,
    State,
    StringTable,
    check_levels,
    get_rate,
    get_value_store,
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

# How to read the extracted value, from the 'Interpret the value as'
# CascadingSingleChoice (JSON-encoded by the agent). After _coerce_value_as:
#   ("counter", None)                    -> monitor the per-second rate
#   ("timestamp", {"format": "auto"})    -> monitor the age in seconds
# ``None`` monitors the value as it stands.
_ValueAs = tuple[str, object] | None


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

# A per-second rate is a different quantity from the counter it came from, so it
# gets its own metric per unit (bytes -> B/s, ...) rather than polluting the
# absolute value's history with a rate.
_UNIT_RATE_METRIC = {
    None: "json_api_rate",
    "count": "json_api_count_rate",
    "bytes": "json_api_bytes_rate",
    "seconds": "json_api_seconds_rate",
    "percent": "json_api_percent_rate",
}

# The age of a timestamp is a duration in seconds; it gets a metric of its own so
# it graphs as a duration without the rule having to choose a unit.
_AGE_METRIC = "json_api_age"


def _metric_name(unit: object) -> str:
    return _UNIT_METRIC.get(unit if isinstance(unit, str) else None, "json_api_value")


def _rate_metric_name(unit: object) -> str:
    return _UNIT_RATE_METRIC.get(unit if isinstance(unit, str) else None, "json_api_rate")


def _render_days(days: float) -> str:
    """A number of days, negative (already expired) included.

    Whole days read as '42 days'; a fraction keeps one decimal so the last day
    before expiry does not render as '0 days'.
    """
    rounded = round(days, 1)
    shown = int(rounded) if float(rounded).is_integer() else rounded
    return f"{shown} days"


def _render_seconds(seconds: float) -> str:
    """Render a number of seconds as a duration, including a negative one.

    ``render.timespan`` raises on a negative value, but a negative duration is an
    ordinary thing to monitor here: the age of a timestamp in the future (a
    certificate expiry, a scheduled run), or simply a negative number the API
    reported for a field whose unit is seconds. Both render as a duration with a
    leading '-' instead of crashing the service.
    """
    return render.timespan(seconds) if seconds >= 0 else f"-{render.timespan(-seconds)}"


# The unit chosen in the rule also decides how the value is *rendered* in the
# summary/details (and the levels line), so "1572864" with unit=bytes reads as
# "1.50 MiB" like the graph does - not just as a bare number. Units without a
# dedicated renderer ("count", or none) fall back to the plain number.
_UNIT_RENDER: dict[str, Callable[[float], str]] = {
    "bytes": render.bytes,
    "seconds": _render_seconds,
    "percent": render.percent,
}


def _render_func(unit: object) -> Callable[[float], str] | None:
    return _UNIT_RENDER.get(unit) if isinstance(unit, str) else None


def _rate_render_func(unit: object) -> Callable[[float], str]:
    """Render a per-second rate: the unit's own rendering plus '/s'.

    A rate is almost never a round number (it is a difference divided by an
    elapsed time), so the unit-less fallback rounds to a few significant digits
    instead of printing the full float repr - '20/s' rather than
    '19.999451493365736/s' - while staying accurate for tiny rates.
    """
    base = _render_func(unit) or _fmt_rate
    return lambda number: f"{base(number)}/s"


def _fmt_rate(number: float) -> str:
    return _fmt_number(number) if float(number).is_integer() else f"{number:.6g}"


@dataclass(frozen=True)
class Item:
    found: bool
    value: object
    error: str | None
    levels_upper: _Levels
    levels_lower: _Levels
    match: _Match
    calc: str | None
    unit: object
    metric_name: str
    render_func: Callable[[float], str] | None
    path: str
    url: str
    # How to read the value: as it stands, as a counter, or as a timestamp.
    value_as: _ValueAs = None
    # The aggregation the agent already applied ('count'/'sum'/...), for Details.
    aggregate: str | None = None
    # Service labels (key, value) the agent resolved for this service, sans the
    # json_api/ namespace prefix (added when the ServiceLabel is emitted).
    service_labels: tuple[tuple[str, str], ...] = ()
    # Extra summary text: the configured template ('{path}' placeholders) and the
    # values those paths resolved to for THIS service's element. Rendering is
    # left here because only the check knows what the summary already says.
    summary: str | None = None
    summary_fields: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EndpointStatus:
    """The outcome of one endpoint's request, for the endpoint's own service."""

    name: str
    url: str
    ok: bool
    error: str | None
    status: int | None
    elapsed: float | None
    size: int | None
    final_url: str | None
    # The peer certificate's notAfter as a Unix epoch, when the agent could read
    # it (HTTPS, verification on, socket still exposed). None means "no
    # certificate info", never "expired".
    cert_expiry: float | None = None
    # Served from the agent's per-endpoint cache instead of a live request, and
    # how old that cached body was. A cached serve has no response time.
    from_cache: bool = False
    cache_age: float | None = None
    # How many attempts the request took. 1 means it worked first time; more
    # means a retry policy absorbed a failure, which the service reports rather
    # than hides.
    attempts: int = 1


@dataclass(frozen=True)
class Section:
    error: str | None
    items: Mapping[str, Item]
    # Host labels (key -> value) aggregated across all results on this host,
    # last value wins per key. Emitted by the section's host_label_function.
    host_labels: Mapping[str, str] = field(default_factory=dict)
    # One entry per configured endpoint, keyed by its name (the service item).
    # Empty for a section written by an agent from before endpoint records.
    endpoints: Mapping[str, EndpointStatus] = field(default_factory=dict)


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


def _coerce_value_as(raw: object) -> _ValueAs:
    """Normalize the 'Interpret the value as' config (list from JSON -> tuple).

    Anything unrecognized falls back to ``None`` ("monitor the value as it
    stands"), so an unknown future choice degrades to the plain behaviour instead
    of failing the service.
    """
    match raw:
        case ["counter", _] | ("counter", _) | "counter":
            return ("counter", None)
        case ["timestamp", dict() as cfg] | ("timestamp", dict() as cfg):
            fmt = cfg.get("format")
            return ("timestamp", {"format": fmt if isinstance(fmt, str) and fmt else "auto"})
        case ["timestamp", _] | ("timestamp", _) | "timestamp":
            return ("timestamp", {"format": "auto"})
    return None


def _coerce_aggregate(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw else None


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


def _summary_fields(raw: object) -> dict[str, str]:
    """The ``{path: rendered}`` values the agent resolved for a summary template."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and k and isinstance(v, str)}


def _coerce_host_labels(raw: object) -> dict[str, str]:
    """The endpoint-level ``{key: value}`` host labels, string-validated."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and k and isinstance(v, str)}


def _optional_number(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw) if math.isfinite(raw) else None


def _optional_int(raw: object) -> int | None:
    number = _optional_number(raw)
    return int(number) if number is not None else None


def _optional_str(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw else None


def _endpoint_statuses(raw: object) -> dict[str, EndpointStatus]:
    """The agent's ``endpoints`` list, keyed by name (the service item).

    A duplicated name (two endpoints configured with the same one) is
    disambiguated the same way a duplicated service name is, so no endpoint can
    silently disappear from the service list.
    """
    statuses: dict[str, EndpointStatus] = {}
    for record in raw if isinstance(raw, list) else []:
        if not isinstance(record, dict):
            continue
        url = _optional_str(record.get("url")) or "?"
        name = _optional_str(record.get("name")) or url
        if name in statuses:
            suffix = 2
            while f"{name} ({suffix})" in statuses:
                suffix += 1
            name = f"{name} ({suffix})"
        statuses[name] = EndpointStatus(
            name=name,
            url=url,
            ok=bool(record.get("ok")),
            error=_optional_str(record.get("error")),
            status=_optional_int(record.get("status")),
            elapsed=_optional_number(record.get("elapsed")),
            size=_optional_int(record.get("size")),
            final_url=_optional_str(record.get("final_url")),
            cert_expiry=_optional_number(record.get("cert_expiry")),
            attempts=_optional_int(record.get("attempts")) or 1,
            from_cache=bool(record.get("from_cache")),
            cache_age=_optional_number(record.get("cache_age")),
        )
    return statuses


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
            unit=result.get("unit"),
            metric_name=_metric_name(result.get("unit")),
            render_func=_render_func(result.get("unit")),
            path=result.get("path", ""),
            url=result.get("url", ""),
            value_as=_coerce_value_as(result.get("value_as")),
            aggregate=_coerce_aggregate(result.get("aggregate")),
            service_labels=_service_labels(result.get("labels")),
            summary=_optional_str(result.get("summary")),
            summary_fields=_summary_fields(result.get("summary_fields")),
        )
    return Section(
        error=payload.get("error"),
        items=items,
        host_labels=_coerce_host_labels(payload.get("host_labels")),
        endpoints=_endpoint_statuses(payload.get("endpoints")),
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


_AGGREGATE_LABEL = {
    "count": "number of elements",
    "sum": "sum of the elements",
    "avg": "average of the elements",
    "min": "smallest element",
    "max": "largest element",
}

_VALUE_AS_LABEL = {
    "counter": "counter (per-second rate)",
    "timestamp": "timestamp (age)",
}


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
    if entry.aggregate:
        lines.append(f"Aggregation: {_AGGREGATE_LABEL.get(entry.aggregate, entry.aggregate)}")
    if entry.value_as is not None:
        lines.append(f"Read as: {_VALUE_AS_LABEL.get(entry.value_as[0], entry.value_as[0])}")
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


# Above this, an epoch value can only be milliseconds: 1e11 seconds is the year
# 5138, while 1e11 milliseconds is 1973 - so any plausible timestamp beyond it is
# a millisecond value. Used only by the 'auto' format.
_EPOCH_MS_THRESHOLD = 1e11

# Fractional seconds with more than microsecond precision (Go/Java emit
# nanoseconds) are not accepted by datetime.fromisoformat; trim the extra digits.
_ISO_SUBSECOND = re.compile(r"(\.\d{6})\d+")


def _parse_iso_timestamp(text: str) -> float | None:
    """An ISO 8601 / RFC 3339 timestamp as a Unix epoch float, or None.

    A trailing 'Z' is normalized for older ``fromisoformat`` implementations, and
    sub-microsecond precision is trimmed. A timestamp without a time zone is read
    as UTC - the alternative, the monitoring server's local zone, would make the
    same JSON mean different things on different servers.
    """
    cleaned = _ISO_SUBSECOND.sub(r"\1", text.strip())
    if cleaned.endswith(("Z", "z")):
        cleaned = f"{cleaned[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _parse_timestamp(value: object, fmt: str) -> float | None:
    """The value as a Unix epoch float, per the configured format, or None."""
    number = _as_number(value)
    match fmt:
        case "epoch":
            return number
        case "epoch_ms":
            return number / 1000.0 if number is not None else None
        case "iso":
            return _parse_iso_timestamp(value) if isinstance(value, str) else None
    # "auto" (and anything unknown): a number is an epoch, in milliseconds when
    # it is far too large to be seconds; everything else is tried as ISO 8601.
    if number is not None:
        return number / 1000.0 if abs(number) > _EPOCH_MS_THRESHOLD else number
    return _parse_iso_timestamp(value) if isinstance(value, str) else None


def _derive(entry: Item) -> tuple[float | None, str, Callable[[float], str] | None, CheckResult]:
    """Turn a counter / timestamp value into the number that is monitored.

    Returns ``(number, metric_name, render_func, extra_results)``: the derived
    number (``None`` when it could not be derived, in which case
    ``extra_results`` explains why and the caller stops), the metric and
    rendering that fit the derived quantity, and results to emit either way (the
    raw counter reading / timestamp as a Details line - it is no longer visible
    in the summary once derived).

    A per-second rate is a different quantity from its counter and an age is a
    duration, so both get their own metric rather than writing into the absolute
    value's history.
    """
    number = _as_number(entry.value)
    if entry.value_as is None:
        return number, entry.metric_name, entry.render_func, []

    kind, cfg = entry.value_as
    if kind == "counter":
        if number is None:
            return (
                None,
                entry.metric_name,
                entry.render_func,
                [
                    Result(
                        state=State.UNKNOWN,
                        summary=f"Counter is not numeric: {_render_value(entry.value)}",
                    )
                ],
            )
        reading = Result(state=State.OK, notice=f"Counter reading: {_fmt_number(number)}")
        try:
            rate = get_rate(get_value_store(), "counter", time.time(), number, raise_overflow=True)
        except GetRateError as exc:
            # No previous reading yet (or the counter went backwards, e.g. the
            # monitored service restarted): keep the service's previous state
            # rather than inventing a rate.
            return None, entry.metric_name, entry.render_func, [reading, IgnoreResults(str(exc))]
        return (
            rate,
            _rate_metric_name(entry.unit),
            _rate_render_func(entry.unit),
            [reading],
        )

    fmt = cfg.get("format") if isinstance(cfg, dict) else None
    stamp = _parse_timestamp(entry.value, fmt if isinstance(fmt, str) else "auto")
    if stamp is None:
        return (
            None,
            entry.metric_name,
            entry.render_func,
            [
                Result(
                    state=State.UNKNOWN,
                    summary=f"Not a valid timestamp: {_render_value(entry.value)}",
                )
            ],
        )
    reading = Result(state=State.OK, notice=f"Timestamp: {_render_value(entry.value)}")
    # An age is a duration in seconds, so it graphs and renders as one unless the
    # rule explicitly chose a unit (e.g. after a transform into hours).
    if isinstance(entry.unit, str):
        return time.time() - stamp, entry.metric_name, entry.render_func, [reading]
    return time.time() - stamp, _AGE_METRIC, _render_seconds, [reading]


# What the derived number is called in the summary and the levels line.
_VALUE_LABEL = {"counter": "Rate", "timestamp": "Age"}


def _value_label(value_as: _ValueAs) -> str:
    return _VALUE_LABEL.get(value_as[0], "Value") if value_as is not None else "Value"


def _value_results(
    entry: Item,
    levels_upper: _Levels,
    levels_lower: _Levels,
    match: _Match,
) -> CheckResult:
    number, metric_name, render_func, extra = _derive(entry)
    yield from extra
    if entry.value_as is not None and number is None:
        return  # _derive already explained why (UNKNOWN / no rate yet)
    label = _value_label(entry.value_as)
    # Once derived, the summary shows the derived number, not the raw JSON value.
    derived = entry.value_as is not None
    if derived and match is not None:
        # A regex over a rate or an age is never what was meant; say so instead of
        # matching against a computed number.
        yield Result(
            state=State.OK,
            notice=f"String matching does not apply to a derived {label.lower()}",
        )

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
            metric_name=metric_name,
            label=label,
            render_func=render_func,
        )
        return

    # Levels configured on a value that is not numeric: a misconfiguration we
    # surface rather than silently pass - even when an 'expected' regex is also
    # set (otherwise it would be hidden behind the regex result).
    misconfigured_levels = has_levels and number is None
    misconfig_note = " (levels configured but value is not numeric)"

    if match is not None and not derived:
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
    # the transformed / derived number when one was computed, else the raw JSON
    # value.
    if number is not None:
        if render_func is not None:
            shown = render_func(number)
        else:
            shown = _fmt_number(number) if (entry.calc or derived) else _render_value(entry.value)
        yield Result(state=State.OK, summary=f"{label}: {shown}")
        yield Metric(metric_name, number)
    else:
        yield Result(state=State.OK, summary=f"Value: {_render_value(entry.value)}")


# One '{path}' placeholder of a summary template; mirrors the agent's own regex.
_SUMMARY_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")

# A summary is a single line that travels into notifications and views, so an
# API returning a paragraph (or a stack trace) must not push the rest of the
# output out of shape. Newlines would additionally split summary from details.
_SUMMARY_MAX_LENGTH = 160


def _render_summary(entry: Item) -> str | None:
    """The configured extra summary text with its placeholders filled in.

    A path the agent could not resolve renders as '(n/a)' rather than as nothing:
    a mistyped path should be visible in the service, not silently absent.
    """
    if not entry.summary:
        return None
    text = _SUMMARY_PLACEHOLDER.sub(
        lambda match: entry.summary_fields.get(match.group(1).strip(), "(n/a)"),
        entry.summary,
    )
    text = " ".join(text.split())  # one line, no runs of whitespace
    if not text:
        return None
    if len(text) > _SUMMARY_MAX_LENGTH:
        text = text[: _SUMMARY_MAX_LENGTH - 1].rstrip() + "…"
    return text


def _with_summary(results: CheckResult, extra: str | None) -> CheckResult:
    """``results`` with ``extra`` appended to the first summary in the stream.

    Appending - rather than replacing - is what keeps this presentation-only:
    the value, the levels annotation that check_levels writes and the state all
    stay exactly as they were. Results carrying only a notice (empty summary)
    are skipped, so the text lands on the line an operator actually reads.
    """
    if not extra:
        yield from results
        return
    appended = False
    for result in results:
        if appended or not isinstance(result, Result) or not result.summary:
            yield result
            continue
        appended = True
        summary = f"{result.summary}, {extra}"
        if result.details == result.summary:
            # Details that merely mirror the summary keep mirroring it, so the
            # context shows up in the service's detail view as well.
            yield Result(state=result.state, summary=summary)
        else:
            yield Result(state=result.state, summary=summary, details=result.details)


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

    # The extra summary text applies to the value line either way: on a missing
    # path the context ("(n/a)" or whatever the API did return) is arguably the
    # more useful half of the message.
    extra = _render_summary(entry)

    if not entry.found:
        yield from _with_summary(
            iter([Result(state=State.UNKNOWN, summary=entry.error or "not found")]), extra
        )
        yield from _context(entry, match)
        return

    yield from _with_summary(_value_results(entry, levels_upper, levels_lower, match), extra)
    yield from _context(entry, match)


def discover_json_api_endpoint(section: Section) -> DiscoveryResult:
    """One service per configured endpoint - no field configuration needed.

    These come for free with any rule: whether the API answered at all, with
    which status, and how long it took. Unwanted ones are removed the standard
    way, with a "Disabled services" rule.
    """
    for name in section.endpoints:
        yield Service(item=name)


def check_json_api_endpoint(
    item: str, params: Mapping[str, object], section: Section
) -> CheckResult:
    endpoint = section.endpoints.get(item)
    if endpoint is None:
        return

    details = [f"URL: {endpoint.url}"]
    # Only worth showing when a redirect actually moved the request elsewhere -
    # that is exactly the case where the URL in the rule misleads.
    if endpoint.final_url and endpoint.final_url != endpoint.url:
        details.append(f"Final URL: {endpoint.final_url}")

    # Every attempt after the first one absorbed a failure. Reporting it is what
    # keeps a retry policy from quietly turning a degrading API into a green
    # service: the request succeeded, but not on the first try.
    retried = max(endpoint.attempts - 1, 0)
    retry_note = f"succeeded after {retried} retr{'y' if retried == 1 else 'ies'}"

    if not endpoint.ok:
        # The request itself failed (unreachable, TLS, timeout, unexpected status,
        # not JSON). CRIT by default; a rule can soften it to WARN for an endpoint
        # that is allowed to be down.
        state = State(_coerce_state(params.get("state_unreachable"), 2))
        failure = endpoint.error or "Request failed"
        if retried:
            failure = f"{failure} (after {endpoint.attempts} attempts)"
        yield Result(
            state=state,
            summary=failure,
            details="\n".join([failure, *details]),
        )
        return

    status = f"HTTP {endpoint.status}" if endpoint.status is not None else "Request succeeded"
    if endpoint.from_cache:
        # Say so in the SUMMARY, not just the details: "HTTP 200" on its own would
        # claim the API answered just now, when nothing was asked.
        age = f" ({_render_seconds(endpoint.cache_age)} old)" if endpoint.cache_age else ""
        status = f"{status}, from cache{age}"
    yield Result(state=State.OK, summary=status, details="\n".join([status, *details]))

    if retried:
        # Default OK: a retry doing its job is not itself a problem. A rule can
        # raise it for an endpoint whose flakiness IS worth knowing about.
        yield Result(
            state=State(_coerce_state(params.get("state_retried"), 0)),
            summary=retry_note,
        )

    if endpoint.elapsed is not None:
        yield from check_levels(
            endpoint.elapsed,
            levels_upper=_coerce_levels(params.get("response_time_levels")),
            metric_name="json_api_response_time",
            label="Response time",
            render_func=render.timespan,
        )
    if endpoint.size is not None:
        yield Result(state=State.OK, notice=f"Response size: {render.bytes(endpoint.size)}")
        yield Metric("json_api_response_size", endpoint.size)
    if endpoint.cert_expiry is not None:
        # Days rather than seconds: it is what a certificate policy is written in
        # ("renew 30 days out"), so it is what the levels ask for. LOWER levels -
        # the alert is about running out of time.
        yield from check_levels(
            (endpoint.cert_expiry - time.time()) / 86400.0,
            levels_lower=_coerce_levels(params.get("cert_expiry_levels")),
            metric_name="json_api_cert_expiry",
            label="Certificate expires in",
            render_func=_render_days,
            notice_only=True,
        )


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

# A second plugin on the same section: the endpoint's own status service, whose
# item is the endpoint name (or its URL). Kept apart from the field services so
# its parameters (response time, state when unreachable) are their own ruleset.
check_plugin_json_api_endpoint = CheckPlugin(
    name="json_api_endpoint",
    sections=["json_api"],
    service_name="JSON API %s",
    discovery_function=discover_json_api_endpoint,
    check_function=check_json_api_endpoint,
    check_ruleset_name="json_api_endpoint",
    check_default_parameters={},
)
