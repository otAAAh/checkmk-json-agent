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
