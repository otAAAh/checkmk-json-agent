#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Labels built from the response: a field's service labels and the labels of the
host a '[*]' element becomes, and the endpoint's host labels.
"""

from cmk.rulesets.v1 import Help, Message, Title
from cmk.rulesets.v1.form_specs import (
    DictElement,
    Dictionary,
    FieldSize,
    List,
    String,
    validators,
)

from .common import _element_filter


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


def _piggyback_labels() -> List:
    return List(
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
                        field_size=FieldSize.LARGE,
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
    )


def _service_labels() -> List:
    return List(
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
                        field_size=FieldSize.LARGE,
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
    )


def _host_labels() -> List:
    return List(
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
    )
