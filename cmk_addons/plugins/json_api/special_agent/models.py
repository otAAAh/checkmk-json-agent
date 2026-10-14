# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The '--endpoint' configuration, read once into typed values.

The server-side call hands every endpoint over as one JSON blob. That blob used
to travel through the agent as a plain dict, and every function re-checked the
part it read - is 'retry' a dict, is 'attempts' a positive whole number, is the
filter's path set - each with its own idea of a default. Here it is read once,
with exactly those rules, into frozen values the rest of the agent can trust.

Two kinds of problem stay apart, as they always were:

* a setting that is absent or unusable for its purpose (a retry policy of zero
  attempts, a filter without a path, a cache TTL of 0) falls back to what the
  agent always did with it - usually "off". That is not an error.
* a blob the agent cannot run at all (no 'url', an extraction without 'path' or
  'service', a list where an object belongs) raises ``EndpointConfigError``. The
  caller reports it on that endpoint's own services, like any other failure of
  one endpoint, and the other endpoints keep reporting.

The values the CHECK interprets (levels, string matching, the transform, the
unit, the metric name and range, how to read the value) are carried through
untouched: the agent never looked inside them, and the section has to keep its
format byte for byte.

This module reads only ``paths`` (for the path grammar) and is read by every
other module of the agent.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from .paths import _strip_root

# How much of the body / of the JSON context a service may carry, whatever the
# rule asks for. The text ends up in the service details, which are stored with
# every check result and shipped in notifications.
_MAX_REPORTED_BYTES = 65536
_DEFAULT_REPORTED_BYTES = 2048
# Smaller than the raw-response default on purpose: the context is repeated on
# every field service of the endpoint, not written once to the endpoint's own.
_DEFAULT_CONTEXT_BYTES = 1024

# However the rule is written, the agent never makes more than this many
# requests for one endpoint. A check that walks a thousand pages is an outage of
# its own - Checkmk kills a special agent that overruns - and a hand-written
# rule (or a blob built by something other than the ruleset) is not bound by the
# ruleset's own range.
_MAX_PAGES = 100
_DEFAULT_MAX_PAGES = 10


class EndpointConfigError(ValueError):
    """An '--endpoint' blob the agent cannot run; the message says why."""


@dataclass(frozen=True)
class Filter:
    """Which elements a '[*]' path, an aggregation or a host label keeps.

    ``path`` is resolved within each element and the scalar there is compared
    with ``value`` per ``op``: 'not_equals', 'regex', 'not_regex', and equality
    for anything else (the ruleset's 'equals' and an absent operator alike).
    """

    path: str
    op: str | None
    value: str


@dataclass(frozen=True)
class ServiceLabel:
    """A SERVICE label read from the response; ``key`` None = the path's last segment."""

    path: str
    key: str | None


@dataclass(frozen=True)
class HostLabel:
    """A HOST label: read from ``path``, or the ``literal`` typed in the rule.

    A path is optional only because a filter plus a literal can describe a label
    on their own ("if any element matches, tag the host"). ``value_field`` ("" =
    none) is read within each element of a '[*]' path.
    """

    path: str | None
    key: str | None
    value_field: str
    literal: str | None
    filter: Filter | None


@dataclass(frozen=True)
class InventoryTarget:
    """Where a field goes in the HW/SW inventory; ``key`` None = the path's last segment."""

    node: str
    key: str | None
    keep_service: bool


@dataclass(frozen=True)
class Extraction:
    """One field to monitor: where it is in the response, and which service it feeds."""

    path: str
    service: str
    # The shared service this field reports into as a LINE (stripped), or None
    # for a service of its own.
    group: str | None = None
    # The field within each '[*]' element that names it, instead of its index.
    label_path: str | None = None
    labels: tuple[ServiceLabel, ...] = ()
    # The summary template as written; its '{path}' placeholders are resolved
    # here, the text is rendered by the check.
    summary: str | None = None
    filter: Filter | None = None
    # 'count' / 'sum' / 'avg' / 'min' / 'max', or None to read the value as it is.
    aggregate: str | None = None
    # The field within each '[*]' element naming the PIGGYBACK host the element
    # becomes (as written; it is sanitised per element).
    piggyback_host: str | None = None
    piggyback_labels: tuple[HostLabel, ...] = ()
    inventory: InventoryTarget | None = None
    # The transform's second path, resolved in the same scope as the value.
    calc_path: str | None = None
    # Interpreted by the check only, and handed to it exactly as the rule has them.
    levels_upper: object = None
    levels_lower: object = None
    match: object = None
    calc: object = None
    unit: object = None
    metric_name: object = None
    value_range: object = None
    value_as: object = None


@dataclass(frozen=True)
class OAuth2:
    """The client-credentials grant: what to ask the identity provider for."""

    token_url: str
    client_id: str
    scope: str | None
    audience: str | None
    # 'post' sends the client credentials in the request body; anything else in
    # an HTTP basic header.
    client_auth: str


@dataclass(frozen=True)
class ClientCert:
    """Mutual TLS: the certificate file and, when separate, its key file."""

    cert: str
    key: str | None


@dataclass(frozen=True)
class Proxy:
    """An explicit proxy choice. Absent = honour HTTP(S)_PROXY from the environment."""

    # 'url' (route through ``url``) or 'no_proxy' (ignore the environment).
    mode: str
    url: str | None


@dataclass(frozen=True)
class Retry:
    """Extra attempts for a transient failure; 0 attempts = a single request."""

    attempts: int = 0
    backoff: float = 0.0


@dataclass(frozen=True)
class ResponseReport:
    """Report the raw response in the endpoint's own service details."""

    max_bytes: int
    headers: bool


@dataclass(frozen=True)
class FieldContext:
    """Report the JSON a value was read from in the FIELD services' details."""

    # 'response' is the whole body; anything else the element the value came from.
    source: str | None
    max_bytes: int


@dataclass(frozen=True)
class CountedPages:
    """Page-number / offset pagination: the agent builds the next URL itself."""

    mode: str  # 'page_number' or 'offset'
    parameter: str
    start: int
    page_size: int | None
    size_parameter: str | None
    total: str | None


@dataclass(frozen=True)
class Pagination:
    """Follow the API's pagination and merge the pages.

    ``source`` (a link in the body or the 'Link' header) and ``counted`` are
    None when the rule does not say where the next page is, and ``items`` when
    it does not say where the collection is. Kept rather than refused: the
    endpoint fails with that message once the first page has been read, which is
    where it always failed.
    """

    source: tuple[str, str] | None
    counted: CountedPages | None
    items: str | None
    max_pages: int
    max_elements: int | None


@dataclass(frozen=True)
class Endpoint:
    """One endpoint: the request, how to authenticate it, and what to read from it."""

    url: str
    # As written; the item and the service prefix strip it.
    name: str | None = None
    service_prefix: bool = False
    method: str = "GET"
    body: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    # 'auth_login' / 'auth_token' / 'auth_header' / 'auth_query' / 'auth_oauth2',
    # or None. The secret itself never travels in the blob.
    auth: str | None = None
    username: str | None = None
    # The NAMES an API key goes into, as written (the defaults apply at use).
    auth_header: str | None = None
    auth_query: str | None = None
    oauth2: OAuth2 | None = None
    verify_cert: bool = True
    ca_bundle: str | None = None
    client_cert: ClientCert | None = None
    follow_redirects: bool = True
    timeout: float | None = None
    cache_ttl: float | None = None
    accept_status: frozenset[int] = frozenset()
    retry: Retry = field(default_factory=Retry)
    proxy: Proxy | None = None
    show_response: ResponseReport | None = None
    field_context: FieldContext | None = None
    pagination: Pagination | None = None
    extractions: tuple[Extraction, ...] = ()
    host_labels: tuple[HostLabel, ...] = ()

    @property
    def effective_body(self) -> str | None:
        """The body actually sent: only POST carries one, never smuggled onto a GET.

        The Content-Type defaulting, the request and the cache key all read this,
        so they can never disagree about whether a body is present.
        """
        return self.body if self.method == "POST" else None


@dataclass(frozen=True)
class FieldName:
    """What a failing endpoint needs of one field to report the failure on it."""

    path: str
    service: str
    group: str | None


@dataclass(frozen=True)
class InvalidEndpoint:
    """An '--endpoint' blob that cannot be run, and what is known of it anyway.

    Enough to put the error on the services the endpoint would have had - its own
    one (named like a working endpoint's) and one per configured field - so a
    broken rule is reported where its services are, instead of the services
    going stale.
    """

    error: str
    url: str = "?"
    name: str | None = None
    service_prefix: bool = False
    fields: tuple[FieldName, ...] = ()


# --- reading the blob --------------------------------------------------------


def _number(raw: object) -> float | None:
    """A JSON number as a float; a bool is not a number here."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def _positive_int(raw: object) -> int | None:
    """A whole number of at least 1, or None - a bool is not a number here."""
    number = _number(raw)
    return int(number) if number is not None and number >= 1 else None


def _stripped(raw: object) -> str | None:
    """A string with something in it, stripped; anything else is None."""
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _optional_str(raw: object, what: str) -> str | None:
    """A string or nothing; a value of another type is a broken blob."""
    if raw is None or isinstance(raw, str):
        return raw
    raise EndpointConfigError(f"{what} is a {type(raw).__name__}, not a string")


def _objects(raw: object, what: str) -> list[Mapping[str, object]]:
    """A list of JSON objects (absent or null = none)."""
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise EndpointConfigError(f"{what} is a {type(raw).__name__}, not a list")
    for entry in raw:
        if not isinstance(entry, dict):
            raise EndpointConfigError(f"an entry of {what} is a {type(entry).__name__}")
    return list(raw)


def _limit(raw: object, default: int) -> int:
    """A byte limit, clamped to ``_MAX_REPORTED_BYTES``; unset or 0 is ``default``."""
    number = _number(raw)
    limit = int(number) if number is not None else 0
    return max(1, min(limit or default, _MAX_REPORTED_BYTES))


def parse_filter(raw: object) -> Filter | None:
    """The element filter, or None when none is configured (no object, no path)."""
    if not isinstance(raw, dict) or not raw.get("path"):
        return None
    path = raw["path"]
    if not isinstance(path, str):
        raise EndpointConfigError(f"the filter path is a {type(path).__name__}")
    op = raw.get("op")
    value = raw.get("value", "")
    if not isinstance(value, str):
        raise EndpointConfigError(f"the filter value is a {type(value).__name__}")
    return Filter(path=path, op=op if isinstance(op, str) else None, value=value)


def parse_service_labels(raw: object) -> tuple[ServiceLabel, ...]:
    """The SERVICE label specs; one without a path labels nothing and is dropped."""
    labels = []
    for spec in _objects(raw or None, "'labels'"):
        path = spec.get("path")
        if not path:
            continue
        if not isinstance(path, str):
            raise EndpointConfigError(f"a label path is a {type(path).__name__}")
        labels.append(
            ServiceLabel(path=path, key=_optional_str(spec.get("key"), "a label key") or None)
        )
    return tuple(labels)


def parse_host_labels(raw: object) -> tuple[HostLabel, ...]:
    """The HOST label specs, as the endpoint and a piggyback host carry them."""
    labels = []
    for spec in _objects(raw or None, "'host_labels'"):
        path = spec.get("path") or None
        if path is not None and not isinstance(path, str):
            raise EndpointConfigError(f"a host label path is a {type(path).__name__}")
        literal = spec.get("value")
        labels.append(
            HostLabel(
                path=path,
                key=_optional_str(spec.get("key"), "a host label key") or None,
                value_field=_optional_str(spec.get("value_field"), "a host label value field")
                or "",
                literal=literal if isinstance(literal, str) and literal else None,
                filter=parse_filter(spec.get("filter")),
            )
        )
    return tuple(labels)


def _inventory(raw: object) -> InventoryTarget | None:
    if not isinstance(raw, dict) or not raw.get("node"):
        return None
    key = _optional_str(raw.get("key"), "the inventory key")
    return InventoryTarget(
        node=str(raw["node"]).strip(),
        key=(key or "").strip() or None,
        keep_service=bool(raw.get("keep_service")),
    )


def _aggregate(raw: Mapping[str, object]) -> str | None:
    """The configured aggregation, or None.

    ``count: true`` is the superseded boolean form of ``aggregate: "count"``: a
    rule saved before the aggregate dropdown still carries it, and so may a
    hand-written '--endpoint' blob. Reading it here keeps both working.
    """
    mode = raw.get("aggregate")
    if isinstance(mode, str) and mode:
        return mode
    return "count" if raw.get("count") else None


def parse_extraction(raw: Mapping[str, object]) -> Extraction:
    """One field to monitor. 'path' and 'service' are required; the rest is optional."""
    path = raw.get("path")
    service = raw.get("service")
    if not isinstance(path, str):
        raise EndpointConfigError("a field has no 'path'")
    if not isinstance(service, str):
        raise EndpointConfigError(f"the field '{path}' has no 'service'")
    host_path = raw.get("piggyback_host")
    return Extraction(
        path=path,
        service=service,
        group=_stripped(raw.get("group")),
        label_path=_optional_str(raw.get("label_path"), "a label path") or None,
        labels=parse_service_labels(raw.get("labels")),
        summary=_optional_str(raw.get("summary") or None, "a summary"),
        filter=parse_filter(raw.get("filter")),
        aggregate=_aggregate(raw),
        piggyback_host=host_path if isinstance(host_path, str) else None,
        piggyback_labels=parse_host_labels(raw.get("piggyback_labels")),
        inventory=_inventory(raw.get("inventory")),
        calc_path=_stripped(raw.get("calc_path")),
        levels_upper=raw.get("levels_upper"),
        levels_lower=raw.get("levels_lower"),
        match=raw.get("match"),
        calc=raw.get("calc"),
        unit=raw.get("unit"),
        metric_name=raw.get("metric_name"),
        value_range=raw.get("value_range"),
        value_as=raw.get("value_as"),
    )


def _headers(raw: object) -> tuple[tuple[str, str], ...]:
    """The request headers as (name, value) pairs - JSON turns them into lists."""
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise EndpointConfigError(f"'headers' is a {type(raw).__name__}, not a list")
    pairs = []
    for pair in raw:
        if (
            not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or not all(isinstance(part, str) for part in pair)
        ):
            raise EndpointConfigError("a header is not a [name, value] pair of strings")
        pairs.append((pair[0], pair[1]))
    return tuple(pairs)


def _oauth2(raw: Mapping[str, object]) -> OAuth2 | None:
    """The OAuth2 settings, or None when the endpoint authenticates otherwise."""
    if raw.get("auth") != "auth_oauth2":
        return None
    spec = raw.get("oauth2")
    if not isinstance(spec, dict) or not spec.get("token_url"):
        return None
    scope = spec.get("scope")
    audience = spec.get("audience")
    client_auth = spec.get("client_auth", "basic")
    return OAuth2(
        token_url=str(spec["token_url"]),
        client_id=str(spec.get("client_id", "")),
        scope=str(scope) if scope else None,
        audience=str(audience) if audience else None,
        client_auth=client_auth if isinstance(client_auth, str) else "basic",
    )


def _client_cert(raw: object) -> ClientCert | None:
    if not isinstance(raw, dict) or not raw.get("cert"):
        return None
    key = raw.get("key")
    return ClientCert(cert=str(raw["cert"]), key=str(key) if key else None)


def _proxy(raw: object) -> Proxy | None:
    """The explicit proxy choice; 'environment', absent or unusable is None."""
    if not isinstance(raw, dict):
        return None
    match raw.get("mode"):
        case "url" if raw.get("url"):
            return Proxy(mode="url", url=str(raw["url"]))
        case "no_proxy":
            return Proxy(mode="no_proxy", url=None)
    return None


def _retry(raw: object) -> Retry:
    """The retry policy; anything but a positive attempt count means none."""
    if not isinstance(raw, dict):
        return Retry()
    attempts = raw.get("attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        return Retry()
    backoff = _number(raw.get("backoff"))
    return Retry(
        attempts=attempts, backoff=backoff if backoff is not None and backoff >= 0 else 0.0
    )


def _accept_status(raw: object) -> frozenset[int]:
    """Extra HTTP status codes to accept beyond 2xx; non-integers are ignored."""
    if not raw:
        return frozenset()
    if not isinstance(raw, (list, tuple)):
        raise EndpointConfigError(f"'accept_status' is a {type(raw).__name__}, not a list")
    return frozenset(code for code in raw if isinstance(code, int))


def _next_source(raw: object) -> tuple[str, str] | None:
    """Where the next page's URL comes from: ``("body", <path>)`` or ``("link_header", "")``.

    The CascadingSingleChoice value as the rule stores it (a list once it has
    been through JSON). Anything else - a missing choice, an empty body path -
    means pagination cannot be followed at all, which is reported as None rather
    than guessed at. The two modes in which the agent BUILDS the next URL itself
    are not a link source and answer None here; see ``_counted_source``.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    mode, value = raw
    if mode == "body" and isinstance(value, str) and value.strip():
        return "body", _strip_root(value)
    if mode == "link_header":
        return "link_header", ""
    return None


def _counted_source(raw: object) -> CountedPages | None:
    """The page-number / offset settings, normalized, or None for a link source.

    Not every API offers a next-page link. Many only take the position as a
    query parameter - '?page=3', or '?offset=50&limit=25' - and leave counting
    to the client. For those the agent does the counting: ``page_number`` adds
    one per page from ``start``; ``offset`` advances by the number of elements
    the previous page actually carried (not by the page size it asked for, which
    an API is free to lower).

    What ends it, since there is no absent link to say so: a page whose
    collection is empty, a page shorter than ``page_size`` AND than a page before
    it (see ``_counted_done``), or the elements read reaching the number at
    ``total`` (when the rule names a path to one). Without those, the last page
    is only known by the request after it - which an API may also answer with a
    404, or with the last page once more (see ``_follow_pagination``). One extra
    request, but never a wrong answer.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    mode, value = raw
    if mode not in ("page_number", "offset") or not isinstance(value, dict):
        return None
    parameter = _stripped(value.get("parameter"))
    if parameter is None:
        return None
    start = _number(value.get("start"))
    total = value.get("total")
    return CountedPages(
        mode=mode,
        parameter=parameter,
        # Both count from where the API does. A page number from 1 almost
        # everywhere (0 for the zero-based ones); an offset from 0, except for the
        # APIs that number the elements from 1 (SCIM's 'startIndex') - where
        # starting at 0 reads one element twice on every page.
        start=int(start)
        if start is not None and start >= 0
        else (1 if mode == "page_number" else 0),
        page_size=_positive_int(value.get("page_size")),
        size_parameter=_stripped(value.get("size_parameter")),
        total=_strip_root(total) if isinstance(total, str) and total.strip() else None,
    )


def _pagination(raw: object) -> Pagination | None:
    """The 'follow pagination' settings, or None when off."""
    if not isinstance(raw, dict):
        return None
    items = raw.get("items")
    pages = _positive_int(raw.get("max_pages")) or _DEFAULT_MAX_PAGES
    return Pagination(
        source=_next_source(raw.get("next")),
        counted=_counted_source(raw.get("next")),
        # '$' (or '$.') is how a response that IS the array says so; _strip_root
        # turns it into the empty path, which resolves to the whole document.
        items=_strip_root(items) if isinstance(items, str) else None,
        max_pages=min(pages, _MAX_PAGES),
        max_elements=_positive_int(raw.get("max_elements")),
    )


def _response_report(raw: object) -> ResponseReport | None:
    if not isinstance(raw, dict):
        return None
    return ResponseReport(
        max_bytes=_limit(raw.get("max_bytes"), _DEFAULT_REPORTED_BYTES),
        headers=bool(raw.get("headers", True)),
    )


def _field_context(raw: object) -> FieldContext | None:
    if not isinstance(raw, dict):
        return None
    source = raw.get("source")
    return FieldContext(
        source=source if isinstance(source, str) else None,
        max_bytes=_limit(raw.get("max_bytes"), _DEFAULT_CONTEXT_BYTES),
    )


def parse_endpoint(raw: Mapping[str, object]) -> Endpoint:
    """One decoded '--endpoint' blob as an ``Endpoint``.

    Raises ``EndpointConfigError`` for a blob the agent cannot run.
    """
    url = raw.get("url")
    if url is None:
        raise EndpointConfigError("no 'url'")
    if not isinstance(url, str):
        raise EndpointConfigError(f"'url' is a {type(url).__name__}, not a string")
    method = raw.get("method", "GET")
    if not isinstance(method, str):
        raise EndpointConfigError(f"'method' is a {type(method).__name__}, not a string")
    auth = raw.get("auth")
    if auth is not None and not isinstance(auth, str):
        raise EndpointConfigError(f"'auth' is a {type(auth).__name__}, not a string")
    username = raw.get("username")
    if auth == "auth_login" and not isinstance(username, str):
        raise EndpointConfigError("login authentication without a 'username'")
    name = raw.get("name")
    auth_header = raw.get("auth_header")
    auth_query = raw.get("auth_query")
    ca_bundle = raw.get("ca_bundle")
    timeout = raw.get("timeout")
    if timeout is not None and _number(timeout) is None:
        raise EndpointConfigError(f"'timeout' is a {type(timeout).__name__}, not a number")
    cache_ttl = _number(raw.get("cache_ttl"))
    # An explicit null used to reach requests as verify=None, which requests
    # reads as "use the session's default" - that is, verify. bool(None) would
    # turn verification OFF, so null keeps meaning on.
    verify = raw.get("verify_cert", True)
    return Endpoint(
        url=url,
        name=name if isinstance(name, str) else None,
        service_prefix=bool(raw.get("service_prefix")),
        method=method,
        body=_optional_str(raw.get("body"), "'body'"),
        headers=_headers(raw.get("headers")),
        auth=auth or None,
        username=username if isinstance(username, str) else None,
        auth_header=auth_header if isinstance(auth_header, str) else None,
        auth_query=auth_query if isinstance(auth_query, str) else None,
        oauth2=_oauth2(raw),
        verify_cert=True if verify is None else bool(verify),
        ca_bundle=str(ca_bundle) if ca_bundle else None,
        client_cert=_client_cert(raw.get("client_cert")),
        follow_redirects=bool(raw.get("follow_redirects", True)),
        timeout=_number(timeout),
        cache_ttl=cache_ttl if cache_ttl is not None and cache_ttl > 0 else None,
        accept_status=_accept_status(raw.get("accept_status")),
        retry=_retry(raw.get("retry")),
        proxy=_proxy(raw.get("proxy")),
        show_response=_response_report(raw.get("show_response")),
        field_context=_field_context(raw.get("field_context")),
        pagination=_pagination(raw.get("pagination")),
        extractions=tuple(
            parse_extraction(spec) for spec in _objects(raw.get("extractions"), "'extractions'")
        ),
        host_labels=parse_host_labels(raw.get("host_labels")),
    )


def invalid_endpoint(raw: Mapping[str, object], error: str) -> InvalidEndpoint:
    """What can still be read from a blob ``parse_endpoint`` refused.

    Read leniently, value by value: this is the blob that is broken, and the
    point is to report that on as many of its services as can be named.
    """
    url = raw.get("url")
    name = raw.get("name")
    fields = []
    extractions = raw.get("extractions")
    for spec in extractions if isinstance(extractions, (list, tuple)) else ():
        if not isinstance(spec, dict):
            continue
        path = spec.get("path")
        service = spec.get("service")
        fields.append(
            FieldName(
                path=path if isinstance(path, str) else "?",
                service=service if isinstance(service, str) else "?",
                group=_stripped(spec.get("group")),
            )
        )
    return InvalidEndpoint(
        error=error,
        url=url if isinstance(url, str) else "?",
        name=name if isinstance(name, str) else None,
        service_prefix=bool(raw.get("service_prefix")),
        fields=tuple(fields),
    )
