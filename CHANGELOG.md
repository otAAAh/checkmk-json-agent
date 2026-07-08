# Changelog

All notable changes to this project, one section per released version.
Generated from the git history by `scripts/gen_changelog.py`.

## [0.6.0] - 2026-07-08

- Deps: bump pytest from 8.3.4 to 9.0.3 ([`27472a4`](https://github.com/otAAAh/checkmk-json-agent/commit/27472a4))
- CI/repo maintenance hardening ([`10d7623`](https://github.com/otAAAh/checkmk-json-agent/commit/10d7623))
- Localize the Setup UI into all Checkmk-supported languages ([`898e29c`](https://github.com/otAAAh/checkmk-json-agent/commit/898e29c))
- Add CI guard that the Explorer stays in sync with the ruleset schema ([`8c4929b`](https://github.com/otAAAh/checkmk-json-agent/commit/8c4929b))
- CI: test on Checkmk 2.4 + 2.5; attest mkp build provenance ([`e35e211`](https://github.com/otAAAh/checkmk-json-agent/commit/e35e211))
- Bump the actions group with 4 updates ([`30d5569`](https://github.com/otAAAh/checkmk-json-agent/commit/30d5569))
- Harden GitHub Actions: pin action SHAs, add CodeQL + Dependabot ([`3273e98`](https://github.com/otAAAh/checkmk-json-agent/commit/3273e98))

## [0.5.0] - 2026-07-06

- Surface misconfigured levels even on a failed 'expected' match ([`8e0eb35`](https://github.com/otAAAh/checkmk-json-agent/commit/8e0eb35))
- Confine malformed-blob and extraction errors to their endpoint ([`cc8c59a`](https://github.com/otAAAh/checkmk-json-agent/commit/cc8c59a))
- Do not send Content-Type for a body that a GET never sends ([`659164d`](https://github.com/otAAAh/checkmk-json-agent/commit/659164d))
- Accept case-insensitive URL schemes in ruleset validation ([`bd551e7`](https://github.com/otAAAh/checkmk-json-agent/commit/bd551e7))

## [0.4.0] - 2026-07-06

- Fix check/ruleset edge cases: URL scheme, non-numeric levels, inf/nan ([`0f0284e`](https://github.com/otAAAh/checkmk-json-agent/commit/0f0284e))
- Harden the special agent: response cap, secret isolation, concurrency ([`cfcbaaf`](https://github.com/otAAAh/checkmk-json-agent/commit/cfcbaaf))
- Improve changelog generation: strip prefixes, drop noise headers, link commits ([`28fcf0b`](https://github.com/otAAAh/checkmk-json-agent/commit/28fcf0b))
- Install pydantic so mypy can type-check the server-side call ([`57cd788`](https://github.com/otAAAh/checkmk-json-agent/commit/57cd788))
- Harden CI and the release pipeline ([`6acdd80`](https://github.com/otAAAh/checkmk-json-agent/commit/6acdd80))
- Refresh CHANGELOG.md for the v0.1.0 tag ([`5247f1e`](https://github.com/otAAAh/checkmk-json-agent/commit/5247f1e))
- Add release pipeline: tag-triggered MKP build + per-version changelog ([`ee3cfae`](https://github.com/otAAAh/checkmk-json-agent/commit/ee3cfae))
- Bring JSON API Explorer up to 0.3.0 feature parity ([`cd37fb3`](https://github.com/otAAAh/checkmk-json-agent/commit/cd37fb3))
- Ship localized Setup UI strings (German) ([`0e56e1a`](https://github.com/otAAAh/checkmk-json-agent/commit/0e56e1a))

## [0.3.0] - 2026-07-03

- Run the pytest suite in the Checkmk container ([`ceb385e`](https://github.com/otAAAh/checkmk-json-agent/commit/ceb385e))
- Surface JSON path and source URL in check Details ([`1034117`](https://github.com/otAAAh/checkmk-json-agent/commit/1034117))
- Resolve Checkmk macros in endpoint URL, body and headers ([`02cffaa`](https://github.com/otAAAh/checkmk-json-agent/commit/02cffaa))

## [0.2.0] - 2026-07-02

- Add per-endpoint 'follow redirects' toggle (SSRF hardening) ([`7a5c920`](https://github.com/otAAAh/checkmk-json-agent/commit/7a5c920))
- Per-field metric units and naming ([`1cc1f2a`](https://github.com/otAAAh/checkmk-json-agent/commit/1cc1f2a))
- Support multiple endpoints per rule ([`c4fe4e6`](https://github.com/otAAAh/checkmk-json-agent/commit/c4fe4e6))
- Support nested [*] array wildcards via cartesian-product expansion ([`2e8cda9`](https://github.com/otAAAh/checkmk-json-agent/commit/2e8cda9))
- Support bracket-quoted path segments for keys containing '.' or '[' ([`4acf1ab`](https://github.com/otAAAh/checkmk-json-agent/commit/4acf1ab))

## [0.1.0] - 2026-06-29

- Add SVG icon (repo logo / social image) ([`7076450`](https://github.com/otAAAh/checkmk-json-agent/commit/7076450))
- Tighten Exchange listing copy (more concise, sales-forward) ([`2c6b366`](https://github.com/otAAAh/checkmk-json-agent/commit/2c6b366))
- README: add CI and license badges ([`053f319`](https://github.com/otAAAh/checkmk-json-agent/commit/053f319))
- Minor polish: timeout option, POST content-type, nested-wildcard error ([`f247cfa`](https://github.com/otAAAh/checkmk-json-agent/commit/f247cfa))
- Add Exchange listing description (paste-ready Markdown) ([`cab664f`](https://github.com/otAAAh/checkmk-json-agent/commit/cab664f))
- Address remaining review items (#2, #3, #5) + add CI ([`6c7e870`](https://github.com/otAAAh/checkmk-json-agent/commit/6c7e870))
- Fix regex-crash and silent-levels findings (#1, #4) ([`b5fa9cd`](https://github.com/otAAAh/checkmk-json-agent/commit/b5fa9cd))
- Support Checkmk 2.4 (version-adaptive secret resolution) ([`284b543`](https://github.com/otAAAh/checkmk-json-agent/commit/284b543))
- Add standalone JSON API Explorer (spike) ([`7e66d90`](https://github.com/otAAAh/checkmk-json-agent/commit/7e66d90))
- Set copyright/author to Benjamin Knapp ([`618003f`](https://github.com/otAAAh/checkmk-json-agent/commit/618003f))
- Add README ([`552a079`](https://github.com/otAAAh/checkmk-json-agent/commit/552a079))
- Guarantee unique service names for array discovery ([`3368a33`](https://github.com/otAAAh/checkmk-json-agent/commit/3368a33))
- Add pytest suite (path resolver, extraction, check, server-side call) ([`c156ef2`](https://github.com/otAAAh/checkmk-json-agent/commit/c156ef2))
- Add array auto-discovery via [*] wildcard ([`d45f11c`](https://github.com/otAAAh/checkmk-json-agent/commit/d45f11c))
- Fix parser_add_secret_option call (keyword-only signature) ([`17b970b`](https://github.com/otAAAh/checkmk-json-agent/commit/17b970b))
- Add MKP packaging and dev tooling ([`30f4287`](https://github.com/otAAAh/checkmk-json-agent/commit/30f4287))
- Add config-driven JSON API special agent (skeleton) ([`4ec6f2d`](https://github.com/otAAAh/checkmk-json-agent/commit/4ec6f2d))
- Add .gitignore ([`03c2a7b`](https://github.com/otAAAh/checkmk-json-agent/commit/03c2a7b))
- Initial commit ([`9be9780`](https://github.com/otAAAh/checkmk-json-agent/commit/9be9780))
