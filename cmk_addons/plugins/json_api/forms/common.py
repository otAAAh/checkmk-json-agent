#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""What more than one section of the endpoint form uses.

A URL is checked the same way for the endpoint and for the OAuth2 token URL, a
query parameter name the same way for an API key and for the counted
pagination's parameter, and an element condition reads the same for a field,
its piggyback-host labels and the endpoint's host labels - so these sit below
all of those sections.
"""

import re
from urllib.parse import urlparse

from cmk.rulesets.v1 import Help, Message, Title
from cmk.rulesets.v1.form_specs import (
    DefaultValue,
    DictElement,
    Dictionary,
    FieldSize,
    SingleChoice,
    SingleChoiceElement,
    String,
    validators,
)


def _validate_url(value: str) -> None:
    # A proper http(s) URI: no surrounding/embedded whitespace (urlparse would
    # silently strip a leading space and accept it, then the agent fails at
    # runtime), scheme http/https (case-insensitive per RFC 3986, requests
    # accepts 'HTTP://') AND a host present ('http://' alone is not usable).
    if re.search(r"\s", value):
        raise validators.ValidationError(Message("The URL must not contain whitespace."))
    parsed = urlparse(value)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise validators.ValidationError(
            Message("Enter a valid http(s) URL, e.g. 'https://host/path'.")
        )


def _validate_query_parameter(value: str) -> None:
    # The name is placed in the query string, so the characters that structure a
    # query string (or end it) cannot appear in it.
    if not value or re.search(r"[\s&=?#]", value):
        raise validators.ValidationError(
            Message("Enter a valid query parameter name, e.g. 'api_key'.")
        )


def _element_filter(title: Title, help_text: Help) -> Dictionary:
    """The 'only the elements matching a condition' predicate.

    Shared by a field's own element filter and by the label specs, which select
    the elements that produce a label with exactly the same three fields. Title
    and help come from the caller, because what the condition selects - services
    or labels - is what differs between them.
    """
    return Dictionary(
        title=title,
        help_text=help_text,
        elements={
            "path": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("Field path (within each element)"),
                    field_size=FieldSize.LARGE,
                    help_text=Help(
                        "Resolved within each element, e.g. 'status' or 'metadata.phase'."
                    ),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "op": DictElement(
                required=True,
                parameter_form=SingleChoice(
                    title=Title("Condition"),
                    elements=[
                        SingleChoiceElement("equals", Title("equals")),
                        SingleChoiceElement("not_equals", Title("does not equal")),
                        SingleChoiceElement("regex", Title("matches regex")),
                        SingleChoiceElement("not_regex", Title("does not match regex")),
                    ],
                    prefill=DefaultValue("not_equals"),
                ),
            ),
            "value": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("Comparison value / pattern"),
                    help_text=Help(
                        "The value to compare against, or the regular "
                        "expression for the regex conditions."
                    ),
                ),
            ),
        },
    )
