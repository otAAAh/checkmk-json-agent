# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the MKP packager, focused on the shipped translation catalogs."""

import gettext
import io
import json
import sys
import tarfile
import tomllib
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


def test_agent_ships_locales_and_no_gui():
    """The agent package ships plugin + locales, but NO GUI 'web' part.

    Keeping the agent GUI-free is what confines it to the public, stable plugin
    APIs; anything touching Checkmk's internal frontend belongs in the separate
    Explorer package.
    """
    output = build_mkp.build()
    try:
        with tarfile.open(output, "r:gz") as tar:
            names = tar.getnames()
            assert "locales.tar" in names
            assert "cmk_addons_plugins.tar" in names
            assert "web.tar" not in names, "agent must not ship a GUI 'web' part"
            manifest = json.loads(tar.extractfile("info.json").read())
            assert manifest["name"] == "json_api"
            assert "web" not in manifest["files"]
            assert sorted(manifest["files"]["locales"]) == sorted(_MO.values())
            locales_tar = tar.extractfile("locales.tar").read()
        with tarfile.open(fileobj=io.BytesIO(locales_tar)) as inner:
            assert sorted(inner.getnames()) == sorted(_MO.values())
    finally:
        output.unlink()


def test_explorer_package_ships_web_and_gui_parts():
    """The Explorer 'extra' package carries the 'web' (bundle) and 'gui' (page
    module) parts — and never the agent's cmk_addons_plugins/locales.

    Web arcnames are relative to local/share/check_mk/web (start with 'htdocs/');
    gui arcnames relative to local/lib/python3/cmk/gui/plugins (e.g. wato/...py).
    """
    web_files = build_mkp._web_files()
    gui_files = build_mkp._gui_files()
    assert web_files, "expected static assets under web/ to be packaged"
    assert gui_files, "expected the GUI page module under gui/ to be packaged"
    web_expected = sorted(str(f.relative_to(build_mkp.WEB_BASE)) for f in web_files)
    gui_expected = sorted(str(f.relative_to(build_mkp.GUI_BASE)) for f in gui_files)
    assert all(name.startswith("htdocs/") for name in web_expected)
    assert "wato/json_explorer/page.py" in gui_expected

    output = build_mkp.build_explorer()
    assert output is not None
    try:
        with tarfile.open(output, "r:gz") as tar:
            names = tar.getnames()
            assert {"web.tar", "gui.tar"} <= set(names)
            assert "cmk_addons_plugins.tar" not in names
            assert "locales.tar" not in names
            manifest = json.loads(tar.extractfile("info.json").read())
            assert manifest["name"] == "json_api_explorer"
            assert sorted(manifest["files"]) == ["gui", "web"]
            assert sorted(manifest["files"]["web"]) == web_expected
            assert sorted(manifest["files"]["gui"]) == gui_expected
            web_tar = tar.extractfile("web.tar").read()
            gui_tar = tar.extractfile("gui.tar").read()
        with tarfile.open(fileobj=io.BytesIO(web_tar)) as inner:
            assert sorted(inner.getnames()) == web_expected
        with tarfile.open(fileobj=io.BytesIO(gui_tar)) as inner:
            assert sorted(inner.getnames()) == gui_expected
    finally:
        output.unlink()


def test_both_packages_share_the_project_version():
    """Agent and Explorer are distinct packages but ship at the same version.

    They are intentionally decoupled in *content* (the agent has no GUI parts),
    but their version numbers are aligned to the project version so users aren't
    confused by two different numbers for one release.
    """
    pyproject = tomllib.loads(
        (Path(build_mkp.__file__).parent.parent / "pyproject.toml").read_text()
    )
    project_version = pyproject["project"]["version"]

    agent = build_mkp.build()
    explorer = build_mkp.build_explorer()
    assert explorer is not None
    try:
        with tarfile.open(agent, "r:gz") as tar:
            assert json.loads(tar.extractfile("info.json").read())["version"] == project_version
        with tarfile.open(explorer, "r:gz") as tar:
            assert json.loads(tar.extractfile("info.json").read())["version"] == project_version
    finally:
        agent.unlink()
        explorer.unlink()
