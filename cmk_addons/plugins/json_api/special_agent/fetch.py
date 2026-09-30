# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Fetching one endpoint: cache, retries, token refresh and the request itself."""

import json
import time

import requests

from .cache import _cache_read, _cache_ttl, _cache_write
from .oauth2 import _access_token, _cached_token, _forget_token, _oauth2_spec, _TokenError
from .pagination import _first_page_url, _follow_pagination, _pagination_spec
from .transport import (
    _DEBUG_BODY_PREVIEW,
    _MAX_RESPONSE_BYTES,
    _REDACTED,
    _accepted_statuses,
    _auth_header_name,
    _auth_params,
    _build_session,
    _client_cert,
    _debug,
    _effective_body,
    _peer_cert_expiry,
    _read_capped,
    _read_reportable,
    _redact_secret,
    _redacted_headers,
    _report_spec,
    _reported_limit,
    _ResponseTooLarge,
    _retryable_status,
    _store_report,
    _verify_arg,
)

# Whatever the retry count, the agent never waits longer than this in total. A
# check that sleeps for minutes is a worse failure than the one it is papering
# over: Checkmk kills a special agent that overruns.
_MAX_RETRY_SLEEP = 30.0


def _retry_policy(endpoint: dict) -> tuple[int, float]:
    """``(retries, backoff)`` for this endpoint; ``(0, 0.0)`` when off."""
    retry = endpoint.get("retry")
    if not isinstance(retry, dict):
        return 0, 0.0
    attempts = retry.get("attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        return 0, 0.0
    backoff = retry.get("backoff")
    if isinstance(backoff, bool) or not isinstance(backoff, (int, float)) or backoff < 0:
        backoff = 0.0
    return attempts, float(backoff)


def _fetch(
    endpoint: dict, secret: str | None, debug: bool = False
) -> tuple[object | None, str | None, dict]:
    """Fetch one endpoint, retrying a transient failure: ``(document, error, meta)``.

    ``meta`` describes the request itself - HTTP status, wall-clock duration,
    body size and the final URL after any redirects - and is filled in on every
    exit path (a failed request still took time). It feeds the endpoint's own
    service, so 'the API answered, but slowly' is visible without configuring a
    field for it.

    With a retry policy configured, a failure a repeat could fix is tried again
    after a doubling wait. ``meta["attempts"]`` counts what it took, so an API
    that only answers on the second attempt is reported rather than quietly
    smoothed over - and the response time stays the successful attempt's, not
    the sum, so the metric keeps meaning what it says.
    """
    meta: dict[str, object] = {
        "status": None,
        "elapsed": None,
        "size": None,
        "final_url": None,
        "cert_expiry": None,
        "from_cache": False,
        "cache_age": None,
        "attempts": 1,
        # Response headers, so an extraction can read one (see _HEADER_PREFIX).
        # Cached alongside the body: replaying the body with someone else's
        # headers would describe a response that never existed.
        "headers": {},
    }
    # A cached body younger than the endpoint's TTL is served without touching the
    # network at all - that is the whole point, for an API with a request quota.
    # No request is made, so there is nothing to retry either.
    if (ttl := _cache_ttl(endpoint)) is not None and (hit := _cache_read(endpoint, ttl, secret)):
        cached_body, cached_meta = hit
        _debug(debug, f"  served from cache ({cached_meta['cache_age']:.0f}s old)")
        try:
            document = json.loads(cached_body)
        except ValueError as exc:
            # Cached something unparseable: fall through and fetch fresh.
            _debug(debug, f"  cached body is not valid JSON ({exc}), fetching")
        else:
            # The body being served IS the cached one, so that is what gets
            # reported - rebuilt here rather than replayed from the cache file,
            # so it follows the settings in force now.
            _store_report(endpoint, cached_meta, cached_body, secret)
            return document, None, cached_meta

    retries, backoff = _retry_policy(endpoint)
    slept = 0.0
    for attempt in range(retries + 1):
        meta["attempts"] = attempt + 1
        document, error, retryable = _attempt_with_token_refresh(endpoint, secret, meta, ttl, debug)
        if error is None or not retryable or attempt == retries:
            return document, error, meta
        delay = min(backoff * (2**attempt), _MAX_RETRY_SLEEP - slept)
        if delay <= 0 and slept >= _MAX_RETRY_SLEEP:
            _debug(debug, "  retry budget exhausted, giving up")
            return document, error, meta
        _debug(debug, f"  {error} - retrying in {max(delay, 0.0):.1f}s")
        if delay > 0:
            time.sleep(delay)
            slept += delay
    # Unreachable: the loop always returns (range is never empty).
    return None, "Request failed", meta


def _attempt_with_token_refresh(
    endpoint: dict, secret: str | None, meta: dict, ttl: float | None, debug: bool
) -> tuple[object | None, str | None, bool]:
    """One request, redone once with a fresh token if a cached one was rejected.

    A provider can invalidate an access token before its stated expiry (a
    rotated client secret, a revoked grant), which reaches us as a 401 the
    endpoint would otherwise report until the cached token finally expired.

    Only a token that came from the CACHE earns the second attempt. A token
    minted seconds ago and rejected means the credentials or the scope are
    wrong, and asking again would just double every check's requests forever.
    """
    oauth2 = _oauth2_spec(endpoint)
    used_cached_token = oauth2 is not None and _cached_token(oauth2, secret) is not None
    document, error, retryable = _attempt(endpoint, secret, meta, ttl, debug)
    if error is None or not used_cached_token or meta.get("status") != 401:
        return document, error, retryable
    _debug(debug, "  HTTP 401 with a cached token - discarding it and retrying once")
    return _attempt(endpoint, secret, meta, ttl, debug, force_new_token=True)


def _attempt(
    endpoint: dict,
    secret: str | None,
    meta: dict,
    ttl: float | None,
    debug: bool,
    force_new_token: bool = False,
) -> tuple[object | None, str | None, bool]:
    """One request: ``(document, error, retryable)``, updating ``meta`` in place.

    ``retryable`` says whether repeating this exact request could plausibly
    succeed. A parse failure, an oversized body and a 4xx are deterministic, so
    they are reported as final however many retries are configured.
    """
    started = time.monotonic()

    def _timed() -> dict:
        meta["elapsed"] = time.monotonic() - started
        return meta

    access_token = None
    if (oauth2 := _oauth2_spec(endpoint)) is not None:
        if force_new_token:
            _forget_token(oauth2, secret)
        try:
            access_token = _access_token(endpoint, oauth2, secret, debug)
        except _TokenError as exc:
            # No token, no request. Retryable: the provider being briefly
            # unreachable is the same class of blip as the API being so.
            _debug(debug, f"  {exc}")
            _timed()
            return None, str(exc), True

    session, headers = _build_session(endpoint, secret, access_token)
    params = _auth_params(endpoint, secret)
    # Only the query-parameter mode can carry the key into a URL, so that is the
    # only mode whose reported text needs scrubbing.
    scrub = (lambda text: _redact_secret(text, secret)) if params else (lambda text: text)
    timeout = endpoint.get("timeout")
    method = endpoint.get("method", "GET")
    allow_redirects = endpoint.get("follow_redirects", True)
    accepted = _accepted_statuses(endpoint)
    body = _effective_body(endpoint)
    if debug:
        _debug(debug, f"{method} {endpoint.get('url', '?')}")
        for name, value in _redacted_headers(headers, _auth_header_name(endpoint)).items():
            _debug(debug, f"  header {name}: {value}")
        if session.auth is not None:
            _debug(debug, "  basic auth: <redacted>")
        if params:
            _debug(debug, f"  query parameter {next(iter(params))}: {_REDACTED}")
        if body is not None:
            _debug(debug, f"  body: {body}")
    # Collected rather than passed inline: a paginated endpoint sends the same
    # request again at the next page's URL, and every setting - authentication,
    # headers, TLS, timeout - has to be the first page's.
    request_kwargs: dict[str, object] = {
        "params": params,
        "data": body,
        "headers": headers,
        "verify": _verify_arg(endpoint),
        "cert": _client_cert(endpoint),
        "allow_redirects": allow_redirects,
        "timeout": timeout if timeout is not None else 30.0,
        "stream": True,  # so _read_capped can bound the buffered body
    }
    try:
        # Where the agent counts the pages, the first one carries the counting
        # parameters too; otherwise this is the rule's URL as written.
        response = session.request(method, _first_page_url(endpoint), **request_kwargs)
        with response:
            status = response.status_code
            meta["status"] = status
            meta["final_url"] = scrub(response.url)
            meta["headers"] = dict(response.headers)
            # While the socket is still open and before the body is read. Done
            # here rather than after the status checks so a certificate is still
            # reported for an endpoint answering 503 - the cert is a property of
            # the connection, not of the response code.
            meta["cert_expiry"] = _peer_cert_expiry(response)
            # With redirects disabled a 3xx is not an error to requests, but it
            # is not the JSON we asked for - report it clearly (this is the SSRF
            # hardening path, where a silent "not valid JSON" would mislead).
            if not allow_redirects and 300 <= status < 400:
                location = scrub(response.headers.get("Location", "?"))
                _debug(debug, f"  HTTP {response.status_code} redirect to {location} (blocked)")
                _timed()
                # A blocked redirect is a property of the request, not a hiccup.
                return (
                    None,
                    f"Unexpected {status} redirect to {location} (redirects disabled)",
                    False,
                )
            # 2xx is always fine; extra codes can be opted in per endpoint so an
            # API that signals via the status code (e.g. 503 + a JSON health
            # body) can still be read. Anything else fails, carrying the code.
            if not (200 <= status < 300 or status in accepted):
                reason = getattr(response, "reason", "") or ""
                # The body of a rejected response is where an API explains
                # itself, and this is the case an operator most needs to see -
                # so read it, but only when the rule asked for it (otherwise the
                # status alone is the answer and the body is never touched).
                if (report := _report_spec(endpoint)) is not None:
                    try:
                        partial, more = _read_reportable(response, _reported_limit(report))
                    except requests.exceptions.RequestException as exc:
                        # Reporting the body is a diagnostic nicety; failing to
                        # read it must not change what the endpoint reports.
                        _debug(debug, f"  could not read the error body: {exc}")
                    else:
                        _store_report(endpoint, meta, partial, secret, complete=not more)
                _timed()
                return (
                    None,
                    f"HTTP {status}{f' {reason}' if reason else ''}",
                    _retryable_status(status),
                )
            raw = _read_capped(response, _MAX_RESPONSE_BYTES)
    except _ResponseTooLarge as exc:
        # The API really does answer with that much; asking again changes nothing.
        _debug(debug, f"  {exc}")
        _timed()
        return None, str(exc), False
    except requests.exceptions.RequestException as exc:
        # Connection reset, DNS hiccup, TLS handshake, timeout: the transient
        # class this whole feature exists for. The message quotes the URL it was
        # trying to reach - query string and all - so it is scrubbed first.
        failure = scrub(str(exc))
        _debug(debug, f"  request failed: {failure}")
        _timed()
        return None, f"Request failed: {failure}", True

    meta["size"] = len(raw)
    if debug:
        preview = raw[:_DEBUG_BODY_PREVIEW].decode("utf-8", "replace")
        suffix = " ...(truncated)" if len(raw) > _DEBUG_BODY_PREVIEW else ""
        _debug(debug, f"  HTTP {response.status_code}, {len(raw)} bytes")
        _debug(debug, f"  body: {preview}{suffix}")

    _store_report(endpoint, meta, raw, secret)
    try:
        document = json.loads(raw)
    except ValueError as exc:
        # The endpoint answered, just not with JSON - a configuration problem,
        # not a blip.
        _timed()
        return None, f"Response is not valid JSON: {exc}", False
    cacheable = raw
    if (pagination := _pagination_spec(endpoint)) is not None:
        error, retryable = _follow_pagination(
            session,
            method,
            request_kwargs,
            accepted,
            endpoint,
            pagination,
            document,
            meta,
            dict(response.headers),
            len(raw),
            scrub,
            debug,
            # response.url, not meta["final_url"]: the latter has the secret
            # masked out of it (for the query-parameter auth mode), and a URL
            # with '<redacted>' in it is not one to resolve links against.
            getattr(response, "url", "") or "",
        )
        if error is not None:
            _timed()
            return None, error, retryable
        if meta.get("pages", 1) > 1:
            # The MERGED document is what the extractions saw, so it is what a
            # cache hit has to replay - caching the first page would serve a
            # collection that shrinks for the length of the TTL. Re-serialized
            # rather than concatenated: the pages were merged as JSON.
            cacheable = json.dumps(document).encode("utf-8")
    timed = _timed()
    if ttl is not None:
        # Only a response we could actually parse is worth caching; an error or a
        # non-JSON body would just be replayed for the whole TTL.
        _cache_write(endpoint, cacheable, timed, secret)
    return document, None, False
