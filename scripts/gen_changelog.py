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
import functools
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHANGELOG = REPO / "CHANGELOG.md"
# Hand-written, per-version operator notes (service renames, new services on the
# next discovery, ...). Nothing generated ever lands here; it is only *read*, and
# only for the release body - CHANGELOG.md stays purely derived from the tags, so
# the CI staleness check keeps working.
UPGRADING = REPO / "UPGRADING.md"

_VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# Conventional-commit style prefix, e.g. "fix:", "feat(agent):", "ci!:".
_PREFIX = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?!?:\s*(?P<rest>.*)$")
_SKIP_SUBJECT = re.compile(r"^(Merge |Bump version\b)")
# A trailing "(#123)" PR reference, as GitHub's squash-merge appends.
_PR_REF = re.compile(r"\s*\(#(\d+)\)\s*$")

# Conventional-commit types we recognise. A leading token from this set is
# stripped from the rendered line regardless of which section it maps to (so
# "ci: run tests" renders as "Run tests", not "Ci: run tests"). An unrecognised
# "word:" prefix (e.g. "Support X: Y") is left untouched.
_KNOWN_TYPES = {
    "feat", "feature", "fix", "bugfix", "ci", "docs", "doc", "test", "tests",
    "refactor", "chore", "perf", "build", "style", "revert",
}  # fmt: skip

# Which section a commit's prefix maps to. Anything unrecognised -> "Other".
_SECTIONS: tuple[tuple[str, set[str]], ...] = (
    ("Features", {"feat", "feature"}),
    ("Fixes", {"fix", "bugfix"}),
    ("Other", set()),  # catch-all, keep last
)


@functools.lru_cache(maxsize=1)
def _repo_url() -> str:
    """The project's GitHub URL, from pyproject, for commit/PR links."""
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    return data["tool"]["mkp"]["download_url"].rstrip("/")


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


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _bucket(subject: str) -> tuple[str, str]:
    """Return (section, cleaned-subject) for a commit subject.

    A recognised conventional-commit prefix is stripped and maps the commit to a
    section; feat/fix get their own, every other known type falls under "Other".
    An unrecognised "word:" prefix is preserved verbatim.
    """
    match = _PREFIX.match(subject)
    if match and match.group("type") in _KNOWN_TYPES:
        ctype = match.group("type")
        rest = match.group("rest") or subject
        for section, types in _SECTIONS:
            if ctype in types:
                return section, _cap(rest)
        return "Other", _cap(rest)
    return "Other", _cap(subject)


def _bullet(short: str, cleaned: str) -> str:
    """A changelog bullet linking the commit (and a trailing PR ref, if any)."""
    repo = _repo_url()
    pr = _PR_REF.search(cleaned)
    if pr:
        number = pr.group(1)
        cleaned = f"{cleaned[: pr.start()]} ([#{number}]({repo}/pull/{number}))"
    return f"- {cleaned} ([`{short}`]({repo}/commit/{short}))"


def _render_version(version: str, date: str | None, commits: list[tuple[str, str]]) -> str:
    header = f"## [{version}]" + (f" - {date}" if date else "")
    lines = [header, ""]
    if not commits:
        lines += ["_No user-facing changes recorded._", ""]
        return "\n".join(lines)

    grouped: dict[str, list[str]] = {name: [] for name, _ in _SECTIONS}
    for short, subject in commits:
        section, cleaned = _bucket(subject)
        grouped[section].append(_bullet(short, cleaned))

    # Only emit section headers when there's more than just "Other" to show;
    # otherwise a repo that doesn't use conventional commits gets a pointless
    # "### Other" under every version.
    nonempty = [name for name, _ in _SECTIONS if grouped[name]]
    show_headers = nonempty != ["Other"]
    for section, _ in _SECTIONS:
        entries = grouped[section]
        if not entries:
            continue
        if show_headers:
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


def upgrade_notes(version: str, text: str | None = None) -> str:
    """The hand-written UPGRADING.md section for ``version``, or ``""``.

    Sections are ``## [X.Y.Z]`` (optionally followed by anything, e.g. a date) and
    run until the next ``## `` heading. The body is returned with its ``###``
    subheadings demoted one level, so it nests under the release notes' own ``##``
    sections instead of competing with them.

    ``text`` is for tests; by default UPGRADING.md is read. A missing file or a
    version with no section is not an error - most releases need no notes.
    """
    if text is None:
        if not UPGRADING.is_file():
            return ""
        text = UPGRADING.read_text()
    pattern = re.compile(
        r"^## \[" + re.escape(version.lstrip("v")) + r"\].*?$(?P<body>.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    found = pattern.search(text)
    if found is None:
        return ""
    body = found.group("body").strip()
    if not body:
        return ""
    demoted = re.sub(r"^###", "####", body, flags=re.MULTILINE)
    return f"### Upgrade notes\n\n{demoted}\n"


def _one_version(version: str) -> str:
    version = version.lstrip("v")
    tag = f"v{version}"
    tags = _version_tags()
    if tag in tags:
        idx = tags.index(tag)
        revrange = f"{tags[idx - 1]}..{tag}" if idx > 0 else tag
        rendered = _render_version(version, _tag_date(tag), _commits(revrange))
    else:
        # Not tagged yet (e.g. building notes before the tag exists): use HEAD range.
        revrange = f"{tags[-1]}..HEAD" if tags else "HEAD"
        rendered = _render_version(version, None, _commits(revrange))
    notes = upgrade_notes(version)
    if not notes:
        return rendered.rstrip() + "\n"
    # Slot the notes in directly under the "## [X.Y.Z]" header, ahead of the
    # generated Features/Fixes sections: what breaks matters more to someone
    # deciding whether to upgrade than what was added.
    header, _, body = rendered.partition("\n")
    return f"{header}\n\n{notes.rstrip()}\n{body}".rstrip() + "\n"


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
