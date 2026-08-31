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
        elif path == "/down":
            self._send(500, {"error": "simulated outage"})
        elif path == "/slow":
            time.sleep(5)
            self._send(200, {"status": "slow-ok"})
        elif path == "/notjson":
            self._send(200, None, raw=b"this is not JSON")
        else:
            self._send(404, {"error": f"no such path: {path}"})

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
