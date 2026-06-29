#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Setup rule for the generic JSON API special agent.

Everything lives in one rule: connection, auth, and the list of fields to
extract (each with optional thresholds / expected-string match). This is the
deliberate UX choice — no separate master-item / discovery / threshold rules.
"""

from cmk.rulesets.v1 import Help, Label, Title
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
                        "one service per array element. A leading '$.' is optional."
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
                        "Defaults to the array index. Pick a field that is unique "
                        "and stable across runs."
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
                ),
            ),
        },
    )


def _parameter_form() -> Dictionary:
    return Dictionary(
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


rule_spec_special_agent_json_api = SpecialAgent(
    name="json_api",
    title=Title("Generic JSON API"),
    topic=Topic.APPLICATIONS,
    parameter_form=_parameter_form,
)
