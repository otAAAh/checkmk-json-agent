# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Turning a fetched document into service results, host labels and summaries."""

import json
import re

from .paths import (
    _HEADER_PREFIX,
    _NO_ELEMENT,
    _PATH_TOKEN,
    _WILDCARD,
    _as_float,
    _expand_wildcards,
    _iter_container,
    _resolve_header,
    _resolve_parent,
    _resolve_path,
    _split_wildcards,
    _strip_root,
    _token_key,
)
from .transport import _MAX_REPORTED_BYTES, _redact_secret

# How much of the JSON context to put into ONE field service's details. Smaller
# than the raw-response default on purpose: this text is repeated on every field
# service of the endpoint, not written once to the endpoint's own service.
_DEFAULT_CONTEXT_BYTES = 1024


def _context_spec(endpoint: dict) -> dict | None:
    """The endpoint's 'report the JSON context in the field services' settings."""
    spec = endpoint.get("field_context")
    return spec if isinstance(spec, dict) else None


def _context_object(spec: dict, document: object, path: str, element: object) -> object:
    """The JSON a field's details should show, per the configured source.

    'response' is the whole document. 'element' is the JSON the value was
    actually read from: the current '[*]' element where there is one, else the
    object containing the value (its parent). An aggregation has no single
    element and a '@header.' path is not in the body at all, so both fall back to
    the document - the alternative would be to report nothing where the context
    is arguably most useful.
    """
    if spec.get("source") == "response":
        return document
    if element is not None and element is not _NO_ELEMENT:
        return element
    if _WILDCARD in path or path.strip().startswith(_HEADER_PREFIX):
        return document
    found, parent = _resolve_parent(document, path)
    return parent if found else document


def _context_text(
    spec: dict | None,
    document: object,
    path: str,
    element: object = None,
    secret: str | None = None,
) -> str | None:
    """The pretty-printed, capped, secret-stripped JSON context, or None.

    Pretty-printed rather than verbatim: the document has been parsed by the time
    a field is extracted, and an indented object is what makes the details worth
    reading. The cap is applied to the ENCODED text, so it bounds what is stored
    with every check result; a cut that splits a multi-byte character decodes to
    U+FFFD rather than failing the whole report.
    """
    if spec is None:
        return None
    raw = spec.get("max_bytes")
    limit = int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0
    limit = max(1, min(limit or _DEFAULT_CONTEXT_BYTES, _MAX_REPORTED_BYTES))
    try:
        text = json.dumps(_context_object(spec, document, path, element), indent=2, default=str)
    except (TypeError, ValueError):
        return None
    encoded = _redact_secret(text, secret).encode("utf-8", "replace")
    if len(encoded) <= limit:
        return encoded.decode("utf-8", "replace")
    shown = encoded[:limit].decode("utf-8", "replace")
    return f"{shown}\n... (truncated at {limit} of {len(encoded)} bytes)"


def _matches_filter(element: object, filt: object) -> bool:
    """Whether a wildcard/count element satisfies the configured filter.

    The filter path is resolved WITHIN the element and the resolved scalar is
    compared per the operator (equals / not_equals / regex / not_regex, the
    regexes being full matches). A missing path, a non-scalar value, or a bad
    regex all count as "no match" (the element is dropped), so a filter only
    ever keeps elements it can positively confirm - including for the negated
    operators, where an element lacking the field is dropped rather than kept.
    """
    if not isinstance(filt, dict) or not filt.get("path"):
        return True  # no filter configured → keep everything
    found, value = _resolve_path(element, filt["path"])
    if not found:
        return False
    text = _label_value(value)
    if text is None:
        return False
    target = filt.get("value", "")
    match filt.get("op"):
        case "not_equals":
            return text != target
        case "regex":
            try:
                return re.fullmatch(target, text) is not None
            except re.error:
                return False
        case "not_regex":
            try:
                return re.fullmatch(target, text) is None
            except re.error:
                return False
        case _:  # "equals" (the default)
            return text == target


def _aggregate_mode(spec: dict) -> str | None:
    """The configured aggregation ('count'/'sum'/'avg'/'min'/'max'), or None.

    ``count: true`` is the superseded boolean form of ``aggregate: "count"``: a
    rule saved before the aggregate dropdown still carries it (the ruleset
    migrates it only when the rule is next opened in Setup), and so may a
    hand-written '--endpoint' blob. Reading it here keeps both working.
    """
    mode = spec.get("aggregate")
    if isinstance(mode, str) and mode:
        return mode
    return "count" if spec.get("count") else None


_AGGREGATE_NOT_NUMERIC = (
    "element is not numeric (use a '[*]' path to aggregate a field inside each element)"
)


def _aggregate_numbers(mode: str, values: list[object]) -> tuple[bool, object, str]:
    """Reduce ``values`` with ``mode`` into ``(found, value, error)``.

    Every value must be numeric; one that is not fails the whole aggregation
    rather than being skipped, so a wrong path is visible instead of quietly
    changing the result. An empty input sums to 0 (a filter that matched nothing
    legitimately totals zero) but has no average, minimum or maximum.
    """
    numbers: list[float] = []
    for value in values:
        number = _as_float(value)
        if number is None:
            return False, None, _AGGREGATE_NOT_NUMERIC
        numbers.append(number)
    if not numbers:
        if mode == "sum":
            return True, 0, ""
        return False, None, "no elements to aggregate"
    match mode:
        case "sum":
            return True, _trim(sum(numbers)), ""
        case "avg":
            return True, _trim(sum(numbers) / len(numbers)), ""
        case "min":
            return True, _trim(min(numbers)), ""
        case "max":
            return True, _trim(max(numbers)), ""
    return False, None, f"unknown aggregation '{mode}'"


def _trim(number: float) -> float | int:
    """An integral result as an int, so a sum of integers reads as '15', not '15.0'.

    Every value goes through ``_as_float`` to be aggregated, which makes even a
    sum of plain integers a float; the check renders the value as it arrives, so
    trimming here is what keeps the service summary looking like the JSON did.
    """
    return int(number) if number.is_integer() else number


def _aggregate_container(mode: str, container: object, filt: object) -> tuple[bool, object, str]:
    """Aggregate the elements of the array/object at a wildcard-free path.

    'count' is the number of elements (array length or number of object keys);
    the numeric functions reduce the elements themselves, so this form fits a
    collection of numbers - a collection of objects wants a '[*]' path naming the
    field to aggregate. A scalar has nothing to aggregate and is surfaced as a
    misconfiguration rather than, say, counting the characters of a string.
    """
    pairs = _iter_container(container)
    if pairs is None:
        return False, None, "value at path is not a list or object (cannot aggregate)"
    elements = [element for _default, element in pairs]
    if isinstance(filt, dict) and filt.get("path"):
        # Aggregate only the matching elements (e.g. how many nodes are NOT 'ok').
        elements = [element for element in elements if _matches_filter(element, filt)]
    if mode == "count":
        return True, len(elements), ""
    return _aggregate_numbers(mode, elements)


def _aggregate_leaves(
    mode: str,
    leaves: list[tuple[list[str], bool, object, str, object]],
    filt: object,
) -> tuple[bool, object, str]:
    """Aggregate the leaves of a '[*]' expansion into one value.

    This is the counterpart of fanning a wildcard out into one service per
    element: 'nodes[*].load' with 'avg' yields a single service holding the
    average load instead of one service per node. The filter applies to the
    element the leaf came from, exactly as it does when fanning out.

    A leaf whose value path is missing is skipped, so every mode - 'count'
    included - sees the same set of elements: the ones that actually have the
    field. 'nodes[*].load' counts the nodes reporting a load, which is what
    naming the field asks for, and keeps 'count' consistent with the average
    over those same values. If NO leaf resolved at all that is reported instead -
    a mistyped value path must not read as an empty collection.
    """
    # A missing container yields exactly one leaf, with no element behind it -
    # identified by the sentinel, so an array holding a single JSON null (a real
    # element, with a real length of 1) is not mistaken for it.
    if len(leaves) == 1 and leaves[0][4] is _NO_ELEMENT:
        return False, None, leaves[0][3]
    filtering = isinstance(filt, dict) and bool(filt.get("path"))
    kept = [
        (found, value)
        for _labels, found, value, _error, element in leaves
        if not filtering or _matches_filter(element, filt)
    ]
    values = [value for found, value in kept if found]
    if kept and not values:
        return False, None, "path not found in any element"
    if mode == "count":
        # Counting needs no numbers, only elements - a collection of strings has
        # a length just as much as one of numbers does.
        return True, len(values), ""
    return _aggregate_numbers(mode, values)


# Checkmk host names are used in file paths, Livestatus queries and config, so
# only this conservative set survives; anything else becomes '_'. Mirrors what
# Checkmk itself accepts for a host name.
_HOST_NAME_INVALID = re.compile(r"[^-0-9A-Za-z_.]")


def _piggyback_host(element: object, host_path: object) -> str | None:
    """The piggyback host name for one element, or ``None`` when there is none.

    The name is read from a field WITHIN the element (the same way a filter or a
    label is), then sanitised: a host name ends up in file paths and config, so
    the character set is restricted rather than trusted. An element whose field is
    missing, non-scalar, or sanitises to nothing yields ``None`` - the caller
    leaves that element's services on the polling host rather than inventing a
    host name for them.
    """
    if not isinstance(host_path, str) or not host_path.strip():
        return None
    found, value = _resolve_path(element, host_path.strip())
    if not found:
        return None
    text = _label_value(value)
    if text is None:
        return None
    cleaned = _HOST_NAME_INVALID.sub("_", text.strip()).strip("_.")
    return cleaned or None


def _inventory_spec(spec: dict, row_key: str | None) -> dict | None:
    """This field's place in the inventory tree, resolved for one result.

    The attribute name defaults to the JSON path's last segment - the same rule
    the service labels use - so the common case needs no extra typing. ``row_key``
    is the '[*]' element's label, which becomes the table row's key column;
    without a wildcard the value is a plain attribute of the node.
    """
    inventory = spec.get("inventory")
    if not isinstance(inventory, dict) or not inventory.get("node"):
        return None
    return {
        "node": str(inventory["node"]).strip(),
        "key": (inventory.get("key") or "").strip() or _label_key_from_path(spec["path"]),
        "row_key": row_key,
        "keep_service": bool(inventory.get("keep_service")),
    }


def _result(
    spec: dict,
    service: str,
    found: bool,
    value: object,
    error: str,
    url: str,
    labels: list[dict] | None = None,
    host: str | None = None,
    summary_fields: dict[str, str] | None = None,
    row_key: str | None = None,
    calc_other: object = None,
    host_labels: dict[str, str] | None = None,
    label: str | None = None,
    context: str | None = None,
) -> dict:
    # Values that are not JSON scalars (dict/list) are reported as strings.
    if found and isinstance(value, (dict, list)):
        value = json.dumps(value)
    return {
        "service": service,
        # This value's own name WITHIN that service, for a field reported in a
        # shared service; None for the ordinary one-field-one-service case. The
        # check turns it into the line's label, so several fields can share a
        # service and still say which is which.
        "label": label,
        # Which host this result belongs to: None is the polling host (its
        # services go into the plain section), a name means a piggyback host and
        # is stripped back out before the section is written.
        "host": host,
        # Host labels for THAT piggyback host, resolved from this element. Also
        # internal routing, stripped with 'host': they belong to the host, not to
        # the service, so they are merged per host in _split_by_host.
        "host_labels": host_labels or {},
        "path": spec["path"],
        "url": url,
        "found": found,
        "value": value if found else None,
        "error": None if found else error,
        "levels_upper": spec.get("levels_upper"),
        "levels_lower": spec.get("levels_lower"),
        "match": spec.get("match"),
        "calc": spec.get("calc"),
        # The second operand of a two-path transform, already resolved in this
        # result's own scope - only the agent has the document and the current
        # '[*]' element, and 'other' means "this element's total", not the
        # document's. None when no second path is configured or it did not
        # resolve; the check then reports the expression as failed rather than
        # quietly computing with a stand-in.
        "calc_other": calc_other,
        "unit": spec.get("unit"),
        # The metric name the rule states, if any. Untouched here for the same
        # reason as value_range: only the check emits metrics.
        "metric_name": spec.get("metric_name"),
        # The range this value moves in, when the rule states one. Untouched
        # here: the check turns it into the metric's boundaries, and only the
        # check emits metrics.
        "value_range": spec.get("value_range"),
        # Passed through for the check: it derives the per-second rate / the age
        # (it owns the value store and knows "now").
        "value_as": spec.get("value_as"),
        # Already applied here; carried along only so the check can say so in the
        # service's Details.
        "aggregate": _aggregate_mode(spec),
        "labels": labels or [],
        # Extra summary text: the template as configured, plus the values its
        # '{path}' placeholders resolved to in THIS element's scope. Split that
        # way because only the agent can resolve a path and only the check knows
        # how the value itself is rendered.
        "summary": spec.get("summary") or None,
        "summary_fields": summary_fields or {},
        # Where this value goes in the HW/SW inventory tree, if anywhere. None
        # for the ordinary "this is a service" case.
        "inventory": _inventory_spec(spec, row_key),
        # The JSON this value was read from, for the endpoints that asked for it.
        # It travels WITH the result rather than being looked up from the
        # endpoint record by the check, because a piggybacked result lands in a
        # different section, which has no endpoint record to look anything up in.
        "context": context,
    }


def _service_prefix(endpoint: dict) -> str:
    """The endpoint name to put in front of this endpoint's service names, or ''.

    Opt-in per endpoint. Two endpoints extracting the same fields otherwise
    produce two identically named services ('JSON Status' twice, the second
    disambiguated to 'JSON Status (2)'), and nothing in the name says which
    application each belongs to.

    Deliberately no fall back to the URL: a URL in a service description travels
    into notifications, availability reports and the metric paths on disk, where
    it is both unreadable and - for a query string - a secret leak. Without a
    configured name the option is simply a no-op (the ruleset rejects that
    combination, so it can only reach here from a hand-written rule).
    """
    if not endpoint.get("service_prefix"):
        return ""
    name = endpoint.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else ""


def _prefixed(prefix: str, service: str) -> str:
    """``service`` behind the endpoint prefix, if there is one."""
    return f"{prefix} {service}" if prefix else service


def _shared_service(spec: dict) -> str | None:
    """The shared service this field reports into, or None for one of its own.

    A field that names one puts its value into that service as a LINE rather than
    becoming a service itself: the check yields one result per line and Checkmk's
    own aggregation makes the service's state the worst of them. The field's own
    'service' name then names the line instead of the service.
    """
    group = spec.get("group")
    return group.strip() if isinstance(group, str) and group.strip() else None


def _extract(
    document: object,
    extractions: list[dict],
    url: str,
    headers: object = None,
    prefix: str = "",
    context: dict | None = None,
    secret: str | None = None,
) -> list[dict]:
    results = []
    for spec in extractions:
        label_specs = spec.get("labels") or []
        # Resolved in the same scope as the service labels: the current '[*]'
        # element, or the document root where there is no element.
        summary = spec.get("summary")
        # This field's service name, endpoint prefix included. Every branch below
        # names its service from this, so a prefixed endpoint cannot end up with
        # some of its services prefixed and some not.
        #
        # Naming a shared service moves the names one step along: that service is
        # what the field reports into, and the field's own name becomes the label
        # of its line inside it.
        shared = _shared_service(spec)
        base = _prefixed(prefix, shared if shared is not None else spec["service"])
        line = spec["service"] if shared is not None else None
        # A header is a single scalar off the response, so none of the body-only
        # machinery (wildcards, aggregation, filters) applies to it.
        if spec["path"].strip().startswith(_HEADER_PREFIX):
            found, value = _resolve_header(headers, spec["path"].strip())
            results.append(
                _result(
                    spec,
                    base,
                    found,
                    value,
                    "header not in response",
                    url,
                    _resolve_labels(label_specs, document),
                    summary_fields=_resolve_summary(summary, document),
                    label=line,
                    context=_context_text(context, document, spec["path"], secret=secret),
                )
            )
            continue

        segments = _split_wildcards(spec["path"])
        aggregate = _aggregate_mode(spec)
        filt = spec.get("filter")
        if len(segments) == 1:
            found, value = _resolve_path(document, spec["path"])
            error = "path not found in response"
            # Aggregating a wildcard-free path reduces the array/object it holds.
            if found and aggregate:
                found, value, error = _aggregate_container(aggregate, value, filt)
            labels = _resolve_labels(label_specs, document)
            results.append(
                _result(
                    spec,
                    base,
                    found,
                    value,
                    error,
                    url,
                    labels,
                    summary_fields=_resolve_summary(summary, document),
                    calc_other=_resolve_calc_other(spec, document),
                    label=line,
                    context=_context_text(context, document, spec["path"], secret=secret),
                )
            )
            continue

        leaves = _expand_wildcards(document, segments, spec.get("label_path"))

        # Aggregating a '[*]' path collapses the expansion into ONE service (the
        # sum/average/... over the elements) instead of fanning it out. Service
        # labels then have no single element to resolve against, so they come
        # from the response root.
        if aggregate:
            found, value, error = _aggregate_leaves(aggregate, leaves, filt)
            labels = _resolve_labels(label_specs, document)
            results.append(
                _result(
                    spec,
                    base,
                    found,
                    value,
                    error,
                    url,
                    labels,
                    summary_fields=_resolve_summary(summary, document),
                    calc_other=_resolve_calc_other(spec, document),
                    label=line,
                    context=_context_text(context, document, spec["path"], secret=secret),
                )
            )
            continue

        # One or more array wildcards: one service per cartesian-product
        # element, labelled by a ' / '-joined composite (one segment per level).
        # An optional filter keeps only the elements whose sub-field matches
        # (e.g. only the nodes that are NOT 'ok'); a not-found leaf is kept so
        # the "array or object not found" error still surfaces.
        host_path = spec.get("piggyback_host")
        for index, (label_segments, found, value, error, element) in enumerate(leaves):
            if (
                found
                and isinstance(filt, dict)
                and filt.get("path")
                and not _matches_filter(element, filt)
            ):
                continue
            # With a piggyback host field, the element becomes its own Checkmk
            # host, so the host carries the identity and the service keeps its
            # plain name - 'JSON Health' on 50 hosts, not 'JSON Health node-01'
            # on one. An element whose host name does not resolve falls back to
            # the labelled name on the polling host, so it is still monitored
            # instead of vanishing.
            host = _piggyback_host(element, host_path)
            # Only meaningful once the element IS its own host; on the polling
            # host they would silently become labels of the polling host, which
            # is emphatically not what "label the host I created" asked for.
            element_host_labels = (
                _resolve_host_labels(spec.get("piggyback_labels") or [], element)
                if host is not None
                else {}
            )
            label = " / ".join(label_segments)
            # With a shared service the expansion fans out into LINES of that one
            # service, not into services - so the element label lands on the line
            # and the service name stays put.
            if line is not None:
                service = base
                element_line = f"{line} {label}" if label else line
            elif host is not None:
                service = base
                element_line = None
            else:
                service = f"{base} {label}" if label else base
                element_line = None
            labels = _resolve_labels(label_specs, element)
            results.append(
                _result(
                    spec,
                    service,
                    found,
                    value,
                    error,
                    url,
                    labels,
                    host,
                    summary_fields=_resolve_summary(summary, element),
                    calc_other=_resolve_calc_other(spec, element),
                    host_labels=element_host_labels,
                    # An element whose label field resolved to an empty string
                    # still needs a row of its own: falling back to None would
                    # quietly turn it into a plain attribute of a node that is
                    # otherwise a table, and several such elements would then
                    # overwrite each other under one key.
                    row_key=label or str(index),
                    label=element_line,
                    context=_context_text(context, document, spec["path"], element, secret),
                )
            )
    return results


def _label_value(value: object) -> str | None:
    """A JSON scalar as a label string, or ``None`` for null/object/array.

    Booleans render as JSON ('true'/'false') to match the value rendering; a
    non-scalar makes no sense as a label value and is skipped by the caller.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return None


def _label_key_from_path(path: str) -> str:
    """The last dict-key segment of a path, used as the default label key.

    'metadata.name' -> 'name', "data['foo.bar']" -> 'foo.bar', 'components[*]' ->
    'components'. Falls back to the cleaned path when it ends in an array index.
    """
    cleaned = _strip_root(path).replace("[*]", "")  # a wildcard is not part of the key
    key = None
    for match in _PATH_TOKEN.finditer(cleaned):
        if (token := _token_key(match)) is not None:
            key = token
    return key or cleaned


# One '{path}' placeholder of a summary template. Braces cannot nest, so the
# body is anything but a brace; the ruleset rejects the shapes this would miss.
_SUMMARY_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


# How much of a collection to describe in a summary. Dumping the JSON of a
# 200-element array into a service summary - which travels into notifications -
# helps nobody, so a non-scalar is reported by its size instead.
def _summary_value(value: object) -> str:
    if isinstance(value, list):
        return f"[{len(value)} items]"
    if isinstance(value, dict):
        return f"{{{len(value)} keys}}"
    if value is None:
        return "null"
    text = _label_value(value)
    return text if text is not None else str(value)


def _resolve_calc_other(spec: dict, element: object) -> object:
    """Resolve the transform's second path within ``element``, or ``None``.

    Resolved in the same scope as the service's labels and summary - the current
    '[*]' element, or the document root where there is no element - so
    'value / other * 100' over 'disks[*].used' with 'total' compares each disk
    against ITS OWN total rather than against some other element's.
    """
    path = spec.get("calc_path")
    if not isinstance(path, str) or not path.strip():
        return None
    found, value = _resolve_path(element, path.strip())
    return value if found else None


def _resolve_summary(template: object, element: object) -> dict[str, str]:
    """Resolve a summary template's '{path}' placeholders against ``element``.

    Returns ``{path: rendered}`` for the placeholders that resolved, keyed by the
    path exactly as written in the template. Unresolvable ones are simply absent:
    the check marks them '(n/a)', so a typo stays visible instead of quietly
    rendering as nothing.
    """
    if not isinstance(template, str) or not template.strip():
        return {}
    fields: dict[str, str] = {}
    for match in _SUMMARY_PLACEHOLDER.finditer(template):
        path = match.group(1).strip()
        if not path or path in fields:
            continue
        found, value = _resolve_path(element, path)
        if found:
            fields[path] = _summary_value(value)
    return fields


def _resolve_labels(label_specs: list[dict], element: object) -> list[dict]:
    """Resolve SERVICE-label specs relative to ``element`` into ``{key, value}``.

    ``element`` is each '[*]' element (or the whole document for a non-wildcard
    extraction), so a per-element service gets its own label value. Unresolved
    paths and non-scalar / null values are skipped, so a label is emitted only
    when it has a value. The 'json_api/' key namespace is added by the check.
    """
    resolved = []
    for spec in label_specs:
        path = spec.get("path")
        if not path:
            continue
        found, value = _resolve_path(element, path)
        if not found:
            continue
        text = _label_value(value)
        if text is None:
            continue
        resolved.append({"key": spec.get("key") or _label_key_from_path(path), "value": text})
    return resolved


def _resolve_host_labels(label_specs: list[dict], document: object) -> dict:
    """Resolve HOST-label specs from the ``document`` root into ``{key: value}``.

    Host labels are host-wide, so they attach to no service and are resolved once
    per endpoint response. ``document`` is the scope the paths are read from: the
    response root for an endpoint's own host labels, one '[*]' element for the
    labels of the piggyback host that element becomes.

    Three shapes are supported:

    * plain path (no '[*]'): one label ``<key>: <scalar at path>`` where ``key``
      is the given key or the path's last segment.
    * wildcard path (contains '[*]', e.g. ``components[*]``): one label PER
      element, keyed ``<key>/<element-id>`` so keys stay unique (a host label map
      cannot repeat a key); the value comes from ``value_field`` resolved within
      each element, defaulting to ``'true'`` (set-membership tags).
    * a literal ``value``: the value is typed in the rule rather than read from
      the response, and a wildcard path then collapses to ONE label - keyed once,
      not once per element - emitted as soon as an element survives the filter.
      This is the classification case: "if any of these elements matches, tag the
      host". A path is optional here; without one only the filter is evaluated.

    An optional ``filter`` (the same predicate an extraction uses) decides which
    elements produce a label. It is resolved in the same scope as the label path:
    per element for a wildcard path, against ``document`` itself otherwise - so a
    plain-path label can be made conditional too ("only in production").

    Later keys win on collision. The 'json_api/' namespace is added by the check.
    """
    labels: dict[str, str] = {}
    for spec in label_specs:
        path = spec.get("path")
        filt = spec.get("filter")
        raw_literal = spec.get("value")
        literal = raw_literal if isinstance(raw_literal, str) and raw_literal else None
        base_key = spec.get("key") or (_label_key_from_path(path) if path else "")
        if not base_key:
            continue  # nothing to key the label by (a path-less spec without a key)
        if not path:
            # Keyed and valued entirely by the rule: the only thing read from the
            # response is the filter's verdict on this scope.
            if literal is not None and _matches_filter(document, filt):
                labels[base_key] = literal
            continue
        value_field = spec.get("value_field") or ""
        segments = _split_wildcards(path)
        if len(segments) == 1:
            found, value = _resolve_path(document, path)
            if not found or not _matches_filter(document, filt):
                continue
            text = literal if literal is not None else _label_value(value)
            if text is not None:
                labels[base_key] = text
            continue
        # Wildcard: one unique label per element - or, with a literal value, one
        # label for the whole collection, as soon as an element matches.
        for label_segments, _found, _value, _error, element in _expand_wildcards(
            document, segments, None
        ):
            # The sentinel means the collection itself was missing, which is not
            # an element and must not be labelled as one.
            if element is _NO_ELEMENT or not _matches_filter(element, filt):
                continue
            if literal is not None:
                labels[base_key] = literal
                break
            suffix = "/".join(str(part) for part in label_segments)
            key = f"{base_key}/{suffix}" if suffix else base_key
            if value_field:
                found, value = _resolve_path(element, value_field)
                text = _label_value(value) if found else None
                if text is None:
                    continue  # value field missing / non-scalar → skip this element
            else:
                text = "true"
            labels[key] = text
    return labels
