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

_DE_MO = "packages/json_api/de/LC_MESSAGES/multisite.mo"


def test_locale_entries_compile_and_translate():
    entries = dict(build_mkp._locale_entries("json_api"))
    assert _DE_MO in entries, "expected the German catalog under the per-package path"
    translation = gettext.GNUTranslations(io.BytesIO(entries[_DE_MO]))
    assert translation.gettext("Authentication") == "Authentifizierung"
    assert translation.gettext("Generic JSON API") == "Generische JSON-API"


def test_build_ships_locales_part():
    output = build_mkp.build()
    try:
        with tarfile.open(output, "r:gz") as tar:
            assert "locales.tar" in tar.getnames()
            manifest = json.loads(tar.extractfile("info.json").read())
            assert manifest["files"]["locales"] == [_DE_MO]
            locales_tar = tar.extractfile("locales.tar").read()
        with tarfile.open(fileobj=io.BytesIO(locales_tar)) as inner:
            assert inner.getnames() == [_DE_MO]
    finally:
        output.unlink()
