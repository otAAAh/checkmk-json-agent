# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the dependency-free PO parser / MO compiler used by the packager."""

import gettext
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pofile  # noqa: E402  (resolved via the path insert above)

_HEADER = "Content-Type: text/plain; charset=UTF-8\n"


def test_parse_po_basic():
    # The header value carries an *escaped* newline (\\n) in PO source.
    cat = pofile.parse_po(
        'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Hello"\nmsgstr "Hallo"\n'
    )
    assert cat["Hello"] == "Hallo"
    assert cat[""] == _HEADER  # the header entry is kept, escape decoded


def test_parse_po_multiline_concatenation():
    text = (
        'msgid ""\nmsgstr ""\n\n'
        'msgid ""\n"first part "\n"second part"\n'
        'msgstr ""\n"erster Teil "\n"zweiter Teil"\n'
    )
    # The empty-id block is the header; the second block's id is the concatenation.
    cat = pofile.parse_po(text)
    assert cat["first part second part"] == "erster Teil zweiter Teil"


def test_parse_po_unescapes_quotes_and_newlines():
    cat = pofile.parse_po('msgid ""\nmsgstr ""\n\nmsgid "a\\"b"\nmsgstr "x\\ny"\n')
    assert cat['a"b'] == "x\ny"


def test_parse_po_skips_fuzzy_and_untranslated():
    text = (
        'msgid ""\nmsgstr ""\n\n'
        '#, fuzzy\nmsgid "Fuzzy"\nmsgstr "Unsicher"\n\n'
        'msgid "Empty"\nmsgstr ""\n\n'
        'msgid "Good"\nmsgstr "Gut"\n'
    )
    cat = pofile.parse_po(text)
    assert "Fuzzy" not in cat  # fuzzy entry dropped
    assert "Empty" not in cat  # untranslated entry dropped
    assert cat["Good"] == "Gut"


def test_compile_mo_roundtrip_with_gettext():
    catalog = {"": _HEADER, "Hello": "Hallo", "Ümlaut": "Träns"}
    translation = gettext.GNUTranslations(io.BytesIO(pofile.compile_mo(catalog)))
    assert translation.gettext("Hello") == "Hallo"
    assert translation.gettext("Ümlaut") == "Träns"  # UTF-8 survives compilation
    assert translation.gettext("missing") == "missing"  # fallback to source


def test_compile_mo_many_keys_binary_search():
    # gettext binary-searches the (sorted) id table; exercise it with many keys.
    catalog = {"": _HEADER}
    catalog.update({f"key {i}": f"wert {i}" for i in range(64)})
    translation = gettext.GNUTranslations(io.BytesIO(pofile.compile_mo(catalog)))
    assert all(translation.gettext(f"key {i}") == f"wert {i}" for i in range(64))
