#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""How a JSON value reads as a number and as text.

Shared by the check and the timestamp parsing, which have to agree on what
counts as a number (a numeric string does, a boolean does not) and on how a
value is written back out (as JSON writes it: 'true', not 'True').
"""

import math


def _render_value(value: object) -> str:
    """Render a value the way it appears in JSON, so 'expected' matches what
    users see (true/false/null, not Python's True/False/None)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    # Reject inf/nan: float("inf")/"nan" parse but make no sense as a metric or
    # a levels comparison, and would render confusingly.
    return number if math.isfinite(number) else None
