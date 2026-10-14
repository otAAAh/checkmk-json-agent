#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""One endpoint of the rule - connection, request, response handling, its fields
and host labels - and the checks across the endpoints of one rule.
"""

from collections import Counter

from cmk.rulesets.v1 import Help, Label, Message, Title
from cmk.rulesets.v1.form_specs import (
    BooleanChoice,
    CascadingSingleChoice,
    CascadingSingleChoiceElement,
    DefaultValue,
    DictElement,
    Dictionary,
    FieldSize,
    FixedValue,
    InputHint,
    Integer,
    List,
    Proxy,
    SingleChoice,
    SingleChoiceElement,
    String,
    TimeMagnitude,
    TimeSpan,
    migrate_to_proxy,
    validators,
)

from .authentication import _authentication
from .common import _element_filter, _validate_url
from .extraction import _extraction
from .labels import _validate_host_label
from .metrics import _validate_unique_group_metrics
from .pagination import _counted_pages, _validate_pagination


def _unique_or_duplicates(value: object, key: str) -> list[str]:
    """The non-empty values of ``key`` across the endpoints that occur twice."""
    if not isinstance(value, (list, tuple)):
        return []
    counts = Counter(
        ep[key].strip()
        for ep in value
        if isinstance(ep, dict) and isinstance(ep.get(key), str) and ep[key].strip()
    )
    return sorted(item for item, count in counts.items() if count > 1)


def validate_unique_endpoints(value: object) -> None:
    # No two endpoints may target the same URL — duplicates would create
    # colliding services from the same source.
    if duplicates := _unique_or_duplicates(value, "url"):
        raise validators.ValidationError(
            Message("Each endpoint URL must be unique. Duplicated: %s") % ", ".join(duplicates)
        )
    # Nor may two carry the same name: it becomes the item of the endpoint's own
    # service, and a collision there can only be resolved positionally at runtime
    # ("<name> (2)"). Reordering the endpoints would then move the suffix to the
    # other one, silently swapping two services' history, downtimes and
    # acknowledgements with nothing in the UI hinting that anything changed.
    if duplicates := _unique_or_duplicates(value, "name"):
        raise validators.ValidationError(
            Message("Each endpoint name must be unique. Duplicated: %s") % ", ".join(duplicates)
        )


def _validate_endpoint(value: object) -> None:
    if not isinstance(value, dict):
        return
    # Prefixing needs something to prefix WITH. The agent deliberately does not
    # fall back to the URL (it would put a URL, query string and all, into every
    # service description), so the option would silently do nothing - report it
    # here instead of letting the rule save and look enabled.
    if value.get("service_prefix"):
        name = value.get("name")
        if not (isinstance(name, str) and name.strip()):
            raise validators.ValidationError(
                Message(
                    "Prefixing the field service names needs an endpoint name. "
                    "Set 'Endpoint name', or turn the prefix off."
                )
            )
    # An API key header configured *twice* - once as authentication, once as a
    # plain additional header - is ambiguous: one silently overwrites the other,
    # and which one wins is an implementation detail. Rejecting it here also
    # stops the clear-text copy this feature exists to remove from being left
    # behind next to the password-store one.
    auth = value.get("auth")
    if not (isinstance(auth, (tuple, list)) and len(auth) == 2 and auth[0] == "auth_header"):
        return
    spec = auth[1]
    header = spec.get("header") if isinstance(spec, dict) else None
    if not isinstance(header, str) or not header.strip():
        return
    headers = value.get("headers")
    if not isinstance(headers, (list, tuple)):
        return
    for entry in headers:
        name = entry.get("name") if isinstance(entry, dict) else None
        if isinstance(name, str) and name.strip().lower() == header.strip().lower():
            raise validators.ValidationError(
                Message(
                    "The header '%s' carries the API key and must not also be set "
                    "under 'Additional request headers'."
                )
                % header.strip()
            )


def endpoint_form() -> Dictionary:
    return Dictionary(
        title=Title("Endpoint"),
        custom_validate=(_validate_endpoint,),
        elements={
            "name": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Endpoint name"),
                    macro_support=True,
                    help_text=Help(
                        "Optional short name for this endpoint, e.g. 'frontend'. It "
                        "names the endpoint's own service - 'JSON API <name>', which "
                        "reports the HTTP status and the response time of the "
                        "request. Without a name the URL is used. The field service "
                        "names keep their plain names unless 'Prefix the field "
                        "service names' below is enabled."
                    ),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "service_prefix": DictElement(
                required=True,
                parameter_form=BooleanChoice(
                    title=Title("Field service names"),
                    label=Label("Prefix the field service names with the endpoint name"),
                    help_text=Help(
                        "Off by default. Two endpoints extracting the same fields "
                        "produce two services with the same name - 'JSON Status' "
                        "twice, the second one disambiguated to 'JSON Status (2)' - "
                        "and nothing in either name says which application it "
                        "belongs to. Turn this on and this endpoint's field "
                        "services are named after it instead: with the endpoint "
                        "named 'app1-health', 'JSON Status' becomes 'JSON "
                        "app1-health Status'. Requires an endpoint name. The "
                        "endpoint's own service follows, from 'JSON API <name>' to "
                        "'JSON <name> API', so that it sorts together with the "
                        "services it describes instead of with every other "
                        "endpoint's status service. Enabling this RENAMES this "
                        "endpoint's services: the old ones become stale and the "
                        "renamed ones have to be discovered, so do it deliberately "
                        "and re-run a service discovery afterwards."
                    ),
                    prefill=DefaultValue(False),
                ),
            ),
            "url": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("URL"),
                    field_size=FieldSize.LARGE,
                    macro_support=True,
                    help_text=Help(
                        "Full URL of the JSON endpoint, including scheme, "
                        "e.g. 'https://app.example.com/actuator/health'."
                    ),
                    custom_validate=(validators.LengthInRange(min_value=1), _validate_url),
                ),
            ),
            "method": DictElement(
                required=True,
                parameter_form=SingleChoice(
                    title=Title("HTTP method"),
                    elements=[
                        SingleChoiceElement("GET", Title("GET")),
                        SingleChoiceElement("POST", Title("POST")),
                    ],
                    prefill=DefaultValue("GET"),
                ),
            ),
            "body": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Request body (for POST)"),
                    field_size=FieldSize.LARGE,
                    macro_support=True,
                ),
            ),
            "headers": DictElement(
                required=False,
                parameter_form=List(
                    title=Title("Additional request headers"),
                    help_text=Help(
                        "Sent with every request to this endpoint. Values are stored "
                        "in clear text and travel on the agent's command line, so an "
                        "API key belongs under 'Authentication' - not here."
                    ),
                    element_template=Dictionary(
                        elements={
                            "name": DictElement(
                                required=True,
                                parameter_form=String(title=Title("Header name")),
                            ),
                            "value": DictElement(
                                required=True,
                                parameter_form=String(
                                    title=Title("Header value"),
                                    field_size=FieldSize.LARGE,
                                    macro_support=True,
                                ),
                            ),
                        }
                    ),
                ),
            ),
            "auth": DictElement(
                required=False,
                parameter_form=_authentication(),
            ),
            "verify_cert": DictElement(
                required=True,
                parameter_form=BooleanChoice(
                    title=Title("TLS certificate verification"),
                    label=Label("Verify the TLS certificate"),
                    help_text=Help("Disabling certificate verification is insecure."),
                    prefill=DefaultValue(True),
                ),
            ),
            "ca_bundle": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Custom CA bundle file"),
                    field_size=FieldSize.LARGE,
                    help_text=Help(
                        "Path on the Checkmk server to a PEM file holding the CA "
                        "certificate(s) to verify the server's certificate against. "
                        "Use this to trust an internal or private CA without turning "
                        "verification off. Ignored when TLS verification is disabled."
                    ),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "client_cert": DictElement(
                required=False,
                parameter_form=Dictionary(
                    title=Title("Client certificate (mutual TLS)"),
                    help_text=Help(
                        "Present a client certificate to the server (mutual TLS). "
                        "The files must exist on the Checkmk server. The private "
                        "key must be unencrypted."
                    ),
                    elements={
                        "cert": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Client certificate file"),
                                field_size=FieldSize.LARGE,
                                help_text=Help(
                                    "Path to the client certificate (PEM). If the "
                                    "file also contains the private key, leave the "
                                    "key field empty."
                                ),
                                custom_validate=(validators.LengthInRange(min_value=1),),
                            ),
                        ),
                        "key": DictElement(
                            required=False,
                            parameter_form=String(
                                title=Title("Private key file"),
                                field_size=FieldSize.LARGE,
                                help_text=Help(
                                    "Path to the client private key (PEM), if it is "
                                    "not bundled with the certificate."
                                ),
                                custom_validate=(validators.LengthInRange(min_value=1),),
                            ),
                        ),
                    },
                ),
            ),
            "follow_redirects": DictElement(
                required=True,
                parameter_form=BooleanChoice(
                    title=Title("HTTP redirects"),
                    label=Label("Follow HTTP redirects"),
                    help_text=Help(
                        "On by default. Disable in locked-down environments to "
                        "harden against SSRF: a target that redirects (e.g. 3xx to "
                        "an internal address) will then fail instead of being "
                        "followed to the redirect location."
                    ),
                    prefill=DefaultValue(True),
                ),
            ),
            "timeout": DictElement(
                required=False,
                parameter_form=TimeSpan(
                    title=Title("Request timeout"),
                    displayed_magnitudes=[TimeMagnitude.SECOND, TimeMagnitude.MILLISECOND],
                    help_text=Help("Per-request timeout. Defaults to 30 seconds."),
                    prefill=DefaultValue(30.0),
                    custom_validate=(validators.NumberInRange(min_value=0.1),),
                ),
            ),
            "cache_ttl": DictElement(
                required=False,
                parameter_form=TimeSpan(
                    title=Title("Re-read at most every"),
                    displayed_magnitudes=[
                        TimeMagnitude.HOUR,
                        TimeMagnitude.MINUTE,
                        TimeMagnitude.SECOND,
                    ],
                    help_text=Help(
                        "Reuse the last response for this endpoint instead of "
                        "requesting it again, as long as it is younger than this. "
                        "For APIs with a request quota, expensive endpoints, or a "
                        "rule shared across many hosts - anything where the "
                        "request RATE is the problem rather than the freshness of "
                        "the data. Without it, every check interval on every host "
                        "issues a request, which can exhaust a quota and turn the "
                        "monitoring itself into the outage. Only a response that "
                        "parsed as JSON is cached, and a failing request is never "
                        "answered from an expired cache - a real outage must not "
                        "hide behind stale data. While a cached body is served the "
                        "endpoint's own service says so and reports no response "
                        "time, because no request was made. Leave unset to always "
                        "fetch fresh."
                    ),
                    prefill=InputHint(300.0),
                    custom_validate=(validators.NumberInRange(min_value=1.0),),
                ),
            ),
            "retry": DictElement(
                required=False,
                parameter_form=Dictionary(
                    title=Title("Retry a failed request"),
                    help_text=Help(
                        "Repeat this endpoint's request when it fails in a way a "
                        "repeat could fix - a connection error, a timeout, or an "
                        "HTTP 429 / 5xx - so a load balancer dropping connections "
                        "for a second during a rolling restart does not become a "
                        "CRIT and a notification. A 4xx, a body that is not JSON "
                        "and an oversized response are never retried: repeating "
                        "them would only burn time. Nothing is hidden - the "
                        "endpoint's own service reports that a retry was needed, "
                        "and can be told to go WARN when one is. Every attempt "
                        "costs wall-clock time inside the check: the worst case is "
                        "(1 + retries) times the request timeout, plus the waiting "
                        "time between attempts. Off by default."
                    ),
                    elements={
                        "attempts": DictElement(
                            required=True,
                            parameter_form=Integer(
                                title=Title("Number of retries"),
                                help_text=Help("Extra attempts after the first one failed."),
                                prefill=DefaultValue(2),
                                custom_validate=(
                                    validators.NumberInRange(min_value=1, max_value=5),
                                ),
                            ),
                        ),
                        "backoff": DictElement(
                            required=True,
                            parameter_form=TimeSpan(
                                title=Title("Wait before retrying"),
                                displayed_magnitudes=[
                                    TimeMagnitude.SECOND,
                                    TimeMagnitude.MILLISECOND,
                                ],
                                help_text=Help(
                                    "Waiting time before the first retry, doubled "
                                    "for each further one. The total waiting time "
                                    "is capped at 30 seconds however many retries "
                                    "are configured."
                                ),
                                prefill=DefaultValue(0.5),
                                custom_validate=(
                                    validators.NumberInRange(min_value=0.0, max_value=30.0),
                                ),
                            ),
                        ),
                    },
                ),
            ),
            "accept_status": DictElement(
                required=False,
                parameter_form=List(
                    title=Title("Additional accepted HTTP status codes"),
                    help_text=Help(
                        "By default only 2xx responses are read; any other status "
                        "makes the endpoint's services UNKNOWN. Add extra status "
                        "codes to accept here - for example 503 for a health "
                        "endpoint that reports its problems with a 503 and a JSON "
                        "body. The body of an accepted response is parsed and "
                        "extracted as usual. 2xx is always accepted."
                    ),
                    element_template=Integer(
                        title=Title("Status code"),
                        custom_validate=(validators.NumberInRange(min_value=100, max_value=599),),
                    ),
                ),
            ),
            "show_response": DictElement(
                required=False,
                parameter_form=Dictionary(
                    title=Title("Report the raw response"),
                    help_text=Help(
                        "Off by default. Put the response itself into the "
                        "Details of this endpoint's own 'JSON API <name>' "
                        "service, exactly as it came off the wire. The Details "
                        "otherwise only link to the URL, which is often not "
                        "reachable from the browser reading the service (the API "
                        "may sit behind a firewall or in another network) and "
                        "always shows the API as it is NOW rather than as it was "
                        "when the check ran. The body of a REJECTED response is "
                        "reported too - an unexpected HTTP status or a body that "
                        "is not JSON - which is the case this exists for: it is "
                        "where the API explains itself. Credentials are stripped "
                        "first: 'Set-Cookie' and any authorization header are "
                        "masked, and the endpoint's own secret is removed "
                        "wherever it appears. Everything else in the response is "
                        "reported verbatim, and a service's Details are stored "
                        "with every check result and travel into notifications - "
                        "so do not turn this on for a response carrying personal "
                        "or otherwise sensitive data."
                    ),
                    elements={
                        "max_bytes": DictElement(
                            required=True,
                            parameter_form=Integer(
                                title=Title("Report at most (bytes)"),
                                help_text=Help(
                                    "Longer bodies are cut off at this many bytes "
                                    "and the service says so. Keep it small: this "
                                    "text is stored with every check result of "
                                    "this service."
                                ),
                                prefill=DefaultValue(2048),
                                custom_validate=(
                                    validators.NumberInRange(min_value=1, max_value=65536),
                                ),
                            ),
                        ),
                        "headers": DictElement(
                            required=True,
                            parameter_form=BooleanChoice(
                                title=Title("Response headers"),
                                label=Label("Report the response headers as well"),
                                help_text=Help(
                                    "On by default. The headers are what a rate "
                                    "limit, a cache directive or a content type "
                                    "is announced in, and they are small."
                                ),
                                prefill=DefaultValue(True),
                            ),
                        ),
                    },
                ),
            ),
            "field_context": DictElement(
                required=False,
                parameter_form=Dictionary(
                    title=Title("Report the JSON context in the field services"),
                    help_text=Help(
                        "Off by default. The setting above reports the response on "
                        "the endpoint's OWN service - but the services that go WARN "
                        "or CRIT, and therefore the ones that notify, are the field "
                        "services, and their Details say only which path was read. "
                        "This puts the JSON itself there too, so the alert carries "
                        "what the API actually said: a service's Details travel into "
                        "notifications as $LONGSERVICEOUTPUT$, and the person on "
                        "call often cannot reach the endpoint at all - wrong "
                        "network, no credentials - while the endpoint's own service "
                        "stayed OK and notified nobody. Credentials are stripped "
                        "exactly as they are for the raw response. Note that this "
                        "text is stored with every check result of EVERY field "
                        "service of this endpoint, so keep the byte budget small."
                    ),
                    elements={
                        "source": DictElement(
                            required=True,
                            parameter_form=SingleChoice(
                                title=Title("What to report"),
                                elements=[
                                    SingleChoiceElement(
                                        "element",
                                        Title("The JSON this value was read from"),
                                    ),
                                    SingleChoiceElement(
                                        "response",
                                        Title("The whole response body"),
                                    ),
                                ],
                                prefill=DefaultValue("element"),
                                help_text=Help(
                                    "'The JSON this value was read from' is the "
                                    "targeted form: for a '[*]' path that element, "
                                    "otherwise the object holding the value - so "
                                    "the service that alerted shows the element "
                                    "that failed with all its sibling fields, and "
                                    "nothing else. An aggregation has no single "
                                    "element and a '@header.' path is not in the "
                                    "body at all, so both report the whole "
                                    "response. 'The whole response body' always "
                                    "does, repeated on every field service - which "
                                    "is why the cap matters more here than on the "
                                    "endpoint's own service."
                                ),
                            ),
                        ),
                        "max_bytes": DictElement(
                            required=True,
                            parameter_form=Integer(
                                title=Title("Report at most (bytes)"),
                                help_text=Help(
                                    "Longer context is cut off at this many bytes "
                                    "and the service says so. Keep it small: this "
                                    "text is stored with every check result of "
                                    "every field service of this endpoint."
                                ),
                                prefill=DefaultValue(1024),
                                custom_validate=(
                                    validators.NumberInRange(min_value=1, max_value=65536),
                                ),
                            ),
                        ),
                    },
                ),
            ),
            "pagination": DictElement(
                required=False,
                parameter_form=Dictionary(
                    title=Title("Follow pagination"),
                    custom_validate=(_validate_pagination,),
                    help_text=Help(
                        "Off by default: one request, one page. An API that "
                        "answers a collection one page at a time then leaves "
                        "every service built from it describing the FIRST page "
                        "only - 'count' over a queue that pages at 25 reports 25 "
                        "however long the queue is, and a '[*]' wildcard creates "
                        "services for the first page's elements alone. Nothing "
                        "says so, which is why this is worth configuring: the "
                        "answer is not missing, it is wrong. With this set the "
                        "agent follows the API's own next-page link - or, for an "
                        "API without one, counts the pages itself - and appends "
                        "each page's collection to the first page's, so the "
                        "wildcards, the aggregations, the filters and the host "
                        "labels all see the whole thing. Every page is requested "
                        "exactly like the first one (same method, headers, "
                        "authentication and timeout), and each one costs a "
                        "request inside the check - so cap the pages, and "
                        "consider a cache TTL for a collection that does not "
                        "change every check interval. A page that cannot be read "
                        "fails the endpoint rather than silently truncating the "
                        "collection; where a further page exists but is not "
                        "followed - a cap, or a link the agent refuses - the "
                        "endpoint's own service reports it and goes WARN."
                    ),
                    elements={
                        "next": DictElement(
                            required=True,
                            parameter_form=CascadingSingleChoice(
                                title=Title("Where the next page's URL comes from"),
                                help_text=Help(
                                    "The API's documentation says which form it "
                                    "uses. Where the API links to the next page, "
                                    "a page that carries no next link (an absent "
                                    "field, a JSON 'null', an empty string, no "
                                    "'Link' header) is the last one, which is how "
                                    "pagination ends. Where it offers no link and "
                                    "takes the position as a query parameter "
                                    "instead, the agent counts: the page number "
                                    "goes up by one per page, the offset by the "
                                    "number of elements the previous page "
                                    "carried. The parameter is set on the first "
                                    "request too, replacing one of that name in "
                                    "the URL."
                                ),
                                elements=[
                                    CascadingSingleChoiceElement(
                                        name="body",
                                        title=Title("A field in the response body"),
                                        parameter_form=String(
                                            title=Title("JSON path to the next page's URL"),
                                            field_size=FieldSize.LARGE,
                                            help_text=Help(
                                                "From the response root, e.g. "
                                                "'links.next', 'next' or "
                                                "'meta.next_page_url'. A relative "
                                                "URL ('/api/v1/jobs?page=2') is "
                                                "resolved against the page it "
                                                "came from."
                                            ),
                                            custom_validate=(
                                                validators.LengthInRange(min_value=1),
                                            ),
                                        ),
                                    ),
                                    CascadingSingleChoiceElement(
                                        name="link_header",
                                        title=Title("The 'Link' response header (rel=\"next\")"),
                                        parameter_form=FixedValue(
                                            value=None,
                                            label=Label(
                                                "Read from the 'Link' header, as RFC 8288 "
                                                "defines it"
                                            ),
                                            help_text=Help(
                                                "The convention of the GitHub, "
                                                "GitLab and Jenkins style APIs: "
                                                "'Link: <https://host/jobs?page=2>; "
                                                'rel="next"\'. The link with '
                                                'rel="next" is followed; the '
                                                "others ('last', 'prev') are "
                                                "ignored."
                                            ),
                                        ),
                                    ),
                                    CascadingSingleChoiceElement(
                                        name="page_number",
                                        title=Title("No link: count the pages ('?page=2')"),
                                        parameter_form=_counted_pages(
                                            parameter_prefill="page",
                                            start=DictElement(
                                                required=True,
                                                parameter_form=Integer(
                                                    title=Title("Number of the first page"),
                                                    help_text=Help(
                                                        "1 for most APIs; 0 where the API "
                                                        "counts from zero."
                                                    ),
                                                    prefill=DefaultValue(1),
                                                    custom_validate=(
                                                        validators.NumberInRange(min_value=0),
                                                    ),
                                                ),
                                            ),
                                        ),
                                    ),
                                    CascadingSingleChoiceElement(
                                        name="offset",
                                        title=Title("No link: count the elements ('?offset=50')"),
                                        parameter_form=_counted_pages(
                                            parameter_prefill="offset",
                                            start=DictElement(
                                                required=False,
                                                parameter_form=Integer(
                                                    title=Title("Offset of the first element"),
                                                    help_text=Help(
                                                        "Unset, counting starts at 0. Set 1 "
                                                        "where the API numbers the elements "
                                                        "from one, such as SCIM's "
                                                        "'startIndex' - otherwise every page "
                                                        "after the first repeats one element."
                                                    ),
                                                    prefill=DefaultValue(1),
                                                    custom_validate=(
                                                        validators.NumberInRange(min_value=0),
                                                    ),
                                                ),
                                            ),
                                        ),
                                    ),
                                ],
                                prefill=DefaultValue("body"),
                            ),
                        ),
                        "items": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("JSON path to the collection to merge"),
                                field_size=FieldSize.LARGE,
                                help_text=Help(
                                    "The array (or object) each page carries a "
                                    "slice of - e.g. 'items', 'data.jobs' or "
                                    "'results'. Use '$' where the response IS the "
                                    "array. Each page's collection is appended to "
                                    "the first page's, and the rest of the "
                                    "document stays as the first page sent it: a "
                                    "'total' or a 'generated_at' next to the "
                                    "collection still resolves, and every path "
                                    "configured below is unchanged. No '[*]' "
                                    "wildcard here - this names the collection "
                                    "itself, not the elements in it."
                                ),
                                custom_validate=(validators.LengthInRange(min_value=1),),
                            ),
                        ),
                        "max_pages": DictElement(
                            required=True,
                            parameter_form=Integer(
                                title=Title("Read at most this many pages"),
                                help_text=Help(
                                    "Including the first one, so '1' disables "
                                    "following entirely. Each page is a request "
                                    "made while the check runs, and a special "
                                    "agent that overruns is killed - so this is "
                                    "the setting that keeps a collection which "
                                    "grew a hundredfold from turning the "
                                    "monitoring into the outage. When the limit "
                                    "is reached while a further page still "
                                    "exists, the endpoint's own service says so "
                                    "and goes WARN instead of quietly reporting "
                                    "part of the collection."
                                ),
                                prefill=DefaultValue(10),
                                custom_validate=(
                                    validators.NumberInRange(min_value=1, max_value=100),
                                ),
                            ),
                        ),
                        "max_elements": DictElement(
                            required=False,
                            parameter_form=Integer(
                                title=Title("And at most this many elements"),
                                help_text=Help(
                                    "Optional second cap, for a page size that is "
                                    "not known in advance. Checked between pages, "
                                    "so a page is never cut in half and the "
                                    "collection can end slightly above this. Like "
                                    "the page limit, reaching it while more pages "
                                    "exist is reported on the endpoint's own "
                                    "service. Remember that a '[*]' wildcard "
                                    "creates one SERVICE per element."
                                ),
                                prefill=InputHint(1000),
                                custom_validate=(validators.NumberInRange(min_value=1),),
                            ),
                        ),
                    },
                ),
            ),
            "proxy": DictElement(
                required=False,
                parameter_form=Proxy(
                    title=Title("HTTP proxy"),
                    help_text=Help(
                        "Route this endpoint's request through an HTTP proxy - "
                        "useful when the Checkmk server reaches the API only via a "
                        "corporate egress proxy. Choose the environment's "
                        "HTTP_PROXY / HTTPS_PROXY variables, an explicit proxy URL, "
                        "or 'no proxy' to bypass any proxy set in the environment. "
                        "Without this setting the environment's proxy variables are "
                        "honoured."
                    ),
                    migrate=migrate_to_proxy,
                ),
            ),
            "extractions": DictElement(
                required=True,
                parameter_form=List(
                    title=Title("Fields to monitor"),
                    help_text=Help(
                        "Each entry becomes one Checkmk service, built from the "
                        "value found at the given JSON path."
                    ),
                    element_template=_extraction(),
                    custom_validate=(_validate_unique_group_metrics,),
                ),
            ),
            "host_labels": DictElement(
                required=False,
                parameter_form=List(
                    title=Title("Host labels"),
                    help_text=Help(
                        "Attach Checkmk host labels to the monitored host, built "
                        "from fields in this endpoint's response. Each key is "
                        "prefixed with 'json_api/' and the value is resolved from "
                        "the response root - so these are host-wide facts (e.g. an "
                        "environment, region or version) and need NO service. A "
                        "path may contain a '[*]' wildcard (e.g. 'components[*]') "
                        "to emit one label per element, keyed "
                        "'<key>/<element>' so keys stay unique. Or classify the "
                        "host from a collection instead: a condition plus a literal "
                        "value turns 'any element of services[*] whose name matches "
                        "^MyApp' into the single label 'json_api/MyApp: yes', which "
                        "folder rules, thresholds, contact groups and views can then "
                        "target. Set at discovery, so pick stable, low-cardinality "
                        "fields."
                    ),
                    element_template=Dictionary(
                        custom_validate=(_validate_host_label,),
                        elements={
                            "path": DictElement(
                                required=False,
                                parameter_form=String(
                                    title=Title("JSON path"),
                                    field_size=FieldSize.LARGE,
                                    help_text=Help(
                                        "From the response root, e.g. 'version', "
                                        "'cluster.region', or a '[*]' wildcard like "
                                        "'components[*]' for one label per element. "
                                        "Can be left empty only where a condition "
                                        "and a literal value describe the label on "
                                        "their own."
                                    ),
                                ),
                            ),
                            "key": DictElement(
                                required=False,
                                parameter_form=String(
                                    title=Title("Label key (optional)"),
                                    help_text=Help(
                                        "Defaults to the path's last segment. For a "
                                        "'[*]' path the element id is appended "
                                        "('<key>/<element>'). The 'json_api/' prefix "
                                        "is added automatically."
                                    ),
                                ),
                            ),
                            "value_field": DictElement(
                                required=False,
                                parameter_form=String(
                                    title=Title("Value field (for '[*]' wildcards)"),
                                    field_size=FieldSize.LARGE,
                                    help_text=Help(
                                        "For a '[*]' path: a path within each "
                                        "element for the label value (e.g. "
                                        "'status'). Defaults to 'true' (a "
                                        "set-membership tag). Ignored without a "
                                        "wildcard."
                                    ),
                                ),
                            ),
                            "value": DictElement(
                                required=False,
                                parameter_form=String(
                                    title=Title("Label value (literal)"),
                                    help_text=Help(
                                        "A value written here instead of read from "
                                        "the response ('yes', 'production', ...). "
                                        "With it a '[*]' path produces ONE label for "
                                        "the whole collection rather than one per "
                                        "element, so the key is used exactly as "
                                        "given - no '<key>/<element>' suffix. "
                                        "Combined with the condition below this is "
                                        "the classification case: 'if any element "
                                        "matches, tag the host'."
                                    ),
                                ),
                            ),
                            "filter": DictElement(
                                required=False,
                                parameter_form=_element_filter(
                                    Title("Only elements matching a condition"),
                                    Help(
                                        "Emit the label only for the elements whose "
                                        "sub-field matches this condition - e.g. "
                                        "'name' matching '^MyApp' over a "
                                        "'services[*]' path. The field path is "
                                        "resolved within each element; an element "
                                        "whose field is missing or is not a scalar "
                                        "never matches. Without a '[*]' wildcard the "
                                        "condition is checked once, in the same "
                                        "scope the path is read from, so the label "
                                        "is set only when it holds."
                                    ),
                                ),
                            ),
                        },
                    ),
                ),
            ),
        },
    )
