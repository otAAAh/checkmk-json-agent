#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Check parameters for JSON API services.

Thresholds (upper/lower levels) and string matching for a discovered ``JSON
<name>`` service, in a rule of their own so they follow the standard Checkmk
parameter model: a default on a top folder, overridable further down the folder
tree / per host / per service.

The values a field carries in the special-agent rule become the service's
*discovered defaults*; a rule of this ruleset overrides them (standard
precedence: plugin defaults < discovered < this rule). Keeping thresholds here -
rather than only in the special-agent rule - is what lets an operator retune a
level from the service's "Parameters" view without touching the agent
connection, and gives the level a per-folder override it never had while it
lived solely in the (first-match-wins) special-agent rule.
"""

from cmk.rulesets.v1 import Title
from cmk.rulesets.v1.form_specs import DictElement, Dictionary
from cmk.rulesets.v1.rule_specs import CheckParameters, HostAndItemCondition, Topic

from cmk_addons.plugins.json_api.lib import levels_lower, levels_upper, string_match


def _parameter_form() -> Dictionary:
    return Dictionary(
        elements={
            "levels_upper": DictElement(
                required=False,
                parameter_form=levels_upper(),
            ),
            "levels_lower": DictElement(
                required=False,
                parameter_form=levels_lower(),
            ),
            "match": DictElement(
                required=False,
                parameter_form=string_match(),
            ),
        },
    )


rule_spec_json_api_check = CheckParameters(
    name="json_api",
    title=Title("Generic JSON API"),
    topic=Topic.APPLICATIONS,
    parameter_form=_parameter_form,
    condition=HostAndItemCondition(item_title=Title("Service name")),
)
