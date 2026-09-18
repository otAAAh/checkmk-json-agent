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
to a bare ``url`` query var. Output: ``{ok, status, json, headers}`` or
``{ok:false, error}``. The headers are returned so the field picker can offer
them: the agent can monitor one with an '@header.' path (an API quota, a
Last-Modified age), and the browser cannot see them for itself because this
request is made server-side.

Two endpoint settings are deliberately NOT applied here, because a preview with
a person waiting is not the same job as an unattended check:

* RETRY — reporting "connection refused" in one second beats retrying for half a
  minute before saying the same thing. Resilience is the agent's job.
* PAGINATION — the agent may walk up to a hundred pages and merge them; the
  wizard fetches one page. It does not hide that: the review step reads the
  endpoint's pagination setting straight off the rule being built and says the
  counts describe the first page, so no flag has to travel back from here.

SECURITY: this performs an HTTP request from the Checkmk server to an
operator-supplied URL — an SSRF vector, exactly like the agent itself. It is
gated on Setup access (``wato.use``) so read-only monitoring users can't use it
as an open proxy, and only reveals a configured password to reach the very
endpoint the operator just entered.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, quote_plus

import requests
from cmk.gui.http import request
from cmk.gui.i18n import _
from cmk.gui.logged_in import user
from cmk.gui.pages import AjaxPage, PageContext, PageEndpoint, PageResult, page_registry

_TIMEOUT = 10


def _resolve_password(value: object) -> str:
    """Reveal a Password FormSpec disk value: ('cmk_postprocessed', kind, (id, pw))."""
    if not (isinstance(value, (list, tuple)) and len(value) == 3):
        return ""
    _tag, kind, ref = value
    pid = ref[0] if isinstance(ref, (list, tuple)) and ref else ""
    plain = ref[1] if isinstance(ref, (list, tuple)) and len(ref) > 1 else ""
    if kind == "explicit_password":
        return plain or ""
    if kind == "stored_password":
        try:
            from cmk.utils import password_store

            return password_store.lookup(password_store.password_store_path(), pid)
        except Exception:
            return ""
    return ""


def _connection() -> dict[str, Any]:
    """The connection as validated disk values (SingleChoice/Password fields are
    hashed on the wire — only the visitor yields real values). Falls back to a
    bare {url} for the legacy ``?url=`` form or when conversion fails."""
    raw = request.get_str_input("connection")
    if raw:
        try:
            from cmk.gui.form_specs import RawFrontendData
            from cmk.gui.form_specs._utils import parse_and_validate_frontend_data
            from cmk.gui.plugins.wato.json_explorer.page import connection_form_spec

            value = parse_and_validate_frontend_data(
                connection_form_spec(), RawFrontendData(json.loads(raw))
            )
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    return {"url": request.get_str_input_mandatory("url").strip()}


class _PreviewError(Exception):
    """A configured connection the preview cannot carry out.

    Reported as the fetch error rather than shrugged off: a preview that
    silently drops part of the connection is exactly the failure this module
    exists to avoid — it would show the operator a response the agent will never
    see (or hide one it will).
    """


class _TokenError(_PreviewError):
    """The OAuth2 token could not be obtained; reported as the fetch error."""


def _auth_header_name(conn: dict[str, Any]) -> str | None:
    """The header an 'API key in a header' connection puts its key into."""
    auth = conn.get("auth")
    if not (isinstance(auth, (list, tuple)) and len(auth) == 2 and auth[0] == "auth_header"):
        return None
    params = auth[1] if isinstance(auth[1], dict) else {}
    name = params.get("header")
    return name if isinstance(name, str) and name.strip() else "X-API-Key"


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

    def rebuild_auth(self, prepared_request: Any, response: Any) -> None:
        super().rebuild_auth(prepared_request, response)
        if not self._secret_header:
            return
        if self.should_strip_auth(response.request.url, prepared_request.url):
            # Headers are a case-insensitive mapping, so the configured spelling
            # need not match what was actually sent.
            prepared_request.headers.pop(self._secret_header, None)


def _verify_arg(conn: dict[str, Any]) -> bool | str:
    """The ``verify`` value for requests: the flag, or a CA-bundle path.

    A custom CA bundle lets a private-CA endpoint be verified without turning
    verification off, so it only applies while verification is on — same rule as
    the agent, or the preview would reject a certificate the agent accepts.
    """
    verify = conn.get("verify_cert", True)
    ca_bundle = conn.get("ca_bundle")
    if verify and ca_bundle:
        return str(ca_bundle)
    return bool(verify)


def _client_cert(conn: dict[str, Any]) -> str | tuple[str, str] | None:
    """The ``cert`` value for requests (mutual TLS): None, certfile, or
    (certfile, keyfile) when the key lives in a separate file."""
    cert = conn.get("client_cert")
    if not isinstance(cert, dict) or not cert.get("cert"):
        return None
    key = cert.get("key")
    return (str(cert["cert"]), str(key)) if key else str(cert["cert"])


def _proxies(conn: dict[str, Any]) -> dict[str, str] | None:
    """The ``requests`` proxy mapping for the endpoint's 'HTTP proxy' choice.

    The stored value is the ``Proxy`` FormSpec's
    ``("cmk_postprocessed", <kind>, <value>)`` triple, where ``stored_proxy``
    only *names* one of the site's global proxies. Resolving that is what
    Checkmk's own helper does for the agent's command line, so the preview asks
    the same helper instead of re-deriving it. ``None`` means "honour the
    environment", which is requests' own default.
    """
    proxy = conn.get("proxy")
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
    return http_proxy_config_from_user_setting(proxy, global_proxies).to_requests_proxies()


def _session(conn: dict[str, Any]) -> requests.Session:
    """A session carrying this endpoint's proxy and redirect handling."""
    session = _RedirectSafeSession(_auth_header_name(conn))
    if (proxies := _proxies(conn)) is not None:
        session.proxies = proxies
    return session


def _access_token(params: dict[str, Any], conn: dict[str, Any]) -> str:
    """Exchange the client credentials for an access token.

    Unlike the agent this does NOT cache: the wizard fetch is a one-off preview
    with a person waiting, and a cache would only add a way for the preview to
    disagree with what the agent will do. The agent owns the caching.
    """
    secret = _resolve_password(params.get("client_secret"))
    data = {"grant_type": "client_credentials"}
    if scope := params.get("scope"):
        data["scope"] = str(scope)
    if audience := params.get("audience"):
        data["audience"] = str(audience)

    session = _session(conn)
    if params.get("client_auth") == "post":
        data["client_id"] = str(params.get("client_id", ""))
        data["client_secret"] = secret
    else:
        session.auth = (str(params.get("client_id", "")), secret)

    try:
        response = session.post(
            params["token_url"],
            data=data,
            timeout=conn.get("timeout") or _TIMEOUT,
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
            _("Token request to %s failed (%s)") % (params["token_url"], type(exc).__name__)
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


def _perform_request(conn: dict[str, Any]) -> requests.Response:
    session = _session(conn)
    headers = {h["name"]: h["value"] for h in conn.get("headers", []) if isinstance(h, dict)}

    auth = conn.get("auth")
    query: dict[str, str] | None = None
    if isinstance(auth, (list, tuple)) and len(auth) == 2:
        kind, params = auth
        params = params if isinstance(params, dict) else {}
        if kind == "auth_login":
            session.auth = (params.get("username", ""), _resolve_password(params.get("password")))
        elif kind == "auth_token":
            headers["Authorization"] = "Bearer " + _resolve_password(params.get("token"))
        elif kind == "auth_header":
            headers[params.get("header") or "X-API-Key"] = _resolve_password(params.get("key"))
        elif kind == "auth_query":
            query = {params.get("parameter") or "api_key": _resolve_password(params.get("key"))}
        elif kind == "auth_oauth2":
            headers["Authorization"] = "Bearer " + _access_token(params, conn)

    method = str(conn.get("method", "GET")).upper()
    body = conn.get("body") if method == "POST" else None
    if body is not None and not any(h.lower() == "content-type" for h in headers):
        headers["Content-Type"] = "application/json"

    return session.request(
        method,
        conn["url"],
        params=query,
        data=body,
        headers=headers,
        timeout=conn.get("timeout") or _TIMEOUT,
        verify=_verify_arg(conn),
        cert=_client_cert(conn),
        allow_redirects=conn.get("follow_redirects", True),
    )


def _redacted(text: str, conn: dict[str, Any]) -> str:
    """``text`` with an API key configured as a query parameter masked out."""
    auth = conn.get("auth")
    if not (isinstance(auth, (list, tuple)) and len(auth) == 2 and auth[0] == "auth_query"):
        return text
    params = auth[1] if isinstance(auth[1], dict) else {}
    secret = _resolve_password(params.get("key"))
    for form in (secret, quote(secret, safe=""), quote_plus(secret)):
        if form:
            text = text.replace(form, "<redacted>")
    return text


class JsonExplorerFetchPage(AjaxPage):
    def page(self, ctx: PageContext) -> PageResult:
        user.need_permission("wato.use")  # gate the SSRF surface to Setup users

        conn = _connection()
        url = str(conn.get("url", "")).strip()
        if not url.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": _("URL must start with http:// or https://")}

        try:
            resp = _perform_request(conn)
        except _PreviewError as exc:
            # Reported on its own: "the token endpoint said no" (or "this proxy
            # cannot be resolved") is a different problem from "the API said
            # no", and conflating them sends the operator to the wrong URL.
            return {"ok": False, "error": str(exc)}
        except requests.RequestException as exc:
            # The message of a connection error quotes the URL it tried to
            # reach - including an API key placed in a query parameter.
            return {"ok": False, "error": _("Request failed: %s") % _redacted(str(exc), conn)}

        try:
            data: Any = resp.json()
        except ValueError:
            return {
                "ok": False,
                "error": _("HTTP %d: response body is not JSON") % resp.status_code,
            }
        return {
            "ok": True,
            "status": resp.status_code,
            "json": data,
            # A plain dict: requests' CaseInsensitiveDict does not survive the
            # JSON round trip, and the picker matches case-insensitively anyway.
            "headers": dict(resp.headers),
        }


page_registry.register(PageEndpoint("json_explorer_fetch", JsonExplorerFetchPage()))
