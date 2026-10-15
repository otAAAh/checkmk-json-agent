#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""How a field's unit names its metric and renders its value.

The unit chosen in the rule decides two things: which metric of
``graphing/json_api.py`` the value is written to, and how the summary prints
it ('1.50 MiB' rather than '1572864'). Both are lookups keyed by the unit, kept
here next to each other - and next to the counter-rate and age variants, which
follow the same rules - so a new unit is added in one place.
"""

import math
from collections.abc import Callable, Mapping
from typing import Final

from cmk.agent_based.v2 import render

# Maps the unit chosen in the rule to the metric defined in graphing/json_api.py.
# ``None`` (no unit chosen, incl. rules from before units existed) keeps the
# original unit-less "json_api_value" so existing metric history is preserved.
#
# A value the API already reports per second (a throughput, a request rate) is
# the same quantity as the rate the check computes from a counter, so it shares
# that metric: 'bytes_per_second' graphs exactly like a counter of bytes does.
#
# The value is never scaled: levels, the value range and the summary all speak
# the unit the API reports in. That is why there is no 'milliseconds' here - a
# latency in ms is the 'seconds' unit behind the transform 'value / 1000'.
_UNIT_METRIC: Final[Mapping[str | None, str]] = {
    None: "json_api_value",
    "count": "json_api_count",
    "bytes": "json_api_bytes",
    "seconds": "json_api_seconds",
    "percent": "json_api_percent",
    "per_second": "json_api_count_rate",
    "bytes_per_second": "json_api_bytes_rate",
    "bits_per_second": "json_api_bits_per_second",
    "celsius": "json_api_celsius",
    "volts": "json_api_volts",
    "amperes": "json_api_amperes",
    "watts": "json_api_watts",
    "hertz": "json_api_hertz",
}

# A per-second rate is a different quantity from the counter it came from, so it
# gets its own metric per unit (bytes -> B/s, ...) rather than polluting the
# absolute value's history with a rate.
#
# Only the units something can be COUNTED in are here. A counter of a rate or of
# a temperature is not a thing, and the ruleset refuses the combination; a rule
# written by hand that asks for it gets the plain, unit-less rate.
_UNIT_RATE_METRIC: Final[Mapping[str | None, str]] = {
    None: "json_api_rate",
    "count": "json_api_count_rate",
    "bytes": "json_api_bytes_rate",
    "seconds": "json_api_seconds_rate",
    "percent": "json_api_percent_rate",
}

# The age of a timestamp is a duration in seconds; it gets a metric of its own so
# it graphs as a duration without the rule having to choose a unit.
_AGE_METRIC = "json_api_age"


def _metric_name(unit: str | None) -> str:
    return _UNIT_METRIC.get(unit, "json_api_value")


def _rate_metric_name(unit: str | None) -> str:
    return _UNIT_RATE_METRIC.get(unit, "json_api_rate")


# The unit chosen in the rule also decides how the value is *rendered* in the
# summary/details (and the levels line), so "1572864" with unit=bytes reads as
# "1.50 MiB" like the graph does - not just as a bare number. Units without a
# dedicated renderer ("count", or none) fall back to the plain number.
_SI_PREFIXES = (
    (1e12, "T"),
    (1e9, "G"),
    (1e6, "M"),
    (1e3, "k"),
    (1.0, ""),
    (1e-3, "m"),
    (1e-6, "µ"),
)


def _render_si(symbol: str) -> Callable[[float], str]:
    """Render with an SI prefix, the way the graph's SINotation does: '1.50 kW'.

    Small values too - a current of 0.02 A reads as '20.00 mA' rather than
    rounding to nothing.
    """

    def _render(number: float) -> str:
        # NaN matches no prefix and infinity every one: neither has a scale, so
        # print what it is rather than 'nan µW'.
        if not math.isfinite(number):
            return f"{number} {symbol}"
        magnitude = abs(number)
        if magnitude == 0:
            return f"{number:.2f} {symbol}"
        index = next(
            (i for i, (candidate, _) in enumerate(_SI_PREFIXES) if magnitude >= candidate),
            len(_SI_PREFIXES) - 1,
        )
        # The prefix is chosen before rounding, so 999.996 W would print as
        # '1000.00 W' where the graph says 1 kW: step up when the rounding does.
        if index > 0 and round(magnitude / _SI_PREFIXES[index][0], 2) >= 1000:
            index -= 1
        factor, prefix = _SI_PREFIXES[index]
        return f"{number / factor:.2f} {prefix}{symbol}"

    return _render


def _render_celsius(number: float) -> str:
    return f"{number:.1f} °C"


_UNIT_RENDER: Final[Mapping[str, Callable[[float], str]]] = {
    "bytes": render.bytes,
    # time_offset, not timespan: timespan raises on a negative value, and a
    # negative duration is ordinary here (the age of a timestamp in the future).
    "seconds": render.time_offset,
    "percent": render.percent,
    "per_second": lambda number: f"{_fmt_rate(number)}/s",
    "bytes_per_second": lambda number: f"{render.bytes(number)}/s",
    "bits_per_second": _render_si("bit/s"),
    "celsius": _render_celsius,
    "volts": _render_si("V"),
    "amperes": _render_si("A"),
    "watts": _render_si("W"),
    "hertz": render.frequency,
}


def _render_func(unit: str | None) -> Callable[[float], str] | None:
    return _UNIT_RENDER.get(unit) if unit is not None else None


def _rate_render_func(unit: str | None) -> Callable[[float], str]:
    """Render a per-second rate: the unit's own rendering plus '/s'.

    A rate is almost never a round number (it is a difference divided by an
    elapsed time), so the unit-less fallback rounds to a few significant digits
    instead of printing the full float repr - '20/s' rather than
    '19.999451493365736/s' - while staying accurate for tiny rates.
    """
    # Only a unit with a rate of its own lends it its rendering: a counter 'in
    # bytes per second' would otherwise read '1.00 KiB/s/s'.
    countable = unit is not None and unit in _UNIT_RATE_METRIC
    base = (_render_func(unit) if countable else None) or _fmt_rate
    return lambda number: f"{base(number)}/s"


def _fmt_rate(number: float) -> str:
    return _fmt_number(number) if number.is_integer() else f"{number:.6g}"


def _fmt_number(number: float) -> str:
    """Render a (calculated) number without a spurious trailing ``.0``."""
    return str(int(number)) if number.is_integer() else str(number)
