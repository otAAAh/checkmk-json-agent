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


class HostLabelSpec(BaseModel, frozen=True):
    path: str
    key: str | None = None
    # For a '[*]' path: field within each element for the value (default 'true').
    value_field: str | None = None


class FilterSpec(BaseModel, frozen=True):
    # Restrict a '[*]'/count extraction to elements whose sub-path matches.
    path: str
    op: Literal["equals", "not_equals", "regex", "not_regex"] = "equals"
    value: str = ""


class InventorySpec(BaseModel, frozen=True):
    # Write the value into the host's HW/SW inventory tree instead of (or as well
    # as) creating a service. The agent resolves the attribute name and, for a
    # '[*]' wildcard, the row key; the check writes the tree.
    node: str
    key: str | None = None
    keep_service: bool = False


class Extraction(BaseModel, frozen=True):
    path: str
    service: str
    label_path: str | None = None
    # Field within each '[*]' element supplying a PIGGYBACK HOST name: the element
    # becomes its own Checkmk host carrying this service, instead of one
    # label-suffixed service on the polling host. Resolved by the agent, which
    # sanitises it and routes the result into a '<<<<host>>>>' section.
    piggyback_host: str | None = None
    # HOST labels for the piggyback host this element becomes, resolved by the
    # agent within the element. Distinct from `labels`, which are SERVICE labels
    # on the service itself.
    piggyback_labels: Sequence[LabelSpec] = ()
    # Keep only wildcard/count elements matching this predicate (opaque to the
    # server-side call; the agent applies it). Serialized via model_dump below.
    filter: FilterSpec | None = None
    unit: str | None = None
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


class ClientCert(BaseModel, frozen=True):
    cert: str
    # Separate private-key file; omit when the key is bundled into the cert file.
    key: str | None = None


class Endpoint(BaseModel, frozen=True):
    url: str
    # Optional short name; names the endpoint's own status service (item),
    # defaulting to the URL.
    name: str | None = None
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
