#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""One field of an endpoint: where its value is, how it is read and judged, and
what its service shows - with the checks that need the field as a whole.
"""

import ast
import re

from cmk.rulesets.v1 import Help, Label, Message, Title
from cmk.rulesets.v1.form_specs import (
    CascadingSingleChoice,
    CascadingSingleChoiceElement,
    DefaultValue,
    DictElement,
    Dictionary,
    FieldSize,
    FixedValue,
    Float,
    InputHint,
    List,
    SingleChoice,
    SingleChoiceElement,
    String,
    validators,
)

from ..lib import levels_lower, levels_upper, string_match
from ..migrations import migrate_extraction as _migrate_extraction
from .common import _element_filter
from .inventory import _INVENTORY_ROW_KEY, _inventory
from .labels import _validate_label_spec
from .metrics import _UNIT_RATE_METRIC, _validate_metric_name
from .value_as import _validate_strptime_pattern, _validate_timestamp_format

# The AST node types a calc expression may contain: an arithmetic tree over
# numeric literals and the variable 'value'. Kept in lock-step with the check's
# own evaluator (_apply_calc in agent_based/json_api.py).
_CALC_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,  # every Name carries a Load context node, harmless
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.UAdd,
    ast.USub,
)


def _validate_calc(value: str) -> None:
    if not value.strip():
        # An empty expression means "no transform" - the check skips a falsy
        # calc, so accept it here instead of tripping on ast.parse("").
        return
    try:
        tree = ast.parse(value, mode="eval")
    except SyntaxError as exc:
        raise validators.ValidationError(
            Message("Invalid arithmetic expression: %s") % str(exc)
        ) from exc
    for node in ast.walk(tree):
        if not isinstance(node, _CALC_ALLOWED_NODES):
            raise validators.ValidationError(
                Message(
                    "Only the variables 'value' and 'other', numbers, parentheses "
                    "and + - * / are allowed."
                )
            )
        if isinstance(node, ast.Name) and node.id not in ("value", "other"):
            raise validators.ValidationError(
                Message("Unknown variable '%s' - only 'value' and 'other' are available.") % node.id
            )
        if isinstance(node, ast.Constant) and (
            isinstance(node.value, bool) or not isinstance(node.value, (int, float))
        ):
            raise validators.ValidationError(Message("Only numeric constants are allowed."))


def _calc_uses_other(expression: object) -> bool:
    """Whether a calc expression names the second operand.

    Parsed rather than substring-matched, so a path or a number that merely
    contains the letters 'other' is not mistaken for the variable.
    """
    if not isinstance(expression, str) or not expression.strip():
        return False
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False  # _validate_calc reports the syntax error itself
    return any(isinstance(node, ast.Name) and node.id == "other" for node in ast.walk(tree))


def _validate_extraction(value: object) -> None:
    """The transform and its second path must agree.

    Either half alone is a silent no-op - an expression naming 'other' with no
    second path can never be computed, and a second path no expression uses is
    resolved and thrown away - so both are rejected at configuration time rather
    than becoming a puzzling UNKNOWN (or nothing at all) on the service.
    """
    if not isinstance(value, dict):
        return
    # A field sent to the inventory INSTEAD of a service has no line to
    # contribute: it is written to the tree and creates nothing. Reporting it
    # into a shared service is therefore a contradiction rather than a
    # combination, and silently ignoring one half of it would be worse.
    inventory = value.get("inventory")
    # For a '[*]' wildcard the tree holds one table ROW per element, keyed by an
    # 'element' column (see _INVENTORY_ROW_KEY in agent_based/json_api.py). A
    # column of that name would also BE the key column, which Checkmk refuses -
    # while writing the tree, where the failure takes the host's WHOLE inventory
    # with it rather than one field. So it is refused here, where it is still a
    # form error. A wildcard-free field is a plain attribute of the node, has no
    # key column, and keeps every name it could ever have.
    if (
        isinstance(inventory, dict)
        and isinstance(inventory.get("key"), str)
        and inventory["key"].strip() == _INVENTORY_ROW_KEY
        and isinstance(value.get("path"), str)
        and "[*]" in value["path"]
    ):
        raise validators.ValidationError(
            Message(
                "'element' is the column the table rows of a '[*]' wildcard are "
                "keyed by, so it cannot also name a column. Choose another "
                "attribute name."
            )
        )
    if value.get("group") and isinstance(inventory, dict) and not inventory.get("keep_service"):
        raise validators.ValidationError(
            Message(
                "A field written to the inventory creates no service, so it cannot "
                "report into a shared one. Tick 'Also create a service for this "
                "field', or clear 'Report in a shared service named'."
            )
        )
    # Host labels for a host this extraction never creates would land nowhere:
    # they are resolved per '[*]' element and attached to the host that element
    # becomes, so without a piggyback host name there is nothing to attach to.
    if value.get("piggyback_labels") and not (
        isinstance(value.get("piggyback_host"), str) and value["piggyback_host"].strip()
    ):
        raise validators.ValidationError(
            Message(
                "'Labels for the created host' needs 'Create one host per element, "
                "named by this field' - without it the element stays a service on "
                "this host and there is no host to label."
            )
        )
    # A counter's rate is the unit per second, so the unit has to be one things
    # are counted in. A counter 'in watts' or 'in bytes per second' has no rate
    # that means anything, and the check would graph it as a bare number.
    value_as = value.get("value_as")
    unit = value.get("unit")
    if (
        isinstance(value_as, (list, tuple))
        and value_as
        and value_as[0] == "counter"
        and isinstance(unit, str)
        and unit not in _UNIT_RATE_METRIC
    ):
        raise validators.ValidationError(
            Message(
                "A counter is read as a rate per second, and this unit is not "
                "one that is counted - it is already a rate, or a measurement "
                "such as a temperature. Choose 'Count', 'Bytes', 'Seconds', "
                "'Percent' or no unit, or read the value as it stands."
            )
        )
    calc = value.get("calc")
    has_path = isinstance(value.get("calc_path"), str) and value["calc_path"].strip()
    uses_other = _calc_uses_other(calc)
    if uses_other and not has_path:
        raise validators.ValidationError(
            Message(
                "The transform uses 'other', so 'Second path for the transform' "
                "must be set to say where that value comes from."
            )
        )
    if has_path and not uses_other:
        raise validators.ValidationError(
            Message(
                "'Second path for the transform' is only used through the "
                "variable 'other' - add it to the transform (e.g. "
                "'value / other * 100') or clear the path."
            )
        )


# A summary template: literal text with '{path}' placeholders, no nesting. The
# same shape the agent's _SUMMARY_PLACEHOLDER resolves, checked here so a stray
# brace is a form error rather than a placeholder that silently never renders.
_SUMMARY_TEMPLATE_PATTERN = re.compile(r"[^{}]*(\{[^{}]+\}[^{}]*)*\Z")


def _validate_value_range(value: object) -> None:
    """A range has to be a range: at least one end, and the right way round."""
    if not isinstance(value, dict):
        return
    low, high = value.get("min"), value.get("max")
    if low is None and high is None:
        raise validators.ValidationError(
            Message("Give at least one end of the range, or leave the range unset.")
        )
    if low is not None and high is not None and low >= high:
        raise validators.ValidationError(
            Message("The lowest value must be smaller than the highest value.")
        )


def _validate_summary(value: str) -> None:
    if not value.strip():
        return
    # A blank placeholder resolves to nothing and would silently disappear, so it
    # is rejected alongside the malformed shapes.
    blank = any(not path.strip() for path in re.findall(r"\{([^{}]*)\}", value))
    if blank or not _SUMMARY_TEMPLATE_PATTERN.match(value):
        raise validators.ValidationError(
            Message(
                "Use '{path}' to insert a field, e.g. '{message} (leader {leader})'. "
                "Braces must be paired and cannot be nested or empty."
            )
        )


def _extraction() -> Dictionary:
    return Dictionary(
        title=Title("Field to monitor"),
        migrate=_migrate_extraction,
        custom_validate=(_validate_extraction,),
        elements={
            "service": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("Service name"),
                    help_text=Help(
                        "Becomes the Checkmk service description for this field - "
                        "or, where the field reports into a shared service, the "
                        "name of its line within that service."
                    ),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "group": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Report in a shared service named"),
                    help_text=Help(
                        "By default every field becomes a service of its own. "
                        "Name a shared service here and this field reports into "
                        "that one service as a LINE instead, alongside the other "
                        "fields naming it - so a small API of 'status', "
                        "'component' and 'timestamp' can be one service rather "
                        "than three. The service's state is the WORST of its "
                        "lines, which is how Checkmk aggregates any check, and "
                        "'Service name' above then names the line rather than the "
                        "service. Each line keeps its own levels, string matching "
                        "and transform from this rule. Two things change, both "
                        "because one service now holds several fields: a "
                        "'Generic JSON API' check-parameters rule cannot describe "
                        "it and is not applied to it, so thresholds for these "
                        "fields live here; and each line's metric is named after "
                        "the line ('json_api_bytes_root_used'), which keeps the "
                        "history of several fields apart but renders as a plain "
                        "number rather than in the field's unit. That runtime "
                        "name also decides which dashboard widgets can use the "
                        "field: a Graph widget draws whatever the service "
                        "carries and is unaffected, but Gauge, Single metric and "
                        "Bar chart are bound to one metric CHOSEN BY NAME, and "
                        "their dropdown only offers a service's real names once "
                        "the widget is filtered to a host and a service - set "
                        "'Metric name' below to pick the name yourself instead. "
                        "A field that needs its unit on the graph, or "
                        "per-service thresholds, is better off with a service "
                        "of its own."
                    ),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "path": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("JSON path"),
                    field_size=FieldSize.LARGE,
                    help_text=Help(
                        "Dotted path into the JSON response, e.g. "
                        "'status', 'components.db.status' or 'items[0].count'. "
                        "Use a '[*]' wildcard (e.g. 'nodes[*].health') to create "
                        "one service per array element - or, when the wildcard "
                        "lands on a JSON object (a map keyed by name, such as a "
                        "Spring Boot Actuator '/health' 'components[*].status'), "
                        "one service per key. Multiple '[*]' wildcards "
                        "(e.g. 'pods[*].containers[*].ready') expand the cartesian "
                        "product, one service per combination. A leading '$.' is "
                        "optional. Keys that themselves contain '.' or '[' can be "
                        "addressed with bracket-quoted segments, e.g. "
                        "\"data['foo.bar'].value\". To monitor a RESPONSE HEADER "
                        "instead of a field of the body, prefix its name with "
                        "'@header.', e.g. '@header.X-RateLimit-Remaining' for an "
                        "API quota or '@header.Last-Modified' with 'Interpret the "
                        "value as' set to a timestamp for the age of the data. "
                        "Header names are matched case-insensitively and none of "
                        "the path syntax above applies to them."
                    ),
                    prefill=InputHint("status"),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "label_path": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Per-element name suffix (for '[*]' wildcards)"),
                    field_size=FieldSize.LARGE,
                    help_text=Help(
                        "When the JSON path contains a '[*]' wildcard, one service "
                        "is created per element. This optional path - relative to "
                        "each element, e.g. 'name' or 'id' - is appended to the "
                        "service name to tell those services apart. It does NOT "
                        "replace the service name. Defaults to the array index "
                        "(or, for an object, the key). With multiple '[*]' "
                        "wildcards it is resolved at every level and the parts are "
                        "joined with ' / ' (e.g. '<pod> / <container>'). Pick a "
                        "field that is unique and stable across runs."
                    ),
                ),
            ),
            "piggyback_host": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Create one host per element, named by this field"),
                    field_size=FieldSize.LARGE,
                    help_text=Help(
                        "When the JSON path contains a '[*]' wildcard, this turns "
                        "every element into a Checkmk host of its own instead of "
                        "one more service on this host. Give a path relative to "
                        "each element that holds the host name, e.g. 'name' or "
                        "'hostname'. The service then keeps its plain name, "
                        "because the host already says which element it is. Set "
                        "the same field on several fields of this endpoint to "
                        "collect them all on the same hosts. Only characters "
                        "valid in a Checkmk host name are kept (letters, digits, "
                        "'-', '_', '.'); anything else becomes '_'. An element "
                        "whose field is missing keeps its service on this host, "
                        "so nothing is lost. IMPORTANT: Checkmk holds piggyback "
                        "data for hosts that do not exist yet - create the hosts "
                        "(manually or with Dynamic host management) or the data "
                        "is never monitored."
                    ),
                ),
            ),
            "piggyback_labels": DictElement(
                required=False,
                parameter_form=List(
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
                ),
            ),
            "labels": DictElement(
                required=False,
                parameter_form=List(
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
                ),
            ),
            "aggregate": DictElement(
                required=False,
                parameter_form=SingleChoice(
                    title=Title("Aggregate a collection into one value"),
                    help_text=Help(
                        "Collapse a collection of elements into a single number "
                        "instead of monitoring one value (or, with a '[*]' "
                        "wildcard, one service per element). Point the JSON path "
                        "either at an array / object (e.g. 'jobs') or at a '[*]' "
                        "wildcard over the values to aggregate (e.g. "
                        "'nodes[*].load'). 'Number of elements' just counts them; "
                        "the other functions need numeric values. The result is a "
                        "number, so a unit, levels, a transform and a metric all "
                        "apply to it. Combine it with the condition below to "
                        "aggregate only the matching elements. A path that holds "
                        "neither an array nor an object makes the service UNKNOWN."
                    ),
                    elements=[
                        SingleChoiceElement("count", Title("Number of elements")),
                        SingleChoiceElement("sum", Title("Sum of the values")),
                        SingleChoiceElement("avg", Title("Average of the values")),
                        SingleChoiceElement("min", Title("Smallest of the values")),
                        SingleChoiceElement("max", Title("Largest of the values")),
                    ],
                    prefill=DefaultValue("count"),
                ),
            ),
            "filter": DictElement(
                required=False,
                parameter_form=_element_filter(
                    Title("Only elements matching a condition"),
                    Help(
                        "For a '[*]' wildcard or an aggregated path: keep only the "
                        "elements whose sub-field matches this condition - e.g. one "
                        "service per node whose 'status' is not 'ok', or count only "
                        "the pods that are not 'Running'. The field path is resolved "
                        "within each element; an element whose field is missing or "
                        "is not a scalar is dropped. Without a '[*]' wildcard or an "
                        "aggregation this has no effect."
                    ),
                ),
            ),
            "value_as": DictElement(
                required=False,
                parameter_form=CascadingSingleChoice(
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
                                    "The difference to the previous check is divided "
                                    "by the elapsed time"
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
                                                SingleChoiceElement(
                                                    "auto", Title("Detect automatically")
                                                ),
                                                SingleChoiceElement(
                                                    "epoch", Title("Unix epoch seconds")
                                                ),
                                                SingleChoiceElement(
                                                    "epoch_ms",
                                                    Title("Unix epoch milliseconds"),
                                                ),
                                                SingleChoiceElement(
                                                    "iso",
                                                    Title(
                                                        "ISO 8601 / RFC 3339, "
                                                        "e.g. '2026-07-28T02:00:00Z'"
                                                    ),
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
                ),
            ),
            "unit": DictElement(
                required=False,
                parameter_form=SingleChoice(
                    title=Title("Unit (for numeric values)"),
                    help_text=Help(
                        "Renders the metric and graph with this unit. Leave unset "
                        "for a plain, unit-less value. Only affects numeric values."
                        "<br><br>The value is not converted: levels and the value "
                        "range are entered in the unit the API reports. For a "
                        "unit the API does not use as it stands, convert it with "
                        "the transform - a latency in milliseconds is 'Seconds' "
                        "with the transform 'value / 1000'."
                    ),
                    elements=[
                        SingleChoiceElement("count", Title("Count (integer)")),
                        SingleChoiceElement("bytes", Title("Bytes (IEC: KiB, MiB, ...)")),
                        SingleChoiceElement("seconds", Title("Seconds (duration)")),
                        SingleChoiceElement("percent", Title("Percent")),
                        SingleChoiceElement(
                            "per_second", Title("Per second (requests, events, ...)")
                        ),
                        SingleChoiceElement(
                            "bytes_per_second", Title("Bytes per second (IEC: KiB/s, MiB/s, ...)")
                        ),
                        SingleChoiceElement(
                            "bits_per_second", Title("Bits per second (SI: kbit/s, Mbit/s, ...)")
                        ),
                        SingleChoiceElement("celsius", Title("Degrees Celsius")),
                        SingleChoiceElement("volts", Title("Volts")),
                        SingleChoiceElement("amperes", Title("Amperes")),
                        SingleChoiceElement("watts", Title("Watts")),
                        SingleChoiceElement("hertz", Title("Hertz")),
                    ],
                    prefill=InputHint(Title("No unit")),
                ),
            ),
            "value_range": DictElement(
                required=False,
                parameter_form=Dictionary(
                    title=Title("Value range (for graphs and gauges)"),
                    help_text=Help(
                        "The range this value moves in, when it has one: a "
                        "battery percentage is 0 to 100, a queue with a cap is 0 "
                        "to that cap. It changes nothing about the state - it "
                        "tells Checkmk what 'full' means, so the graph keeps a "
                        "steady scale instead of rescaling to whatever the last "
                        "hour happened to contain, a gauge dashboard widget has "
                        "a dial to draw, and the service list's bar is filled "
                        "against the real maximum. Leave it unset for a value "
                        "with no natural limit; the bar is then scaled to the "
                        "critical level instead, where one is configured."
                    ),
                    custom_validate=(_validate_value_range,),
                    elements={
                        "min": DictElement(
                            required=False,
                            parameter_form=Float(
                                title=Title("Lowest possible value"),
                                prefill=DefaultValue(0.0),
                            ),
                        ),
                        "max": DictElement(
                            required=False,
                            parameter_form=Float(
                                title=Title("Highest possible value"),
                                prefill=InputHint(100.0),
                            ),
                        ),
                    },
                ),
            ),
            "metric_name": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Metric name"),
                    help_text=Help(
                        "The name Checkmk stores this field's metric under. "
                        "Leave it unset and the plugin picks one: the unit's "
                        "metric for a field with a service of its own "
                        "('json_api_bytes'), or that name plus the line's own "
                        "name for a field in a shared service "
                        "('json_api_bytes_root_used'). Set it where you need to "
                        "KNOW the name in advance - the Gauge, Single metric and "
                        "Bar chart dashboard widgets are bound to one metric "
                        "chosen by name, and their dropdown only lists the names "
                        "a service really has once the widget is filtered to a "
                        "host and a service. Letters, digits and underscores "
                        "only, starting with a letter or a digit, and not one of "
                        "this plugin's own metric names. Two costs: the service "
                        "loses its Perf-O-Meter, because every bar this plugin "
                        "draws is declared against one of its own metrics and a "
                        "name of your own matches none; and changing the name "
                        "later starts a new history under it, so pick it before "
                        "the service has data worth keeping. A field that needs "
                        "its bar is better off keeping the derived name and "
                        "filtering the widget to a host and a service instead."
                    ),
                    custom_validate=(_validate_metric_name,),
                ),
            ),
            "levels_upper": DictElement(
                required=False,
                parameter_form=levels_upper(),
            ),
            "levels_lower": DictElement(
                required=False,
                parameter_form=levels_lower(),
            ),
            "calc": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Transform the numeric value"),
                    field_size=FieldSize.LARGE,
                    help_text=Help(
                        "An arithmetic expression applied to a numeric value "
                        "before the levels and the metric, using the variable "
                        "'value'. Only numbers, parentheses and + - * / are "
                        "allowed. Examples: 'value / 1024 / 1024' (bytes to MiB), "
                        "'value * 1000' (seconds to milliseconds), "
                        "'(value - 32) * 5 / 9' (Fahrenheit to Celsius). A second "
                        "field can be brought in as 'other' - see below - which is "
                        "what turns a used/total pair into a percentage: "
                        "'value / other * 100'."
                    ),
                    prefill=InputHint("value / 1024 / 1024"),
                    custom_validate=(_validate_calc,),
                ),
            ),
            "calc_path": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Second path for the transform ('other')"),
                    field_size=FieldSize.LARGE,
                    help_text=Help(
                        "A second field, made available to the transform above as "
                        "the variable 'other'. Most APIs report a used/total or "
                        "current/limit pair rather than a percentage, so this is "
                        "what lets one service monitor the ratio: point the JSON "
                        "path at 'used', this at 'total', and use "
                        "'value / other * 100' with the unit set to '%'. It is "
                        "resolved in the SAME scope as the value - within each "
                        "'[*]' element, or the response root without a wildcard - "
                        "so every element is compared against its own total. The "
                        "transform must actually use 'other', and 'other' requires "
                        "this path; either one alone is rejected."
                    ),
                    prefill=InputHint("total"),
                ),
            ),
            "match": DictElement(
                required=False,
                parameter_form=string_match(),
            ),
            "summary": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Extra text in the service summary"),
                    field_size=FieldSize.LARGE,
                    help_text=Help(
                        "Appended to the summary, after the value. Write '{path}' "
                        "to insert another field of the same response - resolved "
                        "within the current element for a '[*]' wildcard, from the "
                        "response root otherwise. So a service on 'status' can show "
                        "the reason the API gave next to it, e.g. "
                        "'{message} (leader {leader})', instead of needing a second "
                        "service for it. A path that is not in the response renders "
                        "as '(n/a)'. This is presentation only: it never changes the "
                        "state, the levels or the metric. The text is put on one "
                        "line and truncated if it gets long."
                    ),
                    prefill=InputHint("{message}"),
                    custom_validate=(_validate_summary,),
                ),
            ),
            "inventory": DictElement(
                required=False,
                parameter_form=_inventory(),
            ),
        },
    )
