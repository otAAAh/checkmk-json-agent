# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""AJAX helper that fetches an endpoint URL server-side and returns its JSON.

The JSON field picker needs the endpoint's response to build its tree, but the
browser can't fetch operator endpoints (CORS, and they're typically only
reachable from the Checkmk server). So we fetch it here — the same place the
special agent does — using the SAME connection the wizard configured: method,
body, request headers, TLS (verification, a custom CA bundle, a client
certificate), the HTTP proxy, and authentication (basic / bearer / an API key in
a header or query parameter / OAuth2, all resolved from the password store).
That way the preview matches what the agent will actually see (authenticated,
private-CA, mutual-TLS and proxy-only endpoints all work in the wizard).

The connection helpers below mirror the agent's, one for one — ``_verify_arg``,
``_client_cert``, ``_proxies`` (the agent's ``_apply_proxy``) and the cross-host
header stripping in ``_RedirectSafeSession`` (its ``_Session``). They
are re-implemented rather than imported because the agent ships in the *other*
MKP (and is a stdlib-only executable, not an importable module), so the two must
be kept in step by hand; ``tests/test_explorer_gui.py`` is where that is pinned
down.

Input (POST): ``connection`` = the connection FormEdit value (JSON); falls back
to a bare ``url`` query var. Output: ``{ok, status, json, headers}``, or an
error the ``AjaxPage`` envelope reports (``result_code`` 1). The headers are
returned so the field picker can offer them: the agent can monitor one with an
'@header.' path (an API quota, a Last-Modified age), and the browser cannot see
them for itself because this request is made server-side.

Two endpoint settings are deliberately NOT applied here, because a preview with
a person waiting is not the same job as an unattended check:

* RETRY — reporting "connection refused" in one second beats retrying for half a
  minute before saying the same thing. Resilience is the agent's job.
* PAGINATION — the agent may walk up to a hundred pages and merge them; the
  wizard fetches one page. It does not hide that: the review step reads the
  endpoint's pagination setting straight off the rule being built and says the
  counts describe the first page, so no flag has to travel back from here.
  That one page is still the agent's first page, though: where the agent counts
  the pages itself (a page number or an offset), it asks for the first one with
  the counting parameters set, and so does ``_first_page_url`` here - otherwise
  the preview would show the API's default page size, not the one the rule
  asks for.

SECURITY: this performs an HTTP request from the Checkmk server to an
operator-supplied URL — an SSRF vector, exactly like the agent itself. It is
gated on Setup access (``wato.use``) so read-only monitoring users can't use it
as an open proxy, and only reveals a configured password to reach the very
endpoint the operator just entered.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, quote_plus, unquote_plus, urlsplit, urlunsplit

import requests
from cmk.ccc.exceptions import MKGeneralException
from cmk.gui.exceptions import MKUserError
from cmk.gui.http import request
from cmk.gui.i18n import _
from cmk.gui.logged_in import user
from cmk.gui.pages import AjaxPage, PageContext, PageResult

from .guards import check_csrf, usable_password_ids

_TIMEOUT = 10

# The most of a response the preview reads - the agent's own limit
# (_MAX_RESPONSE_BYTES), so the preview refuses exactly what the agent would. A
# GUI request worker must not buffer an unbounded body.
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MiB

# --- the connection, read once ------------------------------------------------
#
# The visitor hands the connection over as the ruleset's disk value: nested dicts
# and tuples. Rather than have every helper below re-check the part it reads,
# it is read once, here, into frozen values - the split the agent makes in its
# own ``models.py`` (which cannot be imported: it ships in the other MKP).
# Lenient on purpose, with exactly the rules the helpers used to apply: a part
# that is absent or unusable falls back to what the request does without it.
# Secrets stay as the Password FormSpec stored them and are revealed only when
# the request is made, so reading a connection never touches the password store.


@dataclass(frozen=True)
class _ClientCert:
    """Mutual TLS: the certificate file and, when separate, its key file."""

    cert: str
    key: str | None


@dataclass(frozen=True)
class _OAuth2:
    """The client-credentials grant: what to ask the identity provider for."""

    token_url: str
    client_id: str
    # The Password FormSpec's disk value, revealed by _resolve_password.
    client_secret: object
    scope: str | None
    audience: str | None
    # 'post' sends the client credentials in the request body; anything else in
    # an HTTP basic header.
    client_auth: str | None


@dataclass(frozen=True)
class _CountedPages:
    """Page-number / offset pagination, as far as the FIRST page needs it."""

    parameter: str
    start: int
    # Set on the first request only when both are given (a page size alone is
    # only the agent's short-page test).
    page_size: int | None
    size_parameter: str | None


@dataclass(frozen=True)
class _Connection:
    """One endpoint's request, as the preview makes it."""

    url: str
    method: str = "GET"
    # Only ever set for a POST.
    body: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    # 'auth_login' / 'auth_token' / 'auth_header' / 'auth_query' / 'auth_oauth2',
    # or None.
    auth: str | None = None
    username: str = ""
    # The Password FormSpec's disk value of the password, token or API key.
    secret: object = None
    # The NAMES an API key goes into, as written (the defaults apply at use).
    auth_header: str = ""
    auth_query: str = ""
    # Set exactly when ``auth`` is 'auth_oauth2'.
    oauth2: _OAuth2 | None = None
    verify_cert: bool = True
    ca_bundle: str | None = None
    client_cert: _ClientCert | None = None
    follow_redirects: bool = True
    timeout: float | None = None
    # The Proxy FormSpec's disk value, handed to Checkmk's own helper as stored:
    # its shape is Checkmk's to define. None = honour the environment.
    proxy: object = None
    # Only where the agent counts the pages itself; see _first_page_url.
    counted: _CountedPages | None = None


_AUTH_MODES = ("auth_login", "auth_token", "auth_header", "auth_query", "auth_oauth2")
# Where a mode keeps its secret, when not under 'key' (OAuth2 keeps its own).
_SECRET_KEYS = {"auth_login": "password", "auth_token": "token"}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _counted_pages(pagination: object) -> _CountedPages | None:
    """The counting parameters of a page-number / offset pagination, or None.

    Mirrors the agent's ``_counted_source`` defaults - a page number starts at
    1, an offset at 0.
    """
    source = pagination.get("next") if isinstance(pagination, dict) else None
    if not (isinstance(source, (list, tuple)) and len(source) == 2):
        return None
    mode, value = source
    if mode not in ("page_number", "offset") or not isinstance(value, dict):
        return None
    parameter = value.get("parameter")
    if not (isinstance(parameter, str) and parameter.strip()):
        return None
    start = value.get("start")
    if not (isinstance(start, int) and not isinstance(start, bool) and start >= 0):
        start = 1 if mode == "page_number" else 0
    size = value.get("page_size")
    size_parameter = value.get("size_parameter")
    return _CountedPages(
        parameter=parameter.strip(),
        start=start,
        page_size=size
        if isinstance(size, int) and not isinstance(size, bool) and size > 0
        else None,
        size_parameter=(
            size_parameter.strip()
            if isinstance(size_parameter, str) and size_parameter.strip()
            else None
        ),
    )


def _parse_connection(value: Mapping[str, object]) -> _Connection:
    """The connection disk value (or the legacy bare ``{url}``) as a _Connection."""
    raw_auth = value.get("auth")
    auth: str | None = None
    params: Mapping[str, object] = {}
    if isinstance(raw_auth, (list, tuple)) and len(raw_auth) == 2 and raw_auth[0] in _AUTH_MODES:
        auth = raw_auth[0]
        params = raw_auth[1] if isinstance(raw_auth[1], dict) else {}

    oauth2 = None
    if auth == "auth_oauth2":
        scope = params.get("scope")
        audience = params.get("audience")
        client_auth = params.get("client_auth")
        oauth2 = _OAuth2(
            token_url=_text(params.get("token_url")),
            client_id=_text(params.get("client_id")),
            client_secret=params.get("client_secret"),
            scope=str(scope) if scope else None,
            audience=str(audience) if audience else None,
            client_auth=client_auth if isinstance(client_auth, str) else None,
        )

    cert = value.get("client_cert")
    client_cert = None
    if isinstance(cert, dict) and cert.get("cert"):
        key = cert.get("key")
        client_cert = _ClientCert(cert=str(cert["cert"]), key=str(key) if key else None)

    method = str(value.get("method", "GET")).upper()
    body = value.get("body")
    headers = value.get("headers")
    ca_bundle = value.get("ca_bundle")
    timeout = value.get("timeout")
    return _Connection(
        url=str(value.get("url", "")),
        method=method,
        body=body if method == "POST" and isinstance(body, str) else None,
        headers=tuple(
            (h["name"], h["value"])
            for h in (headers if isinstance(headers, (list, tuple)) else ())
            if isinstance(h, dict)
        ),
        auth=auth,
        username=_text(params.get("username")),
        secret=params.get(_SECRET_KEYS.get(auth or "", "key")),
        auth_header=_text(params.get("header")),
        auth_query=_text(params.get("parameter")),
        oauth2=oauth2,
        verify_cert=bool(value.get("verify_cert", True)),
        ca_bundle=str(ca_bundle) if ca_bundle else None,
        client_cert=client_cert,
        follow_redirects=bool(value.get("follow_redirects", True)),
        timeout=(
            timeout if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) else None
        ),
        proxy=value.get("proxy"),
        counted=_counted_pages(value.get("pagination")),
    )


def _resolve_password(value: object) -> str:
    """Reveal a Password FormSpec disk value: ('cmk_postprocessed', kind, (id, pw)).

    A stored password is revealed only when the user may use that entry. The id
    comes from the browser, and the secret goes to a URL the browser chose, so
    without the check any Setup user could read every password in the store by
    pointing the preview at a server of their own.
    """
    if not (isinstance(value, (list, tuple)) and len(value) == 3):
        return ""
    _tag, kind, ref = value
    pid = ref[0] if isinstance(ref, (list, tuple)) and ref else ""
    plain = ref[1] if isinstance(ref, (list, tuple)) and len(ref) > 1 else ""
    if kind == "explicit_password":
        return plain or ""
    if kind == "stored_password":
        if pid not in usable_password_ids():
            raise _PreviewError(
                _("The stored password '%s' does not exist or you may not use it") % pid
            )
        try:
            from cmk.utils import password_store

            secret: str = password_store.lookup(password_store.password_store_path(), pid)
            return secret
        except Exception as exc:
            # Never fall back to an empty secret: the request would go out
            # unauthenticated and the preview would show the API's rejection
            # as if it were the endpoint's answer.
            raise _PreviewError(
                _("Cannot read the stored password '%s' (%s)") % (pid, type(exc).__name__)
            ) from exc
    return ""


def _connection() -> _Connection:
    """The connection, read from its validated disk value (SingleChoice/Password
    fields are hashed on the wire — only the visitor yields real values), or a
    bare {url} for the legacy ``?url=`` form."""
    raw = request.get_str_input("connection")
    if not raw:
        return _parse_connection({"url": request.get_str_input_mandatory("url").strip()})

    from cmk.gui.form_specs import (
        RawFrontendData,
        parse_and_validate_frontend_data,
        validate_value_from_frontend,
    )

    from .page import connection_form_spec

    try:
        data = RawFrontendData(json.loads(raw))
    except ValueError as exc:
        raise MKUserError("connection", _("The connection is not valid JSON: %s") % exc) from exc
    # A connection the form rejects is reported with the fields that are wrong,
    # not replaced by a bare-URL fetch that would then fail for another reason.
    spec = connection_form_spec()
    if problems := validate_value_from_frontend(spec, data):
        raise MKUserError(
            "connection",
            "; ".join(
                f"{' / '.join(str(part) for part in msg.location)}: {msg.message}"
                for msg in problems
            ),
        )
    value = parse_and_validate_frontend_data(spec, data)
    if not isinstance(value, dict):
        raise MKUserError("connection", _("The connection is not a dictionary"))
    return _parse_connection(value)


class _PreviewError(Exception):
    """A configured connection the preview cannot carry out.

    Reported as the fetch error rather than shrugged off: a preview that
    silently drops part of the connection is exactly the failure this module
    exists to avoid — it would show the operator a response the agent will never
    see (or hide one it will).
    """


class _TokenError(_PreviewError):
    """The OAuth2 token could not be obtained; reported as the fetch error."""


def _auth_header_name(conn: _Connection) -> str | None:
    """The header an 'API key in a header' connection puts its key into."""
    if conn.auth != "auth_header":
        return None
    return conn.auth_header if conn.auth_header.strip() else "X-API-Key"


class _RedirectSafeSession(requests.Session):
    """A session that also strips an API-key HEADER on a cross-host redirect.

    ``requests`` already does this for ``Authorization``, which covers the basic,
    bearer and OAuth2 modes — but its ``rebuild_auth`` knows only that one header
    name. An API key lives in a header the *API* names, so without this a
    previewed endpoint that redirects to another host is handed the key in full,
    which is precisely what the password-store modes exist to prevent. The
    preview follows redirects by default (like the agent), so this must be the
    default too.

    The agent's ``_Session`` does the same thing for the same reason; this is its
    counterpart on the GUI side.

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


def _verify_arg(conn: _Connection) -> bool | str:
    """The ``verify`` value for requests: the flag, or a CA-bundle path.

    A custom CA bundle lets a private-CA endpoint be verified without turning
    verification off, so it only applies while verification is on — same rule as
    the agent, or the preview would reject a certificate the agent accepts.
    """
    if conn.verify_cert and conn.ca_bundle:
        return conn.ca_bundle
    return conn.verify_cert


def _client_cert(conn: _Connection) -> str | tuple[str, str] | None:
    """The ``cert`` value for requests (mutual TLS): None, certfile, or
    (certfile, keyfile) when the key lives in a separate file."""
    cert = conn.client_cert
    if cert is None:
        return None
    return (cert.cert, cert.key) if cert.key else cert.cert


def _proxies(conn: _Connection) -> dict[str, str] | None:
    """The ``requests`` proxy mapping for the endpoint's 'HTTP proxy' choice.

    The stored value is the ``Proxy`` FormSpec's
    ``("cmk_postprocessed", <kind>, <value>)`` triple, where ``stored_proxy``
    only *names* one of the site's global proxies. Resolving that is what
    Checkmk's own helper does for the agent's command line, so the preview asks
    the same helper instead of re-deriving it. ``None`` means "honour the
    environment", which is requests' own default.
    """
    proxy = conn.proxy
    if proxy is None:
        return None
    try:
        from cmk.gui.watolib.config_domains import ConfigDomainCore
        from cmk.utils.http_proxy_config import http_proxy_config_from_user_setting
    except ImportError as exc:  # internal API moved — say so, never fetch direct
        raise _PreviewError(
            _("Cannot resolve the configured HTTP proxy on this Checkmk version (%s)") % exc
        ) from exc
    # The site's global settings are only read when the value actually names one
    # of them: an explicit URL, 'no proxy' and 'environment' are self-contained,
    # and a preview should not load the configuration to answer them.
    kind = proxy[1] if isinstance(proxy, (list, tuple)) and len(proxy) == 3 else None
    global_proxies = (
        ConfigDomainCore().load().get("http_proxies", {}) if kind == "stored_proxy" else {}
    )
    # Named for its type: what Checkmk's helper returns for ``requests``.
    proxies: dict[str, str] | None = http_proxy_config_from_user_setting(
        proxy, global_proxies
    ).to_requests_proxies()
    return proxies


def _session(conn: _Connection) -> requests.Session:
    """A session carrying this endpoint's proxy and redirect handling."""
    session = _RedirectSafeSession(_auth_header_name(conn))
    if (proxies := _proxies(conn)) is not None:
        session.proxies = proxies
    return session


def _access_token(oauth2: _OAuth2, conn: _Connection) -> str:
    """Exchange the client credentials for an access token.

    Unlike the agent this does NOT cache: the wizard fetch is a one-off preview
    with a person waiting, and a cache would only add a way for the preview to
    disagree with what the agent will do. The agent owns the caching.
    """
    secret = _resolve_password(oauth2.client_secret)
    data = {"grant_type": "client_credentials"}
    if oauth2.scope:
        data["scope"] = oauth2.scope
    if oauth2.audience:
        data["audience"] = oauth2.audience

    session = _session(conn)
    if oauth2.client_auth == "post":
        data["client_id"] = oauth2.client_id
        data["client_secret"] = secret
    else:
        session.auth = (oauth2.client_id, secret)

    try:
        response = session.post(
            oauth2.token_url,
            data=data,
            timeout=conn.timeout or _TIMEOUT,
            # The token endpoint is part of the trust chain: verification
            # follows the endpoint's own TLS settings rather than being relaxed
            # here, and an mTLS-protected IdP needs the client certificate too.
            verify=_verify_arg(conn),
            cert=_client_cert(conn),
        )
    except requests.RequestException as exc:
        # Only the exception TYPE and the token URL, never the message: requests
        # can quote the request it was making, and with the credentials sent in
        # the body that request contains the client secret. The agent's own
        # token exchange is careful about this for the same reason - and
        # _redacted() below would not have caught it, since it only knows about
        # an API key placed in a query parameter.
        raise _TokenError(
            _("Token request to %s failed (%s)") % (oauth2.token_url, type(exc).__name__)
        ) from exc
    if not 200 <= response.status_code < 300:
        raise _TokenError(
            _(
                "Token request returned HTTP %d — check the client credentials, the "
                "scope, and how the client credentials are sent"
            )
            % response.status_code
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise _TokenError(_("Token response is not valid JSON")) from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise _TokenError(_("Token response carries no 'access_token'"))
    return token


def _with_query(url: str, values: dict[str, str]) -> str:
    """``url`` with each of ``values`` set as a query parameter, replacing any.

    The agent's ``_with_query``: built on the raw query string, so every
    parameter the rule wrote that is not being set goes out byte for byte.
    """
    split = urlsplit(url)
    kept = [
        part
        for part in split.query.split("&")
        if part and unquote_plus(part.split("=", 1)[0]) not in values
    ]
    kept += [f"{quote_plus(name)}={quote_plus(value)}" for name, value in values.items()]
    return urlunsplit(split._replace(query="&".join(kept)))


def _first_page_url(conn: _Connection) -> str:
    """The URL the agent's first request goes to (its ``_first_page_url``).

    The rule's own URL, except where the agent counts the pages: then the first
    page is asked for with the position and, when both are set, the page size.
    """
    counted = conn.counted
    if counted is None:
        return conn.url
    params = {counted.parameter: str(counted.start)}
    if counted.page_size is not None and counted.size_parameter is not None:
        params[counted.size_parameter] = str(counted.page_size)
    return _with_query(conn.url, params)


def _perform_request(conn: _Connection) -> requests.Response:
    session = _session(conn)
    headers = dict(conn.headers)

    query: dict[str, str] | None = None
    if conn.auth == "auth_login":
        session.auth = (conn.username, _resolve_password(conn.secret))
    elif conn.auth == "auth_token":
        headers["Authorization"] = "Bearer " + _resolve_password(conn.secret)
    elif conn.auth == "auth_header":
        # The same name the redirect guard strips, as in the agent: a blank one
        # falls back to 'X-API-Key' in both places, or the guard would watch a
        # header the request never sent.
        headers[_auth_header_name(conn) or "X-API-Key"] = _resolve_password(conn.secret)
    elif conn.auth == "auth_query":
        query = {conn.auth_query or "api_key": _resolve_password(conn.secret)}
    elif conn.oauth2 is not None:
        headers["Authorization"] = "Bearer " + _access_token(conn.oauth2, conn)

    if conn.body is not None and not any(h.lower() == "content-type" for h in headers):
        headers["Content-Type"] = "application/json"

    return session.request(
        conn.method,
        _first_page_url(conn),
        params=query,
        data=conn.body,
        headers=headers,
        timeout=conn.timeout or _TIMEOUT,
        verify=_verify_arg(conn),
        cert=_client_cert(conn),
        allow_redirects=conn.follow_redirects,
        stream=True,  # so _read_capped can bound the buffered body
    )


def _read_capped(response: requests.Response) -> bytes:
    """The response body, refusing to buffer more than ``_MAX_RESPONSE_BYTES``."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            response.close()
            raise _PreviewError(
                _("The response is larger than %d bytes, which is more than the agent accepts")
                % _MAX_RESPONSE_BYTES
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _redacted(text: str, conn: _Connection) -> str:
    """``text`` with an API key configured as a query parameter masked out."""
    if conn.auth != "auth_query":
        return text
    try:
        secret = _resolve_password(conn.secret)
    except _PreviewError:  # the request resolved it first; nothing to mask otherwise
        return text
    for form in (secret, quote(secret, safe=""), quote_plus(secret)):
        if form:
            text = text.replace(form, "<redacted>")
    return text


class JsonExplorerFetchPage(AjaxPage):
    def page(self, ctx: PageContext) -> PageResult:
        # Gate the SSRF surface to the users the wizard is for: Setup users who
        # may edit rules, which is what the wizard ends in.
        user.need_permission("wato.use")
        user.need_permission("wato.rulesets")
        check_csrf(ctx)

        conn = _connection()
        url = conn.url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise MKUserError("url", _("URL must start with http:// or https://"))

        try:
            resp = _perform_request(conn)
            body = _read_capped(resp)
        except _PreviewError as exc:
            # Reported on its own: "the token endpoint said no" (or "this proxy
            # cannot be resolved") is a different problem from "the API said
            # no", and conflating them sends the operator to the wrong URL.
            raise MKGeneralException(str(exc)) from exc
        except requests.RequestException as exc:
            # The message of a connection error quotes the URL it tried to
            # reach - including an API key placed in a query parameter.
            raise MKGeneralException(_("Request failed: %s") % _redacted(str(exc), conn)) from exc

        try:
            data: object = json.loads(body)
        except ValueError as exc:
            raise MKGeneralException(
                _("HTTP %d: response body is not JSON") % resp.status_code
            ) from exc
        return {
            "ok": True,
            "status": resp.status_code,
            "json": data,
            # A plain dict: requests' CaseInsensitiveDict does not survive the
            # JSON round trip, and the picker matches case-insensitively anyway.
            "headers": dict(resp.headers),
        }
