#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""A field's 'Write into the HW/SW inventory' settings and the tree paths they
accept.
"""

import re

from cmk.rulesets.v1 import Help, Label, Message, Title
from cmk.rulesets.v1.form_specs import (
    BooleanChoice,
    DefaultValue,
    DictElement,
    Dictionary,
    String,
    validators,
)

# The column a '[*]' wildcard's inventory table is keyed by. Mirrors
# _INVENTORY_ROW_KEY in agent_based/json_api.py, which is the module that writes
# the tree; kept as a literal rather than imported, because the ruleset and the
# check are loaded independently by Checkmk.
_INVENTORY_ROW_KEY = "element"


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
            "wildcard the elements become one table row each, keyed by an "
            "'element' column holding the element's name. Point this at values "
            "that rarely change: every change is recorded in the inventory "
            "history, so a counter here grows those files without bound. "
            "Inventory runs on its own (slower) schedule, not at every check "
            "interval."
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
                        "column). Defaults to the JSON path's last segment. For "
                        "a '[*]' wildcard it cannot be 'element': that is the "
                        "column the table rows themselves are keyed by."
                    ),
                    custom_validate=(_validate_inventory_key,),
                ),
            ),
            "keep_service": DictElement(
                required=True,
                parameter_form=BooleanChoice(
                    title=Title("Service"),
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
