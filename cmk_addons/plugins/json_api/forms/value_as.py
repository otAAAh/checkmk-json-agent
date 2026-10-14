#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""A field's 'Interpret the value as' choice: the value as it stands, as a counter
whose rate is monitored, or as a timestamp whose age is.
"""

import re
from collections.abc import Mapping
from datetime import datetime

from cmk.rulesets.v1 import Help, Label, Message, Title
from cmk.rulesets.v1.form_specs import (
    CascadingSingleChoice,
    CascadingSingleChoiceElement,
    DefaultValue,
    DictElement,
    Dictionary,
    FieldSize,
    FixedValue,
    SingleChoice,
    SingleChoiceElement,
    String,
    validators,
)


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


def _value_as() -> CascadingSingleChoice:
    return CascadingSingleChoice(
        title=Title("Interpret the value as"),
        help_text=Help(
            "By default the extracted value is monitored as it stands. "
            "Two common API values are worth deriving something else "
            "from: a counter that only ever grows (its rate of change "
            "is what matters, not the total), and a timestamp (its age "
            "is what matters, not the date). The derived number is what "
            "the transform, the levels, the metric and the summary then "
            "use."
        ),
        prefill=DefaultValue("counter"),
        elements=[
            CascadingSingleChoiceElement(
                name="counter",
                title=Title("A counter - monitor its per-second rate"),
                parameter_form=FixedValue(
                    value=None,
                    title=Title("Per-second rate"),
                    label=Label(
                        "The difference to the previous check is divided by the elapsed time"
                    ),
                    help_text=Help(
                        "For monotonically growing counters such as "
                        "'requests_total' or 'bytes_sent': the check "
                        "monitors the change per second instead of the "
                        "absolute total. The first check after a "
                        "restart of the counter cannot compute a rate "
                        "yet and keeps the service's previous state."
                    ),
                ),
            ),
            CascadingSingleChoiceElement(
                name="timestamp",
                title=Title("A timestamp - monitor its age"),
                parameter_form=Dictionary(
                    help_text=Help(
                        "For values such as 'last_backup' or "
                        "'updated_at': the check monitors the number of "
                        "seconds since that point in time, so upper "
                        "levels alert on stale data. A timestamp in the "
                        "future yields a negative age. The age graphs "
                        "and reads as a duration on its own; choose a "
                        "unit below only to override that (e.g. after a "
                        "transform into hours)."
                    ),
                    elements={
                        "format": DictElement(
                            required=True,
                            parameter_form=SingleChoice(
                                title=Title("Timestamp format"),
                                help_text=Help(
                                    "'Detect automatically' reads a "
                                    "number as Unix epoch seconds "
                                    "(milliseconds when it is far too "
                                    "large for seconds) and text as "
                                    "ISO 8601, as an HTTP-date ('Wed, "
                                    "21 Oct 2015 07:28:00 GMT') or in "
                                    "Go's default layout ('2015-10-21 "
                                    "07:28:00 +0000 UTC'). 'Custom "
                                    "format' reads text with the pattern "
                                    "below. A timestamp without a time "
                                    "zone is read as UTC."
                                ),
                                elements=[
                                    SingleChoiceElement("auto", Title("Detect automatically")),
                                    SingleChoiceElement("epoch", Title("Unix epoch seconds")),
                                    SingleChoiceElement(
                                        "epoch_ms",
                                        Title("Unix epoch milliseconds"),
                                    ),
                                    SingleChoiceElement(
                                        "iso",
                                        Title("ISO 8601 / RFC 3339, e.g. '2026-07-28T02:00:00Z'"),
                                    ),
                                    SingleChoiceElement(
                                        "custom",
                                        Title("Custom format (pattern below)"),
                                    ),
                                ],
                                prefill=DefaultValue("auto"),
                            ),
                        ),
                        "pattern": DictElement(
                            required=False,
                            parameter_form=String(
                                title=Title("Custom format pattern"),
                                help_text=Help(
                                    "Only used with 'Custom format'. "
                                    "Python strptime directives, e.g. "
                                    "'%d.%m.%Y %H:%M:%S' for "
                                    "'28.07.2026 02:00:00' or "
                                    "'%m/%d/%Y %I:%M %p' for "
                                    "'07/28/2026 02:00 AM'. Without %z "
                                    "in the pattern the time is read "
                                    "as UTC."
                                ),
                                field_size=FieldSize.LARGE,
                                custom_validate=(_validate_strptime_pattern,),
                            ),
                        ),
                    },
                    custom_validate=(_validate_timestamp_format,),
                ),
            ),
        ],
    )
