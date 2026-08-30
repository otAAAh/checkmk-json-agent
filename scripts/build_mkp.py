#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Build the checkmk-json-agent MKP(s) using only the standard library.

This builds two independent packages (see ``--package``):

* **agent** (``json_api``) — the special agent. Ships:
  - ``cmk_addons_plugins.tar`` — plugin files, relative to
    ``local/lib/python3/cmk_addons/plugins``.
  - ``locales.tar`` — compiled translation catalogs, relative to
    ``local/share/check_mk/locale`` (only when the repo ships translations).
  It touches only the public, stable plugin APIs — no GUI parts.

* **explorer** (``json_api_explorer``) — the optional in-site Explorer. Ships:
  - ``web.tar`` — static GUI assets under ``web/``, relative to
    ``local/share/check_mk/web`` (the legacy "web" MKP part; the newer
    ``cmk_addons/plugins`` layout has no GUI group). Kept a separate package so
    the agent never inherits the Explorer's dependency on Checkmk's internal,
    unversioned cmk-frontend-vue bundle.

Every MKP is a gzipped tar of ``info`` (manifest as a Python literal), the same
manifest as ``info.json``, and the ``<part>.tar`` inner tars listed above. This
mirrors cmk_mkp_tool's on-disk format without importing any cmk package, so the
repo stays self-contained. The ``.po`` sources are compiled to ``.mo`` here with
:mod:`pofile`, so the ``gettext`` tools aren't needed to build.
"""

from __future__ import annotations

import io
import json
import pprint
import sys
import tarfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Repo dir that maps onto the site's local/lib/python3/cmk_addons/plugins:
PLUGINS_BASE = REPO / "cmk_addons" / "plugins"
# Repo dir holding the translation sources (one <lang>/LC_MESSAGES/multisite.po
# per language); packaged into the site's local/share/check_mk/locale.
LOCALES_BASE = REPO / "locales"
# Repo dir mirroring the site's local/share/check_mk/web tree (static GUI
# assets, e.g. htdocs/<...>); packaged into the "web" MKP part.
WEB_BASE = REPO / "web"
# Repo dir mirroring the site's local/lib/python3/cmk/gui/plugins tree (GUI page
# modules auto-imported by the GUI, e.g. wato/<...>.py); packaged into the "gui"
# MKP part.
GUI_BASE = REPO / "gui"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pofile  # noqa: E402  (local module, resolved via the path insert above)


def _load_metadata() -> tuple[dict, dict]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    return data["project"], data["tool"]["mkp"]


def _shipped_files(base: Path) -> list[Path]:
    """Every file under ``base`` worth packaging, or [] when there is no ``base``.

    Build artefacts of the checkout itself (``__pycache__``, ``.pyc``) are never
    part of a package, whichever part the directory feeds.
    """
    if not base.is_dir():
        return []
    return sorted(
        path
        for path in base.rglob("*")
        if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts
    )


def _inner_tar(files: list[Path], base: Path) -> bytes:
    """Uncompressed tar of ``files`` with arcnames relative to ``base``.

    ``base`` is the repo directory that maps onto the site directory the part
    unpacks into, so the arcname is what the site ends up with:
    ``json_api/agent_based/...`` for the plugin part, ``htdocs/json_api/...``
    for the web part, ``wato/json_explorer/...`` for the gui part.

    Ownership is zeroed (the tar must not carry the build machine's user) and
    everything ships read-only except a special agent under ``libexec/``, which
    Checkmk executes.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for file in files:
            arcname = file.relative_to(base)
            info = tar.gettarinfo(str(file), arcname=str(arcname))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if "libexec" in arcname.parts else 0o644
            with file.open("rb") as handle:
                tar.addfile(info, handle)
    return buffer.getvalue()


def _locale_entries(family: str) -> list[tuple[str, bytes]]:
    """Compile each locales/<lang>/LC_MESSAGES/multisite.po to a .mo blob.

    Returns (arcname, mo_bytes) pairs, where arcname is relative to the site's
    ``local/share/check_mk/locale``. We use the per-package subdirectory layout
    (``packages/<family>/...``) so the catalog is isolated to this extension and
    cannot collide with another package's or the site's own multisite.mo.
    """
    entries: list[tuple[str, bytes]] = []
    for po_file in sorted(LOCALES_BASE.glob("*/LC_MESSAGES/multisite.po")):
        lang = po_file.parent.parent.name
        catalog = pofile.parse_po(po_file.read_text(encoding="utf-8"))
        mo_bytes = pofile.compile_mo(catalog)
        arcname = f"packages/{family}/{lang}/LC_MESSAGES/multisite.mo"
        entries.append((arcname, mo_bytes))
    return entries


def _add_bytes(tar: tarfile.TarFile, name: str, content: bytes) -> None:
    """Add in-memory ``content`` as ``name``. A fresh TarInfo is already
    unowned and mode 644, so only the size has to be filled in."""
    info = tarfile.TarInfo(name)
    info.size = len(content)
    tar.addfile(info, io.BytesIO(content))


def _inner_tar_from_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    """Uncompressed tar built from in-memory (arcname, content) pairs."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for arcname, content in entries:
            _add_bytes(tar, arcname, content)
    return buffer.getvalue()


def _manifest(
    *,
    name: str,
    title: str,
    description: str,
    version: str,
    min_required: str,
    usable_until: str | None,
    author: str,
    download_url: str,
    files: dict[str, list[str]],
) -> dict:
    return {
        "title": title,
        "name": name,
        "description": description,
        "version": version,
        "version.packaged": f"checkmk-json-agent {version}",
        "version.min_required": min_required,
        "version.usable_until": usable_until,
        "author": author,
        "download_url": download_url,
        "files": files,
    }


def _write_mkp(name: str, version: str, manifest: dict, part_tars: list[tuple[str, bytes]]) -> Path:
    """Assemble the outer .mkp: info/info.json plus the given inner part tars."""
    parts = [
        ("info", (pprint.pformat(manifest) + "\n").encode()),
        ("info.json", json.dumps(manifest).encode()),
        *part_tars,
    ]
    output = REPO / f"{name}-{version}.mkp"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for entry_name, content in parts:
            _add_bytes(tar, entry_name, content)
    output.write_bytes(buffer.getvalue())
    return output


def build() -> Path:
    """The agent package: plugin code + translations.

    Deliberately GUI-free: it ships only the ``cmk_addons_plugins`` (and
    ``locales``) parts, so the agent depends solely on the public, stable plugin
    APIs. Anything that touches Checkmk's internal frontend lives in the separate
    Explorer package (see :func:`build_explorer`).
    """
    project, mkp = _load_metadata()
    family = mkp["package_name"]
    files = _shipped_files(PLUGINS_BASE / family)
    if not files:
        raise SystemExit(f"No plugin files found under {PLUGINS_BASE / family}")

    relative = [str(f.relative_to(PLUGINS_BASE)) for f in files]
    locale_entries = _locale_entries(family)

    package_files: dict[str, list[str]] = {"cmk_addons_plugins": relative}
    part_tars = [("cmk_addons_plugins.tar", _inner_tar(files, PLUGINS_BASE))]
    if locale_entries:
        package_files["locales"] = [arcname for arcname, _ in locale_entries]
        part_tars.append(("locales.tar", _inner_tar_from_bytes(locale_entries)))

    manifest = _manifest(
        name=family,
        title=mkp["title"],
        description=project["description"],
        version=project["version"],
        min_required=mkp["min_required"],
        usable_until=mkp.get("usable_until"),
        author=mkp["author"],
        download_url=mkp["download_url"],
        files=package_files,
    )
    return _write_mkp(family, project["version"], manifest, part_tars)


def build_explorer() -> Path | None:
    """The Explorer 'extra' package: the wizard bundle (``web``) + its GUI page
    (``gui``).

    Kept separate from the agent on purpose. Only this package touches Checkmk's
    GUI internals — the ``web`` part ships the built Vue app (which compiles the
    real cmk-frontend-vue CmkWizard), and the ``gui`` part ships the Python page
    module that registers ``check_mk/json_explorer.py`` and embeds the app. That
    coupling to internal, version-specific GUI APIs must never reach the
    monitoring agent. Carries its own name/title/``min_required``
    (``[tool.mkp.explorer]`` in pyproject) but *shares the project version*.
    Returns ``None`` when the repo ships no ``web/``.
    """
    project, mkp = _load_metadata()
    explorer = mkp["explorer"]
    web_files = _shipped_files(WEB_BASE)
    if not web_files:
        return None
    # web/ also holds committed, always-present assets (vue-eval.html), so a
    # checkout with no built frontend still yields web_files. Guard on the built
    # wizard's manifest explicitly: without it the Explorer page crashes the GUI
    # with FileNotFoundError, so fail loudly here rather than ship that package.
    wizard_manifest = WEB_BASE / "htdocs" / "json_api" / "wizard" / ".vite" / "manifest.json"
    if not wizard_manifest.is_file():
        raise SystemExit(
            f"Wizard bundle not built: {wizard_manifest.relative_to(REPO)} is missing. "
            "Run 'make frontend' before packaging the Explorer."
        )
    gui_files = _shipped_files(GUI_BASE)

    version = project["version"]
    package_files: dict[str, list[str]] = {"web": [str(f.relative_to(WEB_BASE)) for f in web_files]}
    part_tars = [("web.tar", _inner_tar(web_files, WEB_BASE))]
    if gui_files:
        package_files["gui"] = [str(f.relative_to(GUI_BASE)) for f in gui_files]
        part_tars.append(("gui.tar", _inner_tar(gui_files, GUI_BASE)))

    manifest = _manifest(
        name=explorer["package_name"],
        title=explorer["title"],
        description=explorer["description"],
        version=version,
        min_required=explorer["min_required"],
        usable_until=explorer.get("usable_until"),
        author=mkp["author"],  # author/download_url are shared with the agent
        download_url=mkp["download_url"],
        files=package_files,
    )
    return _write_mkp(explorer["package_name"], version, manifest, part_tars)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build the checkmk-json-agent MKP(s).")
    parser.add_argument(
        "--package",
        choices=["agent", "explorer", "all"],
        default="all",
        help="which package to build (default: all)",
    )
    args = parser.parse_args()

    built: list[Path] = []
    if args.package in ("agent", "all"):
        built.append(build())
    if args.package in ("explorer", "all"):
        explorer_mkp = build_explorer()
        if explorer_mkp is not None:
            built.append(explorer_mkp)
        elif args.package == "explorer":
            raise SystemExit("No web/ assets found — nothing to build for the Explorer package")

    for path in built:
        print(f"Built {path.name} ({path.stat().st_size} bytes)")
