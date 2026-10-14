#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""A field's 'Interpret the value as' choice: the value as it stands, as a counter
whose rate is monitored, or as a timestamp whose age is.
"""

import re
from collections.abc import Mapping
from datetime import datetime

from cmk.rulesets.v1 import Message
from cmk.rulesets.v1.form_specs import validators


def _validate_strptime_pattern(value: str) -> None:
    """A custom timestamp pattern must be one strptime can use at all.

    A typo such as '%Q' is otherwise only noticed at check time, as every value
    being 'Not a valid timestamp'; and a pattern without a single directive
    matches one literal string, which cannot be what was meant.
    """
    # A trailing lone '%' is checked here rather than left to strptime: which
    # error it raises for one differs between Python patch releases (3.12.13
    # reports a mismatch, which reads like a usable pattern).
    if value.replace("%%", "").endswith("%"):
        raise validators.ValidationError(
            Message("A '%' must be followed by a directive; write '%%' for a literal '%'.")
        )
    try:
        datetime.strptime("", value)
    except ValueError as exc:
        if "does not match format" not in str(exc):
            raise validators.ValidationError(
                Message("This is not a valid format pattern: %s") % str(exc)
            ) from exc
    if not re.search(r"%[^%]", value.replace("%%", "")):
        raise validators.ValidationError(
            Message("The pattern needs at least one directive, e.g. '%Y' for the year.")
        )


def _validate_timestamp_format(value: Mapping[str, object]) -> None:
    """'Custom format' needs a pattern to read the value with."""
    if value.get("format") == "custom" and not value.get("pattern"):
        raise validators.ValidationError(Message("'Custom format' needs a format pattern."))
