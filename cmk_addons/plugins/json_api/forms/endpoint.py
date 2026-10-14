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
    DefaultValue,
    DictElement,
    Dictionary,
    FieldSize,
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
from .common import _validate_url
from .extraction import _extraction
from .labels import _host_labels
from .metrics import _validate_unique_group_metrics
from .pagination import _pagination


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
                parameter_form=_pagination(),
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
                parameter_form=_host_labels(),
            ),
        },
    )
