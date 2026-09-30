# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""OAuth 2.0 client credentials: requesting, caching and forgetting access tokens."""

import hashlib
import json
import os
import time
from contextlib import suppress
from pathlib import Path

import requests

from . import cache
from .cache import _prune_cache
from .paths import _as_float
from .transport import _apply_proxy, _client_cert, _debug, _verify_arg

# An access token is refreshed this many seconds BEFORE it expires, so a token
# that is valid when we check cannot expire in flight on a slow request.
_TOKEN_EXPIRY_SKEW = 60.0


# A provider that reports no lifetime at all: assume a short one rather than
# caching a token forever.
_TOKEN_DEFAULT_TTL = 300.0


_TOKEN_CACHE_DIR_NAME = "json_api_token_cache"


class _TokenError(Exception):
    """The token could not be obtained; the endpoint fails with this message."""


def _oauth2_spec(endpoint: dict) -> dict | None:
    """The endpoint's OAuth2 config, or ``None`` when it uses another auth mode."""
    if endpoint.get("auth") != "auth_oauth2":
        return None
    spec = endpoint.get("oauth2")
    return spec if isinstance(spec, dict) and spec.get("token_url") else None


def _token_cache_key(spec: dict, secret: str | None) -> str:
    """A hash over everything that decides WHICH token this is.

    Deliberately not the response cache's key: a token is shared by every request
    that authenticates the same way, and its lifetime comes from the provider's
    'expires_in' rather than from the endpoint's cache TTL. Two endpoints of the
    same rule pointing at the same provider with the same client should reuse one
    token, not fetch two.

    The secret is hashed, never stored - the key becomes a filename.
    """
    identity = json.dumps(
        [
            spec.get("token_url"),
            spec.get("client_id"),
            spec.get("scope"),
            spec.get("audience"),
            spec.get("client_auth", "basic"),
            hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else None,
        ],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _token_cache_path(spec: dict, secret: str | None) -> Path | None:
    # Through the module, so the token cache always lives wherever the response
    # cache does (one place to point both at, e.g. a test's tmp_path).
    directory = cache._cache_dir(_TOKEN_CACHE_DIR_NAME)
    if directory is None:
        return None
    return directory / f"{_token_cache_key(spec, secret)}.json"


def _cached_token(spec: dict, secret: str | None) -> str | None:
    """A cached access token that is still valid, else ``None``."""
    path = _token_cache_path(spec, secret)
    if path is None:
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        token = entry["token"]
        expires_at = float(entry["expires_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(token, str) or not token:
        return None
    if time.time() < expires_at:
        return token
    # Expired: drop it now rather than leaving a dead bearer credential on disk
    # until the 7-day sweep. Tokens live for minutes, so that sweep - written for
    # response bodies whose key changed - is far too slow to be the only cleanup.
    with suppress(OSError):
        path.unlink(missing_ok=True)
    return None


def _store_token(spec: dict, secret: str | None, token: str, ttl: float) -> None:
    """Cache a fresh token. Best effort: failing to cache must not fail the fetch."""
    path = _token_cache_path(spec, secret)
    if path is None:
        return
    entry = {"token": token, "expires_at": time.time() + max(ttl - _TOKEN_EXPIRY_SKEW, 0.0)}
    try:
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        # An access token is a bearer credential at rest. Storing one is the
        # whole point of caching it - the alternative is asking the identity
        # provider on every check - and it cannot be hashed (it has to be sent)
        # or encrypted usefully (the key would have to sit beside it). Checkmk's
        # own password store and the Graph client in cmk/plugins/emailchecks
        # keep credentials on disk the same way. Mitigated by 0600 and by living
        # in the site's tmp, which is cleared with the site.
        # (CodeQL flags this as clear-text storage. The suppression comment
        # below is honoured by the CodeQL CLI but not by GitHub's default code
        # scanning, so the alert also has to be dismissed in the Security tab.)
        temporary.write_text(  # codeql[py/clear-text-storage-sensitive-data]
            json.dumps(entry), encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(path)
        _prune_cache(path.parent)
    except OSError:
        return


def _forget_token(spec: dict, secret: str | None) -> None:
    """Drop the cached token, so the next attempt fetches a fresh one."""
    path = _token_cache_path(spec, secret)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _request_token(
    endpoint: dict, spec: dict, secret: str | None, debug: bool
) -> tuple[str, float]:
    """Exchange the client credentials for an access token: ``(token, ttl)``.

    Raises ``_TokenError`` with a message fit for the service, never leaking the
    secret: a provider that rejects the credentials tends to echo the request
    back, so the body is not reported.
    """
    data: dict[str, str] = {"grant_type": "client_credentials"}
    if scope := spec.get("scope"):
        data["scope"] = str(scope)
    if audience := spec.get("audience"):
        data["audience"] = str(audience)

    session = requests.Session()
    _apply_proxy(session, endpoint)
    if spec.get("client_auth") == "post":
        data["client_id"] = str(spec.get("client_id", ""))
        data["client_secret"] = secret or ""
    else:
        session.auth = (str(spec.get("client_id", "")), secret or "")

    timeout = endpoint.get("timeout")
    _debug(debug, f"  fetching an access token from {spec['token_url']}")
    try:
        response = session.post(
            spec["token_url"],
            data=data,
            # The token endpoint is part of the trust chain: verification follows
            # the endpoint's own TLS settings rather than being relaxed here.
            verify=_verify_arg(endpoint),
            cert=_client_cert(endpoint),
            timeout=timeout if timeout is not None else 30.0,
        )
    except requests.exceptions.RequestException as exc:
        # Only the exception TYPE and the token URL, never the message. requests
        # can quote the request it was making, and with the credentials sent in
        # the body that request literally contains the client secret - redacting
        # it afterwards is a weaker guarantee than never assembling it. The
        # token URL is configuration, not a credential.
        raise _TokenError(
            f"Token request to {spec['token_url']} failed ({type(exc).__name__})"
        ) from exc
    with response:
        if not 200 <= response.status_code < 300:
            reason = getattr(response, "reason", "") or ""
            raise _TokenError(
                f"Token request returned HTTP {response.status_code}"
                f"{f' {reason}' if reason else ''}"
                " (check the client credentials, the scope, and how the client "
                "credentials are sent)"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise _TokenError(f"Token response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _TokenError("Token response is not a JSON object")
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise _TokenError("Token response carries no 'access_token'")
    ttl = _as_float(payload.get("expires_in"))
    return token, ttl if ttl is not None and ttl > 0 else _TOKEN_DEFAULT_TTL


def _access_token(endpoint: dict, spec: dict, secret: str | None, debug: bool) -> str:
    """A valid access token, from the cache when there is one."""
    if (cached := _cached_token(spec, secret)) is not None:
        _debug(debug, "  using the cached access token")
        return cached
    token, ttl = _request_token(endpoint, spec, secret, debug)
    _store_token(spec, secret, token, ttl)
    return token
