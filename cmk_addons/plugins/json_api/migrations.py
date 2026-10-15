#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Upgrade a stored rule value from an older shape to the current one.

The rulesets use these as their ``migrate``, but that runs only where Checkmk
loads a rule through its form: in Setup and in ``cmk-update-config``. Installing
a new version of this package runs neither, so a rule nobody has opened since
reaches the server-side call in its old shape. The server-side call therefore
applies the same functions before parsing; otherwise pydantic drops the old
keys without a word (a flat rule parses as one with no endpoints at all).

Not a plugin part - the discovery loader ignores it - and free of Checkmk
imports, so both the ruleset and the server-side call can use it.
"""


def migrate_to_endpoints(value: object) -> dict[str, object]:
    """Wrap a pre-multi-endpoint rule (flat connection at the top level) into
    the current single-key ``{"endpoints": [...]}`` shape.

    Like :func:`migrate_extraction`, a value that is not a dictionary degrades
    to an empty rule instead of raising: this also runs during rendering, where
    an exception costs the operator the entire form.
    """
    if not isinstance(value, dict):
        return {"endpoints": []}
    if "endpoints" in value:
        return value
    return {"endpoints": [value]}


def migrate_extraction(value: object) -> dict[str, object]:
    """Carry an older extraction shape into the current one.

    Two historical shapes are migrated:

    * the pre-'match' flat ``expected`` regex becomes the equivalent
      ``("must_match", <regex>)``, so existing rules keep their behaviour;
    * the boolean ``count`` ("count the elements at this path") becomes the
      ``"count"`` choice of the richer ``aggregate`` dropdown, which now also
      offers sum / average / minimum / maximum.

    Anything that is not a dictionary yields an EMPTY extraction rather than an
    exception. A migrate runs while the form is being RENDERED, so raising here
    takes down the whole Setup page instead of flagging a field: emptying a
    required entry and saving hands this ``None`` on the re-render, and the
    operator loses the rule form rather than being shown which box to fill in.
    Degrading leaves an empty row for exactly that box.
    """
    if not isinstance(value, dict):
        return {}
    migrated = dict(value)
    if "expected" in migrated and "match" not in migrated:
        expected = migrated.pop("expected")
        if isinstance(expected, str):
            # The old flat regex was OK-on-match, CRIT otherwise - keep that
            # exactly by omitting state_no_match (its CRIT default).
            migrated["match"] = ("must_match", {"pattern": expected})
    if "count" in migrated:
        counted = migrated.pop("count")
        if counted and "aggregate" not in migrated:
            migrated["aggregate"] = "count"
    return migrated


# How many seconds a day has - the factor between the retired levels in days
# and the current ones in seconds.
_SECONDS_PER_DAY = 86400.0


def migrate_endpoint_params(value: object) -> dict[str, object]:
    """Carry the endpoint service's parameters into the current shape.

    Earlier versions stored the certificate levels in DAYS under
    ``cert_expiry_levels``; they are now in SECONDS under
    ``cert_validity_levels``, like Checkmk's own certificate checks. A NEW key
    rather than a new unit under the old one is what makes this safe to run any
    number of times: a migrate runs on every load in Setup, and a value is in
    seconds exactly when it sits under the new key, so nothing is ever
    multiplied twice. The old key is dropped either way; when both are present
    (a hand-edited rule) the new one wins.

    The check applies this too, before reading the parameters: the migrate
    runs only in Setup and ``cmk-update-config``, so a rule nobody has opened
    since the update reaches the check with the old key, in days.

    Like the other migrations, a value that is not a dictionary degrades to an
    empty one instead of raising - this also runs while the form is rendered.
    """
    if not isinstance(value, dict):
        return {}
    if "cert_expiry_levels" not in value:
        return value
    migrated = dict(value)
    days = migrated.pop("cert_expiry_levels")
    if "cert_validity_levels" not in migrated:
        seconds = _days_to_seconds_levels(days)
        if seconds is not None:
            migrated["cert_validity_levels"] = seconds
    return migrated


def _days_to_seconds_levels(levels: object) -> tuple[str, object] | None:
    """SimpleLevels in days as the same levels in seconds; ``None`` if unreadable.

    A rule loaded in Setup carries tuples, one passed through JSON lists - both
    are accepted. Anything else is dropped rather than guessed at: an unreadable
    level is no level, which is also what the check made of it before.
    """
    if not isinstance(levels, (tuple, list)) or len(levels) != 2:
        return None
    kind, bounds = levels
    if kind == "no_levels":
        return ("no_levels", None)
    if kind != "fixed" or not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
        return None
    warn, crit = bounds
    if not isinstance(warn, (int, float)) or not isinstance(crit, (int, float)):
        return None
    if isinstance(warn, bool) or isinstance(crit, bool):
        return None
    return ("fixed", (float(warn) * _SECONDS_PER_DAY, float(crit) * _SECONDS_PER_DAY))
