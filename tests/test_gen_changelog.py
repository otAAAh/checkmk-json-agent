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
