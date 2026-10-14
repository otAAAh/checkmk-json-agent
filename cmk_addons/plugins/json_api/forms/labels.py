#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Labels built from the response: a field's service labels and the labels of the
host a '[*]' element becomes, and the endpoint's host labels.
"""

from cmk.rulesets.v1 import Message
from cmk.rulesets.v1.form_specs import validators


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
