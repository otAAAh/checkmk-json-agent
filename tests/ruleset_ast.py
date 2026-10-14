# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Read the ruleset's shape without importing it.

Several guards need to know which fields the Setup form has — the Explorer
mirror, the preview fetch, the wizard's rule shape. Importing the ruleset costs
a Checkmk Python; AST-parsing it costs nothing and works on every line, which is
why these guards can run wherever the suite runs rather than only where the
package they guard can be imported.
"""

import ast
from pathlib import Path

_FAMILY = Path(__file__).resolve().parent.parent / "cmk_addons" / "plugins" / "json_api"
RULESET = _FAMILY / "rulesets" / "special_agent.py"
# The rule's sections are built in forms/ (one module per section), so a guard
# looking for a builder or a constant has to search those modules as well.
RULESET_FILES = (RULESET, *sorted((_FAMILY / "forms").glob("*.py")))


def ruleset_source() -> str:
    """The text of the rule and of every module its sections are built in."""
    return "\n".join(path.read_text() for path in RULESET_FILES)


def ruleset_definition(name: str) -> ast.stmt:
    """The module-level ``def name`` or ``name = ...`` of the rule, wherever it lives."""
    for path in RULESET_FILES:
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets
            ):
                return node
    raise AssertionError(f"the ruleset no longer defines {name}")


def dictionary_elements(func_name: str) -> dict[str, ast.expr]:
    """The ``elements`` of the first ``Dictionary(...)`` a builder returns.

    ``func_name`` is a ruleset builder such as ``endpoint_form`` or
    ``_parameter_form``; ``ast.walk`` reaches the outer Dictionary first, which
    is the form the builder is named for.
    """
    func = ruleset_definition(func_name)
    assert isinstance(func, ast.FunctionDef), func_name
    elements = next(
        keyword.value
        for call in ast.walk(func)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "Dictionary"
        for keyword in call.keywords
        if keyword.arg == "elements"
    )
    assert isinstance(elements, ast.Dict)
    return {
        key.value: value
        for key, value in zip(elements.keys, elements.values, strict=True)
        if isinstance(key, ast.Constant)
    }


def dictionary_keys(func_name: str) -> set[str]:
    """The field names of a ruleset builder's Dictionary."""
    return set(dictionary_elements(func_name))


def required_keys(func_name: str) -> set[str]:
    """The names of the ``required=True`` fields of a ruleset builder's Dictionary."""
    return {
        name
        for name, element in dictionary_elements(func_name).items()
        if isinstance(element, ast.Call)
        and any(
            keyword.arg == "required"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in element.keywords
        )
    }
