# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the server-side call: rule params -> agent command line."""

import json

from cmk.server_side_calls.v1 import HostConfig, IPv4Config


def _host():
    return HostConfig(name="testhost", ipv4_config=IPv4Config(address="127.0.0.1"))


def _command_args(ssc, params_dict):
    params = ssc.Params.model_validate(params_dict)
    (command,) = list(ssc._commands_function(params, _host()))
    return command.command_arguments


def test_basic_command_line(ssc):
    args = _command_args(
        ssc,
        {
            "url": "https://example.com/health",
            "method": "GET",
            "verify_cert": True,
            "extractions": [{"path": "status", "service": "Health", "expected": "UP"}],
        },
    )
    assert "--url" in args
    assert args[args.index("--url") + 1] == "https://example.com/health"
    assert "--method" in args
    # extractions are passed as a JSON blob
    blob = json.loads(args[args.index("--extractions") + 1])
    assert blob == [
        {
            "path": "status",
            "service": "Health",
            "label_path": None,
            "levels_upper": None,
            "levels_lower": None,
            "expected": "UP",
        }
    ]
    # cert verification on -> no --no-cert-check flag
    assert "--no-cert-check" not in args


def test_headers_body_and_cert_flag(ssc):
    args = _command_args(
        ssc,
        {
            "url": "http://x",
            "method": "POST",
            "body": "{}",
            "verify_cert": False,
            "headers": [{"name": "X-Api", "value": "v1"}],
            "extractions": [],
        },
    )
    assert "--no-cert-check" in args
    assert "--body" in args and args[args.index("--body") + 1] == "{}"
    assert "--header" in args and args[args.index("--header") + 1] == "X-Api:v1"


def test_timeout_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "url": "http://x",
            "method": "GET",
            "verify_cert": True,
            "timeout": 5.0,
            "extractions": [],
        },
    )
    assert "--timeout" in args
    assert args[args.index("--timeout") + 1] == "5.0"


def test_label_path_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "url": "http://x",
            "method": "GET",
            "verify_cert": True,
            "extractions": [{"path": "items[*].count", "service": "Item", "label_path": "name"}],
        },
    )
    blob = json.loads(args[args.index("--extractions") + 1])
    assert blob[0]["label_path"] == "name"
