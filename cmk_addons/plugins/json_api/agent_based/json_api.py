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

This module holds the plugins and what decides a service's state. The parts
that do not - the section's model and parsing, units and their rendering,
timestamp parsing, the transform's evaluator - live in ``../check_lib/``:
Checkmk imports every module of ``agent_based/`` as a plugin file, so helpers
cannot sit next to this one.
"""

import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Final

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
    TableRow,
    check_levels,
    get_rate,
    get_value_store,
    render,
)

from ..check_lib.calc import _apply_calc
from ..check_lib.section import (
    EndpointStatus,
    Item,
    Levels,
    Match,
    MustMatch,
    ReadAsCounter,
    Section,
    StateMap,
    ValueAs,
    _coerce_levels,
    _coerce_match,
    _coerce_state,
    parse_json_api,
)
from ..check_lib.timestamps import _parse_timestamp
from ..check_lib.units import (
    _AGE_METRIC,
    _fmt_number,
    _rate_metric_name,
    _rate_render_func,
)
from ..check_lib.values import _as_number, _render_value
from ..migrations import migrate_endpoint_params

# Checkmk label keys we create are namespaced so they never collide with the
# built-in ``cmk/...`` labels or another plugin's.
_LABEL_NS = "json_api/"


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
        # No discovered parameters. The thresholds and string matching configured
        # in the special-agent rule reach the check through the section on every
        # run; copying them into the autocheck here would freeze them, because
        # discovered parameters take precedence over the section and only change
        # on a re-discovery. A check-parameters rule overrides them per key.
        #
        # Service labels the agent resolved for this service, namespaced. Attached
        # at discovery, so they update when the service is re-discovered. Where
        # fields share a service they contribute to one label set (later wins per
        # key), because the labels describe the SERVICE, not the line.
        labels = {key: value for entry in visible for key, value in entry.service_labels}
        yield Service(
            item=name,
            labels=[ServiceLabel(f"{_LABEL_NS}{key}", value) for key, value in labels.items()],
        )


_AGGREGATE_LABEL: Final[Mapping[str, str]] = {
    "count": "number of elements",
    "sum": "sum of the elements",
    "avg": "average of the elements",
    "min": "smallest element",
    "max": "largest element",
}

_VALUE_AS_LABEL: Final[Mapping[str, str]] = {
    "counter": "counter (per-second rate)",
    "timestamp": "timestamp (age)",
}


def _context(entry: Item, match: Match) -> CheckResult:
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
        lines.append(f"Read as: {_VALUE_AS_LABEL[entry.value_as.kind]}")
    if isinstance(match, MustMatch):
        lines.append(f"Expected pattern: {match.pattern}")
    elif isinstance(match, StateMap):
        parts = [
            f"{key.upper()} /{pattern}/"
            for key, pattern, _state in _state_map_order(match)
            if pattern
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


def _state_map_order(match: StateMap) -> tuple[tuple[str, str | None, State], ...]:
    """The state map's patterns in the order they are tried, with their states."""
    return (
        ("ok", match.ok, State.OK),
        ("warn", match.warn, State.WARN),
        ("crit", match.crit, State.CRIT),
    )


def _evaluate_match(match: MustMatch | StateMap, text: str) -> tuple[State, str]:
    """Return ``(state, description)`` for a string value's configured matching.

    ``must_match`` is OK on a full match, else its ``state_no_match`` (CRIT by
    default). ``state_map`` tries the OK, WARN then CRIT pattern and the first
    full match wins; no match is its ``state_no_match`` (OK by default).
    Raises ``re.error`` on a bad pattern so the caller surfaces it as UNKNOWN.
    """
    if isinstance(match, MustMatch):
        if re.fullmatch(match.pattern, text) is not None:
            return State.OK, ""
        return State(match.state_no_match), f"expected to match '{match.pattern}'"
    for key, pattern, state in _state_map_order(match):
        if pattern and re.fullmatch(pattern, text) is not None:
            return state, f"matched {key.upper()}"
    no_match = match.state_no_match
    return State(no_match), ("no pattern matched" if no_match != 0 else "")


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

    if isinstance(entry.value_as, ReadAsCounter):
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

    stamp = _parse_timestamp(entry.value, entry.value_as.format, entry.value_as.pattern)
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
    if entry.unit is not None:
        return time.time() - stamp, entry.metric_name, entry.render_func, [reading]
    return time.time() - stamp, _AGE_METRIC, render.time_offset, [reading]


# What the derived number is called in the summary and the levels line.
_VALUE_LABEL: Final[Mapping[str, str]] = {"counter": "Rate", "timestamp": "Age"}


def _value_label(value_as: ValueAs, line: str | None = None) -> str:
    """What this value is called in the summary.

    A field of its own is just 'Value' (or 'Rate'/'Age' once derived) - the
    service name already says what it is. A field sharing a service has to say
    which of them it is, so it uses its own name instead.
    """
    if line:
        return line
    return _VALUE_LABEL[value_as.kind] if value_as is not None else "Value"


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
    if isinstance(entry.value_as, ReadAsCounter):
        return _rate_metric_name(entry.unit)
    # A timestamp becomes an age: seconds, unless the rule named a unit itself.
    return entry.metric_name if entry.unit is not None else _AGE_METRIC


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
    levels_upper: Levels,
    levels_lower: Levels,
    match: Match,
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
        # Effective parameters: a check-parameters rule wins per key; where a key
        # is absent the value the agent rule embedded in the section applies.
        # (Services discovered before 0.24.1 still carry the agent rule's values
        # as discovered parameters, which win the same way until re-discovered.)
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
        age = f" ({render.time_offset(endpoint.cache_age)} old)" if endpoint.cache_age else ""
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
    if endpoint.pagination_end:
        # OK: the collection is whole. In the details, so that an end taken from
        # a 404 can be checked by someone who doubts the count.
        yield Result(
            state=State.OK,
            notice=f"End of the collection: {endpoint.pagination_end}",
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
        # In seconds, like Checkmk's own certificate checks. LOWER levels - the
        # alert is about running out of time. time_offset, not timespan: an
        # expired certificate has a negative remaining validity, which timespan
        # refuses. The levels go through the ruleset's migrate first, because a
        # rule nobody has opened since the update still holds them in days.
        yield from check_levels(
            endpoint.cert_expiry - time.time(),
            levels_lower=_coerce_levels(
                migrate_endpoint_params(dict(params)).get("cert_validity_levels")
            ),
            metric_name="json_api_cert_remaining_validity",
            label="Certificate expires in",
            render_func=render.time_offset,
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
