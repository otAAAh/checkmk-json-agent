#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""An endpoint's 'Follow pagination' setting: the checks Setup runs on it and the
settings of the two modes in which the agent counts the pages itself.
"""

from collections.abc import Mapping

from cmk.rulesets.v1 import Help, Message, Title
from cmk.rulesets.v1.form_specs import (
    DefaultValue,
    DictElement,
    Dictionary,
    FieldSize,
    InputHint,
    Integer,
    String,
    validators,
)

from .common import _validate_query_parameter


def _validate_pagination(value: object) -> None:
    """The two paths of a pagination setting must name single places.

    A '[*]' wildcard expands to many values; the collection to merge is ONE
    container and the next-page link is ONE URL, so a wildcard in either is a
    configuration that cannot mean anything - rejected here rather than becoming
    an endpoint that reports "no collection at ..." at runtime.
    """
    if not isinstance(value, dict):
        return
    items = value.get("items")
    if isinstance(items, str) and "[*]" in items:
        raise validators.ValidationError(
            Message(
                "The collection to merge must name the collection itself, "
                "without a '[*]' wildcard - e.g. 'data.items' rather than "
                "'data.items[*]'."
            )
        )
    nxt = value.get("next")
    if not (isinstance(nxt, (tuple, list)) and len(nxt) == 2):
        return
    mode, setting = nxt
    if mode == "body" and isinstance(setting, str) and "[*]" in setting:
        raise validators.ValidationError(
            Message("The path to the next page's URL must not contain a '[*]' wildcard.")
        )
    if mode in ("page_number", "offset") and isinstance(setting, dict):
        _validate_counted_pages(setting)


def _validate_counted_pages(value: Mapping[str, object]) -> None:
    """Settings the agent could only half honour, rejected at save time.

    A page-size parameter with no size to send would go out empty; the same name
    for the position and the size would have one overwrite the other; and a
    wildcard total is many numbers where the end of the collection needs one.
    """
    size_parameter = value.get("size_parameter")
    if size_parameter and value.get("page_size") is None:
        raise validators.ValidationError(
            Message(
                "A page size parameter needs a page size to send. Set 'Page size', "
                "or remove the parameter."
            )
        )
    if size_parameter and size_parameter == value.get("parameter"):
        raise validators.ValidationError(
            Message("The position and the page size need two different parameters.")
        )
    total = value.get("total")
    if isinstance(total, str) and "[*]" in total:
        raise validators.ValidationError(
            Message("The path to the total must not contain a '[*]' wildcard.")
        )


def _counted_pages(parameter_prefill: str, start: DictElement) -> Dictionary:
    """The settings of the two modes in which the agent counts the pages itself.

    ``start`` is where counting starts, which differs by mode: a page number is
    required (1, or 0 for a zero-based API), an offset is optional - 0 unless the
    API numbers its elements from 1, and offset rules from before it existed
    have none.
    """
    elements = {
        "parameter": DictElement(
            required=True,
            parameter_form=String(
                title=Title("Query parameter"),
                help_text=Help(
                    "The name the API reads the position from, e.g. 'page', 'p', "
                    "'offset' or 'skip'. A misspelt name is recognised by the API "
                    "answering the same page again, which stops pagination and is "
                    "reported on the endpoint's own service."
                ),
                prefill=DefaultValue(parameter_prefill),
                custom_validate=(_validate_query_parameter,),
            ),
        ),
        "start": start,
        "page_size": DictElement(
            required=False,
            parameter_form=Integer(
                title=Title("Page size"),
                help_text=Help(
                    "How many elements a full page carries. A page with fewer - "
                    "and fewer than a page before it, since an API may cap the "
                    "size it is asked for - is the last one, which saves the "
                    "request for the empty page after it. Leave it unset where "
                    "the API decides and does not say: then an empty page is "
                    "what ends pagination."
                ),
                prefill=InputHint(100),
                custom_validate=(validators.NumberInRange(min_value=1),),
            ),
        ),
        "size_parameter": DictElement(
            required=False,
            parameter_form=String(
                title=Title("Send the page size as"),
                help_text=Help(
                    "The query parameter that asks the API for that page size, "
                    "e.g. 'limit', 'per_page' or 'size'. Sent on every page, the "
                    "first one included."
                ),
                prefill=InputHint("limit"),
                custom_validate=(_validate_query_parameter,),
            ),
        ),
        "total": DictElement(
            required=False,
            parameter_form=String(
                title=Title("JSON path to the total number of elements"),
                field_size=FieldSize.LARGE,
                help_text=Help(
                    "Where the API states how many elements the whole collection "
                    "holds, e.g. 'total' or 'meta.total_count'. Once that many "
                    "have been read, pagination ends without asking for another "
                    "page. A value that is absent or not a number is ignored."
                ),
                custom_validate=(validators.LengthInRange(min_value=1),),
            ),
        ),
    }
    return Dictionary(elements=elements)
