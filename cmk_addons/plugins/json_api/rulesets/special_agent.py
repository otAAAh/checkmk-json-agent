#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Setup rule for the generic JSON API special agent.

Everything lives in one rule: connection, auth, and the list of fields to
extract (each with optional thresholds / expected-string match). This is the
deliberate UX choice — no separate master-item / discovery / threshold rules.
"""

import ast
import re
from urllib.parse import urlparse

from cmk.rulesets.v1 import Help, Label, Message, Title
from cmk.rulesets.v1.form_specs import (
    BooleanChoice,
    CascadingSingleChoice,
    CascadingSingleChoiceElement,
    DefaultValue,
    DictElement,
    Dictionary,
    FixedValue,
    Float,
    InputHint,
    Integer,
    List,
    Password,
    Proxy,
    SingleChoice,
    SingleChoiceElement,
    String,
    migrate_to_password,
    migrate_to_proxy,
    validators,
)
from cmk.rulesets.v1.rule_specs import SpecialAgent, Topic

from cmk_addons.plugins.json_api.lib import (
    levels_lower,
    levels_upper,
    string_match,
    validate_regex,
)

# Re-exported so the special-agent rule's field validators keep their original
# names; the shared implementation now lives in ``lib`` (used by the
# check-parameters rule too).
_validate_regex = validate_regex


# The AST node types a calc expression may contain: an arithmetic tree over
# numeric literals and the variable 'value'. Kept in lock-step with the check's
# own evaluator (_apply_calc in agent_based/json_api.py).
_CALC_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,  # every Name carries a Load context node, harmless
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.UAdd,
    ast.USub,
)


def _validate_calc(value: str) -> None:
    if not value.strip():
        # An empty expression means "no transform" - the check skips a falsy
        # calc, so accept it here instead of tripping on ast.parse("").
        return
    try:
        tree = ast.parse(value, mode="eval")
    except SyntaxError as exc:
        raise validators.ValidationError(
            Message("Invalid arithmetic expression: %s") % str(exc)
        ) from exc
    for node in ast.walk(tree):
        if not isinstance(node, _CALC_ALLOWED_NODES):
            raise validators.ValidationError(
                Message("Only the variable 'value', numbers, parentheses and + - * / are allowed.")
            )
        if isinstance(node, ast.Name) and node.id != "value":
            raise validators.ValidationError(
                Message("Unknown variable '%s' - only 'value' is available.") % node.id
            )
        if isinstance(node, ast.Constant) and (
            isinstance(node.value, bool) or not isinstance(node.value, (int, float))
        ):
            raise validators.ValidationError(Message("Only numeric constants are allowed."))


def _unique_or_duplicates(value: object, key: str) -> list[str]:
    """The non-empty values of ``key`` across the endpoints that occur twice."""
    if not isinstance(value, (list, tuple)):
        return []
    values = [
        ep[key].strip()
        for ep in value
        if isinstance(ep, dict) and isinstance(ep.get(key), str) and ep[key].strip()
    ]
    return sorted({item for item in values if values.count(item) > 1})


def _validate_unique_endpoints(value: object) -> None:
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


def _validate_url(value: str) -> None:
    # A proper http(s) URI: no surrounding/embedded whitespace (urlparse would
    # silently strip a leading space and accept it, then the agent fails at
    # runtime), scheme http/https (case-insensitive per RFC 3986, requests
    # accepts 'HTTP://') AND a host present ('http://' alone is not usable).
    if re.search(r"\s", value):
        raise validators.ValidationError(Message("The URL must not contain whitespace."))
    parsed = urlparse(value)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise validators.ValidationError(
            Message("Enter a valid http(s) URL, e.g. 'https://host/path'.")
        )


# An HTTP field name as RFC 9110 defines it (a token): letters, digits and a
# handful of symbols, no whitespace. Checked in Setup because requests would
# otherwise raise deep inside the agent, where the error reaches the user as an
# unreachable endpoint rather than as "fix this field".
# '\Z', not '$': '$' also matches before a trailing newline, which would let
# 'X-Api-Key\n' through - and a newline in a header name is header injection.
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def _validate_header_name(value: str) -> None:
    if not _HEADER_NAME_PATTERN.match(value):
        raise validators.ValidationError(
            Message("Enter a valid HTTP header name, e.g. 'X-API-Key'.")
        )


def _validate_query_parameter(value: str) -> None:
    # The name is placed in the query string, so the characters that structure a
    # query string (or end it) cannot appear in it.
    if not value or re.search(r"[\s&=?#]", value):
        raise validators.ValidationError(
            Message("Enter a valid query parameter name, e.g. 'api_key'.")
        )


def _validate_endpoint(value: object) -> None:
    # An API key header configured *twice* - once as authentication, once as a
    # plain additional header - is ambiguous: one silently overwrites the other,
    # and which one wins is an implementation detail. Rejecting it here also
    # stops the clear-text copy this feature exists to remove from being left
    # behind next to the password-store one.
    if not isinstance(value, dict):
        return
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


# A summary template: literal text with '{path}' placeholders, no nesting. The
# same shape the agent's _SUMMARY_PLACEHOLDER resolves, checked here so a stray
# brace is a form error rather than a placeholder that silently never renders.
_SUMMARY_TEMPLATE_PATTERN = re.compile(r"[^{}]*(\{[^{}]+\}[^{}]*)*\Z")


def _validate_summary(value: str) -> None:
    if not value.strip():
        return
    # A blank placeholder resolves to nothing and would silently disappear, so it
    # is rejected alongside the malformed shapes.
    blank = any(not path.strip() for path in re.findall(r"\{([^{}]*)\}", value))
    if blank or not _SUMMARY_TEMPLATE_PATTERN.match(value):
        raise validators.ValidationError(
            Message(
                "Use '{path}' to insert a field, e.g. '{message} (leader {leader})'. "
                "Braces must be paired and cannot be nested or empty."
            )
        )


def _authentication() -> CascadingSingleChoice:
    return CascadingSingleChoice(
        title=Title("Authentication"),
        prefill=DefaultValue("auth_token"),
        elements=[
            CascadingSingleChoiceElement(
                name="auth_login",
                title=Title("Basic authentication (username / password)"),
                parameter_form=Dictionary(
                    elements={
                        "username": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Username"),
                                custom_validate=(validators.LengthInRange(min_value=1),),
                            ),
                        ),
                        "password": DictElement(
                            required=True,
                            parameter_form=Password(
                                title=Title("Password"),
                                migrate=migrate_to_password,
                            ),
                        ),
                    }
                ),
            ),
            CascadingSingleChoiceElement(
                name="auth_token",
                title=Title("Bearer token"),
                parameter_form=Dictionary(
                    elements={
                        "token": DictElement(
                            required=True,
                            parameter_form=Password(
                                title=Title("Token"),
                                help_text=Help("Sent as 'Authorization: Bearer <token>'."),
                                migrate=migrate_to_password,
                            ),
                        ),
                    }
                ),
            ),
            CascadingSingleChoiceElement(
                name="auth_header",
                title=Title("API key in a request header"),
                parameter_form=Dictionary(
                    elements={
                        "header": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Header name"),
                                help_text=Help(
                                    "Name of the header carrying the key, e.g. "
                                    "'X-API-Key', 'apikey' or 'PRIVATE-TOKEN'. For "
                                    "'Authorization: Bearer <token>' use the bearer "
                                    "token choice instead."
                                ),
                                prefill=DefaultValue("X-API-Key"),
                                custom_validate=(_validate_header_name,),
                            ),
                        ),
                        "key": DictElement(
                            required=True,
                            parameter_form=Password(
                                title=Title("API key"),
                                help_text=Help(
                                    "Kept in the Checkmk password store. Unlike a key "
                                    "typed into 'Additional request headers' it is "
                                    "never written to the configuration or to the "
                                    "agent's command line in clear text, and it can be "
                                    "rotated in one place."
                                ),
                                migrate=migrate_to_password,
                            ),
                        ),
                    }
                ),
            ),
            CascadingSingleChoiceElement(
                name="auth_query",
                title=Title("API key in a query parameter"),
                parameter_form=Dictionary(
                    elements={
                        "parameter": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Parameter name"),
                                help_text=Help(
                                    "Name of the query parameter carrying the key, e.g. "
                                    "'api_key'. It is appended to the URL for the "
                                    "request only: the key is redacted wherever the "
                                    "agent reports a URL, and it never appears in the "
                                    "service name. Prefer a header where the API "
                                    "offers one - a key in the URL is visible to "
                                    "proxies and server logs along the way."
                                ),
                                prefill=DefaultValue("api_key"),
                                custom_validate=(_validate_query_parameter,),
                            ),
                        ),
                        "key": DictElement(
                            required=True,
                            parameter_form=Password(
                                title=Title("API key"),
                                help_text=Help(
                                    "Kept in the Checkmk password store, so it is not "
                                    "written to the configuration or to the agent's "
                                    "command line in clear text - unlike a key typed "
                                    "into the URL."
                                ),
                                migrate=migrate_to_password,
                            ),
                        ),
                    }
                ),
            ),
        ],
    )


def _migrate_extraction(value: object) -> dict[str, object]:
    """Carry an older extraction shape into the current one.

    Two historical shapes are migrated:

    * the pre-'match' flat ``expected`` regex becomes the equivalent
      ``("must_match", <regex>)``, so existing rules keep their behaviour;
    * the boolean ``count`` ("count the elements at this path") becomes the
      ``"count"`` choice of the richer ``aggregate`` dropdown, which now also
      offers sum / average / minimum / maximum.
    """
    if not isinstance(value, dict):
        raise TypeError(f"Unexpected extraction value: {value!r}")
    migrated = dict(value)
    if "expected" in migrated and "match" not in migrated:
        expected = migrated.pop("expected")
        if isinstance(expected, str):
            # The old flat regex was OK-on-match, CRIT otherwise - keep that
            # exactly by omitting state_no_match (its CRIT default).
            migrated["match"] = ("must_match", {"pattern": expected})
    if "count" in migrated:
        counted = migrated.pop("count")
        if counted and "aggregate" not in migrated:
            migrated["aggregate"] = "count"
    return migrated


def _extraction() -> Dictionary:
    return Dictionary(
        title=Title("Field to monitor"),
        migrate=_migrate_extraction,
        elements={
            "service": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("Service name"),
                    help_text=Help("Becomes the Checkmk service description for this field."),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "path": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("JSON path"),
                    help_text=Help(
                        "Dotted path into the JSON response, e.g. "
                        "'status', 'components.db.status' or 'items[0].count'. "
                        "Use a '[*]' wildcard (e.g. 'nodes[*].health') to create "
                        "one service per array element - or, when the wildcard "
                        "lands on a JSON object (a map keyed by name, such as a "
                        "Spring Boot Actuator '/health' 'components[*].status'), "
                        "one service per key. Multiple '[*]' wildcards "
                        "(e.g. 'pods[*].containers[*].ready') expand the cartesian "
                        "product, one service per combination. A leading '$.' is "
                        "optional. Keys that themselves contain '.' or '[' can be "
                        "addressed with bracket-quoted segments, e.g. "
                        "\"data['foo.bar'].value\"."
                    ),
                    prefill=InputHint("status"),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "label_path": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Per-element name suffix (for '[*]' wildcards)"),
                    help_text=Help(
                        "When the JSON path contains a '[*]' wildcard, one service "
                        "is created per element. This optional path - relative to "
                        "each element, e.g. 'name' or 'id' - is appended to the "
                        "service name to tell those services apart. It does NOT "
                        "replace the service name. Defaults to the array index "
                        "(or, for an object, the key). With multiple '[*]' "
                        "wildcards it is resolved at every level and the parts are "
                        "joined with ' / ' (e.g. '<pod> / <container>'). Pick a "
                        "field that is unique and stable across runs."
                    ),
                ),
            ),
            "piggyback_host": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Create one host per element, named by this field"),
                    help_text=Help(
                        "When the JSON path contains a '[*]' wildcard, this turns "
                        "every element into a Checkmk host of its own instead of "
                        "one more service on this host. Give a path relative to "
                        "each element that holds the host name, e.g. 'name' or "
                        "'hostname'. The service then keeps its plain name, "
                        "because the host already says which element it is. Set "
                        "the same field on several fields of this endpoint to "
                        "collect them all on the same hosts. Only characters "
                        "valid in a Checkmk host name are kept (letters, digits, "
                        "'-', '_', '.'); anything else becomes '_'. An element "
                        "whose field is missing keeps its service on this host, "
                        "so nothing is lost. IMPORTANT: Checkmk holds piggyback "
                        "data for hosts that do not exist yet - create the hosts "
                        "(manually or with Dynamic host management) or the data "
                        "is never monitored."
                    ),
                ),
            ),
            "labels": DictElement(
                required=False,
                parameter_form=List(
                    title=Title("Service labels"),
                    help_text=Help(
                        "Attach Checkmk service labels to THIS service, built from "
                        "fields in the response. Each key is prefixed with "
                        "'json_api/'. For a '[*]' path the value is resolved within "
                        "each element (e.g. 'name'), so a per-element service gets "
                        "its own label; for a non-wildcard path it is resolved from "
                        "the response root. Host-wide facts belong in the endpoint's "
                        "'Host labels' instead. Labels are set at discovery, so pick "
                        "stable, low-cardinality fields - a value that changes churns "
                        "the label."
                    ),
                    element_template=Dictionary(
                        elements={
                            "path": DictElement(
                                required=True,
                                parameter_form=String(
                                    title=Title("JSON path"),
                                    help_text=Help(
                                        "Relative to each '[*]' element (like the name "
                                        "suffix), or the response root for a "
                                        "non-wildcard path."
                                    ),
                                    custom_validate=(validators.LengthInRange(min_value=1),),
                                ),
                            ),
                            "key": DictElement(
                                required=False,
                                parameter_form=String(
                                    title=Title("Label key (optional)"),
                                    help_text=Help(
                                        "Defaults to the path's last segment. The "
                                        "'json_api/' prefix is added automatically."
                                    ),
                                ),
                            ),
                        }
                    ),
                ),
            ),
            "aggregate": DictElement(
                required=False,
                parameter_form=SingleChoice(
                    title=Title("Aggregate a collection into one value"),
                    help_text=Help(
                        "Collapse a collection of elements into a single number "
                        "instead of monitoring one value (or, with a '[*]' "
                        "wildcard, one service per element). Point the JSON path "
                        "either at an array / object (e.g. 'jobs') or at a '[*]' "
                        "wildcard over the values to aggregate (e.g. "
                        "'nodes[*].load'). 'Number of elements' just counts them; "
                        "the other functions need numeric values. The result is a "
                        "number, so a unit, levels, a transform and a metric all "
                        "apply to it. Combine it with the condition below to "
                        "aggregate only the matching elements. A path that holds "
                        "neither an array nor an object makes the service UNKNOWN."
                    ),
                    elements=[
                        SingleChoiceElement("count", Title("Number of elements")),
                        SingleChoiceElement("sum", Title("Sum of the values")),
                        SingleChoiceElement("avg", Title("Average of the values")),
                        SingleChoiceElement("min", Title("Smallest of the values")),
                        SingleChoiceElement("max", Title("Largest of the values")),
                    ],
                    prefill=DefaultValue("count"),
                ),
            ),
            "filter": DictElement(
                required=False,
                parameter_form=Dictionary(
                    title=Title("Only elements matching a condition"),
                    help_text=Help(
                        "For a '[*]' wildcard or an aggregated path: keep only the "
                        "elements whose sub-field matches this condition - e.g. one "
                        "service per node whose 'status' is not 'ok', or count only "
                        "the pods that are not 'Running'. The field path is resolved "
                        "within each element; an element whose field is missing or "
                        "is not a scalar is dropped. Without a '[*]' wildcard or an "
                        "aggregation this has no effect."
                    ),
                    elements={
                        "path": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Field path (within each element)"),
                                help_text=Help(
                                    "Resolved within each element, e.g. 'status' or "
                                    "'metadata.phase'."
                                ),
                                custom_validate=(validators.LengthInRange(min_value=1),),
                            ),
                        ),
                        "op": DictElement(
                            required=True,
                            parameter_form=SingleChoice(
                                title=Title("Condition"),
                                elements=[
                                    SingleChoiceElement("equals", Title("equals")),
                                    SingleChoiceElement("not_equals", Title("does not equal")),
                                    SingleChoiceElement("regex", Title("matches regex")),
                                    SingleChoiceElement("not_regex", Title("does not match regex")),
                                ],
                                prefill=DefaultValue("not_equals"),
                            ),
                        ),
                        "value": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Comparison value / pattern"),
                                help_text=Help(
                                    "The value to compare against, or the regular "
                                    "expression for the regex conditions."
                                ),
                            ),
                        ),
                    },
                ),
            ),
            "value_as": DictElement(
                required=False,
                parameter_form=CascadingSingleChoice(
                    title=Title("Interpret the value as"),
                    help_text=Help(
                        "By default the extracted value is monitored as it stands. "
                        "Two common API values are worth deriving something else "
                        "from: a counter that only ever grows (its rate of change "
                        "is what matters, not the total), and a timestamp (its age "
                        "is what matters, not the date). The derived number is what "
                        "the transform, the levels, the metric and the summary then "
                        "use."
                    ),
                    prefill=DefaultValue("counter"),
                    elements=[
                        CascadingSingleChoiceElement(
                            name="counter",
                            title=Title("A counter - monitor its per-second rate"),
                            parameter_form=FixedValue(
                                value=None,
                                title=Title("Per-second rate"),
                                label=Label(
                                    "The difference to the previous check is divided "
                                    "by the elapsed time"
                                ),
                                help_text=Help(
                                    "For monotonically growing counters such as "
                                    "'requests_total' or 'bytes_sent': the check "
                                    "monitors the change per second instead of the "
                                    "absolute total. The first check after a "
                                    "restart of the counter cannot compute a rate "
                                    "yet and keeps the service's previous state."
                                ),
                            ),
                        ),
                        CascadingSingleChoiceElement(
                            name="timestamp",
                            title=Title("A timestamp - monitor its age"),
                            parameter_form=Dictionary(
                                help_text=Help(
                                    "For values such as 'last_backup' or "
                                    "'updated_at': the check monitors the number of "
                                    "seconds since that point in time, so upper "
                                    "levels alert on stale data. A timestamp in the "
                                    "future yields a negative age. The age graphs "
                                    "and reads as a duration on its own; choose a "
                                    "unit below only to override that (e.g. after a "
                                    "transform into hours)."
                                ),
                                elements={
                                    "format": DictElement(
                                        required=True,
                                        parameter_form=SingleChoice(
                                            title=Title("Timestamp format"),
                                            help_text=Help(
                                                "'Detect automatically' reads a "
                                                "number as Unix epoch seconds "
                                                "(milliseconds when it is far too "
                                                "large for seconds) and anything "
                                                "else as ISO 8601. A timestamp "
                                                "without a time zone is read as UTC."
                                            ),
                                            elements=[
                                                SingleChoiceElement(
                                                    "auto", Title("Detect automatically")
                                                ),
                                                SingleChoiceElement(
                                                    "epoch", Title("Unix epoch seconds")
                                                ),
                                                SingleChoiceElement(
                                                    "epoch_ms",
                                                    Title("Unix epoch milliseconds"),
                                                ),
                                                SingleChoiceElement(
                                                    "iso",
                                                    Title(
                                                        "ISO 8601 / RFC 3339, "
                                                        "e.g. '2026-07-28T02:00:00Z'"
                                                    ),
                                                ),
                                            ],
                                            prefill=DefaultValue("auto"),
                                        ),
                                    ),
                                },
                            ),
                        ),
                    ],
                ),
            ),
            "unit": DictElement(
                required=False,
                parameter_form=SingleChoice(
                    title=Title("Unit (for numeric values)"),
                    help_text=Help(
                        "Renders the metric and graph with this unit. Leave unset "
                        "for a plain, unit-less value. Only affects numeric values."
                    ),
                    elements=[
                        SingleChoiceElement("count", Title("Count (integer)")),
                        SingleChoiceElement("bytes", Title("Bytes (IEC: KiB, MiB, ...)")),
                        SingleChoiceElement("seconds", Title("Seconds (duration)")),
                        SingleChoiceElement("percent", Title("Percent")),
                    ],
                    prefill=InputHint(Title("No unit")),
                ),
            ),
            "levels_upper": DictElement(
                required=False,
                parameter_form=levels_upper(),
            ),
            "levels_lower": DictElement(
                required=False,
                parameter_form=levels_lower(),
            ),
            "calc": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Transform the numeric value"),
                    help_text=Help(
                        "An arithmetic expression applied to a numeric value "
                        "before the levels and the metric, using the variable "
                        "'value'. Only numbers, parentheses and + - * / are "
                        "allowed. Examples: 'value / 1024 / 1024' (bytes to MiB), "
                        "'value * 1000' (seconds to milliseconds), "
                        "'(value - 32) * 5 / 9' (Fahrenheit to Celsius)."
                    ),
                    prefill=InputHint("value / 1024 / 1024"),
                    custom_validate=(_validate_calc,),
                ),
            ),
            "match": DictElement(
                required=False,
                parameter_form=string_match(),
            ),
            "summary": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Extra text in the service summary"),
                    help_text=Help(
                        "Appended to the summary, after the value. Write '{path}' "
                        "to insert another field of the same response - resolved "
                        "within the current element for a '[*]' wildcard, from the "
                        "response root otherwise. So a service on 'status' can show "
                        "the reason the API gave next to it, e.g. "
                        "'{message} (leader {leader})', instead of needing a second "
                        "service for it. A path that is not in the response renders "
                        "as '(n/a)'. This is presentation only: it never changes the "
                        "state, the levels or the metric. The text is put on one "
                        "line and truncated if it gets long."
                    ),
                    prefill=InputHint("{message}"),
                    custom_validate=(_validate_summary,),
                ),
            ),
        },
    )


def _endpoint() -> Dictionary:
    return Dictionary(
        title=Title("Endpoint"),
        custom_validate=(_validate_endpoint,),
        elements={
            "name": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Endpoint name"),
                    help_text=Help(
                        "Optional short name for this endpoint, e.g. 'frontend'. It "
                        "names the endpoint's own service - 'JSON API <name>', which "
                        "reports the HTTP status and the response time of the "
                        "request. Without a name the URL is used. It does not change "
                        "the field service names."
                    ),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "url": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("URL"),
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
                                parameter_form=String(title=Title("Header value")),
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
                    label=Label("Verify the TLS certificate"),
                    help_text=Help("Disabling certificate verification is insecure."),
                    prefill=DefaultValue(True),
                ),
            ),
            "ca_bundle": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Custom CA bundle file"),
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
                parameter_form=Float(
                    title=Title("Request timeout (seconds)"),
                    help_text=Help("Per-request timeout. Defaults to 30 seconds."),
                    prefill=DefaultValue(30.0),
                    custom_validate=(validators.NumberInRange(min_value=0.1),),
                ),
            ),
            "cache_ttl": DictElement(
                required=False,
                parameter_form=Float(
                    title=Title("Re-read at most every (seconds)"),
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
                            parameter_form=Float(
                                title=Title("Wait before retrying (seconds)"),
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
                        "'<key>/<element>' so keys stay unique. Set at discovery, "
                        "so pick stable, low-cardinality fields."
                    ),
                    element_template=Dictionary(
                        elements={
                            "path": DictElement(
                                required=True,
                                parameter_form=String(
                                    title=Title("JSON path"),
                                    help_text=Help(
                                        "From the response root, e.g. 'version', "
                                        "'cluster.region', or a '[*]' wildcard like "
                                        "'components[*]' for one label per element."
                                    ),
                                    custom_validate=(validators.LengthInRange(min_value=1),),
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
                                    help_text=Help(
                                        "For a '[*]' path: a path within each "
                                        "element for the label value (e.g. "
                                        "'status'). Defaults to 'true' (a "
                                        "set-membership tag). Ignored without a "
                                        "wildcard."
                                    ),
                                ),
                            ),
                        }
                    ),
                ),
            ),
        },
    )


def _migrate_to_endpoints(value: object) -> dict[str, object]:
    """Wrap a pre-multi-endpoint rule (flat connection at the top level) into
    the current single-key ``{"endpoints": [...]}`` shape."""
    if not isinstance(value, dict):
        raise TypeError(f"Unexpected rule value: {value!r}")
    if "endpoints" in value:
        return value
    return {"endpoints": [value]}


def _parameter_form() -> Dictionary:
    return Dictionary(
        migrate=_migrate_to_endpoints,
        elements={
            "endpoints": DictElement(
                required=True,
                parameter_form=List(
                    title=Title("Endpoints"),
                    help_text=Help(
                        "One or more HTTP/JSON endpoints. Each is fetched with "
                        "its own connection settings and extractions; all results "
                        "are merged into one section. An endpoint that cannot be "
                        "reached only affects its own services."
                    ),
                    element_template=_endpoint(),
                    custom_validate=(_validate_unique_endpoints,),
                ),
            ),
        },
    )


rule_spec_special_agent_json_api = SpecialAgent(
    name="json_api",
    title=Title("Generic JSON API"),
    topic=Topic.APPLICATIONS,
    parameter_form=_parameter_form,
)
