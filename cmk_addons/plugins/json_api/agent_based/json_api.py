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
import email.utils
import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cmk.agent_based.v2 import (
    AgentSection,
    Attributes,
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    GetRateError,
    HostLabel,
    HostLabelGenerator,
    IgnoreResults,
    InventoryPlugin,
    InventoryResult,
    Metric,
    Result,
    Service,
    ServiceLabel,
    State,
    StringTable,
    TableRow,
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


def _boundaries(value_range: object) -> tuple[float | None, float | None] | None:
    """The rule's value range as the ``boundaries`` a Metric takes.

    ``None`` for an absent or unusable range, and for a range with neither end -
    the ruleset rejects that, but a hand-written rule is not bound by the form.
    An end that is present but not a number is dropped on its own rather than
    voiding the other: half a range still fixes half the scale.
    """
    if not isinstance(value_range, dict):
        return None

    def _end(key: str) -> float | None:
        value = value_range.get(key)
        return (
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None
        )

    low, high = _end("min"), _end("max")
    return None if low is None and high is None else (low, high)


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
    shown = int(rounded) if rounded.is_integer() else rounded
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
    return _fmt_number(number) if number.is_integer() else f"{number:.6g}"


# The column a '[*]' wildcard's inventory table is keyed by. Checkmk merges the
# rows that share it, which is what lets several fields over one collection fill
# in columns of the same row - so it has to be one name, chosen here rather than
# in the rule.
#
# It cannot be a name a field is likely to want for a column of its own: a
# column that is also the key column makes TableRow raise, which would fail the
# host's whole inventory. It used to be 'name', which is the most natural column
# a field could ask for ('nodes[*].name'), so it is now 'element' - a name that
# describes what the column holds and that Setup reserves.
_INVENTORY_ROW_KEY = "element"


@dataclass(frozen=True)
class InventoryTarget:
    """Where one field's value goes in the HW/SW inventory tree."""

    path: tuple[str, ...]
    key: str
    # The '[*]' element's label. Set means "one table row per element, keyed by
    # this"; None means a plain attribute of the node.
    row_key: str | None = None
    # Whether the field ALSO gets a service. Off is the point of the feature.
    keep_service: bool = False


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
    # The metric name derived from the unit - what this field emits unless the
    # rule overrode it (metric_override) or it shares a service.
    metric_name: str
    render_func: Callable[[float], str] | None
    path: str
    url: str
    # The metric name stated in the rule, used verbatim when set. The escape
    # hatch for a name that has to be known in advance: the Gauge / Single
    # metric / Bar chart dashboard widgets are bound to one metric BY NAME, and
    # a shared service's generated names are neither obvious nor offered by the
    # widget's dropdown until it is filtered to a host and a service.
    metric_override: str | None = None
    # The value's configured range as ``(min, max)``, either end possibly None -
    # the metric's boundaries. Presentation only: it fixes the scale of the
    # graph, the dial of a gauge dashboard widget and the fill of the service
    # list's bar, and never touches the state.
    boundaries: tuple[float | None, float | None] | None = None
    # How to read the value: as it stands, as a counter, or as a timestamp.
    value_as: _ValueAs = None
    # The transform's second operand, resolved by the agent in this service's own
    # scope. None means "no second path, or it did not resolve to anything".
    calc_other: float | None = None
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
    # This value's name WITHIN its service, when several fields share one. None
    # for the ordinary case, where the field IS the service and the generic
    # 'Value' label is right.
    label: str | None = None
    # Set when this field belongs in the inventory tree rather than (or as well
    # as) in a service.
    inventory: "InventoryTarget | None" = None
    # The JSON this value was read from - the '[*]' element, the object holding
    # it, or the whole response - already pretty-printed, capped and stripped of
    # the endpoint's secret by the agent. None means the rule did not ask for it,
    # which is the default.
    context: str | None = None


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
    # Pagination: how many pages the agent read and merged (1 = one page, which
    # is also what an endpoint that does not follow pagination reports), how many
    # elements the merged collection ended up with, and why following stopped
    # while a further page still existed. The last one is the important half: it
    # means the collection - and therefore every count, aggregation and wildcard
    # built from it - is INCOMPLETE, which unfollowed pagination never says.
    pages: int = 1
    elements: int | None = None
    pagination_stopped: str | None = None
    # The raw response, for an endpoint configured to report it: the body as it
    # came off the wire (already capped and secret-stripped by the agent), how
    # long it really was, and whether what is here is only its beginning. None
    # means the rule did not ask for it, which is the default.
    body: str | None = None
    body_truncated: bool = False
    body_size: int | None = None
    # The response headers, credential-bearing ones already masked by the agent.
    # None means they were not requested.
    headers: Mapping[str, str] | None = None
    # Whether this endpoint prefixes its field service names. It decides which of
    # the two endpoint check plugins discovers this record, and therefore how the
    # endpoint's own service is named - see check_plugin_json_api_endpoint.
    prefixed: bool = False


@dataclass(frozen=True)
class Section:
    error: str | None
    # Service name -> its entries, in configuration order. Exactly one for the
    # ordinary one-field-one-service case; several where fields were configured
    # to share a service, which is what lets the check yield one result per field
    # and leave the worst-state aggregation to Checkmk.
    items: Mapping[str, tuple[Item, ...]]
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
        case ["must_match", dict() as cfg]:
            pattern = cfg.get("pattern")
            if isinstance(pattern, str):
                return (
                    "must_match",
                    {
                        "pattern": pattern,
                        "state_no_match": _coerce_state(cfg.get("state_no_match"), 2),
                    },
                )
        case ["must_match", str() as pattern]:
            return ("must_match", {"pattern": pattern, "state_no_match": 2})
        case ["state_map", dict() as cfg]:
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
        case ["counter", _] | "counter":
            return ("counter", None)
        case ["timestamp", dict() as cfg]:
            fmt = cfg.get("format")
            return ("timestamp", {"format": fmt if isinstance(fmt, str) and fmt else "auto"})
        case ["timestamp", _] | "timestamp":
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


def _inventory_target(raw: object) -> InventoryTarget | None:
    """The agent's inventory placement for one result, string-validated."""
    if not isinstance(raw, dict):
        return None
    node, key = raw.get("node"), raw.get("key")
    if not isinstance(node, str) or not isinstance(key, str) or not node or not key:
        return None
    row_key = raw.get("row_key")
    return InventoryTarget(
        # Stripped to match the ruleset validator, which strips before checking:
        # otherwise 'software . applications' passes Setup and then writes to a
        # node distinct from 'software.applications' - invisible in the views and
        # awkward to clean up per host.
        path=tuple(stripped for segment in node.split(".") if (stripped := segment.strip())),
        key=key,
        row_key=row_key if isinstance(row_key, str) and row_key else None,
        keep_service=bool(raw.get("keep_service")),
    )


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


def _unique_name(name: str, taken: Mapping[str, object]) -> str:
    """``name``, suffixed ' (2)', ' (3)', ... until it is not already in ``taken``.

    Two endpoints (or two services) configured with the same name would
    otherwise collapse into one, silently dropping the second.
    """
    if name not in taken:
        return name
    suffix = 2
    while f"{name} ({suffix})" in taken:
        suffix += 1
    return f"{name} ({suffix})"


def _response_headers(raw: object) -> dict[str, str] | None:
    """The reported response headers, or None when the endpoint reports none.

    An empty map is a real answer ("asked for, and there were none"), so it is
    kept distinct from None - the check says so rather than staying silent.
    """
    if not isinstance(raw, dict):
        return None
    return {str(name): str(value) for name, value in raw.items()}


def _endpoint_statuses(raw: object) -> dict[str, EndpointStatus]:
    """The agent's ``endpoints`` list, keyed by the service item.

    A duplicated name (two endpoints configured with the same one) is
    disambiguated the same way a duplicated service name is, so no endpoint can
    silently disappear from the service list.

    The key is the SERVICE ITEM, which is the endpoint name - or, for an endpoint
    that prefixes its field services, that name followed by ' API'. The two
    endpoint check plugins render their item into different service names, so
    each record has exactly one item and one plugin that discovers it.
    """
    statuses: dict[str, EndpointStatus] = {}
    for record in raw if isinstance(raw, list) else []:
        if not isinstance(record, dict):
            continue
        url = _optional_str(record.get("url")) or "?"
        name = _optional_str(record.get("name")) or url
        prefixed = bool(record.get("prefixed"))
        item = _unique_name(f"{name} API" if prefixed else name, statuses)
        statuses[item] = EndpointStatus(
            name=name,
            prefixed=prefixed,
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
            pages=_optional_int(record.get("pages")) or 1,
            elements=_optional_int(record.get("elements")),
            pagination_stopped=_optional_str(record.get("pagination_stopped")),
            body=_optional_str(record.get("body")),
            body_truncated=bool(record.get("body_truncated")),
            body_size=_optional_int(record.get("body_size")),
            headers=_response_headers(record.get("headers")),
        )
    return statuses


def parse_json_api(string_table: StringTable) -> Section | None:
    if not string_table:
        return None
    payload = json.loads(string_table[0][0])
    items: dict[str, list[Item]] = {}
    for result in payload["results"]:
        # A field reported into a shared service carries the name of its line;
        # it JOINS that service rather than becoming one, so it is appended
        # instead of being disambiguated.
        #
        # A field of its own still gets the defensive uniqueness backstop (the
        # agent already makes wildcard labels unique), so a duplicate service
        # name can never silently drop a service - and so an accidental
        # duplicate is never quietly merged into a combined service, which is
        # something the rule has to ask for.
        label = _optional_str(result.get("label"))
        name = result["service"] if label is not None else _unique_name(result["service"], items)
        entry = Item(
            found=result["found"],
            value=result["value"],
            error=result["error"],
            levels_upper=_coerce_levels(result.get("levels_upper")),
            levels_lower=_coerce_levels(result.get("levels_lower")),
            match=_coerce_match(result.get("match"), result.get("expected")),
            calc=_coerce_calc(result.get("calc")),
            calc_other=_optional_number(result.get("calc_other")),
            unit=result.get("unit"),
            boundaries=_boundaries(result.get("value_range")),
            metric_name=_metric_name(result.get("unit")),
            metric_override=_optional_str(result.get("metric_name")),
            render_func=_render_func(result.get("unit")),
            path=result.get("path", ""),
            url=result.get("url", ""),
            value_as=_coerce_value_as(result.get("value_as")),
            aggregate=_coerce_aggregate(result.get("aggregate")),
            service_labels=_service_labels(result.get("labels")),
            summary=_optional_str(result.get("summary")),
            summary_fields=_summary_fields(result.get("summary_fields")),
            inventory=_inventory_target(result.get("inventory")),
            label=label,
            context=_optional_str(result.get("context")),
        )
        items.setdefault(name, []).append(entry)
    return Section(
        error=payload.get("error"),
        items={name: tuple(entries) for name, entries in items.items()},
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


def _visible_entries(entries: tuple[Item, ...]) -> list[Item]:
    """The entries of a service that actually create one.

    A field written to the inventory is a fact, not a state: by default it
    creates NO service, which is the whole point - it must not consume a service
    slot and a check interval to report something that changes twice a year. A
    shared service exists as long as at least one of its fields is visible.
    """
    return [e for e in entries if e.inventory is None or e.inventory.keep_service]


def discover_json_api(section: Section) -> DiscoveryResult:
    for name, entries in section.items.items():
        visible = _visible_entries(entries)
        if not visible:
            continue
        # Seed each service's discovered parameters with the thresholds / match
        # configured in the special-agent rule. These become the service's
        # defaults; a "Generic JSON API" check-parameters rule then overrides
        # them per folder/host/service (precedence: default < discovered < rule).
        # Only carry the keys that are actually set, so an unset level stays
        # absent (and the check falls back to the section for pre-upgrade
        # autochecks that have no discovered parameters at all).
        #
        # A service several fields share gets NONE of this: one set of levels
        # cannot describe several fields, and seeding it from whichever field
        # happened to be first would apply that field's thresholds to all of
        # them. Those services keep their per-field configuration from the agent
        # rule instead, which is where it was written.
        params: dict[str, object] = {}
        if len(visible) == 1:
            entry = visible[0]
            if entry.levels_upper is not None:
                params["levels_upper"] = entry.levels_upper
            if entry.levels_lower is not None:
                params["levels_lower"] = entry.levels_lower
            if entry.match is not None:
                params["match"] = entry.match
        # Service labels the agent resolved for this service, namespaced. Attached
        # at discovery, so they update when the service is re-discovered. Where
        # fields share a service they contribute to one label set (later wins per
        # key), because the labels describe the SERVICE, not the line.
        labels = {key: value for entry in visible for key, value in entry.service_labels}
        yield Service(
            item=name,
            parameters=params,
            labels=[ServiceLabel(f"{_LABEL_NS}{key}", value) for key, value in labels.items()],
        )


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


def _response_context(entry: Item) -> CheckResult:
    """The JSON the value came from, as a details-only block.

    The service that goes CRIT is the one that notifies, and its details are what
    reach the notification as $LONGSERVICEOUTPUT$ - so this is where the API's
    own answer has to be for the person reading the alert, who often cannot
    reach the endpoint at all (wrong network, no credentials) and could otherwise
    only see it on the endpoint's own service, which stayed OK and said nothing.

    Emitted last, after the value and the context lines: the first line of the
    details stays the one an operator scans.
    """
    if entry.context:
        yield Result(state=State.OK, notice=f"Response context:\n{entry.context}")


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


def _apply_calc(value: float, expr: str, other: float | None = None) -> float:
    """Evaluate a small arithmetic expression over ``value`` (and ``other``).

    Only numeric literals, the names ``value`` and ``other``, parentheses and
    + - * / (incl. unary +/-) are supported; anything else raises
    ``ValueError``. This walks the AST and never uses ``eval``, so a rule cannot
    smuggle in code execution. Operators are dispatched with explicit
    ``isinstance`` branches (rather than a lookup table of ``operator``
    callables) so the arithmetic stays statically typed.

    ``other`` is the second path's value, resolved by the agent. An expression
    naming it when it did not resolve is an error rather than a default: silently
    substituting 0 or 1 would turn a missing field into a plausible-looking
    ratio.
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
        if isinstance(node, ast.Name):
            if node.id == "value":
                return value
            if node.id == "other":
                if other is None:
                    raise ValueError("the second path did not resolve to a number")
                return other
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


def _parse_http_date(text: str) -> float | None:
    """An HTTP-date (RFC 9110) as a Unix epoch float, or None.

    The format 'Last-Modified', 'Expires' and a date-form 'Retry-After' are
    written in - 'Wed, 21 Oct 2015 07:28:00 GMT' - which is neither a number nor
    ISO 8601, so reading a header as a timestamp needs it. An HTTP-date is
    always GMT; a parser that nonetheless returns a naive datetime is read as
    UTC rather than as the monitoring server's local zone.
    """
    try:
        parsed = email.utils.parsedate_to_datetime(text.strip())
    except (TypeError, ValueError):
        return None
    if parsed is None:
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
    # it is far too large to be seconds; a string is tried as ISO 8601 and then
    # as an HTTP-date, so a 'Last-Modified' header read via '@header.' resolves
    # without the rule having to name a format.
    if number is not None:
        return number / 1000.0 if abs(number) > _EPOCH_MS_THRESHOLD else number
    if not isinstance(value, str):
        return None
    # Not `iso or http_date`: a timestamp of exactly epoch 0 is a real value and
    # would be discarded as falsy.
    stamp = _parse_iso_timestamp(value)
    return stamp if stamp is not None else _parse_http_date(value)


def _counter_key(entry: Item) -> str:
    """The value-store key this field's counter reading is kept under.

    The store is scoped to the service, which is enough while a field IS a
    service - but several fields can share one (see ``label``), and every line
    of a '[*]' wildcard reporting into a shared service is such a field too.
    They would then all read and write ONE reading: each line would be
    differenced against whichever line was stored last, so one reports a
    nonsense rate and the next sees its counter go backwards and reports none at
    all - alternating on every check.

    The line's own name is what distinguishes them, and it is stable across
    checks (it carries the element's label for a wildcard). A field of its own
    keeps the bare key, so an existing counter's stored reading survives this
    change rather than costing every such service one "no rate yet" check.
    """
    return "counter" if entry.label is None else f"counter.{entry.label}"


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
            rate = get_rate(
                get_value_store(), _counter_key(entry), time.time(), number, raise_overflow=True
            )
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


def _value_label(value_as: _ValueAs, line: str | None = None) -> str:
    """What this value is called in the summary.

    A field of its own is just 'Value' (or 'Rate'/'Age' once derived) - the
    service name already says what it is. A field sharing a service has to say
    which of them it is, so it uses its own name instead.
    """
    if line:
        return line
    return _VALUE_LABEL.get(value_as[0], "Value") if value_as is not None else "Value"


def _metric_slug(line: str) -> str:
    """``line`` as a metric-name suffix: lower-case, underscores, nothing else."""
    slug = re.sub(r"[^a-z0-9]+", "_", line.lower()).strip("_")
    return slug or "value"


def _line_metric_name(metric_name: str, line: str | None) -> str:
    """The metric name for one line of a shared service.

    Metric names have to be unique within a service, and a service's fields
    routinely share a unit - two byte counts would otherwise both be
    'json_api_bytes' and collide. The line's own name disambiguates them.

    Not enough on its own: the slug folds every non-alphanumeric run to '_', so
    'Root used' and 'Root-used' still arrive here as the same name. That last
    collision is resolved by _unique_metric_name, over the whole service.

    The result is not one of the metrics the graphing module declares, so
    Checkmk titles it after the name itself and renders it as a plain number.
    That is the price of a service holding an open set of fields; a field that
    needs its declared unit and colour keeps a service of its own.
    """
    return metric_name if line is None else f"{metric_name}_{_metric_slug(line)}"


def _unique_metric_name(name: str, taken: set[str]) -> str:
    """``name``, suffixed '_2', '_3', ... until this service has not used it.

    The sibling of _unique_name, which does the same for service items. It
    cannot be that helper: Checkmk accepts only letters, digits and underscores
    in a metric name, so ' (2)' is not available here.

    ``taken`` is updated in place - the caller allocates the whole service's
    names through one set.
    """
    candidate = name
    suffix = 2
    while candidate in taken:
        candidate = f"{name}_{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def _emitted_metric_name(entry: Item) -> str:
    """The metric ``entry`` will emit, from its configuration alone.

    Mirrors the name _derive returns on the branches that go on to emit a
    metric; on the branches that cannot derive a number it emits none, so the
    two can never disagree about a name that is actually used (asserted by
    test_the_predicted_metric_name_matches_what_derive_returns).

    Reading this from the configuration rather than from the value is the point:
    the names of a shared service's fields must not depend on what the API
    happened to answer this time round. A field that is briefly missing, or a
    counter still waiting for its second reading, would otherwise renumber the
    fields after it and split their history.
    """
    if entry.value_as is None:
        return entry.metric_name
    if entry.value_as[0] == "counter":
        return _rate_metric_name(entry.unit)
    # A timestamp becomes an age: seconds, unless the rule named a unit itself.
    return entry.metric_name if isinstance(entry.unit, str) else _AGE_METRIC


def _field_metric_name(entry: Item) -> str:
    """The name this field asks for, before the service-wide uniqueness pass.

    A name stated in the rule is used verbatim, in a shared service too: the
    whole point of the option is a name the operator already knows, so appending
    the line's slug to it would defeat it.
    """
    if entry.metric_override is not None:
        return entry.metric_override
    return _line_metric_name(_emitted_metric_name(entry), entry.label)


def _service_metric_names(entries: Sequence[Item]) -> list[str]:
    """One metric name per entry of a service, unique within it.

    Allocated for EVERY entry, in section order, including the ones that will
    not emit a metric at all (an inventory-only field, a non-numeric value):
    an entry's name then depends only on the rule, never on this run's data.
    """
    taken: set[str] = set()
    return [_unique_metric_name(_field_metric_name(entry), taken) for entry in entries]


def _value_results(
    entry: Item,
    levels_upper: _Levels,
    levels_lower: _Levels,
    match: _Match,
    metric_name: str,
) -> CheckResult:
    # The metric's name is allocated for the whole service at once (see
    # _service_metric_names), not derived here: only the caller can see the
    # sibling fields this one has to stay distinct from.
    number, _derived_name, render_func, extra = _derive(entry)
    yield from extra
    if entry.value_as is not None and number is None:
        return  # _derive already explained why (UNKNOWN / no rate yet)
    label = _value_label(entry.value_as, entry.label)
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
            number = _apply_calc(number, entry.calc, entry.calc_other)
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
            boundaries=entry.boundaries,
        )
        if match is not None and not derived:
            # The levels decide the state of a numeric value, so the matching
            # configured alongside them never runs. Said out loud rather than
            # dropped in silence: the Details otherwise list the pattern (see
            # _context) as though it applied, and a state map that quietly does
            # nothing is exactly the kind of thing an operator finds out about
            # during the incident it was written for.
            yield Result(
                state=State.OK,
                notice="String matching is not applied when levels are configured",
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
        summary = f"{label}: {text}"
        if description:
            summary += f" ({description})"
        yield Result(state=state, summary=summary + note)
        return

    if misconfigured_levels:
        yield Result(
            state=State.WARN, summary=f"{label}: {_render_value(entry.value)}{misconfig_note}"
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
        yield Metric(metric_name, number, boundaries=entry.boundaries)
    else:
        yield Result(state=State.OK, summary=f"{label}: {_render_value(entry.value)}")


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
    entries = section.items.get(item)
    if not entries:
        return

    # Several fields sharing one service each get their own result, and Checkmk's
    # own aggregation makes the service's state the worst of them - there is no
    # combining to do here beyond yielding them all.
    #
    # A check-parameters rule cannot describe such a service (one set of levels,
    # several fields), so it is not applied to one: those fields keep the levels
    # and matching the agent rule gave them, which the details spell out.
    combined = len(entries) > 1
    metric_names = _service_metric_names(entries)
    for index, entry in enumerate(entries):
        if entry.inventory is not None and not entry.inventory.keep_service:
            continue  # an inventory-only field of a shared service
        # Effective parameters: a check-parameters rule (or the discovered
        # defaults) wins per key; where a key is absent we fall back to the value
        # the agent embedded in the section, so services discovered by a
        # pre-parameters version keep their thresholds until re-discovered.
        levels_upper = (
            _coerce_levels(params["levels_upper"])
            if "levels_upper" in params and not combined
            else entry.levels_upper
        )
        levels_lower = (
            _coerce_levels(params["levels_lower"])
            if "levels_lower" in params and not combined
            else entry.levels_lower
        )
        match = (
            _coerce_match(params["match"]) if "match" in params and not combined else entry.match
        )

        # Several fields share this service's Details, so each one opens with a
        # header saying which field the lines below belong to. Emitted before the
        # value and its context, which is what makes the Details readable as one
        # block per field rather than an undifferentiated list.
        if entry.label:
            yield Result(state=State.OK, notice=f"[{entry.label}]")

        # The extra summary text applies to the value line either way: on a
        # missing path the context ("(n/a)" or whatever the API did return) is
        # arguably the more useful half of the message.
        extra = _render_summary(entry)

        if not entry.found:
            missing = entry.error or "not found"
            yield from _with_summary(
                iter(
                    [
                        Result(
                            state=State.UNKNOWN,
                            summary=f"{entry.label}: {missing}" if entry.label else missing,
                        )
                    ]
                ),
                extra,
            )
            yield from _context(entry, match)
            yield from _response_context(entry)
            continue

        yield from _with_summary(
            _value_results(entry, levels_upper, levels_lower, match, metric_names[index]), extra
        )
        yield from _context(entry, match)
        yield from _response_context(entry)


def discover_json_api_endpoint(section: Section) -> DiscoveryResult:
    """One service per configured endpoint - no field configuration needed.

    These come for free with any rule: whether the API answered at all, with
    which status, and how long it took. Unwanted ones are removed the standard
    way, with a "Disabled services" rule.

    Only the endpoints that do NOT prefix their field service names: the others
    are discovered by the sibling plugin below, which names them differently.
    """
    for item, endpoint in section.endpoints.items():
        if not endpoint.prefixed:
            yield Service(item=item)


def discover_json_api_endpoint_prefixed(section: Section) -> DiscoveryResult:
    """The same service, for an endpoint that prefixes its field service names.

    Such an endpoint's fields read 'JSON <name> Status'. Leaving its own service
    as 'JSON API <name>' would sort the one service describing the request away
    from every service it describes - all the 'JSON API ...' services cluster
    together, and none of them sits with its own group. The item already carries
    the ' API' suffix (see _endpoint_statuses), so this plugin's 'JSON %s' renders
    it as 'JSON <name> API'.
    """
    for item, endpoint in section.endpoints.items():
        if endpoint.prefixed:
            yield Service(item=item)


def _raw_response_details(endpoint: EndpointStatus) -> list[str]:
    """Details-only lines carrying the response headers and body verbatim.

    Opt-in per endpoint, because a response body is the one thing here that can
    be arbitrarily large and can hold anything the API returns. It is reported
    at all because the alternative - the source URL - is often unreachable from
    the browser reading the service, and always shows the API as it is NOW
    rather than as it was when the check ran.
    """
    lines: list[str] = []
    if endpoint.headers is not None:
        lines.append("Response headers:")
        lines += [f"  {name}: {value}" for name, value in endpoint.headers.items()] or ["  (none)"]
    if endpoint.body is not None:
        # Say what was cut off, or the reader has no way to tell a short response
        # from the beginning of a long one. The full length is unknown where the
        # body was read for this report alone and reading stopped at the limit,
        # so it is not claimed - only the truncation is.
        shown = len(endpoint.body.encode("utf-8", "replace"))
        if endpoint.body_truncated and endpoint.body_size is not None:
            about = f"first {shown} of {endpoint.body_size} bytes"
        elif endpoint.body_truncated:
            about = f"first {shown} bytes, truncated"
        elif endpoint.body_size is not None:
            about = f"{endpoint.body_size} bytes"
        else:
            about = f"{shown} bytes"
        lines.append(f"Response body ({about}):")
        # Verbatim, newlines and all: a pretty-printed body is far easier to read
        # in the details than one folded onto a single line.
        lines.append(endpoint.body)
    return lines


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
            details="\n".join([failure, *details, *_raw_response_details(endpoint)]),
        )
        return

    status = f"HTTP {endpoint.status}" if endpoint.status is not None else "Request succeeded"
    if endpoint.from_cache:
        # Say so in the SUMMARY, not just the details: "HTTP 200" on its own would
        # claim the API answered just now, when nothing was asked.
        age = f" ({_render_seconds(endpoint.cache_age)} old)" if endpoint.cache_age else ""
        status = f"{status}, from cache{age}"
    yield Result(
        state=State.OK,
        summary=status,
        details="\n".join([status, *details, *_raw_response_details(endpoint)]),
    )

    if retried:
        # Default OK: a retry doing its job is not itself a problem. A rule can
        # raise it for an endpoint whose flakiness IS worth knowing about.
        yield Result(
            state=State(_coerce_state(params.get("state_retried"), 0)),
            summary=retry_note,
        )

    if endpoint.pages > 1 or endpoint.elements is not None:
        # Only where pagination is actually configured, and then always - "1 page,
        # 7 elements" is the confirmation that the collection is whole, which is
        # the question this setting exists to answer.
        elements = f", {endpoint.elements} elements" if endpoint.elements is not None else ""
        yield Result(
            state=State.OK,
            notice=f"Pages read: {endpoint.pages}{elements}",
        )
    if endpoint.pagination_stopped:
        # A further page existed and was not read, so every service built from
        # this collection is describing part of it. WARN by default - the
        # collection being incomplete is precisely what following the pages was
        # meant to prevent - and in the SUMMARY, because "HTTP 200" alone would
        # claim the whole answer is here.
        yield Result(
            state=State(_coerce_state(params.get("state_pagination_stopped"), 1)),
            summary=f"Collection incomplete: {endpoint.pagination_stopped}",
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


def _inventory_value(value: object) -> int | float | str | bool | None:
    """A JSON value as an inventory attribute/column value.

    The tree holds scalars. The agent already renders an object or array as its
    JSON text, and a non-finite number is not representable, so anything left
    that is not a scalar is dropped rather than written as a Python repr.
    """
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    return None


def inventory_json_api(section: Section) -> InventoryResult:
    """Write the fields configured as inventory into the host's tree.

    Two shapes, decided by whether the extraction had a '[*]' wildcard:

    * no wildcard - a plain attribute of the node ('version', 'region', ...);
    * a wildcard - one table ROW per element, keyed by the element's own label,
      which is what turns 'nodes[*].version' into a table of nodes with a
      version column. Checkmk merges rows sharing a key column, so several
      extractions over the same collection fill in columns of the same row.

    A field whose path was not in the response contributes nothing: the tree
    keeps what it learned last time rather than recording a hole.
    """
    for entry in (entry for entries in section.items.values() for entry in entries):
        target = entry.inventory
        if target is None or not entry.found:
            continue
        value = _inventory_value(entry.value)
        if value is None:
            continue
        if target.row_key is None:
            yield Attributes(
                path=list(target.path),
                inventory_attributes={target.key: value},
            )
            continue
        if target.key == _INVENTORY_ROW_KEY:
            # A column cannot also be the key column: TableRow refuses that
            # outright, and an exception here would take the WHOLE host's
            # inventory with it, every field of every rule. Setup reserves the
            # name so a rule cannot ask for this; only a column name DERIVED
            # from the path (a JSON field literally called 'element' inside the
            # collection) can still reach it, and dropping that one column is
            # the smallest thing that can go wrong here.
            continue
        yield TableRow(
            path=list(target.path),
            key_columns={_INVENTORY_ROW_KEY: target.row_key},
            inventory_columns={target.key: value},
        )


agent_section_json_api = AgentSection(
    name="json_api",
    parse_function=parse_json_api,
    host_label_function=host_label_json_api,
)

# Facts, not states: a version, a build, a licence tier. They belong in the
# inventory tree - searchable across hosts, with a history of its own - rather
# than in a service that is OK forever.
inventory_plugin_json_api = InventoryPlugin(
    name="json_api",
    sections=["json_api"],
    inventory_function=inventory_json_api,
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

# The same service for an endpoint that prefixes its field service names, under a
# name that sorts WITH the services it describes ('JSON <name> API', next to
# 'JSON <name> Status') instead of with every other endpoint's status service.
# A service_name template is fixed at registration and no item can turn
# 'JSON API %s' into 'JSON <name> API', so this needs a plugin of its own - which
# also keeps the rename confined to the endpoints that opted into prefixing.
# Same check function and same check group: it is the same service, differently
# named, and its response-time and unreachable-state rules must keep working.
check_plugin_json_api_endpoint_prefixed = CheckPlugin(
    name="json_api_endpoint_prefixed",
    sections=["json_api"],
    service_name="JSON %s",
    discovery_function=discover_json_api_endpoint_prefixed,
    check_function=check_json_api_endpoint,
    check_ruleset_name="json_api_endpoint",
    check_default_parameters={},
)
