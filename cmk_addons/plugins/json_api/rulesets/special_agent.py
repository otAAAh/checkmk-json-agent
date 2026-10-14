#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Setup rule for the generic JSON API special agent.

Everything lives in one rule: connection, auth, and the list of fields to
extract (each with optional thresholds / expected-string match). This is the
deliberate UX choice — no separate master-item / discovery / threshold rules.

This module is the rule itself. Its sections are built in ``forms/`` below the
family root, one module per section and each importing only from the ones
before it: ``common`` (what several sections share), ``metrics``, ``labels``,
``value_as``, ``inventory``, ``authentication``, ``pagination``, ``extraction``
(one field) and ``endpoint`` (one endpoint). They live outside ``rulesets/``
because the plugin loader imports every module there as a ruleset plugin; like
``lib`` and ``migrations`` they are imported relatively and ship in the MKP.
"""

from cmk.rulesets.v1 import Help, Title
from cmk.rulesets.v1.form_specs import DictElement, Dictionary, List
from cmk.rulesets.v1.rule_specs import SpecialAgent, Topic

from ..forms.authentication import _validate_header_name
from ..forms.common import _validate_query_parameter, _validate_url
from ..forms.endpoint import _validate_endpoint, endpoint_form, validate_unique_endpoints
from ..forms.extraction import (
    _calc_uses_other,
    _extraction,
    _validate_calc,
    _validate_extraction,
    _validate_summary,
    _validate_value_range,
)
from ..forms.inventory import (
    _INVENTORY_ROW_KEY,
    _validate_inventory_key,
    _validate_inventory_node,
)
from ..forms.labels import _validate_host_label, _validate_label_spec
from ..forms.metrics import (
    _DECLARED_METRICS,
    _field_metric_name,
    _metric_slug,
    _validate_metric_name,
    _validate_unique_group_metrics,
)
from ..forms.pagination import _validate_pagination
from ..forms.value_as import _validate_strptime_pattern, _validate_timestamp_format
from ..lib import validate_regex
from ..migrations import migrate_extraction as _migrate_extraction
from ..migrations import migrate_to_endpoints as _migrate_to_endpoints

# Re-exported so the special-agent rule's field validators keep their original
# names; the shared implementation now lives in ``lib`` (used by the
# check-parameters rule too).
_validate_regex = validate_regex

# The section helpers above are re-exported under the names they had while they
# lived in this module: the tests reach them through it, as the separately
# released Explorer reaches endpoint_form and validate_unique_endpoints.
__all__ = [
    "_DECLARED_METRICS",
    "_INVENTORY_ROW_KEY",
    "_calc_uses_other",
    "_endpoint",
    "_extraction",
    "_field_metric_name",
    "_metric_slug",
    "_migrate_extraction",
    "_migrate_to_endpoints",
    "_validate_calc",
    "_validate_endpoint",
    "_validate_extraction",
    "_validate_header_name",
    "_validate_host_label",
    "_validate_inventory_key",
    "_validate_inventory_node",
    "_validate_label_spec",
    "_validate_metric_name",
    "_validate_pagination",
    "_validate_query_parameter",
    "_validate_regex",
    "_validate_strptime_pattern",
    "_validate_summary",
    "_validate_timestamp_format",
    "_validate_unique_endpoints",
    "_validate_unique_group_metrics",
    "_validate_url",
    "_validate_value_range",
    "endpoint_form",
    "rule_spec_special_agent_json_api",
    "validate_unique_endpoints",
]


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
                    element_template=endpoint_form(),
                    custom_validate=(validate_unique_endpoints,),
                ),
            ),
        },
    )


# The former names of endpoint_form and validate_unique_endpoints. The
# json_api_explorer package imports them, and it is released separately, so an
# older Explorer next to this agent package must still find them.
_endpoint = endpoint_form
_validate_unique_endpoints = validate_unique_endpoints

rule_spec_special_agent_json_api = SpecialAgent(
    name="json_api",
    title=Title("Generic JSON API"),
    topic=Topic.APPLICATIONS,
    parameter_form=_parameter_form,
)
