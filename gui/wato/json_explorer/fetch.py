# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""AJAX helper that fetches an endpoint URL server-side and returns its JSON.

The JSON field picker needs the endpoint's response to build its tree, but the
browser can't fetch operator endpoints (CORS, and they're typically only
reachable from the Checkmk server). So we fetch it here — the same place the
special agent does — using the SAME connection the wizard configured: method,
body, request headers, TLS verification and authentication (basic / bearer,
resolved from the password store). That way the preview matches what the agent
will actually see (authenticated / self-signed endpoints work in the wizard).

Input (POST): ``connection`` = the connection FormEdit value (JSON); falls back
to a bare ``url`` query var. Output: ``{ok, status, json}`` or ``{ok:false, error}``.

SECURITY: this performs an HTTP request from the Checkmk server to an
operator-supplied URL — an SSRF vector, exactly like the agent itself. It is
gated on Setup access (``wato.use``) so read-only monitoring users can't use it
as an open proxy, and only reveals a configured password to reach the very
endpoint the operator just entered.
"""

from __future__ import annotations

import json
from typing import Any

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


def _perform_request(conn: dict[str, Any]) -> requests.Response:
    session = requests.Session()
    headers = {h["name"]: h["value"] for h in conn.get("headers", []) if isinstance(h, dict)}

    auth = conn.get("auth")
    if isinstance(auth, (list, tuple)) and len(auth) == 2:
        kind, params = auth
        params = params if isinstance(params, dict) else {}
        if kind == "auth_login":
            session.auth = (params.get("username", ""), _resolve_password(params.get("password")))
        elif kind == "auth_token":
            headers["Authorization"] = "Bearer " + _resolve_password(params.get("token"))

    method = str(conn.get("method", "GET")).upper()
    body = conn.get("body") if method == "POST" else None
    if body is not None and not any(h.lower() == "content-type" for h in headers):
        headers["Content-Type"] = "application/json"

    return session.request(
        method,
        conn["url"],
        data=body,
        headers=headers,
        timeout=conn.get("timeout") or _TIMEOUT,
        verify=conn.get("verify_cert", True),
        allow_redirects=conn.get("follow_redirects", True),
    )


class JsonExplorerFetchPage(AjaxPage):
    def page(self, ctx: PageContext) -> PageResult:
        user.need_permission("wato.use")  # gate the SSRF surface to Setup users

        conn = _connection()
        url = str(conn.get("url", "")).strip()
        if not url.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": _("URL must start with http:// or https://")}

        try:
            resp = _perform_request(conn)
        except requests.RequestException as exc:
            return {"ok": False, "error": _("Request failed: %s") % exc}

        try:
            data: Any = resp.json()
        except ValueError:
            return {
                "ok": False,
                "error": _("HTTP %d: response body is not JSON") % resp.status_code,
            }
        return {"ok": True, "status": resp.status_code, "json": data}


page_registry.register(PageEndpoint("json_explorer_fetch", JsonExplorerFetchPage()))
