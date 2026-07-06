#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Generate a per-version changelog from the git history.

Releases are cut as annotated ``vX.Y.Z`` tags. For each tag this script collects
the commits between it and its predecessor, drops the noise (merge commits and
the ``Bump version`` housekeeping commit), buckets what is left into
Features / Fixes / Other, and renders Markdown.

Two modes, both stdlib-only so the repo stays self-contained:

  * ``gen_changelog.py``                  -> write the full CHANGELOG.md
  * ``gen_changelog.py --version 0.3.0``  -> print just that version's section
                                             (used as the GitHub Release body)

With ``--version`` the leading ``v`` is optional. Use ``--stdout`` to print the
full changelog instead of writing the file.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHANGELOG = REPO / "CHANGELOG.md"

_VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# Conventional-commit style prefix, e.g. "fix:", "feat(agent):", "ci!:".
_PREFIX = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?!?:\s*(?P<rest>.*)$")
_SKIP_SUBJECT = re.compile(r"^(Merge |Bump version\b)")

# Which section a commit's prefix maps to. Anything unrecognised -> "Other".
_SECTIONS: tuple[tuple[str, set[str]], ...] = (
    ("Features", {"feat", "feature"}),
    ("Fixes", {"fix", "bugfix"}),
    ("Other", set()),  # catch-all, keep last
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _project_version() -> str:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    return data["project"]["version"]


def _version_tags() -> list[str]:
    """All ``vX.Y.Z`` tags, oldest first."""
    matches = [(t, _VERSION_TAG.match(t)) for t in _git("tag", "--list").splitlines()]
    tagged = [(t, m) for t, m in matches if m is not None]
    return [t for t, _ in sorted(tagged, key=lambda tm: tuple(int(n) for n in tm[1].groups()))]


def _tag_date(ref: str) -> str:
    return _git("log", "-1", "--format=%ad", "--date=short", ref)


def _commits(revrange: str) -> list[tuple[str, str]]:
    """(short-hash, subject) for non-merge commits in ``revrange``, newest first."""
    out = _git("log", "--no-merges", "--format=%h%x00%s", revrange)
    commits = []
    for line in out.splitlines():
        if not line:
            continue
        short, subject = line.split("\x00", 1)
        if _SKIP_SUBJECT.match(subject):
            continue
        commits.append((short, subject))
    return commits


def _bucket(subject: str) -> tuple[str, str]:
    """Return (section, cleaned-subject) for a commit subject."""
    match = _PREFIX.match(subject)
    if match:
        ctype = match.group("type")
        rest = match.group("rest") or subject
        for section, types in _SECTIONS:
            if ctype in types:
                return section, rest[:1].upper() + rest[1:]
    return "Other", subject[:1].upper() + subject[1:]


def _render_version(version: str, date: str | None, commits: list[tuple[str, str]]) -> str:
    header = f"## [{version}]" + (f" - {date}" if date else "")
    lines = [header, ""]
    if not commits:
        lines += ["_No user-facing changes recorded._", ""]
        return "\n".join(lines)

    grouped: dict[str, list[str]] = {name: [] for name, _ in _SECTIONS}
    for short, subject in commits:
        section, cleaned = _bucket(subject)
        grouped[section].append(f"- {cleaned} ({short})")

    for section, _ in _SECTIONS:
        entries = grouped[section]
        if entries:
            lines.append(f"### {section}")
            lines.append("")
            lines.extend(entries)
            lines.append("")
    return "\n".join(lines)


def _sections() -> list[tuple[str, str | None, list[tuple[str, str]]]]:
    """Ordered (version, date, commits) newest first, incl. an Unreleased head."""
    tags = _version_tags()
    result: list[tuple[str, str | None, list[tuple[str, str]]]] = []

    # Anything committed after the newest tag is "Unreleased".
    if tags:
        unreleased = _commits(f"{tags[-1]}..HEAD")
        if unreleased:
            result.append(("Unreleased", None, unreleased))
    else:
        # No tags yet: everything is unreleased under the pyproject version.
        return [(_project_version(), None, _commits("HEAD"))]

    for i in range(len(tags) - 1, -1, -1):
        tag = tags[i]
        revrange = f"{tags[i - 1]}..{tag}" if i > 0 else tag
        version = tag[1:]  # strip leading "v"
        result.append((version, _tag_date(tag), _commits(revrange)))
    return result


def _full_changelog() -> str:
    parts = [
        "# Changelog",
        "",
        "All notable changes to this project, one section per released version.",
        "Generated from the git history by `scripts/gen_changelog.py`.",
        "",
    ]
    for version, date, commits in _sections():
        parts.append(_render_version(version, date, commits))
    return "\n".join(parts).rstrip() + "\n"


def _one_version(version: str) -> str:
    version = version.lstrip("v")
    tag = f"v{version}"
    tags = _version_tags()
    if tag in tags:
        idx = tags.index(tag)
        revrange = f"{tags[idx - 1]}..{tag}" if idx > 0 else tag
        return _render_version(version, _tag_date(tag), _commits(revrange)).rstrip() + "\n"
    # Not tagged yet (e.g. building notes before the tag exists): use HEAD range.
    revrange = f"{tags[-1]}..HEAD" if tags else "HEAD"
    return _render_version(version, None, _commits(revrange)).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        help="Print only this version's section (leading 'v' optional).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the full changelog instead of writing CHANGELOG.md.",
    )
    args = parser.parse_args()

    if args.version:
        sys.stdout.write(_one_version(args.version))
        return 0

    content = _full_changelog()
    if args.stdout:
        sys.stdout.write(content)
    else:
        CHANGELOG.write_text(content)
        print(f"Wrote {CHANGELOG.relative_to(REPO)} ({len(content)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
