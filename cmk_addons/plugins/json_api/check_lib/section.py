#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The ``json_api`` section: its model, and parsing the agent's JSON into it.

Everything in the section that comes from the rule - levels, string matching,
how to read the value, the unit - is read here once, into the typed values
below, so the check works on values it can trust instead of re-checking a dict
on every run. A setting that cannot be read falls back to its documented
default rather than failing the section.

Not a plugin part: Checkmk discovers plugins only in the family's group
directories (``agent_based/``, ``rulesets/``, ...), and imports every module it
finds there, so the check's helpers live beside them in ``check_lib/``. The
check imports them relatively (``from ..check_lib.section import ...``), so they
resolve wherever the family is installed, and the MKP ships them like any other
file under the family root.
"""

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from cmk.agent_based.v2 import StringTable

from .units import _metric_name, _render_func

# Level tuples come straight from the SimpleLevels form spec, JSON-encoded by
# the agent: ("fixed", (warn, crit)) or ("no_levels", None) or absent. Only the
# first means anything to check_levels, so _coerce_levels reads the other two
# as None and this is all that is left.
# Plain alias (not a PEP 695 `type` statement) to stay portable to Checkmk 2.4.
Levels = tuple[Literal["fixed"], tuple[float, float]] | None


# The string-matching config from the CascadingSingleChoice form spec, JSON-encoded
# by the agent as ("must_match", {...}) or ("state_map", {...}). It is read into
# one of the two classes below once, by _coerce_match, so that matching and the
# details line work on checked values instead of re-checking a dict each time.
@dataclass(frozen=True)
class MustMatch:
    """The value has to match ``pattern`` in full, else ``state_no_match``."""

    pattern: str
    state_no_match: int = 2


@dataclass(frozen=True)
class StateMap:
    """The first of the OK, WARN, CRIT patterns to match in full sets the state.

    A pattern left out (or empty) is ``None``; ``state_no_match`` applies when
    none of them matches.
    """

    ok: str | None = None
    warn: str | None = None
    crit: str | None = None
    state_no_match: int = 0


Match = MustMatch | StateMap | None


# How to read the extracted value, from the 'Interpret the value as'
# CascadingSingleChoice (JSON-encoded by the agent as ("counter", None) or
# ("timestamp", {"format": ..., "pattern": ...})). Read into one of these by
# _coerce_value_as; ``None`` monitors the value as it stands.
@dataclass(frozen=True)
class ReadAsCounter:
    """Monitor the value's per-second rate."""

    kind: ClassVar[str] = "counter"


@dataclass(frozen=True)
class ReadAsTimestamp:
    """Monitor the value's age in seconds.

    ``format`` is one of the formats _parse_timestamp knows ('auto' when the
    rule names none); ``pattern`` is the strptime pattern of the 'custom' one.
    """

    kind: ClassVar[str] = "timestamp"
    format: str = "auto"
    pattern: str | None = None


ValueAs = ReadAsCounter | ReadAsTimestamp | None


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
    # The JSON value itself. Its type is the data, not configuration: the agent
    # sends a scalar (an object or array arrives as its JSON text), and what the
    # check does with it - a number, a string to match, a timestamp - is decided
    # by looking at it on every run.
    value: object
    error: str | None
    levels_upper: Levels
    levels_lower: Levels
    match: Match
    calc: str | None
    # The unit named in the rule. Any string is kept, an unknown one too: an
    # empty or unknown unit still counts as "the rule chose one" for a
    # timestamp's age (see _derive), which keeps the plain metric then.
    unit: str | None
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
    value_as: ValueAs = None
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
    # Where the agent counted the pages, how it recognised the end when that
    # took an inference - a 404, or the last page once more, in answer to the
    # request after it. The collection is complete; this says why it is taken
    # to be.
    pagination_end: str | None = None
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


def _coerce_levels(raw: object) -> Levels:
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


def _coerce_match(raw: object, legacy_expected: object = None) -> Match:
    """Read the string-match config (a list from JSON) into MustMatch / StateMap.

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
                return MustMatch(pattern, _coerce_state(cfg.get("state_no_match"), 2))
        case ["must_match", str() as pattern]:
            return MustMatch(pattern)
        case ["state_map", dict() as cfg]:
            return StateMap(
                ok=_optional_str(cfg.get("ok")),
                warn=_optional_str(cfg.get("warn")),
                crit=_optional_str(cfg.get("crit")),
                state_no_match=_coerce_state(cfg.get("state_no_match"), 0),
            )
    if isinstance(legacy_expected, str):
        return MustMatch(legacy_expected)
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


def _coerce_value_as(raw: object) -> ValueAs:
    """Read the 'Interpret the value as' config (a list from JSON).

    Anything unrecognized falls back to ``None`` ("monitor the value as it
    stands"), so an unknown future choice degrades to the plain behaviour instead
    of failing the service.
    """
    match raw:
        case ["counter", _] | "counter":
            return ReadAsCounter()
        case ["timestamp", dict() as cfg]:
            return ReadAsTimestamp(
                format=_optional_str(cfg.get("format")) or "auto",
                pattern=_optional_str(cfg.get("pattern")),
            )
        case ["timestamp", _] | "timestamp":
            return ReadAsTimestamp()
    return None


def _coerce_unit(raw: object) -> str | None:
    """The unit named in the rule: any string as it stands, else None.

    Not ``_optional_str``: an empty string is a unit the rule set (if not one
    the check knows), and it is kept apart from no unit at all - see
    ``Item.unit``.
    """
    return raw if isinstance(raw, str) else None


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


def _text(raw: object) -> str:
    """A field the agent writes as text, as text: '' for anything falsy.

    Every reader of these fields treats an empty one as absent and writes a
    present one into an f-string, so this is how they have always read - only
    now the type says so.
    """
    return str(raw) if raw else ""


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
            pagination_end=_optional_str(record.get("pagination_end")),
            body=_optional_str(record.get("body")),
            body_truncated=bool(record.get("body_truncated")),
            body_size=_optional_int(record.get("body_size")),
            headers=_response_headers(record.get("headers")),
        )
    return statuses


def _endpoint_service_names(endpoints: Mapping[str, EndpointStatus]) -> set[str]:
    """The endpoint services' names, as the item a FIELD service would need to get them.

    Field services are 'JSON <item>'. An endpoint's own service is 'JSON API
    <item>' - or 'JSON <item>' with an item ending in ' API' when it prefixes -
    so a field named 'API frontend', or 'API' on a prefixing endpoint, would be
    rendered into the very same service name. Checkmk then drops one of the two
    as a duplicate.
    """
    return {item if endpoint.prefixed else f"API {item}" for item, endpoint in endpoints.items()}


def parse_json_api(string_table: StringTable) -> Section | None:
    if not string_table:
        return None
    payload = json.loads(string_table[0][0])
    endpoints = _endpoint_statuses(payload.get("endpoints"))
    # A field whose service name is an endpoint service's name is moved aside the
    # same way a duplicated field is, so neither service disappears.
    endpoint_names: dict[str, object] = dict.fromkeys(_endpoint_service_names(endpoints))
    shared_names: dict[str, str] = {}
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
        service = result["service"]
        if label is not None:
            # Every line of a shared service must land in the same (possibly
            # moved) service, so the name is decided once per service.
            if service not in shared_names:
                shared_names[service] = _unique_name(service, endpoint_names)
            name = shared_names[service]
        else:
            name = _unique_name(service, {**endpoint_names, **items})
        unit = _coerce_unit(result.get("unit"))
        entry = Item(
            found=bool(result["found"]),
            value=result["value"],
            error=_text(result["error"]) or None,
            levels_upper=_coerce_levels(result.get("levels_upper")),
            levels_lower=_coerce_levels(result.get("levels_lower")),
            match=_coerce_match(result.get("match"), result.get("expected")),
            calc=_coerce_calc(result.get("calc")),
            calc_other=_optional_number(result.get("calc_other")),
            unit=unit,
            boundaries=_boundaries(result.get("value_range")),
            metric_name=_metric_name(unit),
            metric_override=_optional_str(result.get("metric_name")),
            render_func=_render_func(unit),
            path=_text(result.get("path")),
            url=_text(result.get("url")),
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
        error=_text(payload.get("error")) or None,
        items={name: tuple(entries) for name, entries in items.items()},
        host_labels=_coerce_host_labels(payload.get("host_labels")),
        endpoints=endpoints,
    )
