<!-- SPDX-License-Identifier: GPL-2.0-only -->
# checkmk-json-agent

[![CI](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/ci.yml)
[![License: GPL v2](https://img.shields.io/badge/License-GPLv2-blue.svg)](LICENSE)

A generic Checkmk **special agent for monitoring any HTTP/JSON API** — query a
`/health` or `/status` endpoint, extract fields by path, and turn them into
Checkmk services with thresholds and metrics. No custom Python, no MKP
development per integration: it's all one Setup rule.

Targets **Checkmk 2.4+** and the current stable plugin APIs
(`cmk.agent_based.v2`, `cmk.rulesets.v1`, `cmk.server_side_calls.v1`,
`cmk.graphing.v1`).

## Features

- HTTP/HTTPS **GET or POST**, custom headers, optional request body
- **Multiple endpoints per rule**: each with its own method/headers/auth/
  timeout/fields; their results merge into one section, and an unreachable
  endpoint only affects its own services
- **Auth**: none, HTTP basic (username/password), or bearer token — secrets go
  through the Checkmk password store, never onto the command line in clear text
- **Path extraction** with a dotted syntax: `status`, `components.db.status`,
  `items[0].count` (leading `$.` optional); keys containing `.` or `[` can be
  bracket-quoted, e.g. `data['foo.bar'].value`
- **One service per field**, named as you choose
- **Array & object auto-discovery**: a `[*]` wildcard (e.g. `nodes[*].health`)
  creates one service per array element - or per key when it lands on a JSON
  object/map, such as a Spring Boot Actuator `/health` `components[*].status` -
  labelled by a field you pick (or the index/key); multiple wildcards
  (e.g. `pods[*].containers[*].ready`) expand the cartesian product, with
  composite `<pod> / <container>` labels
- **Thresholds**: WARN/CRIT upper and lower levels for numeric values, exposed
  as a metric/graph
- **String matching**: a regex the value must fully match, else CRIT
- TLS verification on by default (with an explicit opt-out)
- Unreachable endpoints and non-JSON responses surface as UNKNOWN on the
  affected services, not a crash

## Requirements

- Checkmk 2.4.0 or newer (any edition)

## Installation

Download the `.mkp` from the [Releases](https://github.com/otAAAh/checkmk-json-agent/releases)
page (or [build it](#building-from-source)), then, as the site user:

```sh
mkp add json_api-0.6.0.mkp
mkp enable json_api 0.6.0
```

Or upload it in the GUI under **Setup → Extension packages**.

## Configuration

Create a rule under **Setup → Agents → Other integrations → Generic JSON API**
(ruleset `special_agents:json_api`). A rule holds one or more **endpoints**;
each endpoint is fetched independently and all results merge into one section.
Each endpoint has:

| Field | Purpose |
|---|---|
| **URL** | Full endpoint URL incl. scheme, e.g. `https://app.example.com/actuator/health`. Checkmk macros (`$HOSTNAME$`, `$HOSTADDRESS$`, custom host macros, ...) are resolved against the monitored host, so one rule can be shared across many hosts. |
| **HTTP method** | `GET` or `POST` |
| **Request body** | Optional body for `POST` (defaults `Content-Type: application/json` unless you set one). Macros are resolved here too. |
| **Additional request headers** | Name/value pairs; macros are resolved in the values |
| **Authentication** | None, basic, or bearer token |
| **Verify the TLS certificate** | On by default |
| **Follow HTTP redirects** | On by default; turn off to harden against redirect-based SSRF |
| **Request timeout (seconds)** | Optional; defaults to 30 |
| **Fields to monitor** | One entry per service (see below) |

Service names must be unique across the whole rule; if two endpoints produce the
same name, the check disambiguates the later one with a ` (2)` suffix.

Each **field to monitor** has:

| Field | Purpose |
|---|---|
| **Service name** | Becomes the service (shown as `JSON <name>`) |
| **JSON path** | Dotted path; use `[*]` for array discovery |
| **Item label path** | For `[*]`: field within each element to label the service (defaults to the array index) |
| **Unit** | Optional: `count` / `bytes` / `seconds` / `percent` — renders the metric and graph with that unit (numeric values only) |
| **Upper / lower levels** | WARN/CRIT for numeric values |
| **Expected value (regex)** | Value must fully match, else CRIT |

### Service states

- **Numeric value with levels** → checked against the levels, emitted as a
  metric named for the field's unit (`json_api_value` when no unit is set)
- **Value with an expected regex** → OK if it fully matches, else CRIT
- **Plain value** → shown in the summary (numeric values still get a metric)
- **Levels set on a non-numeric value** → WARN (so the misconfig is visible)
- **Path not found** → UNKNOWN
- **Endpoint request failed / not JSON** → that endpoint's services go UNKNOWN
  with the error; the other endpoints in the rule keep reporting normally

Values are rendered as they appear in JSON, so an `expected` regex matches
`true` / `false` / `null` — not Python's `True` / `False` / `None`.

The service **Details** view additionally shows where the value came from — the
JSON path, the source endpoint URL, and the expected pattern (when set) — which
makes a misconfigured extraction (wrong path or wrong endpoint) easy to spot.
This is details-only and never changes the summary line or the service state.

## Examples

### A Spring Boot Actuator health endpoint

Given `GET /actuator/health`:

```json
{"status": "UP", "components": {"db": {"status": "UP", "details": {"connections": 7}}}}
```

| Service name | JSON path | Check |
|---|---|---|
| `Health` | `status` | expected `UP` |
| `Database` | `components.db.status` | expected `UP` |
| `DB connections` | `components.db.details.connections` | upper levels `50 / 100` |

Produces services `JSON Health`, `JSON Database`, `JSON DB connections`.

### Array auto-discovery

Given a payload with a `nodes` array:

```json
{"nodes": [{"name": "web-1", "status": "UP"}, {"name": "web-2", "status": "DOWN"}]}
```

| Service name | JSON path | Item label path | Check |
|---|---|---|---|
| `Node` | `nodes[*].status` | `name` | expected `UP` |

Produces `JSON Node web-1` (OK) and `JSON Node web-2` (CRIT). If a label value
repeats across elements, every occurrence is suffixed with its index so two
elements never collapse into one service.

### Multiple endpoints

Add several endpoints to one rule to monitor related APIs together — e.g. a
frontend `/health` and a backend `/actuator/health`. Each endpoint carries its
own connection settings and fields; the services from all of them appear under
the same host. If the backend is unreachable, only its services go UNKNOWN while
the frontend's stay green. Keep service names unique across endpoints (a
collision is auto-suffixed with ` (2)`, but explicit names read better).

## JSON API Explorer

[`explorer/index.html`](explorer/index.html) is a standalone, dependency-free
web page (open it directly in a browser — nothing is uploaded anywhere).
Configure one or more endpoints (URL, method, auth, request body, headers,
timeout, TLS/redirect toggles), paste each endpoint's sample JSON response,
click the fields to monitor, set thresholds/labels, and it generates: the agent
`--endpoint` command line for CLI testing, the rule value for `rules.mk`, and a
REST API request body + `curl` to create the rule on a site. Auth is emitted as
a password-store reference (create the entry under **Setup → Passwords**).

## Security notes

- The agent performs **HTTP requests from the Checkmk server** to operator-configured
  URLs. Treat the rule as trusted input: a URL pointing at internal services (or one
  that **redirects** there) can be used as an SSRF vector. Restrict who can edit the
  rule accordingly.
- **Follow HTTP redirects** is on by default (for back-compat). In locked-down
  environments, turn it off per endpoint so a redirect to an internal address fails
  instead of being followed — closing the redirect-based SSRF amplification path.
- Credentials are stored in the Checkmk **password store** and passed to the agent as
  a store reference, not in clear text on the command line.
- TLS verification is **on by default**; disabling it is insecure and opt-in per rule.

## Building from source

```sh
make mkp        # -> json_api-<version>.mkp
```

The builder uses only the standard library, so no Checkmk install is needed to
package — it compiles the `.po` translation sources to `.mo` itself.

## Translations

The Setup UI (and graph titles) are localizable. The MKP ships compiled
catalogs so the plugin appears in the user's Checkmk language; strings without a
translation fall back to English. Currently shipped: **all Checkmk-supported UI
languages** — German (`de`), Spanish (`es`), French (`fr`), Italian (`it`),
Japanese (`ja`), Dutch (`nl`), Portuguese (`pt_PT`) and Romanian (`ro`). Service
/ check output stays English — Checkmk does not run plugin check output through
translation.

Sources live under `locales/`:

```
locales/
  <lang>/LC_MESSAGES/multisite.po   # one committed catalog per language (de, es, fr, it, ja, nl, pt_PT, ro)
  json_api.pot                     # template, generated by `make pot` (git-ignored)
```

The catalog domain must be `multisite`; the packager installs each language to
`local/share/check_mk/locale/packages/json_api/<lang>/LC_MESSAGES/multisite.mo`
(the per-package layout, so it never collides with the site's own catalogs).

Workflow (needs the `gettext` tools — only for editing translations, not for
building):

```sh
make pot                                    # refresh the template from the code
# add a language:
msginit -l fr -i locales/json_api.pot -o locales/fr/LC_MESSAGES/multisite.po
# after code changes, merge new strings into an existing language:
msgmerge --update locales/de/LC_MESSAGES/multisite.po locales/json_api.pot
```

Then edit the `msgstr` entries and rebuild with `make mkp` — no manual `.mo`
compilation needed.

## Releasing

Releases are cut from annotated version tags. See [`CHANGELOG.md`](CHANGELOG.md)
for the per-version history.

1. Bump `version` in `pyproject.toml` and the `mkp add`/`mkp enable` examples above.
2. `make changelog` to refresh `CHANGELOG.md`, then commit both.
3. Tag and push:

   ```sh
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin main vX.Y.Z
   ```

The [`Release` workflow](.github/workflows/release.yml) triggers on the tag: it
verifies the tag matches `pyproject.toml`, builds the MKP, generates that
version's notes with `scripts/gen_changelog.py --version X.Y.Z`, and publishes a
GitHub Release with the `.mkp` attached. Preview the notes locally with
`make release-notes`.

## Development

The plugin imports the `cmk.*` APIs, which only exist inside a Checkmk site or a
Checkmk dev virtualenv. Point the tooling at one:

```sh
make format
make lint
PYTHON=/path/to/checkmk/.venv/bin/python make test
```

Layout:

```
cmk_addons/plugins/json_api/
  server_side_calls/   rule -> agent command line
  rulesets/            the Setup form
  libexec/             the special agent executable
  agent_based/         section parsing + check
  graphing/            metric definition
  checkman/            man page
```

## Limitations

- Composite service names from nested `[*]` wildcards can grow long; Checkmk
  truncates very long service descriptions
- A fixed set of units (`count` / `bytes` / `seconds` / `percent`); other units
  fall back to the unit-less `json_api_value` metric
- `label_path` uniqueness is enforced by index-suffixing at runtime, not
  validated at config time (the JSON isn't known then)

## License

GPL-2.0-only. See [LICENSE](LICENSE).
