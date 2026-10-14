# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""HTTP transport: the session, auth, TLS, capped reads, debug output and redaction."""

import ssl
import sys
from typing import TypedDict
from urllib.parse import quote, quote_plus

import requests

from .models import Endpoint, Proxy, ResponseReport


class RequestMeta(TypedDict, total=False):
    """What one endpoint's request did, as its own service reports it.

    Filled in on every exit path of a fetch (a failed request still took time)
    and serialized into the endpoint record and, minus the per-request facts,
    into the response cache.
    """

    status: int | None
    elapsed: float | None
    size: int | None
    final_url: str | None
    cert_expiry: float | None
    from_cache: bool
    cache_age: float | None
    attempts: int
    # The response headers, so an extraction can read one ('@header.<name>').
    headers: dict[str, str]
    pages: int
    elements: int | None
    pagination_stopped: str | None
    pagination_end: str | None
    # The raw response, for an endpoint that asked for it.
    body: str
    body_truncated: bool
    body_size: int | None
    headers_reported: dict[str, str]


# Cap the response body we buffer and parse, so a monitored endpoint returning
# a huge (or endless) body cannot exhaust memory on the Checkmk server.
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MiB


class _ResponseTooLarge(Exception):
    """The response body exceeded ``_MAX_RESPONSE_BYTES``."""


def _read_capped(response: requests.Response, limit: int) -> bytes:
    """Read the response body, refusing to buffer more than ``limit`` bytes."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > limit:
            raise _ResponseTooLarge(f"Response exceeds the {limit}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _read_reportable(response: requests.Response, limit: int) -> tuple[bytes, bool]:
    """Up to ``limit`` bytes of the body, and whether there was more after them.

    Unlike ``_read_capped`` an oversized body is not an error here: the report is
    capped by design, so this stops reading at the limit rather than buffering a
    50 MiB error page in order to show 2 KB of it. Used only where the body is
    wanted *for the report alone* - where it also has to be parsed, it has to be
    read whole anyway.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=8192):
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            break
    return b"".join(chunks)[:limit], total > limit


def _apply_proxy(session: requests.Session, endpoint: Endpoint) -> None:
    """Configure the session's HTTP proxy from the endpoint's 'proxy' spec.

    The server-side call resolves the rule's Proxy choice into one of:
      {"mode": "url", "url": "http://proxy:3128"} - route via this proxy
      {"mode": "no_proxy"}                        - ignore any environment proxy
      {"mode": "environment"} / absent            - honour HTTP(S)_PROXY from
                                                    the monitoring host's env
    An explicit proxy URL takes precedence over the environment; 'no_proxy'
    turns off requests' env-based proxy lookup for this request.
    """
    match endpoint.proxy:
        case None:
            return
        case Proxy(mode="url", url=str(url)):
            session.proxies = {"http": url, "https": url}
        case Proxy(mode="no_proxy"):
            session.trust_env = False


def _auth_header_name(endpoint: Endpoint) -> str | None:
    """The header an 'API key in a header' endpoint puts its key into."""
    if endpoint.auth != "auth_header":
        return None
    name = endpoint.auth_header
    return name if name is not None and name.strip() else "X-API-Key"


def _auth_query_name(endpoint: Endpoint) -> str | None:
    """The query parameter an 'API key in a query parameter' endpoint uses."""
    if endpoint.auth != "auth_query":
        return None
    name = endpoint.auth_query
    return name if name is not None and name.strip() else "api_key"


def _auth_params(endpoint: Endpoint, secret: str | None) -> dict[str, str] | None:
    """Query parameters carrying the API key, or ``None``.

    Kept out of ``_build_session`` (and out of the endpoint's URL) so the key is
    added by requests at send time only: the configured URL - the one that names
    the service and is printed in debug output - never contains it.
    """
    name = _auth_query_name(endpoint)
    return {name: secret or ""} if name else None


class _Session(requests.Session):
    """A session that also strips an API-key HEADER on a cross-host redirect.

    ``requests`` already does this for ``Authorization`` - that is what protects
    the bearer-token mode - but its ``rebuild_auth`` knows only that one header
    name. An API key lives in a header the *API* names, so without this a
    monitored endpoint that redirects to another host is handed the key in full,
    which is precisely what the password-store modes exist to prevent. Redirects
    are followed by default, so this must be the default too.

    A key in a query parameter needs no equivalent: the redirect's Location
    replaces the query string rather than carrying it along.
    """

    def __init__(self, secret_header: str | None = None) -> None:
        super().__init__()
        self._secret_header = secret_header

    def rebuild_auth(
        self, prepared_request: requests.PreparedRequest, response: requests.Response
    ) -> None:
        super().rebuild_auth(prepared_request, response)
        if not self._secret_header:
            return
        if self.should_strip_auth(response.request.url, prepared_request.url):
            # Headers are a case-insensitive mapping, so the configured spelling
            # need not match what was actually sent.
            prepared_request.headers.pop(self._secret_header, None)


def _build_session(
    endpoint: Endpoint, secret: str | None, access_token: str | None = None
) -> tuple[requests.Session, dict[str, str]]:
    session = _Session(_auth_header_name(endpoint))
    _apply_proxy(session, endpoint)
    headers = dict(endpoint.headers)
    match endpoint.auth:
        case "auth_login":
            session.auth = (endpoint.username or "", secret or "")
        case "auth_token":
            headers["Authorization"] = "Bearer " + (secret or "")
        case "auth_oauth2":
            # Already exchanged for a token by the caller; from here it is an
            # ordinary bearer token, so requests' own Authorization stripping on
            # a cross-host redirect protects it like any other.
            headers["Authorization"] = "Bearer " + (access_token or "")
        case "auth_header":
            # The API names the header ('X-API-Key', 'PRIVATE-TOKEN', ...); the
            # value comes from the password store, never from the config.
            headers[_auth_header_name(endpoint) or "X-API-Key"] = secret or ""
    if endpoint.effective_body is not None and not any(
        h.lower() == "content-type" for h in headers
    ):
        headers["Content-Type"] = "application/json"
    return session, headers


# Body preview length in --debug output: enough to see the shape of a response
# without dumping a whole (possibly huge) document to the terminal.
_DEBUG_BODY_PREVIEW = 4000


def _debug(enabled: bool, message: str) -> None:
    """Write one diagnostic line to stderr when --debug is set.

    Kept strictly on stderr so a --debug run stays parseable by Checkmk (the
    section still goes to stdout untouched) while a consultant watches the
    request/response detail on the terminal.
    """
    if enabled:
        sys.stderr.write(f"[json_api debug] {message}\n")


_REDACTED = "<redacted>"


def _redacted_headers(headers: dict[str, str], secret_header: str | None = None) -> dict[str, str]:
    """Headers with credential values masked, for safe debug output.

    Bearer tokens live in the Authorization header; basic-auth credentials live
    on ``session.auth`` (never in ``headers``), so they cannot leak here. An API
    key lives in a header the *API* names, which is why the name to mask has to
    be passed in - masking only 'Authorization' would print the key verbatim.
    """
    masked = {"authorization"}
    if secret_header:
        masked.add(secret_header.lower())
    return {
        name: (_REDACTED if name.lower() in masked else value) for name, value in headers.items()
    }


def _redact_secret(text: str, secret: str | None) -> str:
    """``text`` with every occurrence of ``secret`` masked.

    Used for the 'API key in a query parameter' mode, the one authentication
    style whose credential ends up inside a URL: it is then echoed back by
    ``response.url`` (reported as the endpoint's final URL) and quoted in the
    message of any ``requests`` exception. Both are printed to the terminal and
    stored in the agent output on disk, so the key is stripped out of them here.

    The percent-encoded forms are masked too, because a key containing reserved
    characters reaches the wire encoded.
    """
    if not secret:
        return text
    for form in (secret, quote(secret, safe=""), quote_plus(secret)):
        if form and form in text:
            text = text.replace(form, _REDACTED)
    return text


# Response headers that carry a credential of their own. 'Set-Cookie' is a live
# session token: reporting it in a service's details would put it into the
# monitoring history, and from there into anything that reads a service's output.
_SENSITIVE_RESPONSE_HEADERS = frozenset(
    {"set-cookie", "set-cookie2", "authorization", "proxy-authorization"}
)


def _reported_headers(headers: object, secret: str | None) -> dict[str, str]:
    """The response headers as reported: credential-bearing ones masked.

    The secret is stripped from every value as well - an API that echoes the key
    it was given (in a 'WWW-Authenticate' challenge, say) must not have it stored
    with the check result.
    """
    if not isinstance(headers, dict):
        return {}
    return {
        str(name): (
            _REDACTED
            if str(name).lower() in _SENSITIVE_RESPONSE_HEADERS
            else _redact_secret(str(value), secret)
        )
        for name, value in headers.items()
    }


def _store_report(
    endpoint: Endpoint, meta: RequestMeta, raw: bytes, secret: str | None, complete: bool = True
) -> None:
    """Put the reported body (and headers) for ``raw`` into ``meta``, if asked.

    ``complete`` says whether ``raw`` is the whole body. It is not when the body
    was read for the report alone and stopped at the limit, and then the real
    length is unknown - the service says the body was cut off without claiming
    a size it never measured.

    Both are capped and stripped of the secret HERE, in the only function that
    has the whole body, so the untruncated bytes never travel any further. A body
    cut at the limit can split a multi-byte character, which decodes to U+FFFD
    rather than failing the whole report.

    Not stored in the response cache (see _cache_write): the report is derived
    from the body, and the body IS cached, so it is rebuilt on a cache hit
    against the settings in force now rather than the ones a previous check ran
    with.
    """
    spec: ResponseReport | None = endpoint.show_response
    if spec is None:
        return
    limit = spec.max_bytes
    meta["body"] = _redact_secret(raw[:limit].decode("utf-8", "replace"), secret)
    meta["body_truncated"] = len(raw) > limit or not complete
    meta["body_size"] = len(raw) if complete else None
    if spec.headers:
        meta["headers_reported"] = _reported_headers(meta.get("headers"), secret)


def _verify_arg(endpoint: Endpoint) -> bool | str:
    """The ``verify`` value for requests: the configured flag, or a CA-bundle path.

    ``verify_cert`` is the operator's explicit on/off toggle (off is the
    documented, insecure opt-out; see the ruleset help). A custom CA bundle lets
    a private-CA endpoint be verified without turning verification off, so it
    only applies when verification is on. The disabled value is returned
    straight from the config rather than as a literal, so this stays a single
    source of truth for the toggle.
    """
    if endpoint.verify_cert and endpoint.ca_bundle:
        return endpoint.ca_bundle
    return endpoint.verify_cert


def _client_cert(endpoint: Endpoint) -> str | tuple[str, str] | None:
    """The ``cert`` value for requests (mutual TLS): None, certfile, or
    (certfile, keyfile) when the key lives in a separate file."""
    cert = endpoint.client_cert
    if cert is None:
        return None
    return (cert.cert, cert.key) if cert.key else cert.cert


def _peer_cert_expiry(response: requests.Response) -> float | None:
    """The peer certificate's ``notAfter`` as a Unix epoch, or ``None``.

    Read off the still-open socket: the request is made with ``stream=True`` (so
    ``_read_capped`` can bound the body), which means the connection is live at
    this point and no second handshake is needed.

    It does reach through ``requests`` into urllib3's pooled connection, which is
    not a public API, and there are ordinary reasons for it to come up empty - a
    reused connection from the pool may not expose the socket, ``verify=False``
    yields an empty cert dict, and plain HTTP has no certificate at all. So every
    failure degrades to ``None`` and the endpoint service simply reports no
    certificate, rather than the agent breaking over a best-effort extra.
    """
    try:
        connection = getattr(response.raw, "connection", None)
        sock = getattr(connection, "sock", None)
        cert = sock.getpeercert() if sock is not None else None
    except Exception:  # noqa: BLE001 - a best-effort extra must never fail a fetch
        return None
    if not isinstance(cert, dict):
        return None
    not_after = cert.get("notAfter")
    if not isinstance(not_after, str):
        return None
    try:
        return float(ssl.cert_time_to_seconds(not_after))
    except ValueError:
        return None


def _retryable_status(status: int) -> bool:
    """Whether repeating a request that answered ``status`` could help.

    A 5xx is the server saying it failed, and a 429 is it saying "later" - both
    can differ on the next attempt. Everything else (a 4xx, a blocked redirect)
    is a decision about the request itself and would answer exactly the same,
    so retrying it only burns the budget.
    """
    return status == 429 or 500 <= status < 600
