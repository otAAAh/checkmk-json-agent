#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Setup rule for the generic JSON API special agent.

Everything lives in one rule: connection, auth, and the list of fields to
extract (each with optional thresholds / expected-string match). This is the
deliberate UX choice — no separate master-item / discovery / threshold rules.
"""

import re

from cmk.rulesets.v1 import Help, Label, Message, Title
from cmk.rulesets.v1.form_specs import (
    BooleanChoice,
    CascadingSingleChoice,
    CascadingSingleChoiceElement,
    DefaultValue,
    DictElement,
    Dictionary,
    Float,
    InputHint,
    LevelDirection,
    List,
    Password,
    SimpleLevels,
    SingleChoice,
    SingleChoiceElement,
    String,
    migrate_to_password,
    validators,
)
from cmk.rulesets.v1.rule_specs import SpecialAgent, Topic


def _validate_regex(value: str) -> None:
    try:
        re.compile(value)
    except re.error as exc:
        raise validators.ValidationError(
            Message("Invalid regular expression: %s") % str(exc)
        ) from exc


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
        ],
    )


def _extraction() -> Dictionary:
    return Dictionary(
        title=Title("Field to monitor"),
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
                        "one service per array element; multiple '[*]' wildcards "
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
                    title=Title("Item label path (for '[*]' wildcards)"),
                    help_text=Help(
                        "When the path contains a '[*]' wildcard, this optional "
                        "path - relative to each array element, e.g. 'name' or "
                        "'id' - provides the label appended to the service name. "
                        "Defaults to the array index. With multiple '[*]' "
                        "wildcards it is resolved at every level and the labels "
                        "are joined with ' / ' (e.g. '<pod> / <container>'). Pick "
                        "a field that is unique and stable across runs."
                    ),
                ),
            ),
            "levels_upper": DictElement(
                required=False,
                parameter_form=SimpleLevels(
                    title=Title("Upper levels (for numeric values)"),
                    form_spec_template=Float(),
                    level_direction=LevelDirection.UPPER,
                    prefill_fixed_levels=InputHint((0.0, 0.0)),
                ),
            ),
            "levels_lower": DictElement(
                required=False,
                parameter_form=SimpleLevels(
                    title=Title("Lower levels (for numeric values)"),
                    form_spec_template=Float(),
                    level_direction=LevelDirection.LOWER,
                    prefill_fixed_levels=InputHint((0.0, 0.0)),
                ),
            ),
            "expected": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Expected value (regex)"),
                    help_text=Help(
                        "For string values: the service is OK only if the value "
                        "fully matches this regular expression (e.g. 'UP|ok'). "
                        "Otherwise CRIT."
                    ),
                    custom_validate=(_validate_regex,),
                ),
            ),
        },
    )


def _endpoint() -> Dictionary:
    return Dictionary(
        title=Title("Endpoint"),
        elements={
            "url": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("URL"),
                    help_text=Help(
                        "Full URL of the JSON endpoint, including scheme, "
                        "e.g. 'https://app.example.com/actuator/health'."
                    ),
                    custom_validate=(validators.LengthInRange(min_value=1),),
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
