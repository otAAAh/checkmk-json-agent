# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the server-side call: rule params -> agent command line."""

import json

import pytest
from cmk.server_side_calls.v1 import HostConfig, IPv4Config, Secret
from pydantic import ValidationError


def _host(macros=None):
    return HostConfig(
        name="testhost",
        ipv4_config=IPv4Config(address="127.0.0.1"),
        macros=macros or {},
    )


def _command_args(ssc, params_dict, host=None):
    params = ssc.Params.model_validate(params_dict)
    (command,) = list(ssc._commands_function(params, host or _host()))
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
                    "extractions": [
                        {
                            "path": "status",
                            "service": "Health",
                            "match": ("must_match", {"pattern": "UP"}),
                        }
                    ],
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
            "piggyback_host": None,
            "filter": None,
            "unit": None,
            "labels": [],
            "levels_upper": None,
            "levels_lower": None,
            # The CascadingSingleChoice tuple survives the JSON round-trip as a list.
            "match": ["must_match", {"pattern": "UP"}],
            "calc": None,
            "summary": None,
            "aggregate": None,
            # Superseded by 'aggregate', still passed on for unmigrated rules.
            "count": False,
            "value_as": None,
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


def test_follow_redirects_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "follow_redirects": False,
                    "extractions": [],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["follow_redirects"] is False


def test_follow_redirects_defaults_true(ssc):
    args = _command_args(
        ssc,
        {"endpoints": [{"url": "http://x", "verify_cert": True, "extractions": []}]},
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["follow_redirects"] is True


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


def test_service_labels_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [
                        {
                            "path": "nodes[*].up",
                            "service": "Node",
                            "labels": [{"path": "name", "key": None}],
                        }
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["labels"] == [{"path": "name", "key": None}]


def test_host_labels_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "status", "service": "S"}],
                    "host_labels": [
                        {"path": "version", "key": None},
                        {"path": "cluster.region", "key": "region"},
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["host_labels"] == [
        {"path": "version", "key": None, "value_field": None},
        {"path": "cluster.region", "key": "region", "value_field": None},
    ]


def test_unit_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "mem", "service": "Mem", "unit": "bytes"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["unit"] == "bytes"


def test_calc_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "mem", "service": "Mem", "calc": "value / 1024"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["calc"] == "value / 1024"


def test_count_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "items", "service": "Items", "count": True}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["count"] is True


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


def test_macros_resolved_in_url(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://$HOSTNAME$:8080/$HOSTADDRESS$/health",
                    "verify_cert": True,
                    "extractions": [],
                }
            ]
        },
        host=_host({"$HOSTNAME$": "myhost", "$HOSTADDRESS$": "10.0.0.9"}),
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["url"] == "https://myhost:8080/10.0.0.9/health"


def test_macros_resolved_in_body_and_headers(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "method": "POST",
                    "body": '{"host": "$HOSTNAME$"}',
                    "verify_cert": True,
                    "headers": [{"name": "X-Host", "value": "$HOSTADDRESS$"}],
                    "extractions": [],
                }
            ]
        },
        host=_host({"$HOSTNAME$": "myhost", "$HOSTADDRESS$": "10.0.0.9"}),
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["body"] == '{"host": "myhost"}'
    assert endpoint["headers"] == [["X-Host", "10.0.0.9"]]


def test_unknown_macros_left_untouched(ssc):
    args = _command_args(
        ssc,
        {"endpoints": [{"url": "http://$UNSET$/x", "verify_cert": True, "extractions": []}]},
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["url"] == "http://$UNSET$/x"


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


def test_accept_status_serialized(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "verify_cert": True,
                    "accept_status": [503, 202],
                    "extractions": [{"path": "status", "service": "Health"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["accept_status"] == [503, 202]


def test_accept_status_defaults_empty(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "verify_cert": True,
                    "extractions": [{"path": "status", "service": "Health"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["accept_status"] == []


def test_proxy_url_serialized(ssc):
    from cmk.server_side_calls.v1 import URLProxy

    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "verify_cert": True,
                    "proxy": URLProxy(url="http://proxy:3128"),
                    "extractions": [{"path": "status", "service": "Health"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["proxy"] == {"mode": "url", "url": "http://proxy:3128"}


def test_proxy_no_proxy_serialized(ssc):
    from cmk.server_side_calls.v1 import NoProxy

    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "verify_cert": True,
                    "proxy": NoProxy(),
                    "extractions": [{"path": "status", "service": "Health"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["proxy"] == {"mode": "no_proxy"}


def test_proxy_environment_and_absent_are_null(ssc):
    from cmk.server_side_calls.v1 import EnvProxy

    for proxy in (EnvProxy(), None):
        args = _command_args(
            ssc,
            {
                "endpoints": [
                    {
                        "url": "https://example.com/health",
                        "method": "GET",
                        "verify_cert": True,
                        "proxy": proxy,
                        "extractions": [{"path": "status", "service": "Health"}],
                    }
                ]
            },
        )
        (endpoint,) = _endpoints(ssc, args)
        assert endpoint["proxy"] is None


def test_tls_ca_and_client_cert_serialized(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "verify_cert": True,
                    "ca_bundle": "/etc/ssl/internal-ca.pem",
                    "client_cert": {"cert": "/etc/ssl/client.pem", "key": "/etc/ssl/client.key"},
                    "extractions": [{"path": "status", "service": "Health"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["ca_bundle"] == "/etc/ssl/internal-ca.pem"
    assert endpoint["client_cert"] == {
        "cert": "/etc/ssl/client.pem",
        "key": "/etc/ssl/client.key",
    }


def test_tls_ca_and_client_cert_default_null(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "verify_cert": True,
                    "extractions": [{"path": "status", "service": "Health"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["ca_bundle"] is None
    assert endpoint["client_cert"] is None


def test_filter_serialized(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "verify_cert": True,
                    "extractions": [
                        {
                            "path": "nodes[*].health",
                            "service": "Node",
                            "filter": {"path": "health", "op": "not_equals", "value": "ok"},
                        }
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    (extraction,) = endpoint["extractions"]
    assert extraction["filter"] == {"path": "health", "op": "not_equals", "value": "ok"}


def test_filter_defaults_null(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "verify_cert": True,
                    "extractions": [{"path": "status", "service": "Health"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["filter"] is None


def test_aggregate_and_value_as_reach_the_agent(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "name": "frontend",
                    "extractions": [
                        {
                            "path": "nodes[*].load",
                            "service": "Load",
                            "aggregate": "avg",
                            "value_as": ("timestamp", {"format": "iso"}),
                        }
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["name"] == "frontend"
    (extraction,) = endpoint["extractions"]
    assert extraction["aggregate"] == "avg"
    # The CascadingSingleChoice tuple survives the JSON round-trip as a list.
    assert extraction["value_as"] == ["timestamp", {"format": "iso"}]


def test_endpoint_name_is_absent_when_unset(ssc):
    args = _command_args(
        ssc, {"endpoints": [{"url": "https://example.com/health", "extractions": []}]}
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["name"] is None


def test_endpoint_name_resolves_macros(ssc):
    args = _command_args(
        ssc,
        {"endpoints": [{"url": "https://x/health", "name": "$HOSTNAME$ API", "extractions": []}]},
        host=_host({"$HOSTNAME$": "web01"}),
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["name"] == "web01 API"


def test_unmigrated_count_flag_still_reaches_the_agent(ssc):
    # A rule stored before the aggregate dropdown carries count=True; the ruleset
    # migrates it only when the rule is next opened in Setup, so it must survive
    # the trip to the agent (which reads it as aggregate="count").
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://x/health",
                    "extractions": [{"path": "jobs", "service": "Jobs", "count": True}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["count"] is True


@pytest.mark.parametrize("aggregate", ["count", "sum", "avg", "min", "max"])
def test_every_aggregate_choice_validates(ssc, aggregate):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://x/health",
                    "extractions": [{"path": "v", "service": "V", "aggregate": aggregate}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["aggregate"] == aggregate


def test_unknown_aggregate_is_rejected(ssc):
    # The Literal on the model is what keeps a typo out of the agent blob.
    with pytest.raises(ValidationError):
        ssc.Params.model_validate(
            {
                "endpoints": [
                    {
                        "url": "https://x/health",
                        "extractions": [{"path": "v", "service": "V", "aggregate": "median"}],
                    }
                ]
            }
        )


def test_piggyback_host_passed_through(ssc):
    # The agent needs it to route the element's services into a '<<<<host>>>>'
    # section; a model that dropped it would silently disable piggybacking.
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [
                        {
                            "path": "nodes[*].health",
                            "service": "Health",
                            "piggyback_host": "name",
                        }
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["piggyback_host"] == "name"


def test_piggyback_host_defaults_to_none(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "s", "service": "S"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["piggyback_host"] is None


def test_cache_ttl_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {"url": "http://x", "verify_cert": True, "cache_ttl": 300.0, "extractions": []}
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["cache_ttl"] == 300.0


def test_cache_ttl_defaults_to_none(ssc):
    args = _command_args(
        ssc, {"endpoints": [{"url": "http://x", "verify_cert": True, "extractions": []}]}
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["cache_ttl"] is None


def test_api_key_header_name_travels_in_the_blob_and_the_key_does_not(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "auth": ("auth_header", {"header": "X-API-Key", "key": Secret(0)}),
                    "extractions": [],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    # The header NAME is not a credential and the agent needs it; the key itself
    # rides out-of-band, exactly like a bearer token.
    assert endpoint["auth"] == "auth_header"
    assert endpoint["auth_header"] == "X-API-Key"
    assert "key" not in endpoint
    assert "--secret_0-id" in args
    assert isinstance(args[args.index("--secret_0-id") + 1], Secret)


def test_api_key_query_parameter_name_travels_in_the_blob_and_the_key_does_not(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "auth": ("auth_query", {"parameter": "api_key", "key": Secret(0)}),
                    "extractions": [],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["auth"] == "auth_query"
    assert endpoint["auth_query"] == "api_key"
    # The configured URL stays clean: the agent appends the parameter itself.
    assert endpoint["url"] == "http://x"
    assert "--secret_0-id" in args
    assert isinstance(args[args.index("--secret_0-id") + 1], Secret)


def test_retry_policy_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "retry": {"attempts": 3, "backoff": 1.0},
                    "extractions": [],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["retry"] == {"attempts": 3, "backoff": 1.0}


def test_no_retry_policy_means_a_single_attempt(ssc):
    args = _command_args(
        ssc,
        {"endpoints": [{"url": "http://x", "verify_cert": True, "extractions": []}]},
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["retry"] is None
