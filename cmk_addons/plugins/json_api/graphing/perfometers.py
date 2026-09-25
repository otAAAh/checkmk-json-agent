#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Perfometers: the bar Checkmk draws next to each of this plugin's services.

Without one a service shows an empty column in every service list, which is
where an operator looks first — the number is in the summary, but the *shape* of
a list of fifty services (which ones are near their limit) is exactly what a bar
carries and text does not.

**How Checkmk picks one.** The first registered perfometer whose metrics are ALL
present in the service wins, and registration order is the order the variables
are defined in this module. Two consequences shape everything below:

* The bar has to be scaled to something. A JSON field has no natural maximum —
  'queue length' could be 5 or 5 million — so each metric gets THREE perfometers,
  most specific first:

  1. the value range from the rule, when the operator stated one — the true
     scale, and the only one that makes "half full" mean half full;
  2. otherwise the field's own CRIT level, which matches only while that level
     is in the perfdata (a scalar bound is part of Checkmk's match) — "80% of
     the way to critical" is a bar worth looking at;
  3. otherwise an open-ended range for the unit, which still shows magnitude
     without pretending to a scale.

  A range needs BOTH ends in the perfdata to be used as one; with only an upper
  end configured the bar falls through to the CRIT level or the fallback.
* Every perfometer here must belong to exactly one kind of service, or which bar
  appears would depend on definition order across unrelated metrics. A field
  service carries exactly one value metric, so those are naturally disjoint. The
  endpoint service carries three (response time, response size, certificate
  expiry) and therefore gets ONE bar, on the response time: the quantity that
  actually moves from check to check. The other two have their own graphs, and
  the certificate's remaining days sit in the summary where a number beats a bar.

A service holding SEVERAL fields (the 'group' option) gets no bar: its metrics
are named after the fields at runtime, so there is nothing to declare here. That
is the documented price of an open set of fields in one service.

The fallback bounds are all ``Open``: the value may exceed them, and the bar
then approaches full without ever quite reaching it, rather than pinning at 100%
and hiding the difference between 'twice the expected' and 'a thousand times'.
"""

from cmk.graphing.v1 import metrics, perfometers


def _to_range(metric: str) -> perfometers.Perfometer:
    """A bar over the value range the rule states for this field.

    Matches only while both ends are in the service's perfdata, which is exactly
    when the operator configured a range (the check emits it as the metric's
    boundaries). It comes first because a real maximum beats every guess,
    including the critical level: on a value that runs 0-100, 'half full' should
    look half full even when CRIT sits at 80.
    """
    return perfometers.Perfometer(
        name=f"{metric}_in_range",
        focus_range=perfometers.FocusRange(
            # The colour is only used where these are drawn as graph lines,
            # which they never are here - but the API asks for one.
            perfometers.Closed(metrics.MinimumOf(metric, metrics.Color.GRAY)),
            perfometers.Closed(metrics.MaximumOf(metric, metrics.Color.GRAY)),
        ),
        segments=[metric],
    )


def _to_crit(metric: str) -> perfometers.Perfometer:
    """A bar from zero to the field's CRIT level.

    Matches only while that level exists in the service's perfdata, which is
    what makes this the 'specific' variant: no levels, no scale, no match.
    """
    return perfometers.Perfometer(
        name=f"{metric}_to_crit",
        focus_range=perfometers.FocusRange(
            perfometers.Closed(0),
            perfometers.Closed(metrics.CriticalOf(metric)),
        ),
        segments=[metric],
    )


def _open_to(metric: str, upper: float) -> perfometers.Perfometer:
    """A bar over the range this quantity usually occupies."""
    return perfometers.Perfometer(
        name=metric,
        focus_range=perfometers.FocusRange(perfometers.Closed(0), perfometers.Open(upper)),
        segments=[metric],
    )


# --- a field's own value ----------------------------------------------------
# The fallback bounds are a typical magnitude per unit, not a limit: a share is
# a share (0-100), a duration of a minute is already remarkable, a size in the
# gigabytes is a lot, and a bare number has no scale at all beyond "some tens".

perfometer_json_api_value_in_range = _to_range("json_api_value")
perfometer_json_api_value_to_crit = _to_crit("json_api_value")
perfometer_json_api_value = _open_to("json_api_value", 100.0)

perfometer_json_api_count_in_range = _to_range("json_api_count")
perfometer_json_api_count_to_crit = _to_crit("json_api_count")
perfometer_json_api_count = _open_to("json_api_count", 1000.0)

perfometer_json_api_bytes_in_range = _to_range("json_api_bytes")
perfometer_json_api_bytes_to_crit = _to_crit("json_api_bytes")
perfometer_json_api_bytes = _open_to("json_api_bytes", 1073741824.0)  # 1 GiB

perfometer_json_api_seconds_in_range = _to_range("json_api_seconds")
perfometer_json_api_seconds_to_crit = _to_crit("json_api_seconds")
perfometer_json_api_seconds = _open_to("json_api_seconds", 60.0)

# A percentage has a real upper bound, so the bar is closed at 100 - but only in
# the fallback. With levels configured the CRIT variant still wins, because a
# field that goes critical at 5% should not look empty at 4%.
perfometer_json_api_percent_in_range = _to_range("json_api_percent")
perfometer_json_api_percent_to_crit = _to_crit("json_api_percent")
perfometer_json_api_percent = perfometers.Perfometer(
    name="json_api_percent",
    focus_range=perfometers.FocusRange(perfometers.Closed(0), perfometers.Closed(100)),
    segments=["json_api_percent"],
)

# Measurements. A temperature of a hundred degrees is hot for anything an API
# reports on; the electrical bounds are a mains socket (230 V, 16 A, a few kW)
# and the frequency one a CPU clock; a gigabit link fills the bandwidth bar.
# A value the API reports per second already shares the rate metrics below.

perfometer_json_api_bits_per_second_in_range = _to_range("json_api_bits_per_second")
perfometer_json_api_bits_per_second_to_crit = _to_crit("json_api_bits_per_second")
perfometer_json_api_bits_per_second = _open_to("json_api_bits_per_second", 1e9)

perfometer_json_api_celsius_in_range = _to_range("json_api_celsius")
perfometer_json_api_celsius_to_crit = _to_crit("json_api_celsius")
perfometer_json_api_celsius = _open_to("json_api_celsius", 100.0)

perfometer_json_api_volts_in_range = _to_range("json_api_volts")
perfometer_json_api_volts_to_crit = _to_crit("json_api_volts")
perfometer_json_api_volts = _open_to("json_api_volts", 230.0)

perfometer_json_api_amperes_in_range = _to_range("json_api_amperes")
perfometer_json_api_amperes_to_crit = _to_crit("json_api_amperes")
perfometer_json_api_amperes = _open_to("json_api_amperes", 16.0)

perfometer_json_api_watts_in_range = _to_range("json_api_watts")
perfometer_json_api_watts_to_crit = _to_crit("json_api_watts")
perfometer_json_api_watts = _open_to("json_api_watts", 3000.0)

perfometer_json_api_hertz_in_range = _to_range("json_api_hertz")
perfometer_json_api_hertz_to_crit = _to_crit("json_api_hertz")
perfometer_json_api_hertz = _open_to("json_api_hertz", 1e9)

# --- the per-second rate of a counter field ---------------------------------

perfometer_json_api_rate_in_range = _to_range("json_api_rate")
perfometer_json_api_rate_to_crit = _to_crit("json_api_rate")
perfometer_json_api_rate = _open_to("json_api_rate", 100.0)

perfometer_json_api_count_rate_in_range = _to_range("json_api_count_rate")
perfometer_json_api_count_rate_to_crit = _to_crit("json_api_count_rate")
perfometer_json_api_count_rate = _open_to("json_api_count_rate", 100.0)

perfometer_json_api_bytes_rate_in_range = _to_range("json_api_bytes_rate")
perfometer_json_api_bytes_rate_to_crit = _to_crit("json_api_bytes_rate")
perfometer_json_api_bytes_rate = _open_to("json_api_bytes_rate", 1048576.0)  # 1 MiB/s

perfometer_json_api_seconds_rate_in_range = _to_range("json_api_seconds_rate")
perfometer_json_api_seconds_rate_to_crit = _to_crit("json_api_seconds_rate")
perfometer_json_api_seconds_rate = _open_to("json_api_seconds_rate", 1.0)

perfometer_json_api_percent_rate_in_range = _to_range("json_api_percent_rate")
perfometer_json_api_percent_rate_to_crit = _to_crit("json_api_percent_rate")
perfometer_json_api_percent_rate = _open_to("json_api_percent_rate", 100.0)

# --- the age of a timestamp field -------------------------------------------
# A day: an 'updated_at' that is older than that is usually the point of
# monitoring it at all.

perfometer_json_api_age_in_range = _to_range("json_api_age")
perfometer_json_api_age_to_crit = _to_crit("json_api_age")
perfometer_json_api_age = _open_to("json_api_age", 86400.0)

# --- the endpoint's own service ---------------------------------------------
# Only the response time, for the reason in the module docstring. Five seconds:
# an API that answers the monitoring server more slowly than that is in trouble
# whatever it reports.

perfometer_json_api_response_time_in_range = _to_range("json_api_response_time")
perfometer_json_api_response_time_to_crit = _to_crit("json_api_response_time")
perfometer_json_api_response_time = _open_to("json_api_response_time", 5.0)
