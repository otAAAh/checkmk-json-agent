# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The Explorer's server-side preview fetch (``gui/wato/json_explorer/fetch.py``).

The wizard's field picker shows the operator a real response, fetched from the
Checkmk server with the connection they just configured. Its whole value rests
on one promise: that this is the request the special agent will make. Where the
preview quietly drops part of the connection, the promise breaks in both
directions — a private-CA endpoint the agent verifies fine is rejected here, a
proxy-only endpoint looks dead, and an API key can travel somewhere the agent
would never have sent it.

So these tests are mostly about the CONNECTION, not the JSON: the redirect
guard, the TLS material, the proxy, and — at the bottom — a drift guard that
fails when the ruleset grows an endpoint field nobody decided about. That last
one is the point of the file: every gap fixed here existed because a field was
added to the ruleset and the preview was never told.

Loaded by path (``explorer_fetch`` fixture, see conftest); needs the ``cmk.gui``
APIs, so it runs in the same Checkmk Pythons as the rest of the suite.
"""

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from ruleset_ast import dictionary_keys

_FETCH = Path(__file__).resolve().parent.parent / "gui" / "wato" / "json_explorer" / "fetch.py"

# An explicit password as the Password FormSpec stores it on disk.
_SECRET = ("cmk_postprocessed", "explicit_password", ("", "s3cr3t"))


@dataclass
class _Received:
    path: str
    headers: dict[str, str]
    body: bytes


def _reply(handler, status, payload=None, headers=None):
    """Answer one request; every reply carries a Content-Length (keep-alive)."""
    body = json.dumps(payload).encode() if payload is not None else b""
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    if body:
        handler.wfile.write(body)


@pytest.fixture
def serve():
    """Start throwaway HTTP servers; yields ``(base_url, received_requests)``."""
    servers = []

    def _start(respond):
        received: list[_Received] = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
                self._handle()

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
                self._handle()

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                received.append(
                    _Received(
                        self.path,
                        {name.lower(): value for name, value in self.headers.items()},
                        self.rfile.read(length) if length else b"",
                    )
                )
                respond(received[-1], self)

            def log_message(self, *args):  # keep the test output clean
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", received

    yield _start

    for server in servers:
        server.shutdown()
        server.server_close()


# --- TLS material ----------------------------------------------------------


def test_a_ca_bundle_is_passed_to_requests_as_the_verify_argument(explorer_fetch):
    """A private CA must be usable in the preview, as it is in the agent —
    without it the only way to see the response is to switch verification off."""
    assert explorer_fetch._verify_arg({"verify_cert": True, "ca_bundle": "/etc/ssl/own.pem"}) == (
        "/etc/ssl/own.pem"
    )


def test_a_ca_bundle_is_ignored_while_verification_is_off(explorer_fetch):
    """Same rule as the agent's ``_verify_arg``: the bundle is how you verify
    against a private CA, not a second switch that turns verification back on."""
    assert explorer_fetch._verify_arg({"verify_cert": False, "ca_bundle": "/etc/ssl/own.pem"}) is (
        False
    )


def test_verification_is_on_when_the_connection_says_nothing(explorer_fetch):
    assert explorer_fetch._verify_arg({}) is True


@pytest.mark.parametrize(
    "client_cert, expected",
    [
        (None, None),
        ({}, None),
        ({"cert": "/etc/ssl/client.pem"}, "/etc/ssl/client.pem"),
        (
            {"cert": "/etc/ssl/client.pem", "key": "/etc/ssl/client.key"},
            ("/etc/ssl/client.pem", "/etc/ssl/client.key"),
        ),
    ],
)
def test_the_client_certificate_becomes_the_requests_cert_argument(
    explorer_fetch, client_cert, expected
):
    """Mutual TLS: a bundled key is one path, a separate key is a pair — the
    shapes ``requests`` accepts, and the ones the agent builds."""
    assert explorer_fetch._client_cert({"client_cert": client_cert}) == expected


# --- the redirect guard ----------------------------------------------------


def _api_key_connection(url, **extra):
    return {
        "url": url,
        "auth": ("auth_header", {"header": "X-API-Key", "key": _SECRET}),
        **extra,
    }


def test_an_api_key_header_is_stripped_on_a_cross_host_redirect(explorer_fetch, serve):
    """The bug this module's ``_RedirectSafeSession`` exists for.

    ``requests`` strips ``Authorization`` by itself, so basic/bearer/OAuth2 were
    never exposed — but an API key lives in a header the API names, which
    ``requests`` knows nothing about. An endpoint that redirects elsewhere would
    otherwise be handed the key in full, which is exactly what keeping it in the
    password store is meant to prevent.
    """
    target, at_target = serve(lambda request, handler: _reply(handler, 200, {"ok": True}))
    start, _ = serve(
        lambda request, handler: _reply(handler, 302, headers={"Location": f"{target}/moved"})
    )

    response = explorer_fetch._perform_request(_api_key_connection(start))

    assert response.status_code == 200
    assert at_target, "the redirect was not followed"
    assert "x-api-key" not in at_target[0].headers


def test_an_api_key_header_survives_a_same_host_redirect(explorer_fetch, serve):
    """The other half of the guard: a redirect inside the same host is the API
    routing its own request, and dropping the key there would break the preview
    for every endpoint that answers on a canonical path."""

    def respond(request, handler):
        if request.path == "/moved":
            _reply(handler, 200, {"ok": True})
        else:
            _reply(handler, 302, headers={"Location": f"{base}/moved"})

    base, received = serve(respond)

    response = explorer_fetch._perform_request(_api_key_connection(base))

    assert response.status_code == 200
    assert received[-1].path == "/moved"
    assert received[-1].headers["x-api-key"] == "s3cr3t"


# --- the proxy -------------------------------------------------------------


@pytest.mark.parametrize(
    "proxy, expected",
    [
        (None, None),
        (("cmk_postprocessed", "environment_proxy", ""), None),
        (
            ("cmk_postprocessed", "explicit_proxy", "http://proxy:3128"),
            {"http": "http://proxy:3128", "https": "http://proxy:3128"},
        ),
        (("cmk_postprocessed", "no_proxy", ""), {"http": "", "https": ""}),
    ],
)
def test_the_configured_proxy_reaches_requests(explorer_fetch, proxy, expected):
    """An endpoint reachable only through the corporate egress proxy has to be
    previewable. 'Environment' and an absent setting are requests' own default,
    so they add nothing to the session."""
    assert explorer_fetch._proxies({"proxy": proxy} if proxy is not None else {}) == expected


def test_the_session_carries_the_proxy_and_the_redirect_guard(explorer_fetch):
    session = explorer_fetch._session(
        _api_key_connection(
            "https://api.example/v1", proxy=("cmk_postprocessed", "explicit_proxy", "http://p:3128")
        )
    )
    assert session.proxies == {"http": "http://p:3128", "https": "http://p:3128"}
    assert isinstance(session, explorer_fetch._RedirectSafeSession)


# --- OAuth2 ----------------------------------------------------------------


def test_the_token_exchange_goes_through_the_endpoints_own_session(
    explorer_fetch, serve, monkeypatch
):
    """The IdP is reached from the same Checkmk server, behind the same proxy and
    under the same TLS settings as the API — the agent builds its token request
    that way, and a preview that talks to the IdP directly would fail (or
    succeed) where the check does the opposite."""
    sessions = []
    original = explorer_fetch._session

    def recording(conn):
        sessions.append(conn)
        return original(conn)

    monkeypatch.setattr(explorer_fetch, "_session", recording)

    idp, at_idp = serve(
        lambda request, handler: _reply(handler, 200, {"access_token": "tok", "expires_in": 60})
    )
    api, at_api = serve(lambda request, handler: _reply(handler, 200, {"ok": True}))

    connection = {
        "url": api,
        "auth": (
            "auth_oauth2",
            {
                "token_url": f"{idp}/token",
                "client_id": "monitoring",
                "client_secret": _SECRET,
                "client_auth": "post",
            },
        ),
    }
    response = explorer_fetch._perform_request(connection)

    assert response.status_code == 200
    assert b"grant_type=client_credentials" in at_idp[0].body
    assert at_api[0].headers["authorization"] == "Bearer tok"
    assert sessions == [connection, connection], "both requests must use the endpoint's session"


def test_a_failing_token_request_never_reports_the_client_secret(explorer_fetch, serve):
    """The provider echoes the request it rejected, and with 'post' credentials
    that request contains the secret — so only the status is reported."""
    idp, _ = serve(lambda request, handler: _reply(handler, 401, {"error": "invalid_client"}))

    with pytest.raises(explorer_fetch._TokenError) as caught:
        explorer_fetch._access_token(
            {
                "token_url": f"{idp}/token",
                "client_id": "monitoring",
                "client_secret": _SECRET,
                "client_auth": "post",
            },
            {},
        )

    assert "s3cr3t" not in str(caught.value)
    assert "401" in str(caught.value)


# --- the drift guard -------------------------------------------------------

# Every endpoint field of the ruleset, and what the preview does with it. A
# field is either part of the request the operator is previewing (and the
# preview must apply it), or it is not (and the reason is written down here).
# Adding a field to the ruleset without deciding which it is fails the test
# below - which is how the ca_bundle / client_cert / proxy gaps got in.
_APPLIED_TO_THE_REQUEST = {
    "url",
    "method",
    "body",
    "headers",
    "auth",
    "verify_cert",
    "ca_bundle",
    "client_cert",
    "follow_redirects",
    "timeout",
    "proxy",
}
_NOT_PART_OF_THE_REQUEST = {
    "name": "names the endpoint's services, not its request",
    "service_prefix": "names the endpoint's services, not its request",
    "extractions": "what to do with the response, not how to fetch it",
    "host_labels": "what to do with the response, not how to fetch it",
    "show_response": "shapes the check's service details",
    "field_context": "shapes the check's service output",
    "accept_status": "the preview reports the status it got and lets the "
    "operator judge it; failing the fetch would hide the response body that "
    "explains why the API said no",
    "cache_ttl": "the agent's cache belongs to the check; an operator pressing "
    "'Refresh data' is asking for a fresh response",
    "retry": "a person is waiting: report 'connection refused' now rather than half a minute later",
    "pagination": "the wizard fetches one page; the review step already says so, "
    "reading the setting off the rule it is building",
}


def test_every_endpoint_field_is_applied_by_the_preview_or_knowingly_skipped():
    """The guard that would have caught this whole PR a year earlier."""
    classified = _APPLIED_TO_THE_REQUEST | set(_NOT_PART_OF_THE_REQUEST)
    fields = dictionary_keys("_endpoint")

    assert not fields - classified, (
        "new endpoint field(s) in the ruleset that the preview has not been told "
        "about: either apply them in gui/wato/json_explorer/fetch.py and list "
        "them in _APPLIED_TO_THE_REQUEST, or record why they are not part of the "
        "request in _NOT_PART_OF_THE_REQUEST"
    )
    assert not classified - fields, "field(s) listed here no longer exist in the ruleset"


def test_the_preview_reads_every_field_it_claims_to_apply():
    """The list above is only worth something if it describes the code: each
    'applied' field must actually be read out of the connection somewhere in
    fetch.py. Reads the source rather than the module, so this guard also runs
    where the Explorer itself cannot be imported (2.4)."""
    source = _FETCH.read_text()
    missing = {field for field in _APPLIED_TO_THE_REQUEST if f'"{field}"' not in source}
    assert not missing, f"claimed as applied but never read: {sorted(missing)}"
