# Changelog

All notable changes to this project, one section per released version.
Generated from the git history by `scripts/gen_changelog.py`.

## [0.15.0] - 2026-08-31

### Features

- Authenticate with OAuth 2.0 client credentials ([#172](https://github.com/otAAAh/checkmk-json-agent/pull/172)) ([`1eecc5b`](https://github.com/otAAAh/checkmk-json-agent/commit/1eecc5b))

### Fixes

- The wizard's token preview could echo the client secret ([#173](https://github.com/otAAAh/checkmk-json-agent/pull/173)) ([`a41fd92`](https://github.com/otAAAh/checkmk-json-agent/commit/a41fd92))

### Other

- Prepare 0.15.0 - announcement and refreshed Exchange listings ([#174](https://github.com/otAAAh/checkmk-json-agent/pull/174)) ([`6c017aa`](https://github.com/otAAAh/checkmk-json-agent/commit/6c017aa))
- Bump the actions group with 2 updates ([#170](https://github.com/otAAAh/checkmk-json-agent/pull/170)) ([`19ca238`](https://github.com/otAAAh/checkmk-json-agent/commit/19ca238))
- Deps: Bump ruff from 0.16.4 to 0.16.5 ([#169](https://github.com/otAAAh/checkmk-json-agent/pull/169)) ([`df57976`](https://github.com/otAAAh/checkmk-json-agent/commit/df57976))
- Spike — go generic client-credentials for OAuth2 ([#171](https://github.com/otAAAh/checkmk-json-agent/pull/171)) ([`255bc95`](https://github.com/otAAAh/checkmk-json-agent/commit/255bc95))

## [0.14.0] - 2026-08-30

### Features

- Give piggyback hosts host labels from their own element ([#165](https://github.com/otAAAh/checkmk-json-agent/pull/165)) ([`07b7799`](https://github.com/otAAAh/checkmk-json-agent/commit/07b7799))
- Pick response headers instead of typing '@header.' paths ([#162](https://github.com/otAAAh/checkmk-json-agent/pull/162)) ([`3c904b6`](https://github.com/otAAAh/checkmk-json-agent/commit/3c904b6))
- Compute a ratio from a second path with the 'other' variable ([#160](https://github.com/otAAAh/checkmk-json-agent/pull/160)) ([`c294cdf`](https://github.com/otAAAh/checkmk-json-agent/commit/c294cdf))
- Read a value from a response header with an '@header.' path ([#159](https://github.com/otAAAh/checkmk-json-agent/pull/159)) ([`bbd8828`](https://github.com/otAAAh/checkmk-json-agent/commit/bbd8828))

### Fixes

- Do not crash the rule form when a required field is emptied ([#163](https://github.com/otAAAh/checkmk-json-agent/pull/163)) ([`2928cc4`](https://github.com/otAAAh/checkmk-json-agent/commit/2928cc4))

### Other

- The 0.14.0 forum announcement and its upgrade note ([#166](https://github.com/otAAAh/checkmk-json-agent/pull/166)) ([`d5c16db`](https://github.com/otAAAh/checkmk-json-agent/commit/d5c16db))
- Deps: Bump ruff from 0.16.3 to 0.16.4 ([#154](https://github.com/otAAAh/checkmk-json-agent/pull/154)) ([`a0d3da8`](https://github.com/otAAAh/checkmk-json-agent/commit/a0d3da8))
- Use Checkmk's own click-outside and key-shortcut helpers ([#158](https://github.com/otAAAh/checkmk-json-agent/pull/158)) ([`c3f2d44`](https://github.com/otAAAh/checkmk-json-agent/commit/c3f2d44))
- Bump the actions group with 2 updates ([#155](https://github.com/otAAAh/checkmk-json-agent/pull/155)) ([`deddc2d`](https://github.com/otAAAh/checkmk-json-agent/commit/deddc2d))
- Deps: Bump mypy from 2.3.0 to 2.3.1 ([#153](https://github.com/otAAAh/checkmk-json-agent/pull/153)) ([`e628584`](https://github.com/otAAAh/checkmk-json-agent/commit/e628584))
- Collapse duplicated logic, no behaviour change ([#157](https://github.com/otAAAh/checkmk-json-agent/pull/157)) ([`7fa1e91`](https://github.com/otAAAh/checkmk-json-agent/commit/7fa1e91))
- Bump the actions group with 2 updates ([#152](https://github.com/otAAAh/checkmk-json-agent/pull/152)) ([`37aa37e`](https://github.com/otAAAh/checkmk-json-agent/commit/37aa37e))
- Deps: Bump ruff from 0.16.1 to 0.16.3 ([#151](https://github.com/otAAAh/checkmk-json-agent/pull/151)) ([`b0aaa34`](https://github.com/otAAAh/checkmk-json-agent/commit/b0aaa34))
- Refresh the Exchange listing, four releases behind ([#150](https://github.com/otAAAh/checkmk-json-agent/pull/150)) ([`f9aac06`](https://github.com/otAAAh/checkmk-json-agent/commit/f9aac06))
- Add the 0.13.0 forum announcement ([#149](https://github.com/otAAAh/checkmk-json-agent/pull/149)) ([`c9613a8`](https://github.com/otAAAh/checkmk-json-agent/commit/c9613a8))

## [0.13.0] - 2026-08-14

### Features

- Write a field into the HW/SW inventory ([#146](https://github.com/otAAAh/checkmk-json-agent/pull/146)) ([`cae4e0e`](https://github.com/otAAAh/checkmk-json-agent/commit/cae4e0e))
- Retry a failed request per endpoint, with backoff ([#145](https://github.com/otAAAh/checkmk-json-agent/pull/145)) ([`7e9d0ed`](https://github.com/otAAAh/checkmk-json-agent/commit/7e9d0ed))
- Extra summary text with '{path}' placeholders (closes #140) ([#144](https://github.com/otAAAh/checkmk-json-agent/pull/144)) ([`43df229`](https://github.com/otAAAh/checkmk-json-agent/commit/43df229))
- API key authentication from the password store (closes #139) ([#143](https://github.com/otAAAh/checkmk-json-agent/pull/143)) ([`92b1942`](https://github.com/otAAAh/checkmk-json-agent/commit/92b1942))

### Fixes

- Six defects found reviewing the four merged features ([#147](https://github.com/otAAAh/checkmk-json-agent/pull/147)) ([`68d9a3b`](https://github.com/otAAAh/checkmk-json-agent/commit/68d9a3b))

### Other

- Upgrade notes for the inventory target and the cache-key change ([#148](https://github.com/otAAAh/checkmk-json-agent/pull/148)) ([`6a32f4b`](https://github.com/otAAAh/checkmk-json-agent/commit/6a32f4b))
- CI fix: Dependabot Updates on main ([#138](https://github.com/otAAAh/checkmk-json-agent/pull/138)) ([`d59fa0d`](https://github.com/otAAAh/checkmk-json-agent/commit/d59fa0d))
- Bump js-yaml from 4.3.0 to 4.3.1 in /frontend ([#136](https://github.com/otAAAh/checkmk-json-agent/pull/136)) ([`7768f83`](https://github.com/otAAAh/checkmk-json-agent/commit/7768f83))
- Bump the actions group with 3 updates ([#135](https://github.com/otAAAh/checkmk-json-agent/pull/135)) ([`8bc1880`](https://github.com/otAAAh/checkmk-json-agent/commit/8bc1880))
- Bump fast-uri from 3.1.4 to 3.1.5 in /frontend ([#134](https://github.com/otAAAh/checkmk-json-agent/pull/134)) ([`edba561`](https://github.com/otAAAh/checkmk-json-agent/commit/edba561))
- Add the 0.12.0 forum announcement and an announce step ([#133](https://github.com/otAAAh/checkmk-json-agent/pull/133)) ([`d055ed4`](https://github.com/otAAAh/checkmk-json-agent/commit/d055ed4))

## [0.12.0] - 2026-08-03

### Features

- Per-endpoint response cache (TTL) ([#132](https://github.com/otAAAh/checkmk-json-agent/pull/132)) ([`cb5091c`](https://github.com/otAAAh/checkmk-json-agent/commit/cb5091c))
- Report the TLS certificate's remaining validity (closes #128) ([#131](https://github.com/otAAAh/checkmk-json-agent/pull/131)) ([`753bb77`](https://github.com/otAAAh/checkmk-json-agent/commit/753bb77))
- Create one Checkmk host per '[*]' element (closes #127) ([#130](https://github.com/otAAAh/checkmk-json-agent/pull/130)) ([`e2d3ca2`](https://github.com/otAAAh/checkmk-json-agent/commit/e2d3ca2))
- Reject two endpoints sharing a name (closes #116) ([#122](https://github.com/otAAAh/checkmk-json-agent/pull/122)) ([`22413fe`](https://github.com/otAAAh/checkmk-json-agent/commit/22413fe))

### Fixes

- Distinguish a null element from a missing container (closes #114) (#120) ([#126](https://github.com/otAAAh/checkmk-json-agent/pull/126)) ([`aedc00f`](https://github.com/otAAAh/checkmk-json-agent/commit/aedc00f))
- Stop the aggregation preview claiming a pre-filter count (closes #115) ([#123](https://github.com/otAAAh/checkmk-json-agent/pull/123)) ([`5655e4e`](https://github.com/otAAAh/checkmk-json-agent/commit/5655e4e))
- Keep the query string out of the endpoint service item (closes #111) ([#121](https://github.com/otAAAh/checkmk-json-agent/pull/121)) ([`96dff75`](https://github.com/otAAAh/checkmk-json-agent/commit/96dff75))
- Count only the elements that have the field (closes #113) ([#119](https://github.com/otAAAh/checkmk-json-agent/pull/119)) ([`8a0c1af`](https://github.com/otAAAh/checkmk-json-agent/commit/8a0c1af))
- Render a negative duration instead of crashing (closes #110) ([#118](https://github.com/otAAAh/checkmk-json-agent/pull/118)) ([`a20b34f`](https://github.com/otAAAh/checkmk-json-agent/commit/a20b34f))

### Other

- Add UPGRADING.md and put it in the release body (closes #112) ([#125](https://github.com/otAAAh/checkmk-json-agent/pull/125)) ([`e706010`](https://github.com/otAAAh/checkmk-json-agent/commit/e706010))
- Check gui/ too, and align the make targets with CI (closes #117) ([#124](https://github.com/otAAAh/checkmk-json-agent/pull/124)) ([`55c026f`](https://github.com/otAAAh/checkmk-json-agent/commit/55c026f))
- Bump the actions group with 2 updates ([#109](https://github.com/otAAAh/checkmk-json-agent/pull/109)) ([`f1f8034`](https://github.com/otAAAh/checkmk-json-agent/commit/f1f8034))
- Deps: Bump ruff from 0.16.0 to 0.16.1 ([#108](https://github.com/otAAAh/checkmk-json-agent/pull/108)) ([`1f48044`](https://github.com/otAAAh/checkmk-json-agent/commit/1f48044))

## [0.11.0] - 2026-07-29

### Features

- Counter rate / timestamp age + per-endpoint status service (land #105 and #106 on main) ([#107](https://github.com/otAAAh/checkmk-json-agent/pull/107)) ([`8f2c137`](https://github.com/otAAAh/checkmk-json-agent/commit/8f2c137))
- Aggregate a collection into one value (sum/avg/min/max, not just count) ([#104](https://github.com/otAAAh/checkmk-json-agent/pull/104)) ([`a67f78b`](https://github.com/otAAAh/checkmk-json-agent/commit/a67f78b))

### Other

- Bump postcss from 8.5.16 to 8.5.23 in /frontend ([#103](https://github.com/otAAAh/checkmk-json-agent/pull/103)) ([`6801db0`](https://github.com/otAAAh/checkmk-json-agent/commit/6801db0))
- Bump the actions group with 4 updates ([`cca693d`](https://github.com/otAAAh/checkmk-json-agent/commit/cca693d))
- Deps: Bump ruff from 0.15.22 to 0.16.0 ([`75426ae`](https://github.com/otAAAh/checkmk-json-agent/commit/75426ae))
- Bump fast-uri from 3.1.3 to 3.1.4 in /frontend ([`1be05f0`](https://github.com/otAAAh/checkmk-json-agent/commit/1be05f0))

## [0.10.0] - 2026-07-21

### Features

- Filter wildcard/count elements by a predicate ([`0dc3b7e`](https://github.com/otAAAh/checkmk-json-agent/commit/0dc3b7e))
- Custom CA bundle and client certificate (mTLS) ([`9129ec9`](https://github.com/otAAAh/checkmk-json-agent/commit/9129ec9))
- HTTP proxy support per endpoint ([`2ea0ba0`](https://github.com/otAAAh/checkmk-json-agent/commit/2ea0ba0))
- Accept configurable non-2xx HTTP status codes ([`ef0ce65`](https://github.com/otAAAh/checkmk-json-agent/commit/ef0ce65))
- Add --debug flag to the special agent ([`e21fcf2`](https://github.com/otAAAh/checkmk-json-agent/commit/e21fcf2))

### Other

- Build the Explorer frontend before packaging in bridge-check ([`1e19116`](https://github.com/otAAAh/checkmk-json-agent/commit/1e19116))
- Deps: Bump mypy from 2.2.0 to 2.3.0 ([`3064c93`](https://github.com/otAAAh/checkmk-json-agent/commit/3064c93))
- Bump the actions group with 3 updates ([`fa05877`](https://github.com/otAAAh/checkmk-json-agent/commit/fa05877))
- Deps: Bump ruff from 0.15.21 to 0.15.22 ([`7ec301b`](https://github.com/otAAAh/checkmk-json-agent/commit/7ec301b))

## [0.9.0] - 2026-07-15

### Features

- JSON API service/host labels + fix broken Explorer release ([`25a16c1`](https://github.com/otAAAh/checkmk-json-agent/commit/25a16c1))
- Add check-parameters ruleset for per-folder threshold overrides ([`93dc597`](https://github.com/otAAAh/checkmk-json-agent/commit/93dc597))

### Other

- Add Exchange listing for the in-site Explorer package ([`02a4537`](https://github.com/otAAAh/checkmk-json-agent/commit/02a4537))

## [0.8.0] - 2026-07-14

### Features

- Count elements + on-site wizard fixes (completes #75) ([`36ee9d2`](https://github.com/otAAAh/checkmk-json-agent/commit/36ee9d2))
- String matching with OK/WARN/CRIT state mapping ([`727e033`](https://github.com/otAAAh/checkmk-json-agent/commit/727e033))

### Fixes

- Don't crash the check on a blank calc expression ([`a7254e8`](https://github.com/otAAAh/checkmk-json-agent/commit/a7254e8))
- Flush stdout so the agent's section shows on direct CLI runs ([`854bdd7`](https://github.com/otAAAh/checkmk-json-agent/commit/854bdd7))

### Other

- Add supported-Checkmk-version badges to the README ([`d87bb9f`](https://github.com/otAAAh/checkmk-json-agent/commit/d87bb9f))
- Stub raw-dist-missing icons; take bridge-check off the PR path ([`c7a6aab`](https://github.com/otAAAh/checkmk-json-agent/commit/c7a6aab))
- CI: fix frontend-build (drop unresolvable setup-node pin) and bridge-check (Explorer is 2.5.0-only, drop 2.4 image) ([`49d1e3d`](https://github.com/otAAAh/checkmk-json-agent/commit/49d1e3d))
- Add in-site JSON API Explorer wizard (json_api_explorer MKP) ([`43dce51`](https://github.com/otAAAh/checkmk-json-agent/commit/43dce51))
- Deps: bump mypy from 1.13.0 to 2.2.0 ([`836bf48`](https://github.com/otAAAh/checkmk-json-agent/commit/836bf48))
- Deps: bump pytest from 9.0.3 to 9.1.1 ([`5b93194`](https://github.com/otAAAh/checkmk-json-agent/commit/5b93194))
- Deps: bump ruff from 0.15.8 to 0.15.21 ([`6e5eb0d`](https://github.com/otAAAh/checkmk-json-agent/commit/6e5eb0d))
- Deps: bump pydantic from 2.11.7 to 2.13.4 ([`12dfede`](https://github.com/otAAAh/checkmk-json-agent/commit/12dfede))
- Bump the actions group with 2 updates ([`8c33708`](https://github.com/otAAAh/checkmk-json-agent/commit/8c33708))

## [0.7.0] - 2026-07-10

- Refresh Exchange listing for object-map discovery & multi-endpoint ([`fce6b72`](https://github.com/otAAAh/checkmk-json-agent/commit/fce6b72))
- Expand '[*]' wildcard over JSON objects/maps, not just arrays ([`886eab8`](https://github.com/otAAAh/checkmk-json-agent/commit/886eab8))
- Add contributing guide and issue templates ([`9466710`](https://github.com/otAAAh/checkmk-json-agent/commit/9466710))

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
