# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The path grammar: resolving field paths, wildcards and headers in a document."""

import math
import re

_PATH_TOKEN = re.compile(
    r"\['(?P<sq>[^']*)'\]"  # bracket-quoted key, single quotes: ['foo.bar']
    r"|\[\"(?P<dq>[^\"]*)\"\]"  # bracket-quoted key, double quotes: ["foo.bar"]
    r"|\[(?P<idx>\d+)\]"  # array index: [0]
    r"|(?P<key>[^.\[\]]+)"  # plain dotted key
)


def _strip_root(path: str) -> str:
    """``path`` without surrounding space and without a JSONPath '$' / '$.' root."""
    cleaned = path.strip()
    if cleaned.startswith("$."):
        return cleaned[2:]
    if cleaned.startswith("$"):
        return cleaned[1:]
    return cleaned


def _token_key(match: re.Match[str]) -> str | None:
    """The dict key a path token matched, or ``None`` when it is an array index.

    Tested against ``None`` rather than for truthiness: a bracket-quoted key can
    legitimately be empty (``['']``).
    """
    for group in ("sq", "dq", "key"):
        if (key := match.group(group)) is not None:
            return key
    return None


def _resolve_path(data: object, path: str) -> tuple[bool, object]:
    """Resolve a dotted path with optional [index] segments.

    Returns (found, value). Supports e.g. 'a.b', 'a[0].b', leading '$.' is
    stripped. Keys that contain '.' or '[' can be addressed with JSONPath-style
    bracket-quoted segments, e.g. "a['foo.bar'].b" or 'a["foo.bar"].b'. Array
    wildcards ('[*]') are handled one level up, in _expand_wildcards.
    """
    current = data
    for match in _PATH_TOKEN.finditer(_strip_root(path)):
        index = match.group("idx")
        if index is not None:
            i = int(index)
            if not isinstance(current, list) or i >= len(current):
                return False, None
            current = current[i]
            continue
        key = _token_key(match)
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


def _resolve_parent(data: object, path: str) -> tuple[bool, object]:
    """Resolve everything but the LAST segment of a path: the value's container.

    A single-segment path has the document itself as its container, which is the
    right answer: the context of a top-level field IS the response.
    """
    tokens = list(_PATH_TOKEN.finditer(_strip_root(path)))
    if len(tokens) <= 1:
        return True, data
    current = data
    for match in tokens[:-1]:
        index = match.group("idx")
        if index is not None:
            i = int(index)
            if not isinstance(current, list) or i >= len(current):
                return False, None
            current = current[i]
            continue
        key = _token_key(match)
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


_WILDCARD = "[*]"


# The 'element' of a leaf that has no element behind it, because the wildcard's
# container was missing altogether. A distinct sentinel rather than ``None``:
# ``None`` is also an ordinary JSON value, so an array holding one (``[null]``)
# would otherwise be indistinguishable from "there was no array at all".
_NO_ELEMENT = object()


def _split_wildcards(path: str) -> list[str]:
    """Split a path on every '[*]' wildcard into segments.

    A path with N wildcards yields N+1 segments: the part before the first
    wildcard, the part between each consecutive pair, and the trailing value
    path. A leading '.' is stripped from every segment after the first. A path
    with no wildcard yields a single-element list (the whole path).

    'pods[*].containers[*].ready' -> ['pods', 'containers', 'ready']
    'nodes[*].health'             -> ['nodes', 'health']
    'items[*]'                    -> ['items', '']
    'status'                      -> ['status']
    """
    parts = path.split(_WILDCARD)
    return [parts[0]] + [p[1:] if p.startswith(".") else p for p in parts[1:]]


def _as_float(value: object) -> float | None:
    """A JSON value as a finite float, or None when it is not numeric.

    Numeric strings are accepted (APIs do quote numbers); booleans are not (they
    are states, and summing them is never what was meant).
    """
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
    return number if math.isfinite(number) else None


def _iter_container(container: object) -> list[tuple[str, object]] | None:
    """(default_label, element) pairs for a wildcard-expandable container.

    A JSON array expands element-by-element with the index as the default label;
    a JSON object (map) expands entry-by-entry with the key as the default label
    - so a Spring Boot Actuator '/health' 'components' map, keyed by component
    name, iterates just like an array. Returns None for a scalar (or a missing
    node), letting the caller report 'array or object not found'.
    """
    if isinstance(container, list):
        return [(str(index), element) for index, element in enumerate(container)]
    if isinstance(container, dict):
        return [(str(key), value) for key, value in container.items()]
    return None


def _expand_wildcards(
    node: object, segments: list[str], label_path: str | None
) -> list[tuple[list[str], bool, object, str, object]]:
    """Recursively expand the wildcard segments of a path under ``node``.

    ``segments`` is the output of :func:`_split_wildcards`: one entry per
    wildcard level plus the trailing value path. Returns one
    ``(label_segments, found, value, error, element)`` tuple per leaf - i.e. per
    element of the cartesian product of all wildcard levels. ``label_segments``
    carries one label per wildcard level (joined into the composite service name
    by the caller); ``label_path`` is resolved relative to each level's element,
    with the array index (or object key) as the fallback. ``element`` is the leaf
    element node (``_NO_ELEMENT`` when the container was missing) so the caller
    can resolve per-element service labels against it.
    """
    # Base case: no more wildcards, ``segments[0]`` is the value path itself.
    if len(segments) == 1:
        value_path = segments[0]
        if value_path:
            found, value = _resolve_path(node, value_path)
        else:
            found, value = True, node
        return [([], found, value, "path not found in element", node)]

    container_path, *rest = segments
    found_container, container = _resolve_path(node, container_path)
    pairs = _iter_container(container) if found_container else None
    if pairs is None:
        return [([], False, None, "array or object not found at wildcard path", _NO_ELEMENT)]

    expanded: list[tuple[list[str], bool, object, str, object]] = []
    for label, (_default, element) in zip(_element_labels(pairs, label_path), pairs, strict=True):
        for sub_labels, found, value, error, leaf in _expand_wildcards(element, rest, label_path):
            expanded.append(([label, *sub_labels], found, value, error, leaf))
    return expanded


# A path naming a RESPONSE HEADER rather than a field of the body, e.g.
# '@header.X-RateLimit-Remaining'. A prefix rather than a second form field: the
# extraction's 'path' stays the one required "where the value comes from", so no
# existing rule needs migrating and the Explorer's value_raw shape is unchanged.
# A body key literally called '@header' is shadowed by this - documented in the
# ruleset help, and no API in practice has one.
_HEADER_PREFIX = "@header."


def _resolve_header(headers: object, path: str) -> tuple[bool, object]:
    """Resolve a '@header.<name>' path against the response headers.

    HTTP field names are case-insensitive (RFC 9110), and the name reaches us as
    whatever the operator typed, so the lookup is case-folded rather than exact.
    """
    name = path[len(_HEADER_PREFIX) :].strip()
    if not isinstance(headers, dict) or not name:
        return False, None
    wanted = name.lower()
    for header, value in headers.items():
        if isinstance(header, str) and header.lower() == wanted:
            return True, value
    return False, None


def _element_labels(pairs: list[tuple[str, object]], label_path: str | None) -> list[str]:
    """One label per (default_label, element) pair, guaranteed unique.

    The label comes from label_path within each element, falling back to the
    default label (an array index or object key). If a label value repeats
    across elements, every occurrence of it is suffixed with its position, so
    two elements can never collapse into one service.
    """
    labels = []
    for default_label, element in pairs:
        if label_path:
            found, value = _resolve_path(element, label_path)
            labels.append(str(value) if found else default_label)
        else:
            labels.append(default_label)
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return [
        f"{label} [{index}]" if counts[label] > 1 else label for index, label in enumerate(labels)
    ]
