#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""A dependency-free mock HTTP/JSON API for exercising the json_api special agent
and the Explorer wizard end-to-end.

Run it on the Checkmk host (the special agent fetches from there):

    python3 dev/mock_api.py --port 8642

Then point a rule / the wizard at http://localhost:8642/<path>.

Endpoints (all GET unless noted):
  /health   rich document covering every extraction feature (see SAMPLE below)
  /metrics  flat numeric metrics (levels_upper / levels_lower)
  /query    POST — echoes the posted JSON under {"echo": ...} (+ a status)
  /secure   401 unless an Authorization header is present (basic OR bearer);
            returns {"status": "ok", "authorized": true} when it is
  /token    POST - an OAuth 2.0 client-credentials token endpoint. Accepts the
            client id/secret either as HTTP basic auth or in the request body,
            so both settings of "How to send the client credentials" can be
            exercised. Credentials: monitoring / s3cret (dev only!).
  /oauth    401 unless a bearer token issued by /token is presented; returns a
            small document plus how many tokens have been issued so far, which
            is how you can SEE the agent caching the token between checks
  /jobs     a PAGINATED collection, 7 jobs over 3 pages of 3: each page carries
            'items' plus 'links.next' (absent on the last page) AND an RFC 8288
            'Link: <...>; rel="next"' header (both relative, as real APIs
            commonly send them), so both next-page sources can be exercised
            against the same endpoint. Without 'Follow pagination' a
            count over 'items' reports 3 forever; with it, 7. '?page=<n>' picks
            a page by hand, and '?loop=1' makes every page point at itself (the
            loop the agent has to refuse)
  /down     always HTTP 500 (test the "endpoint unreachable/UNKNOWN" path)
  /slow     responds after ~5s (test per-endpoint timeouts)
  /notjson  returns non-JSON text (test the non-JSON UNKNOWN path)
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# One document touching every path feature the agent/Explorer support:
#   status                       -> string (regex "expected" match)
#   version                      -> string (regex match)
#   uptime_seconds / error_rate  -> numeric (levels_upper / levels_lower)
#   components.db.latency_ms     -> nested numeric
#   components[*].status         -> object/map wildcard (label = key)
#   nodes[*].health / [*].load   -> array wildcard (label by 'name')
#   pods[*].containers[*].ready  -> cartesian wildcards (composite label)
#   data['foo.bar'].value        -> bracket-quoted key
HEALTH = {
    "status": "ok",
    "version": "2.4.0",
    "uptime_seconds": 123456,
    "error_rate": 0.02,
    "components": {
        "db": {"status": "ok", "latency_ms": 42.5},
        "cache": {"status": "degraded", "latency_ms": 180.0},
        "queue": {"status": "ok", "latency_ms": 12.0},
    },
    "nodes": [
        {"name": "node-1", "health": "ok", "load": 0.42},
        {"name": "node-2", "health": "critical", "load": 0.97},
    ],
    "pods": [
        {
            "name": "web",
            "containers": [{"name": "nginx", "ready": True}, {"name": "sidecar", "ready": False}],
        },
        {"name": "worker", "containers": [{"name": "app", "ready": True}]},
    ],
    "data": {"foo.bar": {"value": 7}},
}

METRICS = {
    "requests_total": 998877,
    "requests_per_second": 143.7,
    "queue_depth": 5,
    "cpu_load": 0.63,
    "memory_used_mb": 2048,
    "free_disk_percent": 11.5,
}


# The dev-only OAuth 2.0 client the /token endpoint accepts, and the tokens it
# has handed out. Deliberately trivial: this exists to exercise the agent's flow,
# not to model an identity provider.
OAUTH_CLIENT = ("monitoring", "s3cret")
ISSUED_TOKENS: dict[str, float] = {}
# Short enough that a token visibly expires while you watch, long enough that a
# normal check interval reuses the cached one.
OAUTH_TOKEN_TTL = 300


# A paginated collection: 7 jobs served 3 at a time, so following the pages is
# the difference between 'count' reporting 3 and reporting 7.
JOBS = [{"id": n, "state": "running" if n % 3 else "failed", "queue": "batch"} for n in range(1, 8)]
JOBS_PER_PAGE = 3


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: object, *, raw: bytes | None = None) -> None:
        body = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain" if raw is not None else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            self._send(200, HEALTH)
        elif path == "/metrics":
            self._send(200, METRICS)
        elif path == "/secure":
            if self.headers.get("Authorization"):
                self._send(200, {"status": "ok", "authorized": True})
            else:
                self._send(401, {"error": "missing Authorization header"})
        elif path == "/oauth":
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if token and token in ISSUED_TOKENS and time.time() < ISSUED_TOKENS[token]:
                self._send(
                    200,
                    {
                        "status": "UP",
                        "queue": {"depth": 7, "oldest_seconds": 41},
                        # Watch this NOT climb across checks: the agent caches
                        # the token until shortly before it expires.
                        "tokens_issued": len(ISSUED_TOKENS),
                    },
                )
            else:
                self._send(401, {"error": "missing or unknown bearer token"})
        elif path == "/jobs":
            self._jobs()
        elif path == "/down":
            self._send(500, {"error": "simulated outage"})
        elif path == "/slow":
            time.sleep(5)
            self._send(200, {"status": "slow-ok"})
        elif path == "/notjson":
            self._send(200, None, raw=b"this is not JSON")
        else:
            self._send(404, {"error": f"no such path: {path}"})

    def _jobs(self) -> None:
        """One page of JOBS, announcing the next one in the body AND a header."""
        query = dict(
            part.split("=", 1) if "=" in part else (part, "")
            for part in self.path.partition("?")[2].split("&")
            if part
        )
        try:
            page = max(int(query.get("page", 1)), 1)
        except ValueError:
            page = 1
        start = (page - 1) * JOBS_PER_PAGE
        items = JOBS[start : start + JOBS_PER_PAGE]
        # '?loop=1' keeps pointing at the same page, carrying the flag along so
        # the link really does repeat: the pagination loop the agent has to
        # detect and refuse rather than spend its whole page budget on.
        looping = query.get("loop") == "1"
        # RELATIVE, which real APIs commonly send and the agent resolves against
        # the page it came from - so this exercises that path too. It also keeps
        # the request's own 'Host' header out of the response: echoing a
        # client-provided value into a header (and into the body) is header
        # splitting waiting to happen, dev-only server or not.
        next_url = (
            f"/jobs?page={page}&loop=1"
            if looping
            else f"/jobs?page={page + 1}"
            if start + JOBS_PER_PAGE < len(JOBS)
            else None
        )
        body = json.dumps(
            {"items": items, "total": len(JOBS), "page": page, "links": {"next": next_url}}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if next_url:
            # The other next-page source, on the same endpoint: RFC 8288.
            self.send_header("Link", f'<{next_url}>; rel="next"')
        self.end_headers()
        self.wfile.write(body)

    def _oauth_token(self, raw: bytes) -> None:
        """The client-credentials grant, accepting either credential transport."""
        form = {}
        for pair in raw.decode("utf-8", "replace").split("&"):
            key, _, value = pair.partition("=")
            if key:
                from urllib.parse import unquote_plus

                form[unquote_plus(key)] = unquote_plus(value)

        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            with contextlib.suppress(Exception):
                name, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
                form.setdefault("client_id", name)
                form.setdefault("client_secret", secret)

        if form.get("grant_type") != "client_credentials":
            self._send(400, {"error": "unsupported_grant_type"})
            return
        if (form.get("client_id"), form.get("client_secret")) != OAUTH_CLIENT:
            self._send(401, {"error": "invalid_client"})
            return

        token = f"tok-{len(ISSUED_TOKENS) + 1}-{int(time.time())}"
        ISSUED_TOKENS[token] = time.time() + OAUTH_TOKEN_TTL
        self._send(
            200,
            {"access_token": token, "token_type": "Bearer", "expires_in": OAUTH_TOKEN_TTL},
        )

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        if self.path.split("?", 1)[0].rstrip("/") == "/token":
            self._oauth_token(raw)
            return
        try:
            echo = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            echo = raw.decode("utf-8", "replace")
        self._send(200, {"status": "ok", "echo": echo})

    def log_message(self, fmt: str, *args: object) -> None:
        # One concise line per request on stderr.
        print(f"mock_api: {self.command} {self.path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock JSON API for the json_api agent/Explorer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8642)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"mock_api listening on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
