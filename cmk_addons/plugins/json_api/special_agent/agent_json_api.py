# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Generic JSON API special agent.

Fetches one or more JSON documents over HTTP(S), extracts the configured fields
by path, and prints a single 'json_api' section (one line) merging the results
of every endpoint. Each endpoint carries its own method/headers/auth/timeout and
its own list of extractions; a single '--endpoint' JSON blob is passed per
endpoint, with its secret (if any) supplied out-of-band as '--secret_<i>'.

An endpoint that cannot be fetched does not fail the whole data source: each of
its configured services is emitted as 'not found' with the fetch error, so only
those services go bad while the other endpoints keep reporting.

The section payload looks like:

    {"results": [{"service": "...", "path": "...", "url": "...",
                  "found": true, "value": "UP", "error": null,
                  "levels_upper": ["fixed", [80, 90]], "levels_lower": null,
                  "match": ["must_match", "UP|ok"]}, ...],
     "endpoints": [{"name": "...", "url": "...", "ok": true, "error": null,
                    "status": 200, "elapsed": 0.031, "size": 412,
                    "final_url": "...", "cert_expiry": 1800000000.0,
                    "from_cache": false}, ...]}

The 'endpoints' list carries one record per configured endpoint - the outcome of
the request itself (HTTP status, how long it took, how much came back) - which
becomes the endpoint's own 'JSON API <name>' service, independently of the fields
extracted from the body.

This module is the entry point (arguments, secrets, one endpoint end to end,
the section). The work is layered below it, each module importing only from the
ones before it: ``paths`` (the path grammar), ``transport`` (session, auth, TLS,
debug output and redaction), ``cache``, ``oauth2``, ``pagination``, ``fetch``
(one endpoint's request, with cache, retries and token refresh) and ``extract``
(document -> service results and host labels).
"""

import argparse
import json
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from cmk.utils import password_store as _legacy_pwstore

from .extract import (
    _context_spec,
    _extract,
    _prefixed,
    _resolve_host_labels,
    _result,
    _service_prefix,
    _shared_service,
)
from .fetch import _fetch
from .transport import _debug

try:
    # Checkmk 2.5+: public convenience API for secret options.
    from cmk.password_store.v1_unstable import parser_add_secret_option, resolve_secret_option

    _HAVE_PWSTORE_V1 = True
except ImportError:
    # Checkmk 2.4: fall back to the internal password store (same on-disk format).
    _HAVE_PWSTORE_V1 = False


def _add_secret_option(
    parser: argparse.ArgumentParser, name: str, help_text: str, required: bool = True
) -> None:
    """Register a secret option, adapting to the available API.

    Both paths end up reading the same server-side-call rendering: a bare
    ``Secret`` becomes ``--<name>-id "<id>:<password_store_file>"``.
    """
    if _HAVE_PWSTORE_V1:
        parser_add_secret_option(parser, long=f"--{name}", help=help_text, required=required)
    else:
        parser.add_argument(f"--{name}-id", required=required, help=help_text)


def _reveal_secret(args: argparse.Namespace, name: str) -> str:
    """Resolve a secret from the parsed args, across Checkmk 2.4 and 2.5+."""
    if _HAVE_PWSTORE_V1:
        return resolve_secret_option(args, name).reveal()
    secret_id, store_file = getattr(args, f"{name}_id").split(":", 1)
    return _legacy_pwstore.lookup(Path(store_file), secret_id)


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    # First pass: learn how many endpoints there are, so we can register one
    # secret option per endpoint index before the real parse.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--endpoint", action="append", default=[])
    known, _ = pre.parse_known_args(argv)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        action="append",
        default=[],
        required=True,
        metavar="JSON",
        help="JSON endpoint spec, repeatable. One '--secret_<i>' may accompany each.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write request/response diagnostics to stderr (never into the section on "
        "stdout). Intended for running the program by hand - e.g. the call copied out "
        "of 'cmk -D <host>' - while building or debugging a rule.",
    )
    for index in range(len(known.endpoint)):
        _add_secret_option(
            parser, f"secret_{index}", f"Secret for endpoint {index}", required=False
        )
    return parser.parse_args(argv)


# Endpoints are fetched concurrently, but with a modest ceiling.
_MAX_FETCH_WORKERS = 8


def _url_without_query(url: str) -> str:
    """``url`` with its query string and fragment removed.

    An API that authenticates via a query parameter (``?api_key=...``) would
    otherwise carry that secret into a service description, which travels much
    further than the service details do: notifications, availability reports, BI
    aggregations, the metric paths on disk. The full URL stays in the endpoint
    service's details, where it belongs.

    Anything that does not parse - or that parses to nothing at all - is returned
    unchanged rather than replaced by an empty item.
    """
    try:
        split = urlsplit(url)
    except ValueError:
        return url
    return urlunsplit(split._replace(query="", fragment="")) or url


def _endpoint_name(endpoint: dict, url: str) -> str:
    """The endpoint's service item: its configured name, else the bare URL."""
    name = endpoint.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return _url_without_query(url)


def _endpoint_record(
    endpoint: dict, url: str, ok: bool, error: str | None, meta: dict | None = None
) -> dict:
    """One 'endpoints' entry: the outcome of the request itself."""
    meta = meta or {}
    return {
        "name": _endpoint_name(endpoint, url),
        # Whether this endpoint prefixes its field service names. The check needs
        # it to name the endpoint's OWN service: an endpoint whose fields read
        # 'JSON <name> Status' wants 'JSON <name> API' rather than 'JSON API
        # <name>', or the one service describing the request sorts away from
        # every service it describes.
        "prefixed": bool(_service_prefix(endpoint)),
        "url": url,
        "ok": ok,
        "error": error,
        "status": meta.get("status"),
        "elapsed": meta.get("elapsed"),
        "size": meta.get("size"),
        "final_url": meta.get("final_url"),
        "cert_expiry": meta.get("cert_expiry"),
        "from_cache": bool(meta.get("from_cache")),
        "cache_age": meta.get("cache_age"),
        # How many attempts the request took (1 = no retry was needed). Reported
        # so a retry policy cannot silently hide an API that is degrading.
        "attempts": meta.get("attempts", 1),
        # Pagination: how many pages were read, how many elements the merged
        # collection holds, and - when a further page existed but was not
        # followed - why not. 1 page and no reason is what an endpoint that does
        # not paginate reports, which is also the default.
        "pages": meta.get("pages", 1),
        "elements": meta.get("elements"),
        "pagination_stopped": meta.get("pagination_stopped"),
        # How a counted collection's end was recognised, where that took an
        # inference (a 404 past the last page, ...). None when it did not.
        "pagination_end": meta.get("pagination_end"),
        # The raw response, for the endpoints that opted in: the body as it came
        # off the wire (capped, secret-stripped) and the response headers
        # (credential-bearing ones masked). None/absent means "not requested",
        # which is the default.
        "body": meta.get("body"),
        "body_truncated": bool(meta.get("body_truncated")),
        "body_size": meta.get("body_size"),
        "headers": meta.get("headers_reported"),
    }


# A '--endpoint' blob that is not a JSON object. Carried INSIDE the endpoint
# dict rather than raised, so the failure travels the same path as every other
# per-endpoint failure and lands on that endpoint's own service.
_BLOB_ERROR = "_blob_error"


def _parse_endpoint(raw: str) -> dict:
    """One '--endpoint' argument as a dict, or a dict carrying why it is not.

    Parsing all of them up front with a bare ``json.loads`` meant one malformed
    blob raised before anything was written: no section at all, so EVERY service
    of EVERY endpoint on the host goes stale - the one outcome the rest of this
    program is written to avoid, and the loudest possible answer to the smallest
    possible cause. Setup cannot produce such a blob, but a hand-written rule or
    a program call edited by hand can, and that is exactly when the operator is
    already debugging something.
    """
    try:
        endpoint = json.loads(raw)
    except ValueError as exc:
        return {_BLOB_ERROR: f"Endpoint configuration is not valid JSON: {exc}"}
    if not isinstance(endpoint, dict):
        return {
            _BLOB_ERROR: f"Endpoint configuration is a {type(endpoint).__name__}, not an object"
        }
    return endpoint


def _process_endpoint(
    args: argparse.Namespace, index: int, endpoint: dict
) -> tuple[list[dict], dict, dict]:
    """Fetch one endpoint: its (extraction results, host labels, own record).

    Any failure is confined to this endpoint: secret resolution, the fetch, and
    the extraction (including a malformed endpoint blob, e.g. one missing 'url')
    all fall back to a 'not found' result carrying the error, so the endpoint's
    own services go bad while the rest of the section stays intact. A failed
    endpoint contributes no host labels, and its record carries the error for its
    own service.
    """
    url = endpoint.get("url", "?")
    debug = getattr(args, "debug", False)
    _debug(debug, f"endpoint {index}: {url}")
    prefix = _service_prefix(endpoint)

    def _fail(error: str, meta: dict | None = None) -> tuple[list[dict], dict, dict]:
        # Prefixed exactly like the success path: a service that renamed itself
        # while the endpoint was down would go stale and its replacement would
        # be undiscovered, which is precisely when the monitoring is needed.
        results = [
            _result(
                {"path": spec.get("path", "?")},
                _prefixed(prefix, _shared_service(spec) or spec.get("service", "?")),
                False,
                None,
                error,
                url,
                label=spec.get("service", "?") if _shared_service(spec) else None,
            )
            for spec in endpoint.get("extractions", [])
            if isinstance(spec, dict)
        ]
        # Guarantee the failure is visible even when the blob carries no usable
        # extractions to hang it on (otherwise it would silently vanish).
        return (
            results or [_result({"path": "?"}, url, False, None, error, url)],
            {},
            _endpoint_record(endpoint, url, False, error, meta),
        )

    if (blob_error := endpoint.get(_BLOB_ERROR)) is not None:
        # Unparseable: there is no URL, no name and no extraction to hang this
        # on, so it becomes the one service the endpoint always has.
        _debug(debug, f"  {blob_error}")
        return _fail(str(blob_error))

    try:
        secret = _reveal_secret(args, f"secret_{index}") if endpoint.get("auth") else None
    except Exception as exc:  # e.g. a stale/missing password-store reference
        return _fail(f"Secret resolution failed: {exc}")

    try:
        document, error, meta = _fetch(endpoint, secret, debug)
        if error is not None:
            return _fail(error, meta)
        results = _extract(
            document,
            endpoint.get("extractions", []),
            url,
            meta.get("headers"),
            prefix,
            _context_spec(endpoint),
            secret,
        )
        host_labels = _resolve_host_labels(endpoint.get("host_labels", []), document)
        if debug:
            for result in results:
                outcome = (
                    f"found: {result['value']!r}"
                    if result["found"]
                    else f"NOT FOUND: {result['error']}"
                )
                _debug(debug, f"  service {result['service']!r} <- {result['path']} -> {outcome}")
            for key, value in host_labels.items():
                _debug(debug, f"  host label json_api/{key} = {value}")
        return (results, host_labels, _endpoint_record(endpoint, url, True, None, meta))
    except Exception as exc:  # malformed endpoint blob, unexpected extraction error, ...
        return _fail(f"Endpoint processing failed: {exc}")


def _split_by_host(
    results: list[dict],
) -> tuple[list[dict], dict[str, list[dict]], dict[str, dict[str, str]]]:
    """Partition results into the polling host's own and per-piggyback-host.

    ``host`` and ``host_labels`` are internal routing hints, not part of the
    section format, so they are stripped here: the check sees identical payloads
    either way and needs no knowledge of piggybacking at all. Insertion order is
    preserved so the output stays deterministic across runs.

    The third return value is the host labels each piggyback host earned, merged
    across every extraction that placed a service on it (later wins per key) -
    they describe the HOST, so two fields of the same element must not each
    write their own section-level label map.
    """
    own: list[dict] = []
    piggybacked: dict[str, list[dict]] = {}
    labels: dict[str, dict[str, str]] = {}
    for result in results:
        host = result.pop("host", None)
        host_labels = result.pop("host_labels", None) or {}
        if host is None:
            own.append(result)
            continue
        piggybacked.setdefault(host, []).append(result)
        if host_labels:
            labels.setdefault(host, {}).update(host_labels)
    return own, piggybacked, labels


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(sys.argv[1:] if argv is None else argv)

    endpoints = [_parse_endpoint(raw) for raw in args.endpoint]
    results: list[dict] = []
    host_labels: dict[str, str] = {}
    endpoint_records: list[dict] = []
    if endpoints:
        # Fetch endpoints concurrently so total runtime is the slowest endpoint,
        # not the sum. pool.map preserves input order, keeping the merged section
        # (and thus service-name disambiguation) deterministic across runs.
        with ThreadPoolExecutor(max_workers=min(len(endpoints), _MAX_FETCH_WORKERS)) as pool:
            per_endpoint = pool.map(
                lambda item: _process_endpoint(args, item[0], item[1]),
                enumerate(endpoints),
            )
            for endpoint_results, endpoint_host_labels, record in per_endpoint:
                results.extend(endpoint_results)
                host_labels.update(endpoint_host_labels)  # later endpoints win per key
                endpoint_records.append(record)

    own, piggybacked, piggyback_labels = _split_by_host(results)
    # The polling host's own section: the endpoint records live here too, because
    # they describe the REQUEST, which belongs to the host holding the rule - not
    # to any element the response happened to contain.
    payload = {"results": own, "host_labels": host_labels, "endpoints": endpoint_records}
    sys.stdout.write("<<<json_api:sep(0)>>>\n")
    sys.stdout.write(json.dumps(payload) + "\n")
    for host, host_results in piggybacked.items():
        # One section per piggyback host, in the same format - so the check parses
        # it with no idea it was piggybacked, and every field feature (levels,
        # match, counter, timestamp, ...) works there unchanged.
        sys.stdout.write(f"<<<<{host}>>>>\n")
        sys.stdout.write("<<<json_api:sep(0)>>>\n")
        sys.stdout.write(
            json.dumps({"results": host_results, "host_labels": piggyback_labels.get(host, {})})
            + "\n"
        )
    if piggybacked:
        # Close the last piggyback section, or everything after it in the agent
        # output would be attributed to that host.
        sys.stdout.write("<<<<>>>>\n")
    # Flush explicitly so the section is delivered no matter how stdout is
    # connected. Checkmk always reads the agent through a pipe (block-buffered,
    # flushed at exit), but a consultant copying the program call out of
    # `cmk -D <host>` and running it by hand on a TTY otherwise sees nothing
    # until the buffer happens to flush — making a working agent look broken.
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
