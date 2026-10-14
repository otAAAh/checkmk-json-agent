#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""An endpoint's 'Follow pagination' setting: the checks Setup runs on it and the
settings of the two modes in which the agent counts the pages itself.
"""

from collections.abc import Mapping

from cmk.rulesets.v1 import Help, Label, Message, Title
from cmk.rulesets.v1.form_specs import (
    CascadingSingleChoice,
    CascadingSingleChoiceElement,
    DefaultValue,
    DictElement,
    Dictionary,
    FieldSize,
    FixedValue,
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


def _pagination() -> Dictionary:
    return Dictionary(
        title=Title("Follow pagination"),
        custom_validate=(_validate_pagination,),
        help_text=Help(
            "Off by default: one request, one page. An API that "
            "answers a collection one page at a time then leaves "
            "every service built from it describing the FIRST page "
            "only - 'count' over a queue that pages at 25 reports 25 "
            "however long the queue is, and a '[*]' wildcard creates "
            "services for the first page's elements alone. Nothing "
            "says so, which is why this is worth configuring: the "
            "answer is not missing, it is wrong. With this set the "
            "agent follows the API's own next-page link - or, for an "
            "API without one, counts the pages itself - and appends "
            "each page's collection to the first page's, so the "
            "wildcards, the aggregations, the filters and the host "
            "labels all see the whole thing. Every page is requested "
            "exactly like the first one (same method, headers, "
            "authentication and timeout), and each one costs a "
            "request inside the check - so cap the pages, and "
            "consider a cache TTL for a collection that does not "
            "change every check interval. A page that cannot be read "
            "fails the endpoint rather than silently truncating the "
            "collection; where a further page exists but is not "
            "followed - a cap, or a link the agent refuses - the "
            "endpoint's own service reports it and goes WARN."
        ),
        elements={
            "next": DictElement(
                required=True,
                parameter_form=CascadingSingleChoice(
                    title=Title("Where the next page's URL comes from"),
                    help_text=Help(
                        "The API's documentation says which form it "
                        "uses. Where the API links to the next page, "
                        "a page that carries no next link (an absent "
                        "field, a JSON 'null', an empty string, no "
                        "'Link' header) is the last one, which is how "
                        "pagination ends. Where it offers no link and "
                        "takes the position as a query parameter "
                        "instead, the agent counts: the page number "
                        "goes up by one per page, the offset by the "
                        "number of elements the previous page "
                        "carried. The parameter is set on the first "
                        "request too, replacing one of that name in "
                        "the URL."
                    ),
                    elements=[
                        CascadingSingleChoiceElement(
                            name="body",
                            title=Title("A field in the response body"),
                            parameter_form=String(
                                title=Title("JSON path to the next page's URL"),
                                field_size=FieldSize.LARGE,
                                help_text=Help(
                                    "From the response root, e.g. "
                                    "'links.next', 'next' or "
                                    "'meta.next_page_url'. A relative "
                                    "URL ('/api/v1/jobs?page=2') is "
                                    "resolved against the page it "
                                    "came from."
                                ),
                                custom_validate=(validators.LengthInRange(min_value=1),),
                            ),
                        ),
                        CascadingSingleChoiceElement(
                            name="link_header",
                            title=Title("The 'Link' response header (rel=\"next\")"),
                            parameter_form=FixedValue(
                                value=None,
                                label=Label("Read from the 'Link' header, as RFC 8288 defines it"),
                                help_text=Help(
                                    "The convention of the GitHub, "
                                    "GitLab and Jenkins style APIs: "
                                    "'Link: <https://host/jobs?page=2>; "
                                    'rel="next"\'. The link with '
                                    'rel="next" is followed; the '
                                    "others ('last', 'prev') are "
                                    "ignored."
                                ),
                            ),
                        ),
                        CascadingSingleChoiceElement(
                            name="page_number",
                            title=Title("No link: count the pages ('?page=2')"),
                            parameter_form=_counted_pages(
                                parameter_prefill="page",
                                start=DictElement(
                                    required=True,
                                    parameter_form=Integer(
                                        title=Title("Number of the first page"),
                                        help_text=Help(
                                            "1 for most APIs; 0 where the API counts from zero."
                                        ),
                                        prefill=DefaultValue(1),
                                        custom_validate=(validators.NumberInRange(min_value=0),),
                                    ),
                                ),
                            ),
                        ),
                        CascadingSingleChoiceElement(
                            name="offset",
                            title=Title("No link: count the elements ('?offset=50')"),
                            parameter_form=_counted_pages(
                                parameter_prefill="offset",
                                start=DictElement(
                                    required=False,
                                    parameter_form=Integer(
                                        title=Title("Offset of the first element"),
                                        help_text=Help(
                                            "Unset, counting starts at 0. Set 1 "
                                            "where the API numbers the elements "
                                            "from one, such as SCIM's "
                                            "'startIndex' - otherwise every page "
                                            "after the first repeats one element."
                                        ),
                                        prefill=DefaultValue(1),
                                        custom_validate=(validators.NumberInRange(min_value=0),),
                                    ),
                                ),
                            ),
                        ),
                    ],
                    prefill=DefaultValue("body"),
                ),
            ),
            "items": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("JSON path to the collection to merge"),
                    field_size=FieldSize.LARGE,
                    help_text=Help(
                        "The array (or object) each page carries a "
                        "slice of - e.g. 'items', 'data.jobs' or "
                        "'results'. Use '$' where the response IS the "
                        "array. Each page's collection is appended to "
                        "the first page's, and the rest of the "
                        "document stays as the first page sent it: a "
                        "'total' or a 'generated_at' next to the "
                        "collection still resolves, and every path "
                        "configured below is unchanged. No '[*]' "
                        "wildcard here - this names the collection "
                        "itself, not the elements in it."
                    ),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "max_pages": DictElement(
                required=True,
                parameter_form=Integer(
                    title=Title("Read at most this many pages"),
                    help_text=Help(
                        "Including the first one, so '1' disables "
                        "following entirely. Each page is a request "
                        "made while the check runs, and a special "
                        "agent that overruns is killed - so this is "
                        "the setting that keeps a collection which "
                        "grew a hundredfold from turning the "
                        "monitoring into the outage. When the limit "
                        "is reached while a further page still "
                        "exists, the endpoint's own service says so "
                        "and goes WARN instead of quietly reporting "
                        "part of the collection."
                    ),
                    prefill=DefaultValue(10),
                    custom_validate=(validators.NumberInRange(min_value=1, max_value=100),),
                ),
            ),
            "max_elements": DictElement(
                required=False,
                parameter_form=Integer(
                    title=Title("And at most this many elements"),
                    help_text=Help(
                        "Optional second cap, for a page size that is "
                        "not known in advance. Checked between pages, "
                        "so a page is never cut in half and the "
                        "collection can end slightly above this. Like "
                        "the page limit, reaching it while more pages "
                        "exist is reported on the endpoint's own "
                        "service. Remember that a '[*]' wildcard "
                        "creates one SERVICE per element."
                    ),
                    prefill=InputHint(1000),
                    custom_validate=(validators.NumberInRange(min_value=1),),
                ),
            ),
        },
    )
