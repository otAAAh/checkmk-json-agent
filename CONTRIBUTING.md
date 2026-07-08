<!-- SPDX-License-Identifier: GPL-2.0-only -->
# Contributing

Thanks for taking an interest in **checkmk-json-agent**! This is a small,
single-maintainer Checkmk plugin, so contributions of any size — a bug report, a
doc fix, a new feature, a translation — are genuinely welcome.

## Ways to contribute

- **Ask a question or share an idea** →
  [Discussions](https://github.com/otAAAh/checkmk-json-agent/discussions).
  Great for "how do I monitor X?", showing off a config, or floating a feature
  before writing code.
- **Report a bug** → [open an issue](https://github.com/otAAAh/checkmk-json-agent/issues).
  Include your Checkmk version and edition, the plugin version, the (redacted)
  rule, a sample of the JSON response, and what you expected vs. what happened.
- **Report a security problem** → **don't** open a public issue; follow
  [`SECURITY.md`](SECURITY.md) and use GitHub's private advisory flow.
- **Send a change** → open a pull request (see below).

## Development setup

The plugin imports the `cmk.*` plugin APIs, which only exist inside a Checkmk
site or a Checkmk dev virtualenv. Two things need a Python:

- **Dev tooling** (format, lint, type-check, build) runs against the pinned dev
  dependencies — no Checkmk needed:

  ```sh
  pip install -r requirements-dev.txt
  ```

- **The test suite** imports `cmk.*`, so it needs a Checkmk site Python. Point
  `make` at a Checkmk dev venv:

  ```sh
  PYTHON=/path/to/checkmk/.venv/bin/python make test
  ```

  (CI runs the suite inside the official Checkmk containers for both the 2.4 and
  2.5 lines — you don't need both locally.)

## Before you open a PR

Run the same checks CI runs. All of them should be green:

```sh
make format      # ruff format
make lint        # ruff check
make typecheck   # mypy
make test        # pytest (needs a site Python, see above)
```

If your change touches translatable Setup/graphing strings, also keep the
catalogs in sync:

```sh
make pot         # refresh the template from the code
# then msgmerge each locales/<lang>/LC_MESSAGES/multisite.po and translate,
# see the "Translations" section in the README
make check-po    # verify every catalog matches the source strings (CI does this)
```

If your change is user-visible, the released `CHANGELOG.md` sections are derived
from git tags and checked by CI — you don't hand-edit them. Just write a clear
commit message; the changelog is regenerated with `make changelog` at release
time.

## Pull request guidelines

- **Keep it focused.** One logical change per PR is easier to review and revert.
- **Match the surrounding style.** Every source file carries the
  `SPDX-License-Identifier: GPL-2.0-only` header — keep it on new files.
- **Add or update tests** for behaviour changes. The suite lives in `tests/`.
- **Update the docs** (`README.md`, the checkman man page, the Explorer) when
  behaviour or config changes. The Explorer (`explorer/index.html`) mirrors the
  ruleset schema and is checked against it by `tests/test_explorer.py`, so a new
  ruleset field needs a matching Explorer update.
- **Explain the "why"** in the PR description, not just the "what".

## Project layout

```
cmk_addons/plugins/json_api/
  server_side_calls/   rule -> agent command line
  rulesets/            the Setup form
  libexec/             the special agent executable
  agent_based/         section parsing + check
  graphing/            metric definition
  checkman/            man page
explorer/              standalone, dependency-free config Explorer
locales/               translation catalogs (one .po per language)
scripts/               build_mkp.py, gen_changelog.py, ...
tests/                 pytest suite
```

## Compatibility

The plugin targets **Checkmk 2.4+** and the current stable plugin APIs
(`cmk.agent_based.v2`, `cmk.rulesets.v1`, `cmk.server_side_calls.v1`,
`cmk.graphing.v1`). The agent branches on the password-store API to support both
2.4 and 2.5+, so please keep changes working across that range (CI exercises
both).

## License

By contributing, you agree that your contributions are licensed under the
project's **GPL-2.0-only** license. See [LICENSE](LICENSE).
