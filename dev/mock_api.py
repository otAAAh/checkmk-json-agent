#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""A dependency-free mock HTTP/JSON API for exercising the json_api special agent
and the Explorer wizard end-to-end.

Run it on the Checkmk host (the special agent fetches from there):

    python3 dev/mock_api.py                      # HTTP on 8642
    python3 dev/mock_api.py --tls --proxy        # + HTTPS on 8643, proxy on 8644

Then point a rule / the wizard at http://localhost:8642/<path>. Every path and
the rule settings it exercises are listed in docs/mock-api.md; the short list:

  /health       rich document: wildcards, filters, aggregations, matching, labels
  /metrics      flat numeric metrics (levels)
  /query        POST - echoes the posted JSON under {"echo": ...}
  /secure       401 unless an Authorization header is present (basic OR bearer)
  /token        POST - OAuth 2.0 client-credentials token endpoint
  /oauth        401 unless a bearer token issued by /token is presented
  /apikey       401 unless the API key arrives in 'X-API-Key' or '?api_key='
  /redirect     302 to /health; '?cross=1' redirects to the other loopback name
  /maintenance  503 with a JSON body and Retry-After (accept_status)
  /flaky        503 for the first N requests of a series, then 200 (retry)
  /counter      rising counters, a hit counter, rate-limit + Last-Modified headers
  /times        one timestamp in every format the agent reads
  /sensors      one value per unit: bytes, seconds, %, °C, V, A, W, Hz
  /jobs         paginated by a next link: body 'links.next' AND a 'Link' header
  /items        paginated by counting: page number or offset, page size, total
  /big          a large body (truncation of the reported response / context)
  /proxied      says whether the request came through the mock proxy
  /mtls         HTTPS only: 403 unless a client certificate was presented
  /down         always HTTP 500
  /slow         responds after ~5s (timeouts)
  /notjson      returns non-JSON text

Development only: no real authentication, fixed dev credentials, and it binds to
127.0.0.1 by default. Do not expose it.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import email.utils
import http.client
import json
import select
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote_plus, urlsplit

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

# One value per unit the rule offers, as a flat document and as a list of
# sensors (for a '[*]' wildcard labelled by 'name').
SENSORS = {
    "disk_used_bytes": 1572864,
    "backup_duration_seconds": 5400,
    "cpu_percent": 87.5,
    "temperature_celsius": 61.3,
    "supply_volts": 230.4,
    "load_amperes": 0.02,
    "power_watts": 1250.0,
    "cpu_hertz": 2400000000,
    "open_files_count": 42,
    "sensors": [
        {"name": "inlet", "celsius": 24.5, "status": "ok"},
        {"name": "cpu", "celsius": 71.0, "status": "warning"},
        {"name": "psu", "celsius": 48.2, "status": "ok"},
    ],
}


# The dev-only API key /apikey accepts, in a header or a query parameter.
API_KEY = "dev-key"

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

# A collection for the COUNTED pagination modes (no next link at all): 23 items,
# so no page size divides it and the last page is always a short one.
ITEMS = [{"id": n, "size": n * 10, "ok": n % 5 != 0} for n in range(1, 24)]
ITEMS_DEFAULT_PAGE_SIZE = 5

# Per-process state the stateful paths share. One lock, because the server
# handles each request in a thread of its own.
STARTED = time.time()
_LOCK = threading.Lock()
_HITS: dict[str, int] = {}
_FLAKY: dict[str, int] = {}


def _hit(name: str) -> int:
    """Count a request to ``name`` and return the new total."""
    with _LOCK:
        _HITS[name] = _HITS.get(name, 0) + 1
        return _HITS[name]


def _timestamps(now: datetime) -> dict[str, object]:
    """The same moments in every format the 'timestamp' reading accepts."""
    two_hours_ago = now - timedelta(hours=2)
    in_twenty_days = now + timedelta(days=20)
    return {
        # format 'auto'
        "epoch_seconds": int(two_hours_ago.timestamp()),
        "epoch_milliseconds": int(two_hours_ago.timestamp() * 1000),
        "iso_utc": two_hours_ago.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "iso_offset": two_hours_ago.astimezone().isoformat(timespec="seconds"),
        "iso_naive": two_hours_ago.strftime("%Y-%m-%dT%H:%M:%S"),
        "iso_nanoseconds": two_hours_ago.strftime("%Y-%m-%dT%H:%M:%S.123456789Z"),
        "http_date": email.utils.format_datetime(two_hours_ago, usegmt=True),
        "go_layout": two_hours_ago.strftime("%Y-%m-%d %H:%M:%S +0000 UTC"),
        # format 'custom', pattern '%d.%m.%Y %H:%M:%S'
        "custom_german": two_hours_ago.strftime("%d.%m.%Y %H:%M:%S"),
        # a timestamp in the FUTURE: a negative age (an expiry date)
        "expires_at": in_twenty_days.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "not_a_timestamp": "yesterday-ish",
    }


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1, so a client can reuse the connection the way requests does.
    protocol_version = "HTTP/1.1"

    def _send(
        self,
        code: int,
        payload: object,
        *,
        raw: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain" if raw is not None else "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> dict[str, str]:
        return dict(parse_qsl(urlsplit(self.path).query, keep_blank_values=True))

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        routes = {
            "/health": lambda: self._send(200, HEALTH),
            "/metrics": lambda: self._send(200, METRICS),
            "/secure": self._secure,
            "/oauth": self._oauth,
            "/apikey": self._apikey,
            "/redirect": self._redirect,
            "/maintenance": self._maintenance,
            "/flaky": self._flaky,
            "/counter": self._counter,
            "/times": lambda: self._send(200, _timestamps(datetime.now(UTC))),
            "/sensors": lambda: self._send(200, SENSORS),
            "/jobs": self._jobs,
            "/items": self._items,
            "/big": self._big,
            "/proxied": self._proxied,
            "/mtls": self._mtls,
            "/down": lambda: self._send(500, {"error": "simulated outage"}),
            "/slow": self._slow,
            "/notjson": lambda: self._send(200, None, raw=b"this is not JSON"),
        }
        route = routes.get(path)
        if route is None:
            self._send(404, {"error": f"no such path: {path}"})
        else:
            route()

    def _secure(self) -> None:
        if self.headers.get("Authorization"):
            self._send(200, {"status": "ok", "authorized": True})
        else:
            self._send(401, {"error": "missing Authorization header"})

    def _oauth(self) -> None:
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

    def _apikey(self) -> None:
        """Both API-key modes: the 'X-API-Key' header, or '?api_key='.

        '?echo=1' puts the received key back into the body, the way some APIs
        do - which is what the agent's redaction of the reported response and
        of the --debug output is for.
        """
        query = self._query()
        if self.headers.get("X-API-Key") == API_KEY:
            via = "header"
        elif query.get("api_key") == API_KEY:
            via = "query"
        else:
            self._send(401, {"error": "missing or wrong API key"})
            return
        body: dict[str, object] = {"status": "ok", "authorized_via": via}
        if query.get("echo") == "1":
            body["received_key"] = API_KEY
        self._send(200, body)

    def _redirect(self) -> None:
        """302 to /health - or, with '?cross=1', to /apikey on the OTHER host.

        "Other host" is the other loopback name (localhost <-> 127.0.0.1), on
        this server's own port: requests treats that as a different host, so
        it strips the credentials - and /apikey answering 401 there is how you
        see that the API key did not travel along. The target is chosen from a
        fixed pair, never copied from the request's Host header.
        """
        port = self.server.server_address[1]
        scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
        if self._query().get("cross") == "1":
            current = (self.headers.get("Host") or "").split(":", 1)[0]
            other = "127.0.0.1" if current == "localhost" else "localhost"
            location = f"{scheme}://{other}:{port}/apikey"
        else:
            location = "/health"
        self._send(302, {"redirect": location}, headers={"Location": location})

    def _maintenance(self) -> None:
        """A 503 whose JSON body is still worth reading: 'Accept HTTP status'."""
        self._send(
            503,
            {"status": "maintenance", "retry_after_seconds": 120, "message": "planned"},
            headers={"Retry-After": "120"},
        )

    def _flaky(self) -> None:
        """Fail the first '?fail=N' (default 2) requests of a series, then succeed.

        A series is named by '?id=' (default 'default') and starts over after
        its success, so every check run sees the same pattern: exactly what the
        endpoint's retry policy has to absorb.
        """
        query = self._query()
        series = query.get("id", "default")
        try:
            failures = max(int(query.get("fail", 2)), 0)
        except ValueError:
            failures = 2
        with _LOCK:
            attempt = _FLAKY.get(series, 0) + 1
            _FLAKY[series] = 0 if attempt > failures else attempt
        if attempt <= failures:
            self._send(503, {"error": f"transient failure {attempt} of {failures}"})
        else:
            self._send(200, {"status": "ok", "attempt": attempt})

    def _counter(self) -> None:
        """Counters that only rise, plus the headers APIs announce limits in.

        'requests_total' and 'bytes_sent' grow with time, so read as a counter
        they give a steady per-second rate. 'served' counts the requests this
        path has answered: with a cache TTL on the rule it stops climbing on
        every check. The headers are for '@header.' fields.
        """
        served = _hit("counter")
        elapsed = time.time() - STARTED
        self._send(
            200,
            {
                "requests_total": int(1_000_000 + elapsed * 25),
                "bytes_sent": int(5_000_000_000 + elapsed * 1_048_576),
                "served": served,
            },
            headers={
                "X-RateLimit-Limit": "1000",
                "X-RateLimit-Remaining": str(max(1000 - served, 0)),
                "Last-Modified": email.utils.formatdate(STARTED, usegmt=True),
                "ETag": f'"counter-{served}"',
            },
        )

    def _jobs(self) -> None:
        """One page of JOBS, announcing the next one in the body AND a header."""
        query = self._query()
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
        headers = {"Link": f'<{next_url}>; rel="next"'} if next_url else {}
        self._send(
            200,
            {"items": items, "total": len(JOBS), "page": page, "links": {"next": next_url}},
            headers=headers,
        )

    def _items(self) -> None:
        """ITEMS by a counted position - no next link anywhere.

        Page number: '?page=<n>' (1-based) with '?per_page=<size>'.
        Offset:      '?offset=<n>' (0-based) with '?limit=<size>', or SCIM's
                     1-based '?startIndex=<n>' with '?count=<size>'.
        The size defaults to 5; 'total' always carries the whole count.
        """
        query = self._query()

        def number(*names: str, default: int, minimum: int) -> int:
            for name in names:
                with contextlib.suppress(KeyError, ValueError):
                    return max(int(query[name]), minimum)
            return default

        size = number("per_page", "limit", "count", default=ITEMS_DEFAULT_PAGE_SIZE, minimum=1)
        if "startIndex" in query:
            start = number("startIndex", default=1, minimum=1) - 1
        elif "offset" in query:
            start = number("offset", default=0, minimum=0)
        else:
            start = (number("page", default=1, minimum=1) - 1) * size
        self._send(
            200,
            {"items": ITEMS[start : start + size], "total": len(ITEMS), "start": start},
        )

    def _big(self) -> None:
        """A body of about '?kb=<n>' kilobytes (default 200), as valid JSON.

        For the truncation of the reported response and of the field context;
        '?mb=60' goes past the agent's 50 MiB response limit.
        """
        query = self._query()
        try:
            size = (
                int(query["mb"]) * 1024 * 1024
                if "mb" in query
                else int(query.get("kb", 200)) * 1024
            )
        except ValueError:
            size = 200 * 1024
        size = min(max(size, 1024), 64 * 1024 * 1024)
        rows = [{"id": n, "payload": "x" * 90} for n in range(size // 110 + 1)]
        self._send(200, {"status": "ok", "rows": len(rows), "data": rows})

    def _proxied(self) -> None:
        """Whether the request came through the mock proxy (its 'Via' header)."""
        via = self.headers.get("Via")
        self._send(200, {"through_proxy": via is not None, "via": via})

    def _mtls(self) -> None:
        """HTTPS only: 200 with the client certificate's subject, else 403."""
        if not isinstance(self.connection, ssl.SSLSocket):
            self._send(400, {"error": "use the HTTPS port (--tls) for /mtls"})
            return
        cert = self.connection.getpeercert()
        if not cert:
            self._send(403, {"error": "no client certificate presented"})
            return
        subject = dict(item for rdn in cert.get("subject", ()) for item in rdn)
        self._send(200, {"status": "ok", "client": subject.get("commonName")})

    def _slow(self) -> None:
        time.sleep(5)
        self._send(200, {"status": "slow-ok"})

    def _oauth_token(self, raw: bytes) -> None:
        """The client-credentials grant, accepting either credential transport."""
        form = {}
        for pair in raw.decode("utf-8", "replace").split("&"):
            key, _, value = pair.partition("=")
            if key:
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
        scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
        print(f"mock_api[{scheme}]: {self.command} {self.path}", file=sys.stderr)


# --- TLS ----------------------------------------------------------------------


def make_certificates(directory: Path, days: int) -> dict[str, Path]:
    """A throwaway CA, a server certificate for localhost/127.0.0.1 and a client
    certificate, all signed by that CA. Needs the 'openssl' command.

    ``days`` is the SERVER certificate's validity: keep it short to see the
    endpoint service's certificate-expiry levels go WARN/CRIT.
    """
    if shutil.which("openssl") is None:
        raise SystemExit("mock_api: --tls needs the 'openssl' command")
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        name: directory / name
        for name in ("ca.pem", "ca.key", "server.pem", "server.key", "client.pem", "client.key")
    }

    def openssl(*args: str) -> None:
        subprocess.run(["openssl", *args], check=True, capture_output=True)

    # The extensions Python's strict verification (the default since 3.13)
    # insists on: a CA marked as one, and key identifiers linking each
    # certificate to its issuer. curl accepts certificates without them.
    openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
        "-subj", "/CN=json_api mock CA",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-addext", "subjectKeyIdentifier=hash",
        "-keyout", str(files["ca.key"]), "-out", str(files["ca.pem"]),
    )  # fmt: skip
    leaf = (
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n"
    )
    for role, cn, ext, validity in (
        (
            "server",
            "localhost",
            "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth",
            days,
        ),
        ("client", "json_api mock client", "extendedKeyUsage=clientAuth", 3650),
    ):
        csr = directory / f"{role}.csr"
        extfile = directory / f"{role}.ext"
        extfile.write_text(leaf + ext + "\n")
        openssl(
            "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={cn}",
            "-keyout", str(files[f"{role}.key"]), "-out", str(csr),
        )  # fmt: skip
        openssl(
            "x509", "-req", "-in", str(csr), "-days", str(validity),
            "-CA", str(files["ca.pem"]), "-CAkey", str(files["ca.key"]), "-CAcreateserial",
            "-extfile", str(extfile), "-out", str(files[f"{role}.pem"]),
        )  # fmt: skip
        csr.unlink()
        extfile.unlink()
    for key in ("ca.key", "server.key", "client.key"):
        files[key].chmod(0o600)
    return files


def tls_server(host: str, port: int, files: dict[str, Path]) -> ThreadingHTTPServer:
    """The same API over HTTPS. A client certificate is optional (only /mtls
    insists), and verified against the mock CA when one is presented."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(files["server.pem"], files["server.key"])
    context.load_verify_locations(files["ca.pem"])
    context.verify_mode = ssl.CERT_OPTIONAL
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    server = ThreadingHTTPServer((host, port), Handler)
    # The handshake runs on the first read, in the request's own thread, so one
    # stalled client cannot block the accept loop.
    server.socket = context.wrap_socket(
        server.socket, server_side=True, do_handshake_on_connect=False
    )
    return server


# --- proxy --------------------------------------------------------------------

# The proxy only forwards to these hosts, and only to the mock's own ports: it is
# for testing the agent's proxy setting against this mock, never a way to reach
# anything else, on this machine or beyond. Both names are this machine, so it
# always connects to _PROXY_UPSTREAM.
_PROXY_TARGETS = {"localhost", "127.0.0.1"}
_PROXY_UPSTREAM = "127.0.0.1"
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "proxy-connection",
}  # fmt: skip


class ProxyHandler(BaseHTTPRequestHandler):
    """A minimal forward proxy: absolute-URI requests for HTTP, CONNECT for HTTPS.

    A forwarded HTTP request gets a 'Via' header (which /proxied reports) and
    its response an 'X-Mock-Proxy' header (readable as '@header.X-Mock-Proxy').
    A CONNECT tunnel is opaque, so for HTTPS the proxy's log line is the proof.
    """

    def _refuse(self, code: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _mock_port(self, requested: object) -> int | None:
        """The mock's own port the request asks for, or None for any other.

        The allow-listed value itself is returned, not the requested one, so
        the connection's port never comes from the request.
        """
        allowed: frozenset[int] = getattr(self.server, "mock_ports", frozenset())
        return next((port for port in allowed if str(port) == str(requested)), None)

    def _forward(self) -> None:
        target = urlsplit(self.path)
        port = self._mock_port(target.port or 80)
        if target.scheme != "http" or target.hostname not in _PROXY_TARGETS or port is None:
            self._refuse(403, "the mock proxy only forwards to the mock API itself")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_BY_HOP}
        headers["Via"] = "1.1 json_api-mock-proxy"
        connection = http.client.HTTPConnection(_PROXY_UPSTREAM, port, timeout=30)
        try:
            path = target.path + (f"?{target.query}" if target.query else "")
            connection.request(self.command, path or "/", body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
        except OSError as exc:
            self._refuse(502, f"upstream failed: {exc}")
            return
        finally:
            connection.close()
        self.send_response(response.status)
        for name, value in response.getheaders():
            if name.lower() not in _HOP_BY_HOP and name.lower() != "content-length":
                self.send_header(name, value)
        self.send_header("X-Mock-Proxy", "forwarded")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _forward  # noqa: N815
    do_POST = _forward  # noqa: N815

    def do_CONNECT(self) -> None:  # noqa: N802
        host, _, requested = self.path.rpartition(":")
        port = self._mock_port(requested)
        if host not in _PROXY_TARGETS or port is None:
            self._refuse(403, "the mock proxy only tunnels to the mock API itself")
            return
        try:
            upstream = socket.create_connection((_PROXY_UPSTREAM, port), timeout=30)
        except OSError as exc:
            self._refuse(502, f"upstream failed: {exc}")
            return
        self.send_response(200, "Connection established")
        self.end_headers()
        sockets = [self.connection, upstream]
        try:
            while True:
                readable, _, broken = select.select(sockets, [], sockets, 30)
                if broken or not readable:
                    break
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is self.connection else self.connection).sendall(data)
        finally:
            upstream.close()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"mock_api[proxy]: {self.command} {self.path}", file=sys.stderr)


def proxy_server(host: str, port: int, mock_ports: set[int]) -> ThreadingHTTPServer:
    """The forward proxy, allowed to reach only ``mock_ports`` on this machine."""
    server = ThreadingHTTPServer((host, port), ProxyHandler)
    server.mock_ports = frozenset(mock_ports)  # type: ignore[attr-defined]
    return server


# --- main ---------------------------------------------------------------------


def _serve(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def main() -> None:
    # Line-buffered, so the startup banner shows up at once when the output goes
    # to a file or a pipe (the request log goes to stderr, which is unbuffered).
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description="Mock JSON API for the json_api agent/Explorer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8642, help="HTTP port (default 8642)")
    parser.add_argument(
        "--tls", action="store_true", help="also serve HTTPS on --tls-port with a throwaway CA"
    )
    parser.add_argument("--tls-port", type=int, default=8643)
    parser.add_argument(
        "--tls-days",
        type=int,
        default=20,
        help="server certificate validity in days (default 20: inside the 30/7 days "
        "the certificate levels suggest, so with those set the endpoint service goes WARN)",
    )
    parser.add_argument(
        "--tls-dir",
        type=Path,
        default=None,
        help="where to write the certificates (default: a fresh temporary directory)",
    )
    parser.add_argument(
        "--proxy", action="store_true", help="also run a forward proxy on --proxy-port"
    )
    parser.add_argument("--proxy-port", type=int, default=8644)
    args = parser.parse_args()

    servers = [ThreadingHTTPServer((args.host, args.port), Handler)]
    print(f"mock_api: HTTP   on http://{args.host}:{args.port}")
    if args.tls:
        directory = args.tls_dir or Path(tempfile.mkdtemp(prefix="json_api_mock_tls_"))
        files = make_certificates(directory, args.tls_days)
        servers.append(tls_server(args.host, args.tls_port, files))
        print(f"mock_api: HTTPS  on https://localhost:{args.tls_port}")
        print(f"          CA bundle (rule: 'CA bundle'):    {files['ca.pem']}")
        print(f"          client certificate / key (mTLS): {files['client.pem']}")
        print(f"                                           {files['client.key']}")
        print(f"          server certificate valid for {args.tls_days} days")
    if args.proxy:
        mock_ports = {args.port, args.tls_port} if args.tls else {args.port}
        servers.append(proxy_server(args.host, args.proxy_port, mock_ports))
        print(f"mock_api: proxy  on http://{args.host}:{args.proxy_port}")
    print("mock_api: Ctrl-C to stop; see docs/mock-api.md")

    threads = [_serve(server) for server in servers]
    with contextlib.suppress(KeyboardInterrupt):
        for thread in threads:
            thread.join()
    for server in servers:
        server.shutdown()


if __name__ == "__main__":
    main()
