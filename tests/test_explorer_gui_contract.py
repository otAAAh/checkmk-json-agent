# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The wizard's three-way contract: browser → AJAX page → rule.

The Explorer creates a rule in three hops. The Vue app posts a payload; the
``json_explorer_create`` / ``json_explorer_validate`` pages read it, run it
through the ruleset's own FormSpec visitors and hand back a ``value_raw``; the
REST API stores that under the ruleset. Nothing type-checks across those hops —
a key renamed on one side of a POST body is a runtime error at best, and at
worst a validation step that silently stops validating.

These guards read both sides and compare them. They are deliberately static:

* they parse sources rather than importing anything, so they run on every
  Checkmk line the suite runs on — including 2.4, where the Explorer itself
  cannot be imported at all;
* and running the pages for real is not on offer. ``page()`` needs a GUI request
  context (a Password field's visitor lists the password store, which reaches
  for the session), so exercising the conversion means booting a Flask app the
  way a background job does — ``BackgroundJobFlaskApp(...).test_request_context``
  plus ``SuperUserContext`` — four internal, unversioned APIs deep, in the one
  package whose reason for existing is that the AGENT never depends on those.
  A guard that breaks on every Checkmk release protects nothing; this is the
  coverage that survives.

What is NOT covered here, and is worth remembering: whether the visitors turn a
given payload into the rule value we expect. That is Checkmk's code, exercised
end to end by the wizard itself on a site.
"""

import ast
import re
from pathlib import Path

import pytest
from ruleset_ast import dictionary_keys

_ROOT = Path(__file__).resolve().parent.parent
_GUI = _ROOT / "gui" / "wato" / "json_explorer"
_CREATE = _GUI / "create.py"
_VALIDATE = _GUI / "validate.py"
_PAGE = _GUI / "page.py"
_WIZARD_STATE = _ROOT / "frontend" / "src" / "composables" / "useExplorer.ts"
_FRONTEND_SRC = _ROOT / "frontend" / "src"


def _string_keys_read_from(source: Path, variables: set[str]) -> set[str]:
    """The string keys a module reads out of the named variables.

    Catches both spellings the pages use: ``entry["connection"]`` and
    ``payload.get("endpoints", [])``.
    """
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text())):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in variables
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in variables
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
    return keys


def _wizard_payload_keys() -> set[str]:
    """The keys the Vue app puts in the create payload.

    Read out of the ``const payload = {...}`` literal in the wizard state — the
    single place that body is built.
    """
    source = _WIZARD_STATE.read_text()
    start = source.index("const payload = {")
    end = source.index("\n  }", start)
    literal = source[start:end]
    return set(re.findall(r"^\s{4,6}([a-z_]+)[,:]", literal, flags=re.MULTILINE))


def test_the_create_page_reads_the_payload_the_wizard_sends():
    """A key renamed on one side alone produces a KeyError in the page (or, for
    an optional one, an endpoint quietly missing its services)."""
    assert _wizard_payload_keys() == _string_keys_read_from(_CREATE, {"payload", "entry"})


def test_the_rule_the_page_writes_fits_the_ruleset():
    """``create.py`` assembles the rule by hand: ``{"endpoints": [...]}``, each
    endpoint the connection's own fields plus 'extractions' and 'host_labels'.
    Every one of those names has to be a field of the ruleset, or the rule the
    wizard builds is rejected by the REST API with a validation error the
    operator can do nothing about."""
    source = _CREATE.read_text()

    assert '"value_raw": repr({"endpoints": endpoints})' in source, (
        "the rule's top-level shape changed — update this guard with it"
    )
    assert "endpoints" in dictionary_keys("_parameter_form")

    endpoint_fields = dictionary_keys("_endpoint")
    for folded_in in ("extractions", "host_labels"):
        assert f'"{folded_in}": {folded_in}' in source
        assert folded_in in endpoint_fields


def test_every_spec_the_wizard_asks_to_validate_is_one_the_page_knows():
    """``validateSpec('typo')`` does not fail loudly: the page answers 'unknown
    spec', the client turns any non-OK reply into an empty message list, and the
    step then validates NOTHING while looking exactly as it should."""
    builders = _string_keys_from_dict_literal(_VALIDATE, "builders")
    asked_for = {
        name
        for path in _FRONTEND_SRC.rglob("*.ts")
        for name in re.findall(r"validateSpec\(\s*'([^']+)'", path.read_text())
    }

    assert asked_for, "no validateSpec() call found — has the client been renamed?"
    assert asked_for <= builders


def _string_keys_from_dict_literal(source: Path, variable: str) -> set[str]:
    """The string keys of ``<variable> = {...}`` in a module."""
    for node in ast.walk(ast.parse(source.read_text())):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            return {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
    raise AssertionError(f"no dict literal assigned to {variable!r} in {source.name}")


@pytest.mark.parametrize("module", [_CREATE, _VALIDATE], ids=lambda path: path.name)
def test_the_form_spec_builders_the_pages_import_exist(module):
    """The pages import their FormSpecs from page.py inside the handler, so a
    builder renamed in page.py is not an import error at load time — it is a
    500 the first time an operator opens the wizard."""
    imported = {
        alias.name
        for node in ast.walk(ast.parse(module.read_text()))
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.endswith("json_explorer.page")
        for alias in node.names
    }
    defined = {
        node.name
        for node in ast.parse(_PAGE.read_text()).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert imported, f"{module.name} no longer imports any FormSpec builder"
    assert imported <= defined
