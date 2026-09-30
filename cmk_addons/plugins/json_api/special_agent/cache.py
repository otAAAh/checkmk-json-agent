# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The opt-in per-endpoint response cache."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from .transport import _effective_body

# Per-endpoint response cache. Opt-in: no TTL configured means "always fetch",
# which is the right default for monitoring. It exists for rate-limited,
# expensive or fanned-out endpoints, where the request RATE is the problem rather
# than the freshness. Checkmk's own fetcher cache cannot express this - it is
# host-wide, all-or-nothing, and sized by a site-global setting during checking.
_CACHE_DIR_NAME = "json_api_cache"


# Files this old are removed on the next write, so a rule edit that changes an
# endpoint's identity (and thus its key) cannot leak files forever.
_CACHE_PRUNE_AFTER = 7 * 86400


def _cache_dir(name: str = _CACHE_DIR_NAME) -> Path | None:
    """A cache directory of the given name, or ``None`` if there isn't one.

    Prefers the tmp directory Checkmk hands the agent, then the site's own tmp,
    and only then the system default - a monitoring cache belongs inside the site
    so it is cleaned out with it. ``None`` (nothing writable) disables caching
    rather than failing the fetch.

    ``name`` separates the response cache from the OAuth2 token cache: they are
    keyed differently and expire differently, so they must not share a directory
    (the pruner walks a whole directory).
    """
    directory = Path(tempfile.gettempdir()) / name
    if mk_tmp := os.environ.get("MK_TMPDIR"):
        directory = Path(mk_tmp) / name
    elif omd_root := os.environ.get("OMD_ROOT"):
        directory = Path(omd_root) / "tmp" / name
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return directory


def _cache_key(endpoint: dict, secret: str | None = None) -> str:
    """A stable hash over everything that changes this endpoint's response.

    The credential is part of the identity, because for the API-key modes it is
    the ordinary case rather than a pathological one: several rules can poll the
    SAME multi-tenant URL with the same header name and a different key each, and
    the response they get back is per-key. Without it they would share one cache
    file and serve each other's tenant data for the whole TTL.

    Only a hash of the secret is used, never the secret: the cache key ends up in
    a filename, and a SHA-256 of a credential is not one. The secret itself still
    reaches neither the endpoint blob nor the disk.
    """
    identity = json.dumps(
        [
            endpoint.get("url"),
            endpoint.get("method", "GET"),
            _effective_body(endpoint),
            endpoint.get("headers"),
            endpoint.get("auth"),
            # The header / query parameter an API key goes into - a name, not a
            # credential.
            endpoint.get("auth_header"),
            endpoint.get("auth_query"),
            hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else None,
            endpoint.get("verify_cert", True),
            endpoint.get("ca_bundle"),
            endpoint.get("client_cert"),
            endpoint.get("accept_status"),
            endpoint.get("proxy"),
            # The cached body is the MERGED one, so how the pages were followed
            # is part of what it is: a changed collection path or page limit must
            # not be answered from a cache built under the old settings.
            endpoint.get("pagination"),
        ],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _cache_ttl(endpoint: dict) -> float | None:
    """The configured cache TTL in seconds, or ``None`` for "always fetch"."""
    ttl = endpoint.get("cache_ttl")
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
        return None
    return float(ttl) if ttl > 0 else None


def _cache_read(endpoint: dict, ttl: float, secret: str | None = None) -> tuple[bytes, dict] | None:
    """A cached ``(body, meta)`` younger than ``ttl``, else ``None``.

    Anything unreadable or malformed counts as a miss, so a broken cache file
    costs one extra request and nothing else.
    """
    directory = _cache_dir()
    if directory is None:
        return None
    path = directory / f"{_cache_key(endpoint, secret)}.json"
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        stored = float(entry["stored"])
        body = entry["body"].encode("utf-8")
        meta = entry["meta"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not isinstance(meta, dict):
        return None
    age = time.time() - stored
    # A negative age means the clock moved backwards; treat it as a miss rather
    # than serving something we cannot reason about.
    if age < 0 or age > ttl:
        return None
    # 'elapsed' and 'attempts' are dropped deliberately: no request was made, so
    # there is no response time and nothing was retried. Replaying either would
    # report a measurement that never happened, over and over, for the whole TTL -
    # and a stale 'attempts' would hold the endpoint service at the state
    # configured for "a retry was needed" across checks that made no request at
    # all. Status, size and the final URL DO still describe the body being
    # served, so they are kept.
    return body, {**meta, "elapsed": None, "attempts": 1, "from_cache": True, "cache_age": age}


def _cache_write(endpoint: dict, body: bytes, meta: dict, secret: str | None = None) -> None:
    """Store a fresh response. Best effort: a failure must not fail the fetch."""
    directory = _cache_dir()
    if directory is None:
        return
    path = directory / f"{_cache_key(endpoint, secret)}.json"
    entry = {
        "stored": time.time(),
        "body": body.decode("utf-8", "replace"),
        # Facts about the REQUEST, not about the body: replaying them on a cache
        # hit would describe a request that never happened. The reported body and
        # headers are dropped too - they are rebuilt from the cached body on a
        # hit, so a rule that changed its reporting settings meanwhile is honoured.
        "meta": {
            k: v
            for k, v in meta.items()
            if k
            not in (
                "from_cache",
                "cache_age",
                "attempts",
                "body",
                "body_truncated",
                "body_size",
                "headers_reported",
            )
        },
    }
    try:
        # Write-then-rename so a concurrent read never sees a half-written file,
        # and 0600 because a response body can hold anything the API returns.
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(entry), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        _prune_cache(directory)
    except OSError:
        return


def _prune_cache(directory: Path) -> None:
    """Drop cache files nothing will read again (stale keys after a rule edit)."""
    cutoff = time.time() - _CACHE_PRUNE_AFTER
    try:
        entries = list(directory.glob("*.json"))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
        except OSError:
            continue
