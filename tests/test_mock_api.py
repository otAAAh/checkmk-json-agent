# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The dev mock API (``dev/mock_api.py``), driven by the real special agent.

The mock exists so every connection setting of the rule can be tried against
something real. That only holds while each path still does what docs/mock-api.md
promises, so these run the AGENT against it: an API key that is accepted, a
redirect that drops it on the way to another host, a 503 the rule accepts, a
series that fails twice before it succeeds, a collection paged by counting, and
- where 'openssl' exists - HTTPS with a private CA, mutual TLS and the proxy.
"""

import contextlib
import importlib.util
import io
import json
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_MOCK = Path(__file__).resolve().parent.parent / "dev" / "mock_api.py"


@pytest.fixture(scope="module")
def mock():
    spec = importlib.util.spec_from_file_location("mock_api", _MOCK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def http(mock):
    server = _start(ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler))
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def tls(mock, tmp_path_factory):
    if shutil.which("openssl") is None:
        pytest.skip("the mock's HTTPS mode needs the 'openssl' command")
    files = mock.make_certificates(tmp_path_factory.mktemp("tls"), days=20)
    server = _start(mock.tls_server("127.0.0.1", 0, files))
    proxy = _start(ThreadingHTTPServer(("127.0.0.1", 0), mock.ProxyHandler))
    yield {
        "base": f"https://localhost:{server.server_address[1]}",
        "proxy": f"http://127.0.0.1:{proxy.server_address[1]}",
        **{name: str(path) for name, path in files.items()},
    }
    for running in (server, proxy):
        running.shutdown()
        running.server_close()


def _run(agent, endpoint, secret=None):
    """The agent's own section for one endpoint: (endpoint record, {service: value})."""
    argv = ["--endpoint", json.dumps(endpoint)] + (["--secret_0", secret] if secret else [])
    if secret and not agent._HAVE_PWSTORE_V1:
        pytest.skip("passing a secret directly needs the public password store API")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        agent.main(argv)
    lines = out.getvalue().splitlines()
    payload = json.loads(lines[lines.index("<<<json_api:sep(0)>>>") + 1])
    values = {r["service"]: r["value"] if r["found"] else None for r in payload["results"]}
    return payload["endpoints"][0], values


def _field(path, service="v", **extra):
    return {"path": path, "service": service, **extra}


def test_an_api_key_is_accepted_in_a_header_and_in_the_query(agent, http):
    _, by_header = _run(
        agent,
        {
            "url": f"{http}/apikey",
            "auth": "auth_header",
            "auth_header": "X-API-Key",
            "extractions": [_field("authorized_via")],
        },
        "dev-key",
    )
    _, by_query = _run(
        agent,
        {
            "url": f"{http}/apikey",
            "auth": "auth_query",
            "auth_query": "api_key",
            "extractions": [_field("authorized_via")],
        },
        "dev-key",
    )
    assert (by_header["v"], by_query["v"]) == ("header", "query")


def test_a_cross_host_redirect_drops_the_api_key(agent, http):
    record, _ = _run(
        agent,
        {
            "url": f"{http}/redirect?cross=1",
            "auth": "auth_header",
            "auth_header": "X-API-Key",
            "extractions": [_field("authorized_via")],
        },
        "dev-key",
    )
    assert record["status"] == 401
    assert record["final_url"].startswith("http://localhost:")


def test_an_accepted_503_is_read(agent, http):
    endpoint = {"url": f"{http}/maintenance", "extractions": [_field("status")]}
    refused, _ = _run(agent, endpoint)
    accepted, values = _run(agent, {**endpoint, "accept_status": [503]})
    assert refused["ok"] is False
    assert accepted["ok"] is True and values["v"] == "maintenance"


def test_the_retry_policy_outlasts_a_flaky_series(agent, http):
    record, values = _run(
        agent,
        {
            "url": f"{http}/flaky?id=pytest",
            "retry": {"attempts": 2, "backoff": 0.0},
            "extractions": [_field("attempt")],
        },
    )
    assert record["attempts"] == 3 and values["v"] == 3


@pytest.mark.parametrize(
    "counted",
    [
        {"parameter": "page", "page_size": 5, "size_parameter": "per_page"},
        {"parameter": "offset", "page_size": 5, "size_parameter": "limit", "total": "total"},
    ],
    ids=["page_number", "offset"],
)
def test_counted_pagination_reads_every_item(agent, http, counted):
    mode = "page_number" if counted["parameter"] == "page" else "offset"
    record, values = _run(
        agent,
        {
            "url": f"{http}/items",
            "pagination": {"next": [mode, counted], "items": "items"},
            "extractions": [_field("items", aggregate="count")],
        },
    )
    assert values["v"] == 23 and record["pages"] == 5


def test_https_with_the_mock_ca_reports_the_certificate(agent, tls):
    record, values = _run(
        agent,
        {
            "url": f"{tls['base']}/health",
            "ca_bundle": tls["ca.pem"],
            "extractions": [_field("status")],
        },
    )
    assert values["v"] == "ok"
    assert record["cert_expiry"] is not None


def test_mutual_tls_presents_the_client_certificate(agent, tls):
    endpoint = {
        "url": f"{tls['base']}/mtls",
        "ca_bundle": tls["ca.pem"],
        "extractions": [_field("client")],
    }
    without, _ = _run(agent, endpoint)
    _, values = _run(
        agent, {**endpoint, "client_cert": {"cert": tls["client.pem"], "key": tls["client.key"]}}
    )
    assert without["status"] == 403
    assert values["v"] == "json_api mock client"


def test_the_proxy_forwards_http_and_tunnels_https(agent, http, tls):
    proxy = {"mode": "url", "url": tls["proxy"]}
    _, plain = _run(
        agent,
        {"url": f"{http}/proxied", "proxy": proxy, "extractions": [_field("through_proxy")]},
    )
    _, tunnelled = _run(
        agent,
        {
            "url": f"{tls['base']}/health",
            "ca_bundle": tls["ca.pem"],
            "proxy": proxy,
            "extractions": [_field("status")],
        },
    )
    assert plain["v"] is True and tunnelled["v"] == "ok"
