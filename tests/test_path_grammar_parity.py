# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The agent's half of the picker contract: paths mean the same on both sides.

A path is written once, by clicking a field in the Explorer's picker, and then
read twice — by the wizard (to preview the value and its state) and by the
special agent (to build the service). Two implementations of one grammar: the
agent's ``_resolve_path`` / ``_expand_wildcards`` here, and the TypeScript port
in ``frontend/src/lib/jsonpaths.ts``. Where they drift, the wizard previews a
value the check will never report — the failure is invisible until a service
comes up "path not found" in production.

So both sides answer the SAME cases, from ``tests/fixtures/json_path_cases.json``:
this module drives the agent, ``jsonpaths.test.ts`` drives the port. A change to
the grammar that touches only one side fails here or there.

The fixture holds well-formed paths only. For a MALFORMED path the two differ
deliberately — the port refuses it outright ("no match"), while the agent's
tokenizer walks the tokens it recognises and ignores the junk, so 'a..b'
resolves as 'a.b'. Each side pins its own behaviour; see
``test_a_malformed_path_resolves_the_tokens_it_can`` below.
"""

import json
from pathlib import Path

import pytest

_CASES = json.loads((Path(__file__).parent / "fixtures" / "json_path_cases.json").read_text())


def _agent_values(agent, document, path):
    """Every value the agent resolves for ``path``, wildcards expanded.

    The agent splits the job in two — ``_split_wildcards`` cuts the path at each
    '[*]', ``_expand_wildcards`` walks the product — and the check reports one
    service per found leaf, which is what the picker previews.
    """
    segments = agent._split_wildcards(path)
    return [
        value
        for _labels, found, value, _error, _element in agent._expand_wildcards(
            document, segments, None
        )
        if found
    ]


@pytest.mark.parametrize("case", _CASES["cases"], ids=lambda case: case["name"])
def test_the_agent_resolves_the_shared_path_cases(agent, case):
    document = _CASES["documents"][case["document"]]

    assert _agent_values(agent, document, case["path"]) == case["values"]


def test_a_malformed_path_resolves_the_tokens_it_can(agent):
    """Recorded, not endorsed: the agent's tokenizer scans for the segments it
    knows and skips anything between them, so a typo silently resolves to the
    path the operator probably meant. The wizard's port refuses such a path
    instead, which is why these cases stay out of the shared fixture."""
    document = {"a": {"b": 1}}

    assert agent._resolve_path(document, "a..b") == (True, 1)
    assert agent._resolve_path(document, "a.b[") == (True, 1)
