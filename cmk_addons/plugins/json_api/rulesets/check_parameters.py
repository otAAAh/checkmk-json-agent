#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Check parameters for JSON API services and endpoints.

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

The endpoint services ("JSON API <name>", one per configured endpoint) have their
own ruleset here: their parameters describe the *request* - how long it may take,
and how bad it is when it fails - which has nothing to do with a field's
thresholds, and they are never configured in the special-agent rule at all.
"""

from cmk.rulesets.v1 import Help, Title
from cmk.rulesets.v1.form_specs import (
    DefaultValue,
    DictElement,
    Dictionary,
    Float,
    InputHint,
    LevelDirection,
    ServiceState,
    SimpleLevels,
)
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


def _endpoint_parameter_form() -> Dictionary:
    return Dictionary(
        elements={
            "response_time_levels": DictElement(
                required=False,
                parameter_form=SimpleLevels(
                    title=Title("Upper levels for the response time"),
                    help_text=Help(
                        "How long the request to this endpoint may take, in seconds. "
                        "Measured from sending the request to having read the whole "
                        "response body. Without levels the response time is only "
                        "recorded as a metric."
                    ),
                    form_spec_template=Float(),
                    level_direction=LevelDirection.UPPER,
                    prefill_fixed_levels=InputHint((2.0, 5.0)),
                ),
            ),
            "cert_expiry_levels": DictElement(
                required=False,
                parameter_form=SimpleLevels(
                    title=Title("Lower levels for the TLS certificate's remaining validity"),
                    help_text=Help(
                        "How many days the endpoint's TLS certificate must still be "
                        "valid for. Read from the connection the agent already makes, "
                        "so no second check against the same URL is needed. Only "
                        "available for HTTPS endpoints with certificate verification "
                        "enabled; otherwise the certificate is not reported and these "
                        "levels never apply. Without levels the remaining validity is "
                        "only recorded as a metric."
                    ),
                    form_spec_template=Float(unit_symbol="days"),
                    level_direction=LevelDirection.LOWER,
                    prefill_fixed_levels=InputHint((30.0, 7.0)),
                ),
            ),
            "state_retried": DictElement(
                required=False,
                parameter_form=ServiceState(
                    title=Title("State when the request needed a retry"),
                    help_text=Help(
                        "Applies when the endpoint's retry policy absorbed a failed "
                        "attempt: the request did succeed, but not on the first try. "
                        "OK by default - a retry doing its job is not itself a "
                        "problem. Raise it for an endpoint whose flakiness is worth "
                        "knowing about, so retries never quietly turn a degrading "
                        "API into a permanently green service."
                    ),
                    prefill=DefaultValue(ServiceState.OK),
                ),
            ),
            "state_unreachable": DictElement(
                required=False,
                parameter_form=ServiceState(
                    title=Title("State when the endpoint cannot be read"),
                    help_text=Help(
                        "Applies when the request fails outright: connection refused, "
                        "a TLS error, a timeout, an HTTP status that is not accepted, "
                        "or a response that is not JSON."
                    ),
                    prefill=DefaultValue(ServiceState.CRIT),
                ),
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

rule_spec_json_api_endpoint_check = CheckParameters(
    name="json_api_endpoint",
    title=Title("Generic JSON API endpoint"),
    topic=Topic.APPLICATIONS,
    parameter_form=_endpoint_parameter_form,
    condition=HostAndItemCondition(item_title=Title("Endpoint")),
)
