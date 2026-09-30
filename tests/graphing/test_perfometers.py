# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The bar drawn next to each service — which one, and why that one.

Checkmk picks the FIRST registered perfometer whose metrics are all present in
the service, in the order the variables are defined. That rule is the whole
design here, and it is invisible in the definitions themselves: a bar scaled to
the CRIT level has to be declared before the open-ended fallback for the same
metric, or the fallback swallows every service and the specific variant is dead
code nobody notices.

So the tests below re-apply that rule to realistic perfdata rather than
asserting the definitions look right. The rule is re-implemented here (four
lines) instead of imported: ``cmk.gui.graphing._perfometer`` is internal and
GUI-side, while this package must keep working on 2.4 where it does not exist in
that shape at all.
"""

import pytest
from cmk.graphing.v1 import metrics
from cmk.graphing.v1 import perfometers as perfometers_api


def _definitions(perfometers_module):
    """The perfometers in DEFINITION order, which is registration order."""
    return [
        value for name, value in vars(perfometers_module).items() if name.startswith("perfometer_")
    ]


def _required_metrics(perfometer) -> set[str]:
    """Every metric the perfometer needs before it can be drawn."""
    required = {segment for segment in perfometer.segments if isinstance(segment, str)}
    for bound in (perfometer.focus_range.lower, perfometer.focus_range.upper):
        if isinstance(bound.value, _SCALAR_BOUNDS):
            required.add(bound.value.metric_name)
    return required


# The perfdata scalar each bound kind is read from, which is also what Checkmk
# requires to be present before the perfometer can match at all.
_SCALAR_OF = {
    metrics.WarningOf: "warn",
    metrics.CriticalOf: "crit",
    metrics.MinimumOf: "min",
    metrics.MaximumOf: "max",
}
_SCALAR_BOUNDS = tuple(_SCALAR_OF)


def _required_scalars(perfometer) -> set[tuple[str, str]]:
    """The ``(metric, scalar)`` pairs the perfometer's bounds need in perfdata."""
    return {
        (bound.value.metric_name, _SCALAR_OF[type(bound.value)])
        for bound in (perfometer.focus_range.lower, perfometer.focus_range.upper)
        if isinstance(bound.value, _SCALAR_BOUNDS)
    }


def _first_match(perfometers_module, service_metrics: dict[str, set[str]]) -> str | None:
    """The name of the perfometer Checkmk would draw for that service.

    ``service_metrics`` maps each metric in the service's perfdata to the
    scalars it carries ('crit' when the field has upper levels).
    """
    for perfometer in _definitions(perfometers_module):
        if not _required_metrics(perfometer) <= set(service_metrics):
            continue
        if all(
            scalar in service_metrics[metric] for metric, scalar in _required_scalars(perfometer)
        ):
            return perfometer.name
    return None


@pytest.mark.parametrize(
    "service_metrics, expected",
    [
        # A field whose rule states a value range: scaled to the range, which is
        # the true maximum and beats every other scale.
        ({"json_api_count": {"min", "max"}}, "json_api_count_in_range"),
        # ... including when levels are configured as well.
        ({"json_api_count": {"min", "max", "crit"}}, "json_api_count_in_range"),
        # Half a range is not a range: without both ends there is nothing to
        # scale to, so the next variant takes over.
        ({"json_api_count": {"max", "crit"}}, "json_api_count_to_crit"),
        ({"json_api_count": {"max"}}, "json_api_count"),
        # A field with upper levels but no range: scaled to CRIT, the only other
        # scale a JSON value usually has.
        ({"json_api_count": {"crit"}}, "json_api_count_to_crit"),
        # The same field without levels falls back to the open range rather than
        # drawing nothing.
        ({"json_api_count": set()}, "json_api_count"),
        # A percentage without levels has a real upper bound of its own.
        ({"json_api_percent": set()}, "json_api_percent"),
        ({"json_api_percent": {"crit"}}, "json_api_percent_to_crit"),
        # A measurement gets the same three variants as every other unit ...
        ({"json_api_celsius": {"min", "max"}}, "json_api_celsius_in_range"),
        # ... except the CRIT scale for one that can be negative: a bar from
        # zero to a CRIT of -10 °C has its top below its bottom.
        ({"json_api_celsius": {"crit"}}, "json_api_celsius"),
        ({"json_api_amperes": {"crit"}}, "json_api_amperes"),
        ({"json_api_watts": {"crit"}}, "json_api_watts_to_crit"),
        ({"json_api_hertz": set()}, "json_api_hertz"),
        # A throughput the API reports as a gauge records into the rate metric,
        # so it gets the rate's bar.
        ({"json_api_bytes_rate": set()}, "json_api_bytes_rate"),
        # A counter field reports a rate, and nothing else.
        ({"json_api_bytes_rate": {"crit"}}, "json_api_bytes_rate_to_crit"),
        # A timestamp field reports an age.
        ({"json_api_age": set()}, "json_api_age"),
        # The endpoint service carries three metrics and gets exactly one bar,
        # on the response time — the quantity that moves between checks.
        (
            {
                "json_api_response_time": set(),
                "json_api_response_size": set(),
                "json_api_cert_expiry": {"crit"},
            },
            "json_api_response_time",
        ),
        # A service holding several fields names its metrics after them at
        # runtime, so there is nothing to match: no bar, by design.
        ({"json_api_value_queue_depth": {"crit"}}, None),
        # Same for a name stated in the rule ('Metric name'), which is the
        # price of choosing it: every bar here is declared against one of this
        # plugin's own metrics, so a name of the operator's own matches none.
        # A field that needs both keeps the derived name.
        ({"disk_free_pct": {"crit"}}, None),
    ],
)
def test_the_right_bar_is_drawn(perfometers, service_metrics, expected):
    assert _first_match(perfometers, service_metrics) == expected


def test_each_metrics_variants_run_from_specific_to_general(perfometers):
    """Definition order IS the behaviour. A variant needs every scalar its
    bounds name, so the one needing MORE has to come first: declare the
    open-ended bar before the CRIT-scaled one and it swallows every service,
    leaving the specific variants as dead code nobody notices."""
    previous: dict[str, int] = {}
    for perfometer in _definitions(perfometers):
        metric = next(iter(_required_metrics(perfometer)))
        specificity = len(_required_scalars(perfometer))
        assert specificity < previous.get(metric, 99), (
            f"{perfometer.name} can never match: a less specific bar for "
            f"{metric} is declared before it"
        )
        previous[metric] = specificity


def test_every_bar_belongs_to_exactly_one_kind_of_service(perfometers):
    """Two perfometers whose metrics can appear in the SAME service would make
    the bar depend on definition order across unrelated metrics — a change in
    one place silently altering another service's display."""
    for perfometer in _definitions(perfometers):
        assert len(_required_metrics(perfometer)) == 1, (
            f"{perfometer.name} spans several metrics; make sure they cannot "
            "co-occur with another perfometer's before allowing this"
        )


def test_every_bar_is_drawn_from_a_defined_metric(perfometers, graphing):
    defined = {getattr(graphing, name).name for name in dir(graphing) if name.startswith("metric_")}

    for perfometer in _definitions(perfometers):
        assert _required_metrics(perfometer) <= defined


def test_every_field_metric_has_a_bar(perfometers, check, graphing):
    """A unit that gains a metric but no perfometer would quietly go back to
    showing nothing — the state this change exists to end."""
    covered = {metric for p in _definitions(perfometers) for metric in _required_metrics(p)}
    emitted = set(check._UNIT_METRIC.values()) | set(check._UNIT_RATE_METRIC.values())

    assert emitted <= covered
    assert check._AGE_METRIC in covered
    # The endpoint's other two metrics are deliberately uncovered: they share a
    # service with the response time, which already has the bar.
    assert {"json_api_response_size", "json_api_cert_expiry"} & covered == set()


def test_the_perfometer_names_are_unique(perfometers):
    """Names are the registry key across every plugin on the site."""
    names = [p.name for p in _definitions(perfometers)]

    assert len(names) == len(set(names))
    assert all(isinstance(p, perfometers_api.Perfometer) for p in _definitions(perfometers))


# The quantities that can go below zero: a freezer, a DC rail, a discharging
# battery.
_SIGNED_METRICS = ("json_api_celsius", "json_api_volts", "json_api_amperes")


def _bars_of(perfometers_module, metric: str) -> list:
    return [p for p in _definitions(perfometers_module) if _required_metrics(p) == {metric}]


@pytest.mark.parametrize("metric", _SIGNED_METRICS)
def test_a_negative_reading_still_draws_a_bar(perfometers, metric):
    """A hard zero at the bottom would draw -18 °C and -40 °C as the same empty
    bar. Every bar a signed quantity can get either admits values below its
    lower end (Open) or takes both ends from the operator's stated range."""
    for perfometer in _bars_of(perfometers, metric):
        lower = perfometer.focus_range.lower
        assert isinstance(lower, perfometers_api.Open) or isinstance(
            lower.value, metrics.MinimumOf
        ), f"{perfometer.name} empties the bar below {lower.value}"


@pytest.mark.parametrize("metric", _SIGNED_METRICS)
def test_a_negative_crit_cannot_invert_the_range(perfometers, metric):
    """A bar from zero to CRIT meets a CRIT of -10 °C with a range whose top is
    under its bottom, which Checkmk cannot project and draws as nothing. No bar
    for a signed quantity may pair a fixed end with the CRIT level."""
    for perfometer in _bars_of(perfometers, metric):
        ends = (perfometer.focus_range.lower.value, perfometer.focus_range.upper.value)
        assert not any(isinstance(end, metrics.CriticalOf) for end in ends), perfometer.name


def test_every_fixed_range_runs_upwards(perfometers):
    """Where both ends are numbers the range can be checked here, before a site
    draws it as an empty bar."""
    for perfometer in _definitions(perfometers):
        lower = perfometer.focus_range.lower.value
        upper = perfometer.focus_range.upper.value
        if isinstance(lower, (int, float)) and isinstance(upper, (int, float)):
            assert lower < upper, perfometer.name
