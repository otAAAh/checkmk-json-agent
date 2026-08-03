# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the changelog generator's pure (git-free) helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gen_changelog  # noqa: E402  (resolved via the path insert above)


def test_version_tag_regex():
    assert gen_changelog._VERSION_TAG.match("v1.2.3")
    assert gen_changelog._VERSION_TAG.match("v0.10.0")
    assert gen_changelog._VERSION_TAG.match("v1.2") is None
    assert gen_changelog._VERSION_TAG.match("1.2.3") is None
    assert gen_changelog._VERSION_TAG.match("v1.2.3-rc1") is None


def test_bucket_feat_and_fix_are_stripped_and_capitalised():
    assert gen_changelog._bucket("feat: add cool thing") == ("Features", "Add cool thing")
    assert gen_changelog._bucket("feature(agent): thing") == ("Features", "Thing")
    assert gen_changelog._bucket("fix: a bug") == ("Fixes", "A bug")
    assert gen_changelog._bucket("bugfix: another") == ("Fixes", "Another")


def test_bucket_other_known_prefixes_are_stripped_too():
    # Regression for the "Ci: run tests" bug: a recognised non-feat/fix prefix
    # must still be stripped, landing under Other without its type token.
    assert gen_changelog._bucket("ci: run tests") == ("Other", "Run tests")
    assert gen_changelog._bucket("docs: update readme") == ("Other", "Update readme")
    assert gen_changelog._bucket("chore(deps): bump") == ("Other", "Bump")


def test_bucket_plain_subject_falls_through_to_other():
    assert gen_changelog._bucket("Add a plain feature") == ("Other", "Add a plain feature")
    # A leading lowercase word without a colon is not a conventional prefix.
    assert gen_changelog._bucket("tighten copy") == ("Other", "Tighten copy")
    # An unrecognised "word:" prefix is preserved verbatim (not stripped).
    assert gen_changelog._bucket("Support X: do Y") == ("Other", "Support X: do Y")


def test_bullet_links_commit_and_trailing_pr(monkeypatch):
    monkeypatch.setattr(gen_changelog, "_repo_url", lambda: "https://example.com/o/r")
    assert (
        gen_changelog._bullet("abc1234", "A change")
        == "- A change ([`abc1234`](https://example.com/o/r/commit/abc1234))"
    )
    bullet = gen_changelog._bullet("abc1234", "Squashed thing (#42)")
    assert "([#42](https://example.com/o/r/pull/42))" in bullet
    assert "([`abc1234`](https://example.com/o/r/commit/abc1234))" in bullet
    assert "(#42)" not in bullet.replace("[#42]", "")  # the raw ref was rewritten


def test_render_version_groups_sections_in_order(monkeypatch):
    monkeypatch.setattr(gen_changelog, "_repo_url", lambda: "https://example.com/o/r")
    rendered = gen_changelog._render_version(
        "1.2.3",
        "2026-01-01",
        [("aaa1111", "feat: shiny"), ("bbb2222", "just a change")],
    )
    assert "## [1.2.3] - 2026-01-01" in rendered
    assert "### Features" in rendered
    assert "- Shiny (" in rendered
    assert "### Other" in rendered
    assert "- Just a change (" in rendered
    # Features must be rendered before Other.
    assert rendered.index("### Features") < rendered.index("### Other")


def test_render_version_omits_headers_when_only_other(monkeypatch):
    monkeypatch.setattr(gen_changelog, "_repo_url", lambda: "https://example.com/o/r")
    rendered = gen_changelog._render_version("1.0.0", "2026-01-01", [("aaa1111", "just a change")])
    assert "### Other" not in rendered
    assert "### Features" not in rendered
    assert "- Just a change (" in rendered


def test_render_version_without_date_and_without_commits():
    rendered = gen_changelog._render_version("0.9.0", None, [])
    assert rendered.startswith("## [0.9.0]")
    assert " - " not in rendered.splitlines()[0]  # no date suffix
    assert "_No user-facing changes recorded._" in rendered


_UPGRADING = """<!-- SPDX-License-Identifier: GPL-2.0-only -->
# Upgrade notes

Preamble prose that belongs to no version.

## [Unreleased]

### Something coming

Not released yet.

## [0.11.0]

### Every endpoint gains a service of its own

New services appear on the next discovery.

### Name your endpoints first

Renaming later loses the history.

## [0.9.0]

### Older note

Still here.
"""


def test_upgrade_notes_extracts_one_version():
    notes = gen_changelog.upgrade_notes("0.11.0", _UPGRADING)
    assert notes.startswith("### Upgrade notes\n")
    assert "Every endpoint gains a service of its own" in notes
    assert "Name your endpoints first" in notes
    # Strictly this version: neither the neighbouring sections nor the preamble.
    assert "Older note" not in notes
    assert "Something coming" not in notes
    assert "belongs to no version" not in notes


def test_upgrade_notes_demotes_subheadings_to_nest_under_the_release_notes():
    # The release body's own sections are '###', so a note's '###' would compete
    # with them rather than sit inside 'Upgrade notes'.
    notes = gen_changelog.upgrade_notes("0.11.0", _UPGRADING)
    assert "#### Every endpoint gains a service of its own" in notes
    assert "\n### Every endpoint" not in notes


def test_upgrade_notes_tolerates_a_leading_v_and_a_trailing_date():
    assert gen_changelog.upgrade_notes("v0.11.0", _UPGRADING)
    dated = _UPGRADING.replace("## [0.11.0]", "## [0.11.0] - 2026-07-29")
    assert "Every endpoint" in gen_changelog.upgrade_notes("0.11.0", dated)


def test_upgrade_notes_absent_for_a_version_without_any():
    # Most releases need no notes; that is not an error.
    assert gen_changelog.upgrade_notes("0.10.0", _UPGRADING) == ""
    assert gen_changelog.upgrade_notes("0.11.0", "") == ""


def test_upgrade_notes_absent_for_an_empty_section():
    empty = "## [0.11.0]\n\n## [0.9.0]\n\n### Older\n\nBody.\n"
    assert gen_changelog.upgrade_notes("0.11.0", empty) == ""


def test_shipped_upgrading_file_parses_and_covers_0_11_0():
    # The real file, not a fixture: a malformed heading would silently drop the
    # notes from the release body.
    assert "Upgrade notes" in gen_changelog.upgrade_notes("0.11.0")
