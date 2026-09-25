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
            "group": None,
            "label_path": None,
            "piggyback_host": None,
            "piggyback_labels": [],
            "filter": None,
            "unit": None,
            "metric_name": None,
            "value_range": None,
            "labels": [],
            "levels_upper": None,
            "levels_lower": None,
            # The CascadingSingleChoice tuple survives the JSON round-trip as a list.
            "match": ["must_match", {"pattern": "UP"}],
            "calc": None,
            "calc_path": None,
            "inventory": None,
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
        {"path": "version", "key": None, "value_field": None, "value": None, "filter": None},
        {
            "path": "cluster.region",
            "key": "region",
            "value_field": None,
            "value": None,
            "filter": None,
        },
    ]


def test_host_label_filter_and_literal_value_passed_through(ssc):
    """The classification shape: a condition over a collection, one literal label."""
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "status", "service": "S"}],
                    "host_labels": [
                        {
                            "path": "services[*]",
                            "key": "MyApp",
                            "value": "yes",
                            "filter": {"path": "name", "op": "regex", "value": "^MyApp"},
                        }
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["host_labels"] == [
        {
            "path": "services[*]",
            "key": "MyApp",
            "value_field": None,
            "value": "yes",
            "filter": {"path": "name", "op": "regex", "value": "^MyApp"},
        }
    ]


def test_piggyback_labels_carry_a_filter_and_a_literal_value(ssc):
    """The same two fields on the labels of a host an element becomes."""
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
                            "service": "S",
                            "piggyback_host": "name",
                            "piggyback_labels": [
                                {"path": "region"},
                                {
                                    "key": "db",
                                    "value": "yes",
                                    "filter": {"path": "role", "op": "equals", "value": "db"},
                                },
                            ],
                        }
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["piggyback_labels"] == [
        {"path": "region", "key": None, "value": None, "filter": None},
        {
            "path": None,
            "key": "db",
            "value": "yes",
            "filter": {"path": "role", "op": "equals", "value": "db"},
        },
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


def test_oauth2_keeps_only_the_non_secret_half_in_the_blob(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "auth": (
                        "auth_oauth2",
                        {
                            "token_url": "https://idp/token",
                            "client_id": "monitoring",
                            "client_secret": Secret(0),
                            "scope": "api://monitoring/.default",
                            "client_auth": "post",
                        },
                    ),
                    "extractions": [],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["auth"] == "auth_oauth2"
    # A token URL, a client id and a scope are not credentials, so they ride in
    # the blob like every other setting.
    assert endpoint["oauth2"]["token_url"] == "https://idp/token"
    assert endpoint["oauth2"]["client_id"] == "monitoring"
    assert endpoint["oauth2"]["scope"] == "api://monitoring/.default"
    assert endpoint["oauth2"]["client_auth"] == "post"
    # The client secret is not in the blob - it travels as its own option, the
    # same way every other secret does.
    assert "client_secret" not in endpoint["oauth2"]
    assert "--secret_0-id" in args


def test_oauth2_token_url_resolves_macros(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "auth": (
                        "auth_oauth2",
                        {
                            "token_url": "https://$HOSTNAME$.idp/token",
                            "client_id": "c",
                            "client_secret": Secret(0),
                        },
                    ),
                    "extractions": [],
                }
            ]
        },
        host=_host({"$HOSTNAME$": "myhost"}),
    )
    (endpoint,) = _endpoints(ssc, args)
    # One shared rule can point at a per-host identity provider.
    assert endpoint["oauth2"]["token_url"] == "https://myhost.idp/token"


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


def test_inventory_spec_passed_through(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [
                        {
                            "path": "version",
                            "service": "Version",
                            "inventory": {
                                "node": "software.applications.json_api",
                                "keep_service": False,
                            },
                        }
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["extractions"][0]["inventory"] == {
        "node": "software.applications.json_api",
        "key": None,
        "keep_service": False,
    }


def test_service_prefix_rides_in_the_endpoint_blob(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://app1/health",
                    "name": "app1-health",
                    "service_prefix": True,
                    "extractions": [{"path": "status", "service": "Status"}],
                }
            ]
        },
    )
    (blob,) = _endpoints(ssc, args)
    assert blob["service_prefix"] is True
    assert blob["name"] == "app1-health"


def test_service_prefix_defaults_to_off_for_a_rule_that_predates_it(ssc):
    args = _command_args(ssc, {"endpoints": [{"url": "https://app1/health"}]})
    (blob,) = _endpoints(ssc, args)
    assert blob["service_prefix"] is False


def test_a_macro_in_the_endpoint_name_reaches_the_service_prefix(ssc):
    # The name becomes part of every service description, so it has to be the
    # resolved one - not '$HOSTNAME$'.
    args = _command_args(
        ssc,
        {"endpoints": [{"url": "https://x/health", "name": "$HOSTNAME$", "service_prefix": True}]},
        host=_host({"$HOSTNAME$": "app1"}),
    )
    (blob,) = _endpoints(ssc, args)
    assert (blob["name"], blob["service_prefix"]) == ("app1", True)


def test_report_raw_response_rides_in_the_endpoint_blob(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://app1/health",
                    "show_response": {"max_bytes": 4096, "headers": False},
                }
            ]
        },
    )
    (blob,) = _endpoints(ssc, args)
    assert blob["show_response"] == {"max_bytes": 4096, "headers": False}


def test_report_raw_response_is_absent_unless_configured(ssc):
    args = _command_args(ssc, {"endpoints": [{"url": "https://app1/health"}]})
    (blob,) = _endpoints(ssc, args)
    assert blob["show_response"] is None


def test_the_shared_service_rides_in_the_endpoint_blob(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://app1/health",
                    "extractions": [
                        {"path": "status", "service": "Status", "group": "Health"},
                        {"path": "component", "service": "Component", "group": "Health"},
                        {"path": "other", "service": "Other"},
                    ],
                }
            ]
        },
    )
    (blob,) = _endpoints(ssc, args)
    assert [(e["service"], e["group"]) for e in blob["extractions"]] == [
        ("Status", "Health"),
        ("Component", "Health"),
        ("Other", None),
    ]


def test_field_context_passed_through(ssc):
    """The endpoint blob carries the field-context settings for the agent."""
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "status", "service": "S"}],
                    "field_context": {"source": "element", "max_bytes": 512},
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["field_context"] == {"source": "element", "max_bytes": 512}


def test_field_context_absent_by_default(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "status", "service": "S"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["field_context"] is None


def test_pagination_passed_through(ssc):
    """The endpoint blob carries where the pages are; the agent owns the policy."""
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "items", "service": "Queue"}],
                    "pagination": {
                        "next": ("body", "links.next"),
                        "items": "items",
                        "max_pages": 5,
                        "max_elements": 500,
                    },
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    # A tuple becomes a list on the way through JSON, which is what the agent
    # reads it as.
    assert endpoint["pagination"] == {
        "next": ["body", "links.next"],
        "items": "items",
        "max_pages": 5,
        "max_elements": 500,
    }


def test_pagination_link_header_and_defaults(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "items", "service": "Queue"}],
                    "pagination": {"next": ("link_header", None), "items": "$"},
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["pagination"] == {
        "next": ["link_header", None],
        "items": "$",
        "max_pages": 10,
        "max_elements": None,
    }


def test_counted_pagination_passed_through(ssc):
    """The page-number and offset modes carry their settings, defaults filled."""
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "items", "service": "Queue"}],
                    "pagination": {
                        "next": (
                            "offset",
                            {"parameter": "offset", "page_size": 50, "size_parameter": "limit"},
                        ),
                        "items": "items",
                    },
                },
                {
                    "url": "http://y",
                    "verify_cert": True,
                    "extractions": [{"path": "items", "service": "Queue"}],
                    "pagination": {
                        "next": ("page_number", {"parameter": "page", "total": "meta.total"}),
                        "items": "items",
                    },
                },
            ]
        },
    )
    offset, page = _endpoints(ssc, args)
    assert offset["pagination"]["next"] == [
        "offset",
        {
            "parameter": "offset",
            "start": 1,
            "page_size": 50,
            "size_parameter": "limit",
            "total": None,
        },
    ]
    assert page["pagination"]["next"] == [
        "page_number",
        {
            "parameter": "page",
            "start": 1,
            "page_size": None,
            "size_parameter": None,
            "total": "meta.total",
        },
    ]


def test_pagination_absent_by_default(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "http://x",
                    "verify_cert": True,
                    "extractions": [{"path": "status", "service": "S"}],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)
    assert endpoint["pagination"] is None


def test_a_value_range_reaches_the_agent(ssc):
    """The range is configured in the rule and used by the check, so it has to
    survive the one hop between them that drops anything it does not model."""
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
                            "path": "battery",
                            "service": "Battery",
                            "unit": "percent",
                            "value_range": {"min": 0.0, "max": 100.0},
                        }
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)

    assert endpoint["extractions"][0]["value_range"] == {"min": 0.0, "max": 100.0}


def test_half_a_value_range_reaches_the_agent_as_half_a_range(ssc):
    args = _command_args(
        ssc,
        {
            "endpoints": [
                {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "verify_cert": True,
                    "extractions": [
                        {"path": "queue", "service": "Queue", "value_range": {"min": 0.0}}
                    ],
                }
            ]
        },
    )
    (endpoint,) = _endpoints(ssc, args)

    assert endpoint["extractions"][0]["value_range"] == {"min": 0.0, "max": None}
