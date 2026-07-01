# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the server-side call: rule params -> agent command line."""

import json

from cmk.server_side_calls.v1 import HostConfig, IPv4Config, Secret


def _host():
    return HostConfig(name="testhost", ipv4_config=IPv4Config(address="127.0.0.1"))


def _command_args(ssc, params_dict):
    params = ssc.Params.model_validate(params_dict)
    (command,) = list(ssc._commands_function(params, _host()))
    return command.command_arguments


def _endpoints(ssc, args):
    """Parse all --endpoint JSON blobs from a command line."""
    return [json.loads(v) for k, v in zip(args, args[1:], strict=False) if k == "--endpoint"]


def test_basic_command_line(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "verify_cert": True,
                    "extractions": [{"path": "status", "service": "Health", "expected": "UP"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["url"] == "https://example.com/health"
    assert endpoint["method"] == "GET"
    assert endpoint["verify_cert"] is True
    assert endpoint["auth"] is None
    assert endpoint["extractions"] == [
        {
            "path": "status",
            "service": "Health",
            "label_path": None,
            "levels_upper": None,
            "levels_lower": None,
            "expected": "UP",
        }
    ]


def test_headers_body_and_cert_flag(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "method": "POST",
                    "body": "{}",
                    "verify_cert": False,
                    "headers": [{"name": "X-Api", "value": "v1"}],
                    "extractions": [],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["verify_cert"] is False
    assert endpoint["body"] == "{}"
    assert endpoint["headers"] == [["X-Api", "v1"]]


def test_timeout_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {"url": "http://x", "verify_cert": True, "timeout": 5.0, "extractions": []}
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["timeout"] == 5.0


def test_label_path_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [
                        {"path": "items[*].count", "service": "Item", "label_path": "name"}
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["label_path"] == "name"


def test_multiple_endpoints_each_with_own_config(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {"url": "http://a", "method": "GET", "verify_cert": True, "extractions": []},
                {"url": "http://b", "method": "POST", "verify_cert": False, "extractions": []},
            ]
        },
    )
    first, second = _endpoints(ssc, args)
    assert (first["url"], first["method"]) == ("http://a", "GET")
    assert (second["url"], second["method"], second["verify_cert"]) == ("http://b", "POST", False)


def test_token_secret_rides_alongside_its_endpoint(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "auth": ("auth_token", {"token": Secret(0)}),
                    "extractions": [],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["auth"] == "auth_token"
    # The secret travels as --secret_0-id, not inside the (loggable) endpoint blob.
    assert "--secret_0-id" in args
    assert "token" not in endpoint
    secret = args[args.index("--secret_0-id") + 1]
    assert isinstance(secret, Secret)


def test_login_secret_keeps_username_in_blob(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "auth": ("auth_login", {"username": "user", "password": Secret(0)}),
                    "extractions": [],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["auth"] == "auth_login"
    assert endpoint["username"] == "user"
    assert "--secret_0-id" in args
