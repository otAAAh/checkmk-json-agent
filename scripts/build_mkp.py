#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Build the checkmk-json-agent MKP using only the standard library.

An MKP is a gzipped tar containing:

  - ``info``       — the manifest as a Python literal dict
  - ``info.json``  — the same manifest as JSON (for external tools)
  - ``cmk_addons_plugins.tar`` — an *uncompressed* tar of the plugin files,
    with paths relative to ``local/lib/python3/cmk_addons/plugins``.
  - ``locales.tar`` — an *uncompressed* tar of the compiled translation
    catalogs, with paths relative to ``local/share/check_mk/locale`` (only
    present when the repo ships translations).

This mirrors cmk_mkp_tool's on-disk format without importing any cmk package,
so the repo stays self-contained. The ``.po`` sources are compiled to ``.mo``
here with :mod:`pofile`, so the ``gettext`` tools aren't needed to build.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pofile  # noqa: E402  (local module, resolved via the path insert above)


def _load_metadata() -> tuple[dict, dict]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    return data["project"], data["tool"]["mkp"]


def _plugin_files(family: str) -> list[Path]:
    return sorted(
        path
        for path in (PLUGINS_BASE / family).rglob("*")
        if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts
    )


def _inner_tar(files: list[Path]) -> bytes:
    """Uncompressed tar of the plugin files, paths relative to PLUGINS_BASE."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for file in files:
            arcname = file.relative_to(PLUGINS_BASE)
            info = tar.gettarinfo(str(file), arcname=str(arcname))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            # Special agent executables under libexec/ must stay executable.
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


def _inner_tar_from_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    """Uncompressed tar built from in-memory (arcname, content) pairs."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for arcname, content in entries:
            info = tarfile.TarInfo(arcname)
            info.size = len(content)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _add_bytes(tar: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(content))


def build() -> Path:
    project, mkp = _load_metadata()
    family = mkp["package_name"]
    files = _plugin_files(family)
    if not files:
        raise SystemExit(f"No plugin files found under {PLUGINS_BASE / family}")

    relative = [str(f.relative_to(PLUGINS_BASE)) for f in files]
    locale_entries = _locale_entries(family)

    package_files = {"cmk_addons_plugins": relative}
    if locale_entries:
        package_files["locales"] = [arcname for arcname, _ in locale_entries]

    manifest = {
        "title": mkp["title"],
        "name": family,
        "description": project["description"],
        "version": project["version"],
        "version.packaged": f"checkmk-json-agent {project['version']}",
        "version.min_required": mkp["min_required"],
        "version.usable_until": mkp.get("usable_until"),
        "author": mkp["author"],
        "download_url": mkp["download_url"],
        "files": package_files,
    }

    parts = [
        ("info", (pprint.pformat(manifest) + "\n").encode()),
        ("info.json", json.dumps(manifest).encode()),
        ("cmk_addons_plugins.tar", _inner_tar(files)),
    ]
    if locale_entries:
        parts.append(("locales.tar", _inner_tar_from_bytes(locale_entries)))

    output = REPO / f"{family}-{project['version']}.mkp"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in parts:
            _add_bytes(tar, name, content)
    output.write_bytes(buffer.getvalue())
    return output


if __name__ == "__main__":
    path = build()
    print(f"Built {path.name} ({path.stat().st_size} bytes)")
