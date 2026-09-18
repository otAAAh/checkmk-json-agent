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
from collections import Counter
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
                Message(
                    "Only the variables 'value' and 'other', numbers, parentheses "
                    "and + - * / are allowed."
                )
            )
        if isinstance(node, ast.Name) and node.id not in ("value", "other"):
            raise validators.ValidationError(
                Message("Unknown variable '%s' - only 'value' and 'other' are available.") % node.id
            )
        if isinstance(node, ast.Constant) and (
            isinstance(node.value, bool) or not isinstance(node.value, (int, float))
        ):
            raise validators.ValidationError(Message("Only numeric constants are allowed."))


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


def _validate_pagination(value: object) -> None:
    """The two paths of a pagination setting must name single places.

    A '[*]' wildcard expands to many values; the collection to merge is ONE
    container and the next-page link is ONE URL, so a wildcard in either is a
    configuration that cannot mean anything - rejected here rather than becoming
    an endpoint that reports "no collection at ..." at runtime.
    """
    if not isinstance(value, dict):
        return
    items = value.get("items")
    if isinstance(items, str) and "[*]" in items:
        raise validators.ValidationError(
            Message(
                "The collection to merge must name the collection itself, "
                "without a '[*]' wildcard - e.g. 'data.items' rather than "
                "'data.items[*]'."
            )
        )
    nxt = value.get("next")
    if not (isinstance(nxt, (tuple, list)) and len(nxt) == 2 and nxt[0] == "body"):
        return
    if isinstance(nxt[1], str) and "[*]" in nxt[1]:
        raise validators.ValidationError(
            Message("The path to the next page's URL must not contain a '[*]' wildcard.")
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


def _calc_uses_other(expression: object) -> bool:
    """Whether a calc expression names the second operand.

    Parsed rather than substring-matched, so a path or a number that merely
    contains the letters 'other' is not mistaken for the variable.
    """
    if not isinstance(expression, str) or not expression.strip():
        return False
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False  # _validate_calc reports the syntax error itself
    return any(isinstance(node, ast.Name) and node.id == "other" for node in ast.walk(tree))


def _validate_extraction(value: object) -> None:
    """The transform and its second path must agree.

    Either half alone is a silent no-op - an expression naming 'other' with no
    second path can never be computed, and a second path no expression uses is
    resolved and thrown away - so both are rejected at configuration time rather
    than becoming a puzzling UNKNOWN (or nothing at all) on the service.
    """
    if not isinstance(value, dict):
        return
    # A field sent to the inventory INSTEAD of a service has no line to
    # contribute: it is written to the tree and creates nothing. Reporting it
    # into a shared service is therefore a contradiction rather than a
    # combination, and silently ignoring one half of it would be worse.
    inventory = value.get("inventory")
    if value.get("group") and isinstance(inventory, dict) and not inventory.get("keep_service"):
        raise validators.ValidationError(
            Message(
                "A field written to the inventory creates no service, so it cannot "
                "report into a shared one. Tick 'Also create a service for this "
                "field', or clear 'Report in a shared service named'."
            )
        )
    # Host labels for a host this extraction never creates would land nowhere:
    # they are resolved per '[*]' element and attached to the host that element
    # becomes, so without a piggyback host name there is nothing to attach to.
    if value.get("piggyback_labels") and not (
        isinstance(value.get("piggyback_host"), str) and value["piggyback_host"].strip()
    ):
        raise validators.ValidationError(
            Message(
                "'Labels for the created host' needs 'Create one host per element, "
                "named by this field' - without it the element stays a service on "
                "this host and there is no host to label."
            )
        )
    calc = value.get("calc")
    has_path = isinstance(value.get("calc_path"), str) and value["calc_path"].strip()
    uses_other = _calc_uses_other(calc)
    if uses_other and not has_path:
        raise validators.ValidationError(
            Message(
                "The transform uses 'other', so 'Second path for the transform' "
                "must be set to say where that value comes from."
            )
        )
    if has_path and not uses_other:
        raise validators.ValidationError(
            Message(
                "'Second path for the transform' is only used through the "
                "variable 'other' - add it to the transform (e.g. "
                "'value / other * 100') or clear the path."
            )
        )


def _validate_label_spec(value: object) -> None:
    """A label spec has to be able to produce both halves of its label.

    A path supplies the value and, by default, the key. Without one the label is
    described entirely by the rule - the 'if this matches, tag the host' case -
    and then both halves have to be written down, or there is nothing to emit.
    """
    if not isinstance(value, dict) or value.get("path"):
        return
    if not value.get("key") or not value.get("value"):
        raise validators.ValidationError(
            Message(
                "Without a 'JSON path' the label is described by the rule alone, so "
                "both a 'Label key' and a literal 'Label value' are needed."
            )
        )


def _validate_host_label(value: object) -> None:
    """A host-label spec: the label-spec rules, plus one value source only."""
    _validate_label_spec(value)
    if isinstance(value, dict) and value.get("value") and value.get("value_field"):
        raise validators.ValidationError(
            Message(
                "Set either a literal 'Label value' or a 'Value field' to read it from, not both."
            )
        )


# A summary template: literal text with '{path}' placeholders, no nesting. The
# same shape the agent's _SUMMARY_PLACEHOLDER resolves, checked here so a stray
# brace is a form error rather than a placeholder that silently never renders.
_SUMMARY_TEMPLATE_PATTERN = re.compile(r"[^{}]*(\{[^{}]+\}[^{}]*)*\Z")


def _validate_value_range(value: object) -> None:
    """A range has to be a range: at least one end, and the right way round."""
    if not isinstance(value, dict):
        return
    low, high = value.get("min"), value.get("max")
    if low is None and high is None:
        raise validators.ValidationError(
            Message("Give at least one end of the range, or leave the range unset.")
        )
    if low is not None and high is not None and low >= high:
        raise validators.ValidationError(
            Message("The lowest value must be smaller than the highest value.")
        )


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


# One segment of an inventory tree path. The tree is keyed by these all over
# Checkmk (views, "Search hosts by inventory data", reports), so a typo here
# produces a malformed node that is awkward to clean up per host - checked in
# Setup instead.
_INVENTORY_SEGMENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\Z")
_INVENTORY_ROOTS = ("hardware", "software", "networking")


def _validate_inventory_node(value: str) -> None:
    segments = [segment.strip() for segment in value.strip().split(".")]
    if len(segments) < 2 or not all(
        _INVENTORY_SEGMENT_PATTERN.match(segment) for segment in segments
    ):
        raise validators.ValidationError(
            Message(
                "Enter a dotted inventory path of at least two segments, using "
                "lower-case letters, digits and '_' - e.g. "
                "'software.applications.json_api'."
            )
        )
    if segments[0] not in _INVENTORY_ROOTS:
        raise validators.ValidationError(
            Message("The inventory path must start with 'hardware', 'software' or 'networking'.")
        )


def _validate_inventory_key(value: str) -> None:
    if value.strip() and not _INVENTORY_SEGMENT_PATTERN.match(value.strip()):
        raise validators.ValidationError(
            Message(
                "Use lower-case letters, digits and '_' for the attribute name, e.g. 'version'."
            )
        )


def _inventory() -> Dictionary:
    return Dictionary(
        title=Title("Write into the HW/SW inventory"),
        help_text=Help(
            "Put this field into the host's inventory tree instead of making it a "
            "service. Meant for facts rather than states - a version, a build, a "
            "region, a licence tier - which are not worth a service that is OK "
            "forever, and which the inventory can do something with that services "
            "cannot: it is searchable ACROSS hosts ('which hosts still run a "
            "version below 4.2?') and keeps a history of its own. For a '[*]' "
            "wildcard the elements become one table row each, keyed by the "
            "element's name. Point this at values that rarely change: every change "
            "is recorded in the inventory history, so a counter here grows those "
            "files without bound. Inventory runs on its own (slower) schedule, not "
            "at every check interval."
        ),
        elements={
            "node": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("Inventory tree node"),
                    help_text=Help(
                        "Dotted path of the node the value is written to, e.g. "
                        "'software.applications.json_api'. Must start with "
                        "'hardware', 'software' or 'networking'."
                    ),
                    prefill=DefaultValue("software.applications.json_api"),
                    custom_validate=(_validate_inventory_node,),
                ),
            ),
            "key": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Attribute name"),
                    help_text=Help(
                        "Name of the attribute (or, for a '[*]' wildcard, the "
                        "column). Defaults to the JSON path's last segment."
                    ),
                    custom_validate=(_validate_inventory_key,),
                ),
            ),
            "keep_service": DictElement(
                required=True,
                parameter_form=BooleanChoice(
                    label=Label("Also create a service for this field"),
                    help_text=Help(
                        "Off by default: an inventory field creates NO service, "
                        "which is the point - it does not consume a service slot "
                        "or a check interval to report something that changes "
                        "twice a year. Turn it on for a value you want in the "
                        "inventory AND want to alert on, e.g. a version you also "
                        "match against a regex."
                    ),
                    prefill=DefaultValue(False),
                ),
            ),
        },
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
                name="auth_oauth2",
                title=Title("OAuth 2.0 (client credentials)"),
                parameter_form=Dictionary(
                    help_text=Help(
                        "The machine-to-machine OAuth 2.0 grant: the agent exchanges a "
                        "client ID and secret for a short-lived access token and sends "
                        "it as 'Authorization: Bearer <token>'. The token is cached "
                        "until shortly before it expires, so a rule polling every "
                        "minute does not ask the identity provider every minute. No "
                        "browser and no user consent are involved - if your API only "
                        "works with a token obtained by a person logging in, this is "
                        "not the right mode."
                    ),
                    elements={
                        "token_url": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Token URL"),
                                help_text=Help(
                                    "The identity provider's token endpoint, e.g. "
                                    "'https://login.example.com/oauth2/v2.0/token'. "
                                    "This is NOT the API URL above."
                                ),
                                prefill=InputHint("https://login.example.com/oauth2/v2.0/token"),
                                custom_validate=(_validate_url,),
                            ),
                        ),
                        "client_id": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Client ID"),
                                custom_validate=(validators.LengthInRange(min_value=1),),
                            ),
                        ),
                        "client_secret": DictElement(
                            required=True,
                            parameter_form=Password(
                                title=Title("Client secret"),
                                migrate=migrate_to_password,
                            ),
                        ),
                        "scope": DictElement(
                            required=False,
                            parameter_form=String(
                                title=Title("Scope"),
                                help_text=Help(
                                    "Space-separated scopes to request, if the provider "
                                    "needs them - e.g. 'api://monitoring/.default'."
                                ),
                            ),
                        ),
                        "audience": DictElement(
                            required=False,
                            parameter_form=String(
                                title=Title("Audience"),
                                help_text=Help(
                                    "Sent as the 'audience' parameter. Required by some "
                                    "providers (Auth0, for instance) to say which API "
                                    "the token is for; leave empty otherwise."
                                ),
                            ),
                        ),
                        "client_auth": DictElement(
                            required=False,
                            parameter_form=SingleChoice(
                                title=Title("How to send the client credentials"),
                                help_text=Help(
                                    "RFC 6749 allows both, and providers disagree about "
                                    "which they accept - a wrong choice here shows up as "
                                    "an unhelpful 401 from the token URL. Try the "
                                    "Authorization header first; switch to the request "
                                    "body if the provider rejects it."
                                ),
                                elements=[
                                    SingleChoiceElement(
                                        name="basic",
                                        title=Title("In the Authorization header (HTTP basic)"),
                                    ),
                                    SingleChoiceElement(
                                        name="post",
                                        title=Title("In the request body"),
                                    ),
                                ],
                                prefill=DefaultValue("basic"),
                            ),
                        ),
                    },
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

    Anything that is not a dictionary yields an EMPTY extraction rather than an
    exception. A migrate runs while the form is being RENDERED, so raising here
    takes down the whole Setup page instead of flagging a field: emptying a
    required entry and saving hands this ``None`` on the re-render, and the
    operator loses the rule form rather than being shown which box to fill in.
    Degrading leaves an empty row for exactly that box.
    """
    if not isinstance(value, dict):
        return {}
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


def _element_filter(title: Title, help_text: Help) -> Dictionary:
    """The 'only the elements matching a condition' predicate.

    Shared by a field's own element filter and by the label specs, which select
    the elements that produce a label with exactly the same three fields. Title
    and help come from the caller, because what the condition selects - services
    or labels - is what differs between them.
    """
    return Dictionary(
        title=title,
        help_text=help_text,
        elements={
            "path": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("Field path (within each element)"),
                    help_text=Help(
                        "Resolved within each element, e.g. 'status' or 'metadata.phase'."
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
    )


def _extraction() -> Dictionary:
    return Dictionary(
        title=Title("Field to monitor"),
        migrate=_migrate_extraction,
        custom_validate=(_validate_extraction,),
        elements={
            "service": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("Service name"),
                    help_text=Help(
                        "Becomes the Checkmk service description for this field - "
                        "or, where the field reports into a shared service, the "
                        "name of its line within that service."
                    ),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "group": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Report in a shared service named"),
                    help_text=Help(
                        "By default every field becomes a service of its own. "
                        "Name a shared service here and this field reports into "
                        "that one service as a LINE instead, alongside the other "
                        "fields naming it - so a small API of 'status', "
                        "'component' and 'timestamp' can be one service rather "
                        "than three. The service's state is the WORST of its "
                        "lines, which is how Checkmk aggregates any check, and "
                        "'Service name' above then names the line rather than the "
                        "service. Each line keeps its own levels, string matching "
                        "and transform from this rule. Two things change, both "
                        "because one service now holds several fields: a "
                        "'Generic JSON API' check-parameters rule cannot describe "
                        "it and is not applied to it, so thresholds for these "
                        "fields live here; and each line's metric is named after "
                        "the line, which keeps the history of several fields "
                        "apart but renders as a plain number rather than in the "
                        "field's unit. A field that needs its unit on the graph, "
                        "or per-service thresholds, is better off with a service "
                        "of its own."
                    ),
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
                        "\"data['foo.bar'].value\". To monitor a RESPONSE HEADER "
                        "instead of a field of the body, prefix its name with "
                        "'@header.', e.g. '@header.X-RateLimit-Remaining' for an "
                        "API quota or '@header.Last-Modified' with 'Interpret the "
                        "value as' set to a timestamp for the age of the data. "
                        "Header names are matched case-insensitively and none of "
                        "the path syntax above applies to them."
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
            "piggyback_labels": DictElement(
                required=False,
                parameter_form=List(
                    title=Title("Labels for the created host"),
                    help_text=Help(
                        "Checkmk HOST labels for the host each '[*]' element becomes, "
                        "built from fields within that element - so the 50 hosts a "
                        "'nodes[*]' rule creates can carry their own region, role or "
                        "tier and be targeted by folder rules, views and filters. "
                        "Needs 'Create one host per element' above; without it the "
                        "element stays a service on this host and there is nothing "
                        "to label. Each key is prefixed with 'json_api/'. A label can "
                        "also be a classification rather than a field: a condition on "
                        "the element plus a literal value tags only the hosts the "
                        "condition holds for. These are HOST labels, unlike 'Service "
                        "labels' below, which describe the individual service."
                    ),
                    element_template=Dictionary(
                        custom_validate=(_validate_label_spec,),
                        elements={
                            "path": DictElement(
                                required=False,
                                parameter_form=String(
                                    title=Title("JSON path"),
                                    help_text=Help(
                                        "Relative to each '[*]' element, e.g. "
                                        "'region'. Can be left empty only where a "
                                        "condition and a literal value describe the "
                                        "label on their own."
                                    ),
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
                parameter_form=_element_filter(
                    Title("Only elements matching a condition"),
                    Help(
                        "For a '[*]' wildcard or an aggregated path: keep only the "
                        "elements whose sub-field matches this condition - e.g. one "
                        "service per node whose 'status' is not 'ok', or count only "
                        "the pods that are not 'Running'. The field path is resolved "
                        "within each element; an element whose field is missing or "
                        "is not a scalar is dropped. Without a '[*]' wildcard or an "
                        "aggregation this has no effect."
                    ),
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
            "value_range": DictElement(
                required=False,
                parameter_form=Dictionary(
                    title=Title("Value range (for graphs and gauges)"),
                    help_text=Help(
                        "The range this value moves in, when it has one: a "
                        "battery percentage is 0 to 100, a queue with a cap is 0 "
                        "to that cap. It changes nothing about the state - it "
                        "tells Checkmk what 'full' means, so the graph keeps a "
                        "steady scale instead of rescaling to whatever the last "
                        "hour happened to contain, a gauge dashboard widget has "
                        "a dial to draw, and the service list's bar is filled "
                        "against the real maximum. Leave it unset for a value "
                        "with no natural limit; the bar is then scaled to the "
                        "critical level instead, where one is configured."
                    ),
                    custom_validate=(_validate_value_range,),
                    elements={
                        "min": DictElement(
                            required=False,
                            parameter_form=Float(
                                title=Title("Lowest possible value"),
                                prefill=DefaultValue(0.0),
                            ),
                        ),
                        "max": DictElement(
                            required=False,
                            parameter_form=Float(
                                title=Title("Highest possible value"),
                                prefill=InputHint(100.0),
                            ),
                        ),
                    },
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
                        "'(value - 32) * 5 / 9' (Fahrenheit to Celsius). A second "
                        "field can be brought in as 'other' - see below - which is "
                        "what turns a used/total pair into a percentage: "
                        "'value / other * 100'."
                    ),
                    prefill=InputHint("value / 1024 / 1024"),
                    custom_validate=(_validate_calc,),
                ),
            ),
            "calc_path": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Second path for the transform ('other')"),
                    help_text=Help(
                        "A second field, made available to the transform above as "
                        "the variable 'other'. Most APIs report a used/total or "
                        "current/limit pair rather than a percentage, so this is "
                        "what lets one service monitor the ratio: point the JSON "
                        "path at 'used', this at 'total', and use "
                        "'value / other * 100' with the unit set to '%'. It is "
                        "resolved in the SAME scope as the value - within each "
                        "'[*]' element, or the response root without a wildcard - "
                        "so every element is compared against its own total. The "
                        "transform must actually use 'other', and 'other' requires "
                        "this path; either one alone is rejected."
                    ),
                    prefill=InputHint("total"),
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
            "inventory": DictElement(
                required=False,
                parameter_form=_inventory(),
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
                        "agent follows the API's own next-page link and appends "
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
                                    "Both forms are common; the API's "
                                    "documentation says which one it uses. A "
                                    "page that carries no next link (an absent "
                                    "field, a JSON 'null', an empty string, no "
                                    "'Link' header) is the last one, which is how "
                                    "pagination ends."
                                ),
                                elements=[
                                    CascadingSingleChoiceElement(
                                        name="body",
                                        title=Title("A field in the response body"),
                                        parameter_form=String(
                                            title=Title("JSON path to the next page's URL"),
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
                                ],
                                prefill=DefaultValue("body"),
                            ),
                        ),
                        "items": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("JSON path to the collection to merge"),
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


def _migrate_to_endpoints(value: object) -> dict[str, object]:
    """Wrap a pre-multi-endpoint rule (flat connection at the top level) into
    the current single-key ``{"endpoints": [...]}`` shape.

    Like :func:`_migrate_extraction`, a value that is not a dictionary degrades
    to an empty rule instead of raising: this also runs during rendering, where
    an exception costs the operator the entire form.
    """
    if not isinstance(value, dict):
        return {"endpoints": []}
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
