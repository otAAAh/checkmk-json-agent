# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Guards on the translation catalogs' CONTENT.

``make check-po`` (which CI runs) answers whether every source string has an
entry. It cannot answer whether the entry says the right thing - and a wrong
one is invisible in review: the diff shows a plausible sentence in a language
the reviewer may not read, in a file where every line looks alike.

The failure these guard against is a specific, mechanical one. ``msgmerge``
carries a changed or newly added string over from a similar entry and marks it
``#, fuzzy``; clearing that flag without rewriting the text leaves the *other*
string's translation in place. That is how all eight catalogs came to answer the
'Labels for the created host' VALIDATION message with the field's TITLE, so a
misconfiguration was reported to non-English operators as a label.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pofile  # noqa: E402  (resolved via the path insert above)

_LOCALES = sorted((Path(__file__).resolve().parent.parent / "locales").glob("*/LC_MESSAGES"))

# How much longer one source string may be than another before sharing one
# translation is a mistake rather than a coincidence. A Title and the Help or
# validation message next to it differ by far more than this; two genuinely
# synonymous strings differ by far less.
_LENGTH_RATIO = 2


def _catalog(directory: Path) -> dict[str, str]:
    """The translations that will actually SHIP from this directory.

    Parsed with the packager's own parser, so fuzzy and untranslated entries are
    dropped here exactly as they are when the .mo is built - a fuzzy entry
    renders as English at runtime and is check-po's business, not this file's.
    """
    return pofile.parse_po((directory / "multisite.po").read_text(encoding="utf-8"))


def test_there_are_catalogs_to_check():
    # Guard the guard: a glob that matches nothing passes every test below.
    assert len(_LOCALES) >= 8


@pytest.mark.parametrize("directory", _LOCALES, ids=lambda d: d.parent.name)
def test_no_translation_is_shared_by_two_unequal_source_strings(directory: Path):
    """One translation for a short string AND a much longer one is a mix-up.

    A three-word Title cannot also be the translation of a three-line validation
    message. If a real pair of synonyms ever trips this, the honest fix is to
    reword one of them rather than to loosen the ratio: identical output for
    visibly different input is confusing in the form too.
    """
    catalog = _catalog(directory)
    sources: dict[str, list[str]] = {}
    for msgid, msgstr in catalog.items():
        if msgid:  # the empty id is the catalog header
            sources.setdefault(msgstr, []).append(msgid)

    mixed = {
        msgstr: ids
        for msgstr, ids in sources.items()
        if len(ids) > 1 and max(map(len, ids)) > _LENGTH_RATIO * min(map(len, ids))
    }
    assert not mixed, (
        f"{directory.parent.name}: one translation answers two very differently "
        f"sized source strings, which is what a Title left in a Help entry looks "
        f"like: {mixed}"
    )


@pytest.mark.parametrize("directory", _LOCALES, ids=lambda d: d.parent.name)
def test_the_validation_message_is_not_the_field_title(directory: Path):
    """The regression itself, pinned by name.

    Kept alongside the general guard because this one message is the reason the
    guard exists, and because it names the two entries: a search for either
    lands on the explanation of what went wrong.
    """
    catalog = _catalog(directory)
    title = "Labels for the created host"
    message = next(mid for mid in catalog if mid.startswith(f"'{title}' needs"))
    assert catalog[message] != catalog[title]
    # Not just different - it has to be the message, which is a sentence.
    assert len(catalog[message]) > 2 * len(catalog[title])
