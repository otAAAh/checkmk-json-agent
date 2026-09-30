# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Following an API's pagination and merging the pages into one collection."""

import copy
import json
from collections.abc import Callable, Iterable
from urllib.parse import quote_plus, unquote_plus, urljoin, urlsplit, urlunsplit

import requests

from .paths import _HEADER_PREFIX, _resolve_header, _resolve_path, _strip_root
from .transport import (
    _MAX_RESPONSE_BYTES,
    _debug,
    _read_capped,
    _ResponseTooLarge,
    _retryable_status,
)

# A paginated collection arrives one page at a time, and the agent asks for one
# page - so a '[*]' expansion or an aggregation over it silently describes the
# FIRST page and nothing else. 'count' over a queue that pages at 25 reports 25
# however long the queue is: an answer that is wrong without saying so, which is
# worse than an error. Following the pages is opt-in per endpoint because it
# costs requests, but it is the only way that answer becomes true.
#
# However the rule is written, the agent never makes more than this many
# requests for one endpoint. A check that walks a thousand pages is an outage of
# its own - Checkmk kills a special agent that overruns - and a hand-written
# rule (or a blob built by something other than the ruleset) is not bound by the
# ruleset's own range.
_MAX_PAGES = 100


_DEFAULT_MAX_PAGES = 10


def _pagination_spec(endpoint: dict) -> dict | None:
    """The endpoint's 'follow pagination' settings, or None when off."""
    spec = endpoint.get("pagination")
    return spec if isinstance(spec, dict) else None


def _next_source(spec: dict) -> tuple[str, str] | None:
    """Where the next page's URL comes from: ``("body", <path>)`` or ``("link_header", "")``.

    The CascadingSingleChoice value as the rule stores it (a list once it has
    been through JSON). Anything else - a missing choice, an empty body path -
    means pagination cannot be followed at all, which is reported as None rather
    than guessed at. The two modes in which the agent BUILDS the next URL itself
    are not a link source and answer None here; see ``_counted_source``.
    """
    raw = spec.get("next")
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    mode, value = raw
    if mode == "body" and isinstance(value, str) and value.strip():
        return "body", _strip_root(value)
    if mode == "link_header":
        return "link_header", ""
    return None


def _positive_int(raw: object) -> int | None:
    """A whole number of at least 1, or None - a bool is not a number here."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 1:
        return int(raw)
    return None


def _counted_source(spec: dict) -> dict | None:
    """The page-number / offset settings, normalized, or None for a link source.

    Not every API offers a next-page link. Many only take the position as a
    query parameter - '?page=3', or '?offset=50&limit=25' - and leave counting
    to the client. For those the agent does the counting: ``page_number`` adds
    one per page from ``start``; ``offset`` advances by the number of elements
    the previous page actually carried (not by the page size it asked for, which
    an API is free to lower).

    What ends it, since there is no absent link to say so: a page whose
    collection is empty, a page shorter than ``page_size`` AND than a page before
    it (see ``_counted_done``), or the elements read reaching the number at
    ``total`` (when the rule names a path to one). Without those, the last page
    is only known by the request after it - which an API may also answer with a
    404, or with the last page once more (see ``_follow_pagination``). One extra
    request, but never a wrong answer.
    """
    raw = spec.get("next")
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    mode, value = raw
    if mode not in ("page_number", "offset") or not isinstance(value, dict):
        return None
    parameter = value.get("parameter")
    if not (isinstance(parameter, str) and parameter.strip()):
        return None
    start = value.get("start")
    size_parameter = value.get("size_parameter")
    total = value.get("total")
    return {
        "mode": mode,
        "parameter": parameter.strip(),
        # Both count from where the API does. A page number from 1 almost
        # everywhere (0 for the zero-based ones); an offset from 0, except for the
        # APIs that number the elements from 1 (SCIM's 'startIndex') - where
        # starting at 0 reads one element twice on every page.
        "start": (
            int(start)
            if isinstance(start, (int, float)) and not isinstance(start, bool) and start >= 0
            else (1 if mode == "page_number" else 0)
        ),
        "page_size": _positive_int(value.get("page_size")),
        "size_parameter": (
            size_parameter.strip()
            if isinstance(size_parameter, str) and size_parameter.strip()
            else None
        ),
        "total": _strip_root(total) if isinstance(total, str) and total.strip() else None,
    }


def _with_query(url: str, values: dict[str, str], drop: Iterable[str] = ()) -> str:
    """``url`` with each of ``values`` set as a query parameter, replacing any.

    Built on the raw query string rather than a parse-and-reencode round trip, so
    every parameter the rule wrote that is NOT being set goes out byte for byte as
    written - an API that signs its query string, or cares about '%20' versus
    '+', sees what it was configured with. ``drop`` removes parameters without
    replacing them: the API key's, which requests adds again at send time.
    """
    split = urlsplit(url)
    names = {*values, *drop}
    kept = [
        part
        for part in split.query.split("&")
        if part and unquote_plus(part.split("=", 1)[0]) not in names
    ]
    kept += [f"{quote_plus(name)}={quote_plus(value)}" for name, value in values.items()]
    return urlunsplit(split._replace(query="&".join(kept)))


# The answers to a request past a counted collection's last page that mean 'no
# such page' rather than a failure: 404 Not Found (Django REST Framework's
# 'Invalid page.', and most hand-written APIs) and 416 Range Not Satisfiable (an
# offset beyond the end). Deliberately no 400: an API refusing to page past a
# window (Elasticsearch's 10000) answers 400 with the rest still there.
_PAST_THE_END_STATUSES = frozenset({404, 416})


def _no_further_page(found: bool, addition: object, container: object) -> bool:
    """Whether a counted page's collection says 'there is none' in another way.

    No collection at all, a null one, or an empty container of the other kind
    (``{}`` where the pages carry a list). An empty collection of the SAME kind
    is merged like any page and ends it through ``_counted_done``.
    """
    if not found or addition is None:
        return True
    return (
        isinstance(addition, (list, dict))
        and not addition
        and isinstance(addition, list) != isinstance(container, list)
    )


def _counted_params(counted: dict, position: int) -> dict[str, str]:
    """The query parameters that request the page at ``position``."""
    params = {counted["parameter"]: str(position)}
    if counted["size_parameter"] is not None and counted["page_size"] is not None:
        params[counted["size_parameter"]] = str(counted["page_size"])
    return params


def _first_page_url(endpoint: dict) -> str:
    """The URL the first request goes to.

    The rule's own URL - except where the agent counts the pages, and then the
    first page is asked for with the same parameters as every later one. Leaving
    them to the configured URL would let the first page come back at the API's
    default size while the following ones are asked for at another, so offsets
    and the short-page test would disagree about what a page is.
    """
    # A missing URL fails here exactly as the request itself would have.
    url = endpoint["url"]
    spec = _pagination_spec(endpoint)
    counted = _counted_source(spec) if spec is not None else None
    if counted is None:
        return url
    return _with_query(url, _counted_params(counted, counted["start"]))


def _counted_done(
    counted: dict, document: object, received: int, read: int, largest_before: int
) -> bool:
    """Whether the page just read was the last one.

    ``received`` is how many elements that page carried, ``read`` how many all
    pages have carried so far, ``largest_before`` the most any EARLIER page
    carried (0 for the first page). A 'total' that is absent or not a number
    decides nothing: the other signals still end it.

    A short page is only the last one if it is short of what the API actually
    sent before, not just of the page size the rule asked for: an API is free to
    cap it ('limit=500' answered with 100), and treating its first capped page
    as the last would report 100 of 2000 elements as the whole collection. So
    the first page never ends it by being short - at most one extra request,
    never an undercount.
    """
    if received == 0:
        return True
    if (
        counted["page_size"] is not None
        and received < counted["page_size"]
        and received < largest_before
    ):
        return True
    if counted["total"] is not None:
        found, total = _resolve_path(document, counted["total"])
        if (
            found
            and isinstance(total, (int, float))
            and not isinstance(total, bool)
            and read >= total
        ):
            return True
    return False


def _items_path(spec: dict) -> str | None:
    """The path to the collection each page carries, '' for the response root.

    '$' (or '$.') is how a response that IS the array says so; ``_strip_root``
    turns it into the empty path, which ``_resolve_path`` resolves to the whole
    document.
    """
    raw = spec.get("items")
    return _strip_root(raw) if isinstance(raw, str) else None


def _pagination_limits(spec: dict) -> tuple[int, int | None]:
    """``(max pages, max elements)``, the page count clamped to ``_MAX_PAGES``."""
    pages = _positive_int(spec.get("max_pages")) or _DEFAULT_MAX_PAGES
    return min(pages, _MAX_PAGES), _positive_int(spec.get("max_elements"))


def _container_len(container: object) -> int:
    """How many elements the merged collection holds so far."""
    return len(container) if isinstance(container, (list, dict)) else 0


def _next_candidate(source: tuple[str, str], document: object, headers: object) -> str | None:
    """The next page's URL as this page states it, or None for "there is none".

    'There is none' is how pagination ENDS, so an absent field, a JSON ``null``
    and an empty string all mean the same thing: the last page has been read.
    """
    mode, path = source
    if mode == "body":
        found, value = _resolve_path(document, path)
        if found and isinstance(value, str) and value.strip():
            return value.strip()
        return None
    # RFC 8288: Link: <https://api/jobs?page=2>; rel="next", <...>; rel="last".
    # Parsed with requests' own parser rather than a regex of ours - it is the
    # same code that fills response.links, and the header is fiddlier than it
    # looks (several links per header, quoted parameters, relative URLs).
    found, header = _resolve_header(headers, f"{_HEADER_PREFIX}Link")
    if not found or not isinstance(header, str):
        return None
    for link in requests.utils.parse_header_links(header):
        if link.get("rel") == "next" and (url := link.get("url", "").strip()):
            return url
    return None


def _next_target(
    candidate: str, current_url: str, origin: str, seen: set[str]
) -> tuple[str | None, str | None]:
    """``(url to fetch, reason not to)`` for a next-page link.

    Relative links are the norm ('/api/v1/jobs?page=2'), so the candidate is
    resolved against the page it came from.

    Two links are refused rather than followed. One on ANOTHER HOST would make
    the response body decide where the Checkmk server sends an authenticated
    request - the same SSRF shape the 'follow redirects' switch exists to
    close - and the endpoint's credentials travel with every page. One already
    fetched means the API is pointing at itself, which would otherwise spend the
    whole page budget re-reading one page. Both stop pagination with a reason the
    endpoint's own service reports; neither fails the endpoint, so the data that
    WAS read still monitors the API.
    """
    url = urljoin(current_url, candidate)
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None, f"the next page link is not an http(s) URL ({candidate})"
    if parsed.netloc.lower() != origin:
        return None, f"the next page link points to another host ({parsed.netloc})"
    if url in seen:
        return None, "the API repeated a page link (pagination loop)"
    return url, None


def _merge_page(container: object, addition: object) -> str | None:
    """Append one page's collection to the first page's, in place; error if any.

    In place because ``container`` is the object inside the first page's document
    that every extraction will read: extending it is what makes the whole
    collection visible to a '[*]' wildcard and an aggregation, while the rest of
    the document (a 'total', a 'generated_at') stays the first page's.

    A JSON object pages by key rather than by position, so the two container
    kinds the rest of the agent already treats alike are both merged - and a page
    whose collection is neither, or is not the same kind as the first page's, is
    an error: silently skipping it would under-count exactly the way unfollowed
    pagination does.
    """
    if isinstance(container, list) and isinstance(addition, list):
        container.extend(addition)
        return None
    if isinstance(container, dict) and isinstance(addition, dict):
        container.update(addition)
        return None
    return (
        f"its collection is a {type(addition).__name__}, "
        f"but the first page's is a {type(container).__name__}"
    )


def _follow_pagination(
    session: requests.Session,
    method: str,
    request_kwargs: dict,
    accepted: set[int],
    endpoint: dict,
    spec: dict,
    document: object,
    meta: dict,
    headers: object,
    read_bytes: int,
    scrub: Callable[[str], str],
    debug: bool,
    served_url: str = "",
) -> tuple[str | None, bool]:
    """Read the remaining pages and merge them into ``document``.

    Returns ``(error, retryable)`` like ``_attempt`` does, and records what it
    took in ``meta``: how many pages were read, how large the merged collection
    is, and - where a next page existed but was not followed - why not.

    A page that cannot be read fails the whole endpoint rather than being
    dropped. Half a collection looks exactly like a shrinking one: 'unhealthy
    nodes: 0' because page 2 timed out is the failure mode this whole feature
    exists to remove, so it is reported as an error the endpoint's services can
    show instead.

    Every page reuses the first page's session, headers, authentication and
    timeout - the same request, at a different URL - so a cursor that only the
    API understands needs no configuration here.

    ``served_url`` is the URL the first page actually came from (``response.url``
    after any redirects), which is what relative links resolve against and what
    decides which host the following pages may come from.
    """
    source = _next_source(spec)
    counted = _counted_source(spec)
    items = _items_path(spec)
    if (source is None and counted is None) or items is None:
        # The rule asked for pagination without saying where the next page or the
        # collection is. Reported rather than ignored: the services would
        # otherwise describe one page while the rule claims to follow them all.
        return "Pagination is configured without a next-page link or a collection path", False
    found, container = _resolve_path(document, items)
    if not found:
        return f"Pagination: the response has no collection at '{items or '$'}'", False
    if not isinstance(container, (list, dict)):
        return (
            f"Pagination: '{items or '$'}' is a {type(container).__name__}, "
            "not a collection that pages can be appended to"
        ), False

    max_pages, max_elements = _pagination_limits(spec)
    # Where the FIRST PAGE CAME FROM, not what the rule asked for. They differ
    # when the endpoint redirects, and then the configured URL is the wrong
    # answer to both questions this needs one for: a relative next link
    # ('?page=2') resolves against the page that offered it, and the host the
    # pages may come from is the host actually serving them. Using the rule's
    # URL instead sent page 2 to the pre-redirect path (an endpoint-wide failure
    # on an API that works) and refused every absolute link a cross-host
    # redirect produced, reporting the collection as incomplete.
    #
    # It relaxes nothing: requests only followed that redirect because the rule
    # allows redirects, and it is the same hop the credentials already travelled.
    url = endpoint.get("url", "")
    served = served_url or url
    origin = urlsplit(served).netloc.lower()
    # Both, so an API linking back to the URL the rule names is still recognised
    # as the loop it is.
    seen = {url, served}
    current_url = served
    page_document = document
    page_headers = headers
    pages = 1
    total_bytes = read_bytes
    stopped: str | None = None
    # Where the agent counts the pages: the page just read (a copy - the first
    # page's collection is the container the others are merged into), how many
    # elements it and all pages so far carried, and the position it was asked
    # for. The API key's parameter is dropped from the URL the pages are counted
    # from: 'served' has it because requests put it there, and requests adds it
    # again to every page.
    previous: object = copy.copy(container)
    received = read = _container_len(container)
    largest_before = 0
    position = counted["start"] if counted is not None else 0
    # How the end was recognised, where that is worth saying: an API answering
    # the request after its last page with a 404, no collection, or the last page
    # once more. The collection is complete - this is not 'pagination_stopped' -
    # but it is an inference the operator should be able to see.
    ended: str | None = None
    auth_names = tuple(request_kwargs.get("params") or ())

    while True:
        if counted is not None:
            if _counted_done(counted, page_document, received, read, largest_before):
                break
            position += 1 if counted["mode"] == "page_number" else received
            candidate = _with_query(served, _counted_params(counted, position), auth_names)
        else:
            link = None if source is None else _next_candidate(source, page_document, page_headers)
            if link is None:
                break
            candidate = link
        # The caps are only consulted once a further page actually exists, so an
        # API with exactly as many pages as the limit is read whole and reported
        # as complete - a truncation note has to mean something was left behind.
        # Where the agent counts, 'exists' is as much as it can know: the page just
        # read was full, and no total says it was the last.
        if pages >= max_pages:
            stopped = f"the page limit ({max_pages}) was reached"
            break
        if max_elements is not None and _container_len(container) >= max_elements:
            stopped = f"the element limit ({max_elements}) was reached"
            break
        next_url: str | None
        if counted is not None:
            # Built from the URL the API served, so it is on that host by
            # construction and needs neither of a link's checks. The loop a link
            # check catches has its own counterpart below: an API that ignores
            # the parameter, recognised by what it sends back.
            next_url = candidate
        else:
            next_url, reason = _next_target(candidate, current_url, origin, seen)
            if next_url is None:
                stopped = reason
                break
        page = pages + 1
        _debug(debug, f"  page {page}: {method} {scrub(next_url)}")
        try:
            response = session.request(method, next_url, **request_kwargs)
            with response:
                status = response.status_code
                if not (200 <= status < 300 or status in accepted):
                    reason_phrase = getattr(response, "reason", "") or ""
                    if counted is not None and status in _PAST_THE_END_STATUSES:
                        # Where the agent counts, it asks for the page after the
                        # last one unless something told it not to, and many APIs
                        # answer that with 'no such page' (Django REST
                        # Framework's 404 'Invalid page.'). Page 1 succeeded with
                        # the same parameters, so this is the end of the
                        # collection, not a broken endpoint. Only these codes: a
                        # 400 is as likely a window the API will not page past
                        # (with the rest still there), a 401/403/5xx a real
                        # failure.
                        ended = (
                            f"page {page} answered HTTP {status}"
                            f"{f' {reason_phrase}' if reason_phrase else ''}"
                        )
                        break
                    return (
                        f"Page {page} failed: HTTP {status}"
                        f"{f' {reason_phrase}' if reason_phrase else ''}",
                        _retryable_status(status),
                    )
                # The 50 MiB ceiling is a budget for the whole endpoint, not per
                # page: a hundred pages of half a megabyte are just as able to
                # exhaust the monitoring host as one huge body.
                raw = _read_capped(response, _MAX_RESPONSE_BYTES - total_bytes)
                page_headers = dict(response.headers)
        except _ResponseTooLarge:
            return (
                f"Pagination exceeds the {_MAX_RESPONSE_BYTES}-byte limit at page {page}",
                False,
            )
        except requests.exceptions.RequestException as exc:
            return f"Page {page} failed: Request failed: {scrub(str(exc))}", True
        try:
            page_document = json.loads(raw)
        except ValueError as exc:
            return f"Page {page} is not valid JSON: {exc}", False
        found, addition = _resolve_path(page_document, items)
        if counted is not None and _no_further_page(found, addition, container):
            # Past the end, some APIs drop the collection or null it instead of
            # sending an empty one. Where the agent counts, that request was a
            # guess at a further page, so this is the answer 'there is none' -
            # not a malformed page. (A link is the API's own promise of a page,
            # so there it still fails.)
            ended = (
                f"page {page} carried an empty {type(addition).__name__}"
                if found and addition is not None
                else f"page {page} has no collection at '{items or '$'}'"
            )
            break
        if not found:
            return f"Page {page} has no collection at '{items or '$'}'", False
        if counted is not None and addition and addition == previous:
            if pages == 1:
                # Page 2 is page 1 again: the API does not read the parameter (a
                # misspelt name is the usual reason) and would answer the first
                # page until the page limit, every copy merged in. Not merged, so
                # what is reported is the page that is real - and said to be
                # incomplete, since whether there is more is exactly what cannot
                # be told.
                stopped = (
                    f"page {page} repeated page {pages} - the API does not seem to "
                    f"read the '{counted['parameter']}' parameter"
                )
            else:
                # Page 2 differed from page 1, so the parameter IS read. A repeat
                # later is an API answering past its end with the last page once
                # more (a clamped page number): the end, not merged a second time.
                ended = f"page {page} repeated page {pages}"
            break
        if (mismatch := _merge_page(container, addition)) is not None:
            return f"Page {page} cannot be merged: {mismatch}", False
        total_bytes += len(raw)
        pages = page
        seen.add(next_url)
        current_url = next_url
        previous = addition
        largest_before = max(largest_before, received)
        received = _container_len(addition)
        read += received

    if stopped is not None:
        _debug(debug, f"  pagination stopped after {pages} pages: {stopped}")
    if ended is not None:
        _debug(debug, f"  end of the collection after {pages} pages: {ended}")
    meta["pages"] = pages
    meta["elements"] = _container_len(container)
    meta["pagination_stopped"] = stopped
    meta["pagination_end"] = ended
    # What the endpoint really read, across every page: the response size is a
    # cost the operator is watching, and one page of it is not the cost.
    meta["size"] = total_bytes
    return None, False
