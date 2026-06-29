#!/usr/bin/env python3
# Copyright (C) 2026 checkmk-json-agent contributors
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
    # The level tuples are produced by the SimpleLevels form spec, i.e.
    # ("fixed", (warn, crit)) or ("no_levels", None). We pass them through
    # verbatim to the check via the agent section, so they stay opaque here.
    levels_upper: object = None
    levels_lower: object = None
    expected: str | None = None


class Params(BaseModel, frozen=True):
    url: str
    method: Literal["GET", "POST"] = "GET"
    body: str | None = None
    headers: Sequence[Header] = ()
    verify_cert: bool = True
    auth: (
        tuple[Literal["auth_login"], AuthLogin] | tuple[Literal["auth_token"], AuthToken] | None
    ) = None
    extractions: Sequence[Extraction] = ()


def _commands_function(
    params: Params,
    _host_config: HostConfig,
) -> Iterable[SpecialAgentCommand]:
    args: list[str | Secret] = [
        "--url",
        params.url,
        "--method",
        params.method,
        "--extractions",
        json.dumps([e.model_dump() for e in params.extractions]),
    ]
    for header in params.headers:
        args += ["--header", f"{header.name}:{header.value}"]
    if params.body is not None:
        args += ["--body", params.body]
    if not params.verify_cert:
        args.append("--no-cert-check")
    # Auth subcommand must come last: the agent parses it with subparsers that
    # consume all remaining arguments.
    match params.auth:
        case ("auth_login", AuthLogin(username=username, password=password)):
            args += ["auth_login", "--username", username, "--password-id", password]
        case ("auth_token", AuthToken(token=token)):
            args += ["auth_token", "--token-id", token]
    yield SpecialAgentCommand(command_arguments=args)


special_agent_json_api = SpecialAgentConfig(
    name="json_api",
    parameter_parser=Params.model_validate,
    commands_function=_commands_function,
)
