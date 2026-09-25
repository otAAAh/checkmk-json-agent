#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Server-side call: translate the Setup rule into the agent command line.

The ``name="json_api"`` below makes Checkmk look for and execute
``cmk_addons/plugins/json_api/libexec/agent_json_api``.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from cmk.server_side_calls.v1 import (
    EnvProxy,
    HostConfig,
    NoProxy,
    Secret,
    SpecialAgentCommand,
    SpecialAgentConfig,
    URLProxy,
    replace_macros,
)
from pydantic import BaseModel


class Header(BaseModel, frozen=True):
    name: str
    value: str


class AuthLogin(BaseModel, frozen=True):
    username: str
    password: Secret


class AuthToken(BaseModel, frozen=True):
    token: Secret


class AuthHeader(BaseModel, frozen=True):
    # A static API key sent in a header of the API's choosing ('X-API-Key',
    # 'PRIVATE-TOKEN', ...). Only the header NAME is carried in the endpoint blob;
    # the key itself travels as '--secret_<i>-id' like every other secret.
    header: str
    key: Secret


class AuthQuery(BaseModel, frozen=True):
    # The same key, but in a query parameter. Only the parameter NAME is carried
    # here; the agent appends the value at request time and redacts it again
    # before reporting any URL.
    parameter: str
    key: Secret


class AuthOAuth2(BaseModel, frozen=True):
    # The machine-to-machine grant: the agent trades these for a short-lived
    # access token. Only the SECRET travels as a Secret reference; the token URL,
    # client id, scope and audience are not credentials and ride in the endpoint
    # blob like every other non-secret setting.
    token_url: str
    client_id: str
    client_secret: Secret
    scope: str | None = None
    audience: str | None = None
    # Whether the client id/secret go in an HTTP basic header or the POST body.
    # RFC 6749 allows both and providers disagree; see the ruleset help.
    client_auth: Literal["basic", "post"] = "basic"


class LabelSpec(BaseModel, frozen=True):
    path: str
    # Optional key override; the agent derives it from the path's last segment
    # when unset. The check adds the json_api/ namespace prefix.
    key: str | None = None


class FilterSpec(BaseModel, frozen=True):
    # Restrict a '[*]'/count extraction - or a host label - to elements whose
    # sub-path matches.
    path: str
    op: Literal["equals", "not_equals", "regex", "not_regex"] = "equals"
    value: str = ""


class HostLabelSpec(BaseModel, frozen=True):
    # A path is optional only because a filter plus a literal value can describe
    # a label entirely on their own ("if any element matches, tag the host").
    path: str | None = None
    key: str | None = None
    # For a '[*]' path: field within each element for the value (default 'true').
    value_field: str | None = None
    # A value typed in the rule instead of read from the response. It also makes
    # a '[*]' path emit ONE label for the whole collection rather than one per
    # element, so the key needs no per-element suffix to stay unique.
    value: str | None = None
    # Which elements produce a label (opaque here; the agent applies it).
    filter: FilterSpec | None = None


class PiggybackLabelSpec(BaseModel, frozen=True):
    # Host labels for the host one '[*]' element becomes: the same shape as
    # HostLabelSpec minus 'value_field', which needs a wildcard to read from and
    # the element is already one level below it.
    path: str | None = None
    key: str | None = None
    value: str | None = None
    filter: FilterSpec | None = None


class InventorySpec(BaseModel, frozen=True):
    # Write the value into the host's HW/SW inventory tree instead of (or as well
    # as) creating a service. The agent resolves the attribute name and, for a
    # '[*]' wildcard, the row key; the check writes the tree.
    node: str
    key: str | None = None
    keep_service: bool = False


class ValueRange(BaseModel, frozen=True):
    # The range the value moves in, when it has one. Presentation only: it
    # becomes the metric's boundaries, which is what gives a graph a steady
    # scale, a gauge widget its dial and the service list's bar a real maximum.
    # Either end may be absent - a queue with a floor of zero and no ceiling is
    # a range worth stating too.
    min: float | None = None
    max: float | None = None


class Extraction(BaseModel, frozen=True):
    path: str
    service: str
    # Report this field into a shared service of this name instead of one of its
    # own; 'service' then names its LINE within that service. The check yields
    # one result per line and Checkmk's aggregation takes the worst state.
    group: str | None = None
    label_path: str | None = None
    # Field within each '[*]' element supplying a PIGGYBACK HOST name: the element
    # becomes its own Checkmk host carrying this service, instead of one
    # label-suffixed service on the polling host. Resolved by the agent, which
    # sanitises it and routes the result into a '<<<<host>>>>' section.
    piggyback_host: str | None = None
    # HOST labels for the piggyback host this element becomes, resolved by the
    # agent within the element. Distinct from `labels`, which are SERVICE labels
    # on the service itself.
    piggyback_labels: Sequence[PiggybackLabelSpec] = ()
    # Keep only wildcard/count elements matching this predicate (opaque to the
    # server-side call; the agent applies it). Serialized via model_dump below.
    filter: FilterSpec | None = None
    unit: str | None = None
    # The metric name stated in the rule, overriding the one the check would
    # derive from the unit (and, in a shared service, the line's name). Opaque
    # here; the check is the only place that emits a metric.
    metric_name: str | None = None
    # Carried through to the check, which turns it into the metric's boundaries.
    value_range: ValueRange | None = None
    # Fields attached as SERVICE labels on this service (resolved per '[*]'
    # element by the agent). Host-wide labels live on the endpoint (host_labels).
    labels: Sequence[LabelSpec] = ()
    # The level tuples are produced by the SimpleLevels form spec, i.e.
    # ("fixed", (warn, crit)) or ("no_levels", None). We pass them through
    # verbatim to the check via the agent section, so they stay opaque here.
    levels_upper: object = None
    levels_lower: object = None
    # ("must_match", <regex>) or ("state_map", {"ok"/"warn"/"crit": <regex>}),
    # straight from the CascadingSingleChoice form spec. Opaque here; the check
    # interprets it.
    match: object = None
    # Arithmetic expression over 'value' (and 'other'), applied to a numeric
    # value by the check.
    calc: str | None = None
    # Second path supplying 'other' in that expression, resolved by the agent in
    # the same scope as the value itself.
    calc_path: str | None = None
    # Extra text for the service summary, with '{path}' placeholders. The agent
    # resolves the paths (it has the document and the current '[*]' element); the
    # check renders the text. Presentation only - never touches the state.
    summary: str | None = None
    # Where this field goes in the inventory tree, if anywhere.
    inventory: InventorySpec | None = None
    # Collapse the collection at the path into one number: count / sum / avg /
    # min / max (resolved by the agent, which has the document).
    aggregate: Literal["count", "sum", "avg", "min", "max"] | None = None
    # Superseded by ``aggregate``: the old boolean "count the elements at this
    # path". A rule saved before the aggregate dropdown still carries it (the
    # ruleset migrates it only when the rule is next opened in Setup), so it is
    # passed on and the agent reads it as aggregate="count".
    count: bool = False
    # ("counter", None) or ("timestamp", {"format": ...}) from the
    # CascadingSingleChoice: derive a per-second rate / an age from the value.
    # Opaque here; the check interprets it.
    value_as: object = None


class Retry(BaseModel, frozen=True):
    # Extra attempts for a request that failed in a way a repeat could fix, and
    # the (doubling) wait between them. The agent owns the policy.
    attempts: int = 2
    backoff: float = 0.5


class ShowResponse(BaseModel, frozen=True):
    # Report the response itself in the endpoint service's details: how much of
    # the body, and whether the headers come too. The agent caps and redacts.
    max_bytes: int = 2048
    headers: bool = True


class FieldContext(BaseModel, frozen=True):
    # Report the JSON a value was read from in the FIELD services' details (the
    # ones that alert), not just on the endpoint's own service: the element the
    # value came from, or the whole response body. The agent caps and redacts.
    source: Literal["element", "response"] = "element"
    max_bytes: int = 1024


class CountedPages(BaseModel, frozen=True):
    # For an API with no next-page link, which takes the position as a query
    # parameter instead: the agent counts the pages (or the elements, for an
    # offset) and asks for the next one itself.
    parameter: str
    # The first page's number; unused for an offset, which starts at 0.
    start: int = 1
    # The page size: a shorter page is the last one. Sent as 'size_parameter'
    # when that is set, otherwise only used to recognise the last page.
    page_size: int | None = None
    size_parameter: str | None = None
    # A path to the number of elements the whole collection holds.
    total: str | None = None


class Pagination(BaseModel, frozen=True):
    # Follow an API that answers one page at a time and merge the pages, so a
    # '[*]' expansion or an aggregation describes the whole collection instead of
    # its first page. The agent owns the policy (which links it will follow, the
    # caps, the merging); only where to look travels here.
    #
    # The next-page link: ("body", "<path>") reads it from the response, e.g.
    # 'links.next'; ("link_header", None) takes it from the RFC 8288 'Link'
    # header's rel="next"; ("page_number", {...}) and ("offset", {...}) build the
    # next URL from a counted query parameter. Straight from the
    # CascadingSingleChoice form spec.
    next: (
        tuple[Literal["body"], str]
        | tuple[Literal["link_header"], None]
        | tuple[Literal["page_number", "offset"], CountedPages]
    )
    # The collection each page carries ('data.items', or '$' for a response that
    # IS the array); it is what the pages are appended to.
    items: str
    max_pages: int = 10
    # A ceiling on the merged collection, checked between pages. None = no limit
    # beyond the page count.
    max_elements: int | None = None


class ClientCert(BaseModel, frozen=True):
    cert: str
    # Separate private-key file; omit when the key is bundled into the cert file.
    key: str | None = None


class Endpoint(BaseModel, frozen=True):
    url: str
    # Optional short name; names the endpoint's own status service (item),
    # defaulting to the URL.
    name: str | None = None
    # Put that name in front of this endpoint's FIELD service names too, so two
    # endpoints extracting the same fields do not produce two identically named
    # services. Needs a name (the agent never falls back to the URL here).
    service_prefix: bool = False
    method: Literal["GET", "POST"] = "GET"
    body: str | None = None
    headers: Sequence[Header] = ()
    verify_cert: bool = True
    # Path to a custom CA bundle to verify the server against (private CAs).
    ca_bundle: str | None = None
    # Client certificate for mutual TLS.
    client_cert: ClientCert | None = None
    follow_redirects: bool = True
    timeout: float | None = None
    # Reuse the last response while it is younger than this many seconds
    # (the agent owns the cache). None = always fetch fresh.
    cache_ttl: float | None = None
    # Extra HTTP status codes to accept beyond 2xx (the agent reads their body).
    accept_status: Sequence[int] = ()
    # Retry policy for a transient failure. None = a single attempt.
    retry: Retry | None = None
    # Report the raw response in the endpoint's own service details. None = no.
    show_response: ShowResponse | None = None
    # Report the JSON context in this endpoint's FIELD services. None = no.
    field_context: FieldContext | None = None
    # Follow the API's pagination and merge the pages. None = read one page.
    pagination: Pagination | None = None
    # HTTP proxy: the framework resolves the rule's Proxy choice into one of
    # these before parsing (stored_proxy ids are resolved to a URLProxy).
    proxy: URLProxy | NoProxy | EnvProxy | None = None
    auth: (
        tuple[Literal["auth_login"], AuthLogin]
        | tuple[Literal["auth_token"], AuthToken]
        | tuple[Literal["auth_header"], AuthHeader]
        | tuple[Literal["auth_query"], AuthQuery]
        | tuple[Literal["auth_oauth2"], AuthOAuth2]
        | None
    ) = None
    extractions: Sequence[Extraction] = ()
    # Host-wide labels, resolved from the response root by the agent.
    host_labels: Sequence[HostLabelSpec] = ()


class Params(BaseModel, frozen=True):
    endpoints: Sequence[Endpoint] = ()


def _proxy_spec(proxy: URLProxy | NoProxy | EnvProxy | None) -> dict[str, str] | None:
    """The agent-facing proxy blob, or None to honour the environment.

    'environment' and an absent proxy both mean "use HTTP(S)_PROXY", so they
    collapse to None; only an explicit URL or 'no proxy' need to be carried.
    """
    match proxy:
        case URLProxy(url=url):
            return {"mode": "url", "url": url}
        case NoProxy():
            return {"mode": "no_proxy"}
        case _:  # EnvProxy or None
            return None


def _endpoint_json(endpoint: Endpoint, macros: Mapping[str, str]) -> str:
    """Serialize an endpoint for the agent's '--endpoint' argument.

    Checkmk macros (``$HOSTNAME$``, ``$HOSTADDRESS$``, custom host macros, ...)
    are resolved against the monitored host in the URL, request body and header
    values, so a single rule can be shared across many hosts.

    Secrets are deliberately excluded here; they travel separately as
    '--secret_<i>' so they never appear inside this (loggable) blob.
    """
    spec: dict[str, object] = {
        "url": replace_macros(endpoint.url, macros),
        # The name becomes a service item, so resolve macros here too - that way
        # one shared rule can still name the endpoint per host.
        "name": replace_macros(endpoint.name, macros) if endpoint.name is not None else None,
        "service_prefix": endpoint.service_prefix,
        "method": endpoint.method,
        "body": replace_macros(endpoint.body, macros) if endpoint.body is not None else None,
        "headers": [[h.name, replace_macros(h.value, macros)] for h in endpoint.headers],
        "verify_cert": endpoint.verify_cert,
        "ca_bundle": endpoint.ca_bundle,
        "client_cert": endpoint.client_cert.model_dump() if endpoint.client_cert else None,
        "follow_redirects": endpoint.follow_redirects,
        "timeout": endpoint.timeout,
        "cache_ttl": endpoint.cache_ttl,
        "accept_status": list(endpoint.accept_status),
        "retry": endpoint.retry.model_dump() if endpoint.retry else None,
        "show_response": (endpoint.show_response.model_dump() if endpoint.show_response else None),
        "field_context": (endpoint.field_context.model_dump() if endpoint.field_context else None),
        "pagination": endpoint.pagination.model_dump() if endpoint.pagination else None,
        "proxy": _proxy_spec(endpoint.proxy),
        "auth": endpoint.auth[0] if endpoint.auth else None,
        "extractions": [e.model_dump() for e in endpoint.extractions],
        "host_labels": [label.model_dump() for label in endpoint.host_labels],
    }
    # The non-secret half of the chosen authentication: a username, or the name of
    # the header / query parameter the key goes into. Names are not credentials,
    # so they belong in the blob; the values never do.
    match endpoint.auth:
        case ("auth_login", AuthLogin(username=username)):
            spec["username"] = username
        case ("auth_header", AuthHeader(header=header)):
            spec["auth_header"] = header
        case ("auth_query", AuthQuery(parameter=parameter)):
            spec["auth_query"] = parameter
        case ("auth_oauth2", AuthOAuth2() as oauth2):
            # Macros are resolved in the token URL too: a shared rule may well
            # point at a per-host identity provider.
            spec["oauth2"] = {
                "token_url": replace_macros(oauth2.token_url, macros),
                "client_id": oauth2.client_id,
                "scope": oauth2.scope,
                "audience": oauth2.audience,
                "client_auth": oauth2.client_auth,
            }
    return json.dumps(spec)


def _commands_function(
    params: Params,
    host_config: HostConfig,
) -> Iterable[SpecialAgentCommand]:
    args: list[str | Secret] = []
    for index, endpoint in enumerate(params.endpoints):
        args += ["--endpoint", _endpoint_json(endpoint, host_config.macros)]
        # The secret (a password-store reference) rides alongside its endpoint,
        # keyed by index so the agent can match them up.
        match endpoint.auth:
            case ("auth_login", AuthLogin(password=password)):
                args += [f"--secret_{index}-id", password]
            case ("auth_token", AuthToken(token=token)):
                args += [f"--secret_{index}-id", token]
            case ("auth_header", AuthHeader(key=key)) | ("auth_query", AuthQuery(key=key)):
                args += [f"--secret_{index}-id", key]
            case ("auth_oauth2", AuthOAuth2(client_secret=client_secret)):
                args += [f"--secret_{index}-id", client_secret]
    yield SpecialAgentCommand(command_arguments=args)


special_agent_json_api = SpecialAgentConfig(
    name="json_api",
    parameter_parser=Params.model_validate,
    commands_function=_commands_function,
)
