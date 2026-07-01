#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Server-side call: translate the Setup rule into the agent command line.

The ``name="json_api"`` below makes Checkmk look for and execute
``cmk_addons/plugins/json_api/libexec/agent_json_api``.
"""

import json
from collections.abc import Iterable, Sequence
from typing import Literal

from cmk.server_side_calls.v1 import (
    HostConfig,
    Secret,
    SpecialAgentCommand,
    SpecialAgentConfig,
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


class Extraction(BaseModel, frozen=True):
    path: str
    service: str
    label_path: str | None = None
    # The level tuples are produced by the SimpleLevels form spec, i.e.
    # ("fixed", (warn, crit)) or ("no_levels", None). We pass them through
    # verbatim to the check via the agent section, so they stay opaque here.
    levels_upper: object = None
    levels_lower: object = None
    expected: str | None = None


class Endpoint(BaseModel, frozen=True):
    url: str
    method: Literal["GET", "POST"] = "GET"
    body: str | None = None
    headers: Sequence[Header] = ()
    verify_cert: bool = True
    follow_redirects: bool = True
    timeout: float | None = None
    auth: (
        tuple[Literal["auth_login"], AuthLogin] | tuple[Literal["auth_token"], AuthToken] | None
    ) = None
    extractions: Sequence[Extraction] = ()


class Params(BaseModel, frozen=True):
    endpoints: Sequence[Endpoint] = ()


def _endpoint_json(endpoint: Endpoint) -> str:
    """Serialize an endpoint for the agent's '--endpoint' argument.

    Secrets are deliberately excluded here; they travel separately as
    '--secret_<i>' so they never appear inside this (loggable) blob.
    """
    spec: dict[str, object] = {
        "url": endpoint.url,
        "method": endpoint.method,
        "body": endpoint.body,
        "headers": [[h.name, h.value] for h in endpoint.headers],
        "verify_cert": endpoint.verify_cert,
        "follow_redirects": endpoint.follow_redirects,
        "timeout": endpoint.timeout,
        "auth": endpoint.auth[0] if endpoint.auth else None,
        "extractions": [e.model_dump() for e in endpoint.extractions],
    }
    if endpoint.auth and endpoint.auth[0] == "auth_login":
        spec["username"] = endpoint.auth[1].username
    return json.dumps(spec)


def _commands_function(
    params: Params,
    _host_config: HostConfig,
) -> Iterable[SpecialAgentCommand]:
    args: list[str | Secret] = []
    for index, endpoint in enumerate(params.endpoints):
        args += ["--endpoint", _endpoint_json(endpoint)]
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
