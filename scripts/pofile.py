# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Minimal, dependency-free gettext PO parser and MO compiler.

This lets ``build_mkp.py`` turn the committed ``.po`` sources into the binary
``.mo`` catalogs an MKP ships, without requiring the ``gettext`` tools (or any
Checkmk package) at build time - keeping the packaging self-contained.

Only the subset the plugin needs is supported: singular messages with the
default context. Fuzzy and untranslated entries are dropped, mirroring
``msgfmt``. The empty-id header entry is kept (gettext reads the charset there).
"""

from __future__ import annotations

import array
import re
import struct
from collections.abc import Mapping

_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    '"': '"',
    "\\": "\\",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
}


def _unescape(quoted: str) -> str:
    """Decode the C-style escapes inside a single PO string literal body."""
    out: list[str] = []
    i = 0
    while i < len(quoted):
        char = quoted[i]
        if char == "\\" and i + 1 < len(quoted):
            out.append(_ESCAPES.get(quoted[i + 1], quoted[i + 1]))
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _string_body(line: str) -> str:
    """Return the unescaped content of the quoted string on a PO line."""
    return _unescape(line[line.index('"') + 1 : line.rindex('"')])


def parse_po(text: str) -> dict[str, str]:
    """Parse PO text into a ``{msgid: msgstr}`` catalog.

    Entries are separated by blank lines (as emitted by ``msgfmt``/``xgettext``).
    Fuzzy entries and entries with an empty translation are skipped, except the
    header entry (empty msgid), which is always kept.
    """
    catalog: dict[str, str] = {}
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.splitlines()
        fuzzy = any(line.startswith("#,") and "fuzzy" in line for line in lines)
        msgid: str | None = None
        msgstr: str | None = None
        mode: str | None = None
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("msgid "):
                msgid, mode = _string_body(line), "id"
            elif line.startswith("msgstr "):
                msgstr, mode = _string_body(line), "str"
            elif line.startswith('"'):
                if mode == "id" and msgid is not None:
                    msgid += _string_body(line)
                elif mode == "str" and msgstr is not None:
                    msgstr += _string_body(line)
        if msgid is None or msgstr is None or fuzzy:
            continue
        if msgid == "" or msgstr != "":
            catalog[msgid] = msgstr
    return catalog


def compile_mo(catalog: Mapping[str, str]) -> bytes:
    """Compile a ``{msgid: msgstr}`` catalog into GNU MO binary format.

    Follows the layout expected by :mod:`gettext`: a 7-word header, the message
    id and translation offset tables, then the NUL-terminated strings. Keys are
    sorted by their UTF-8 bytes, as gettext binary-searches them.
    """
    keys = sorted((k.encode("utf-8") for k in catalog), key=lambda b: b)
    values = {k.encode("utf-8"): catalog[k].encode("utf-8") for k in catalog}

    ids = b""
    strs = b""
    offsets: list[tuple[int, int, int, int]] = []
    for key in keys:
        value = values[key]
        offsets.append((len(ids), len(key), len(strs), len(value)))
        ids += key + b"\x00"
        strs += value + b"\x00"

    count = len(keys)
    keytable_start = 7 * 4 + count * 8 + count * 8
    valuetable_start = keytable_start + len(ids)
    key_offsets: list[int] = []
    value_offsets: list[int] = []
    for id_off, id_len, str_off, str_len in offsets:
        key_offsets += [id_len, id_off + keytable_start]
        value_offsets += [str_len, str_off + valuetable_start]

    header = struct.pack(
        "Iiiiiii",
        0x950412DE,  # magic
        0,  # version
        count,
        7 * 4,  # offset of key table
        7 * 4 + count * 8,  # offset of value table
        0,  # hash table size
        0,  # hash table offset
    )
    return header + array.array("i", key_offsets + value_offsets).tobytes() + ids + strs
