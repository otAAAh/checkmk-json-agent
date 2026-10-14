#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Reading a JSON value as a point in time, for a field read as a timestamp.

APIs write timestamps in every format there is: Unix epochs in seconds or
milliseconds, ISO 8601 / RFC 3339, HTTP-dates in headers, Go's default layout,
and whatever else a strptime pattern can describe. Each parser returns a Unix
epoch float or None and never raises on a value it cannot read: an unreadable
timestamp is a service state, not a crashed check.
"""

import email.utils
import re
from datetime import UTC, datetime

from .values import _as_number

# Above this, an epoch value can only be milliseconds: 1e11 seconds is the year
# 5138, while 1e11 milliseconds is 1973 - so any plausible timestamp beyond it is
# a millisecond value. Used only by the 'auto' format.
_EPOCH_MS_THRESHOLD = 1e11

# Fractional seconds with more than microsecond precision (Go/Java emit
# nanoseconds) are not accepted by datetime.fromisoformat; trim the extra digits.
_ISO_SUBSECOND = re.compile(r"(\.\d{6})\d+")


def _parse_iso_timestamp(text: str) -> float | None:
    """An ISO 8601 / RFC 3339 timestamp as a Unix epoch float, or None.

    A trailing 'Z' is normalized for older ``fromisoformat`` implementations, and
    sub-microsecond precision is trimmed. A timestamp without a time zone is read
    as UTC - the alternative, the monitoring server's local zone, would make the
    same JSON mean different things on different servers.
    """
    cleaned = _ISO_SUBSECOND.sub(r"\1", text.strip())
    if cleaned.endswith(("Z", "z")):
        cleaned = f"{cleaned[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _parse_http_date(text: str) -> float | None:
    """An HTTP-date (RFC 9110) as a Unix epoch float, or None.

    The format 'Last-Modified', 'Expires' and a date-form 'Retry-After' are
    written in - 'Wed, 21 Oct 2015 07:28:00 GMT' - which is neither a number nor
    ISO 8601, so reading a header as a timestamp needs it. An HTTP-date is
    always GMT; a parser that nonetheless returns a naive datetime is read as
    UTC rather than as the monitoring server's local zone.
    """
    try:
        parsed = email.utils.parsedate_to_datetime(text.strip())
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


# Go's default time layout, what ``time.Time.String()`` (and so ``fmt.Print``)
# emits: '2006-01-02 15:04:05.999999999 -0700 MST'. APIs written in Go leak it
# into their JSON (Cloudflare's origin certificate 'expires_on'). The zone name
# is an abbreviation, or the offset again when the zone has none; a time read
# from the monotonic clock carries an ' m=+0.000000001' suffix.
_GO_TIME = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?) ([+-]\d{4}) \S+(?: m=[+-]\d+(?:\.\d+)?)?"
)


def _parse_go_time(text: str) -> float | None:
    """A timestamp in Go's default layout as a Unix epoch float, or None.

    Only the numeric offset is read: a zone abbreviation is ambiguous ('IST' is
    three different zones), and Go always writes the offset next to it.
    """
    match = _GO_TIME.fullmatch(text.strip())
    if match is None:
        return None
    return _parse_iso_timestamp(f"{match[1]}{match[2]}")


def _parse_custom_timestamp(text: str, pattern: str) -> float | None:
    """The text read with a strptime pattern as a Unix epoch float, or None.

    Without '%z' in the pattern the result is naive and read as UTC, like an ISO
    8601 timestamp without a zone. A pattern strptime rejects (the ruleset
    validates it, but a rule imported over the REST API is not) is a value that
    cannot be read, not a crashed check.
    """
    try:
        parsed = datetime.strptime(text.strip(), pattern)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _parse_timestamp(value: object, fmt: str, pattern: str | None = None) -> float | None:
    """The value as a Unix epoch float, per the configured format, or None."""
    number = _as_number(value)
    match fmt:
        case "custom":
            # A number is read as text: a pattern such as '%Y%m%d' is for exactly
            # the digits an API may send as a JSON number.
            if pattern is None or isinstance(value, bool) or value is None:
                return None
            return _parse_custom_timestamp(str(value), pattern)
        case "epoch":
            return number
        case "epoch_ms":
            return number / 1000.0 if number is not None else None
        case "iso":
            return _parse_iso_timestamp(value) if isinstance(value, str) else None
    # "auto" (and anything unknown): a number is an epoch, in milliseconds when
    # it is far too large to be seconds; a string is tried as ISO 8601, then as
    # an HTTP-date, so a 'Last-Modified' header read via '@header.' resolves
    # without the rule having to name a format, and then in Go's default layout.
    if number is not None:
        return number / 1000.0 if abs(number) > _EPOCH_MS_THRESHOLD else number
    if not isinstance(value, str):
        return None
    # Not `iso or http_date or ...`: a timestamp of exactly epoch 0 is a real
    # value and would be discarded as falsy.
    for parse in (_parse_iso_timestamp, _parse_http_date, _parse_go_time):
        if (stamp := parse(value)) is not None:
            return stamp
    return None
