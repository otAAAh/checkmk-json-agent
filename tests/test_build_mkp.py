# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the MKP packager, focused on the shipped translation catalogs."""

import gettext
import io
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_mkp  # noqa: E402  (resolved via the path insert above)

# Every Checkmk-supported language we ship a catalog for.
_LANGS = ["de", "es", "fr", "it", "ja", "nl", "pt_PT", "ro"]
_MO = {lang: f"packages/json_api/{lang}/LC_MESSAGES/multisite.mo" for lang in _LANGS}


def test_locale_entries_compile_and_translate():
    entries = dict(build_mkp._locale_entries("json_api"))
    assert set(entries) == set(_MO.values()), "expected one compiled catalog per shipped language"
    # Spot-check a few languages actually translate (not just compile).
    de = gettext.GNUTranslations(io.BytesIO(entries[_MO["de"]]))
    assert de.gettext("Authentication") == "Authentifizierung"
    assert de.gettext("Generic JSON API") == "Generische JSON-API"
    fr = gettext.GNUTranslations(io.BytesIO(entries[_MO["fr"]]))
    assert fr.gettext("Authentication") == "Authentification"
    ja = gettext.GNUTranslations(io.BytesIO(entries[_MO["ja"]]))
    assert ja.gettext("Password") == "パスワード"


def test_build_ships_locales_part():
    output = build_mkp.build()
    try:
        with tarfile.open(output, "r:gz") as tar:
            assert "locales.tar" in tar.getnames()
            manifest = json.loads(tar.extractfile("info.json").read())
            assert sorted(manifest["files"]["locales"]) == sorted(_MO.values())
            locales_tar = tar.extractfile("locales.tar").read()
        with tarfile.open(fileobj=io.BytesIO(locales_tar)) as inner:
            assert sorted(inner.getnames()) == sorted(_MO.values())
    finally:
        output.unlink()
