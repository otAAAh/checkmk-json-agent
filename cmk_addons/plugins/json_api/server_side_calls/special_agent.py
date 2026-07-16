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


class Extraction(BaseModel, frozen=True):
    path: str
    service: str
    label_path: str | None = None
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
    # Arithmetic expression over 'value', applied to a numeric value by the check.
    calc: str | None = None
    # Monitor the length of the list/object at the path (resolved by the agent).
    count: bool = False


class Endpoint(BaseModel, frozen=True):
    url: str
    method: Literal["GET", "POST"] = "GET"
    body: str | None = None
    headers: Sequence[Header] = ()
    verify_cert: bool = True
    follow_redirects: bool = True
    timeout: float | None = None
    # Extra HTTP status codes to accept beyond 2xx (the agent reads their body).
    accept_status: Sequence[int] = ()
    # HTTP proxy: the framework resolves the rule's Proxy choice into one of
    # these before parsing (stored_proxy ids are resolved to a URLProxy).
    proxy: URLProxy | NoProxy | EnvProxy | None = None
    auth: (
        tuple[Literal["auth_login"], AuthLogin] | tuple[Literal["auth_token"], AuthToken] | None
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
        "method": endpoint.method,
        "body": replace_macros(endpoint.body, macros) if endpoint.body is not None else None,
        "headers": [[h.name, replace_macros(h.value, macros)] for h in endpoint.headers],
        "verify_cert": endpoint.verify_cert,
        "follow_redirects": endpoint.follow_redirects,
        "timeout": endpoint.timeout,
        "accept_status": list(endpoint.accept_status),
        "proxy": _proxy_spec(endpoint.proxy),
        "auth": endpoint.auth[0] if endpoint.auth else None,
        "extractions": [e.model_dump() for e in endpoint.extractions],
        "host_labels": [label.model_dump() for label in endpoint.host_labels],
    }
    if endpoint.auth and endpoint.auth[0] == "auth_login":
        spec["username"] = endpoint.auth[1].username
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
    yield SpecialAgentCommand(command_arguments=args)


special_agent_json_api = SpecialAgentConfig(
    name="json_api",
    parameter_parser=Params.model_validate,
    commands_function=_commands_function,
)
