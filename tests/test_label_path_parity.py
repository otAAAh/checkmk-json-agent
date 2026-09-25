# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The agent's half of the element-naming contract.

A '[*]' field names each element's service by a suffix the agent decides alone:
the field ``label_path`` points at, else the position, with every occurrence of a
repeated suffix suffixed again by its position. The wizard's review step and the
standalone Explorer both preview those names from the sample - and warn when they
repeat, because a position-suffixed service swaps readings as soon as the API
reorders its elements. Both are ports, so all three answer the same cases from
``tests/fixtures/label_path_cases.json``: this module drives the agent,
``frontend/src/lib/elementlabels.test.ts`` the wizard and ``tests/test_explorer.py``
the Explorer. The ``issues`` in the fixture are the ports' notion only; the agent
has no warnings, just names.
"""

import json
from pathlib import Path

import pytest

_CASES = json.loads((Path(__file__).parent / "fixtures" / "label_path_cases.json").read_text())


@pytest.mark.parametrize("case", _CASES["cases"], ids=lambda case: case["name"])
def test_the_agent_names_the_shared_label_path_cases(agent, case):
    leaves = agent._expand_wildcards(
        case["document"], agent._split_wildcards(case["path"]), case["label_path"] or None
    )

    assert [
        " / ".join(labels) for labels, found, _value, _error, _element in leaves if found
    ] == case["labels"]
    if "hosts" in case:
        # The host each found leaf becomes, resolved against the LEAF element as
        # the agent's collector does; None = its services stay on the polling host.
        assert [
            agent._piggyback_host(element, case["piggyback_host"])
            for _labels, found, _value, _error, element in leaves
            if found
        ] == case["hosts"]
