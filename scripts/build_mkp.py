#!/usr/bin/env python3
# Copyright (C) 2026 checkmk-json-agent contributors
# SPDX-License-Identifier: GPL-2.0-only
"""Build the checkmk-json-agent MKP using only the standard library.

An MKP is a gzipped tar containing:

  - ``info``       — the manifest as a Python literal dict
  - ``info.json``  — the same manifest as JSON (for external tools)
  - ``cmk_addons_plugins.tar`` — an *uncompressed* tar of the plugin files,
    with paths relative to ``local/lib/python3/cmk_addons/plugins``.

This mirrors cmk_mkp_tool's on-disk format without importing any cmk package,
so the repo stays self-contained.
"""

from __future__ import annotations

import io
import json
import pprint
import tarfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Repo dir that maps onto the site's local/lib/python3/cmk_addons/plugins:
PLUGINS_BASE = REPO / "cmk_addons" / "plugins"


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
        "files": {"cmk_addons_plugins": relative},
    }

    parts = [
        ("info", (pprint.pformat(manifest) + "\n").encode()),
        ("info.json", json.dumps(manifest).encode()),
        ("cmk_addons_plugins.tar", _inner_tar(files)),
    ]

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
