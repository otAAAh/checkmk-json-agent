#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Form specs shared between the special-agent rule and the check-parameters rule.

The special agent (``rulesets/special_agent.py``) carries the *discovered
defaults* for a field's thresholds / string matching; the check-parameters rule
(``rulesets/check_parameters.py``) lets those same values be overridden per
folder / host / service. Both build the identical form controls, so the widgets
(and their translated strings) live here once. This module is not a plugin part
- the discovery loader ignores it - but it ships in the MKP (packaged by
``rglob`` from the family root) and is importable at runtime as
``cmk_addons.plugins.json_api.lib``.
"""

import re

from cmk.rulesets.v1 import Help, Message, Title
from cmk.rulesets.v1.form_specs import (
    CascadingSingleChoice,
    CascadingSingleChoiceElement,
    DefaultValue,
    DictElement,
    Dictionary,
    Float,
    InputHint,
    LevelDirection,
    ServiceState,
    SimpleLevels,
    String,
    validators,
)


def validate_regex(value: str) -> None:
    try:
        re.compile(value)
    except re.error as exc:
        raise validators.ValidationError(
            Message("Invalid regular expression: %s") % str(exc)
        ) from exc


def levels_upper() -> SimpleLevels:
    return SimpleLevels(
        title=Title("Upper levels (for numeric values)"),
        form_spec_template=Float(),
        level_direction=LevelDirection.UPPER,
        prefill_fixed_levels=InputHint((0.0, 0.0)),
    )


def levels_lower() -> SimpleLevels:
    return SimpleLevels(
        title=Title("Lower levels (for numeric values)"),
        form_spec_template=Float(),
        level_direction=LevelDirection.LOWER,
        prefill_fixed_levels=InputHint((0.0, 0.0)),
    )


def string_match() -> CascadingSingleChoice:
    return CascadingSingleChoice(
        title=Title("String matching"),
        help_text=Help("How to turn a string value into a Checkmk service state."),
        prefill=DefaultValue("must_match"),
        elements=[
            CascadingSingleChoiceElement(
                name="must_match",
                title=Title("Value must match a regular expression"),
                parameter_form=Dictionary(
                    elements={
                        "pattern": DictElement(
                            required=True,
                            parameter_form=String(
                                title=Title("Expected value (regex)"),
                                help_text=Help(
                                    "For string values: the value must fully match "
                                    "this regular expression (e.g. 'UP|ok')."
                                ),
                                custom_validate=(validate_regex,),
                            ),
                        ),
                        "state_no_match": DictElement(
                            required=False,
                            parameter_form=ServiceState(
                                title=Title("State when the value does not match"),
                                prefill=DefaultValue(ServiceState.CRIT),
                            ),
                        ),
                    },
                ),
            ),
            CascadingSingleChoiceElement(
                name="state_map",
                title=Title("Map the value to a state (OK / WARN / CRIT)"),
                parameter_form=Dictionary(
                    help_text=Help(
                        "For string values with several known states: each pattern "
                        "is a regular expression matched against the whole value. "
                        "They are tried in the order OK, WARN, CRIT and the first "
                        "match wins."
                    ),
                    elements={
                        "ok": DictElement(
                            required=False,
                            parameter_form=String(
                                title=Title("OK when the value matches (regex)"),
                                custom_validate=(validate_regex,),
                            ),
                        ),
                        "warn": DictElement(
                            required=False,
                            parameter_form=String(
                                title=Title("WARN when the value matches (regex)"),
                                custom_validate=(validate_regex,),
                            ),
                        ),
                        "crit": DictElement(
                            required=False,
                            parameter_form=String(
                                title=Title("CRIT when the value matches (regex)"),
                                custom_validate=(validate_regex,),
                            ),
                        ),
                        "state_no_match": DictElement(
                            required=False,
                            parameter_form=ServiceState(
                                title=Title("State when nothing matches"),
                                prefill=DefaultValue(ServiceState.OK),
                            ),
                        ),
                    },
                ),
            ),
        ],
    )
