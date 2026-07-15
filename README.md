<!-- SPDX-License-Identifier: GPL-2.0-only -->
# checkmk-json-agent

[![CI](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/ci.yml)
[![Frontend build](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/frontend-build.yml/badge.svg)](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/frontend-build.yml)
[![Checkmk agent 2.4+](https://img.shields.io/badge/Checkmk_agent-2.4%2B-15d1a0?logo=checkmk&logoColor=white)](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/ci.yml)
[![Checkmk explorer 2.5+](https://img.shields.io/badge/Checkmk_explorer-2.5%2B-15d1a0?logo=checkmk&logoColor=white)](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/frontend-build.yml)
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
- **Count elements**: instead of monitoring a value, monitor *how many*
  elements a path holds — array length or number of object keys. Where `[*]`
  fans a collection out into one service per element, `count` collapses it into
  a single "how many" service (queue length, number of unhealthy nodes, ...).
  The count is a number, so a unit, WARN/CRIT levels, the transform and a metric
  all apply to it; a path that is not an array or object becomes UNKNOWN
- **Transform the numeric value**: an optional arithmetic expression (using the
  variable `value`) applied to a numeric value before levels and the metric —
  e.g. `value / 1024 / 1024` for bytes→MiB or `(value - 32) * 5 / 9` for °F→°C;
  only numbers, parentheses and `+ - * /` are allowed and it is evaluated safely
  (never `eval`)
- **Thresholds**: WARN/CRIT upper and lower levels for numeric values, exposed
  as a metric/graph
- **String matching**: two modes — either a regex the value must fully match
  (with a configurable state when it does not, default CRIT), or map the value
  to a state by matching it against separate OK / WARN / CRIT regexes (tried in
  that order, first full match wins; configurable state when nothing matches)
- TLS verification on by default (with an explicit opt-out)
- Unreachable endpoints and non-JSON responses surface as UNKNOWN on the
  affected services, not a crash

## Requirements

- Checkmk 2.4.0 or newer (any edition)

## Installation

Download the `.mkp` from the [Releases](https://github.com/otAAAh/checkmk-json-agent/releases)
page (or [build it](#building-from-source)), then, as the site user:

```sh
mkp add json_api-0.8.0.mkp
mkp enable json_api 0.8.0
```

Or upload it in the GUI under **Setup → Extension packages**.

Optionally also install the companion **in-site Explorer** wizard
(`json_api_explorer`, Checkmk 2.5+) — a guided setup that builds a rule from a
live API response. Install it after the `json_api` agent package; see
[JSON API Explorer](#json-api-explorer).

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
| **Count the number of elements at this path** | Optional: when the path points at an array or object, monitor its number of elements (array length / object keys) instead of the value; the count is a number, so unit, levels, transform and metric all apply. A path that is not an array or object becomes UNKNOWN |
| **Unit** | Optional: `count` / `bytes` / `seconds` / `percent` — renders the metric and graph with that unit (numeric values only) |
| **Transform the numeric value** | Optional arithmetic expression on the variable `value` (e.g. `value / 1024 / 1024`), applied to a numeric value before levels and the metric; only numbers, parentheses and `+ - * /` are allowed |
| **Upper / lower levels** | WARN/CRIT for numeric values |
| **String matching** | Either *must match a regex* (choose the state when it does **not** match, default CRIT) or *map the value to a state* (separate OK / WARN / CRIT regexes, first full match wins; choose the state when nothing matches, default OK) |

### Overriding thresholds per folder / host / service

The levels and string matching a field carries in the special-agent rule are the
service's **defaults**. Because a special-agent rule is matched first-match-wins
(it can't be layered), those defaults alone can't be re-tuned for a subset of
hosts without cloning the whole rule. So they are also exposed as a normal
**check-parameters** rule:

> **Setup → Service monitoring rules → Applications → Generic JSON API**
> (ruleset `checkgroup_parameters:json_api`)

A rule there overrides the upper/lower levels and the string matching for the
matching `JSON <name>` services — with the usual Checkmk precedence (plugin
defaults < the value configured in the agent rule < this rule) and the usual
folder/host/service conditions. So you can set a default on a top folder and
override it further down, or retune a level straight from a service's
**Parameters** view, without touching the endpoint/auth configuration. Fields
you leave untouched keep the agent-rule defaults.

### Service states

- **Numeric value with levels** → checked against the levels, emitted as a
  metric named for the field's unit (`json_api_value` when no unit is set)
- **Numeric value with a transform** → the arithmetic expression is applied
  first, and the transformed value is what the levels check, what the metric
  records, and what the service shows; a broken expression or a non-finite
  result makes the service UNKNOWN
- **Value with must-match string matching** → OK if it fully matches the regex,
  otherwise your chosen no-match state (default CRIT)
- **Value with a state map** → tried against the OK, WARN, then CRIT regexes in
  that order; the first full match sets the state, and if none matches your
  chosen no-match state applies (default OK)
- **Count enabled on an array or object** → the number of elements is what the
  levels check, the metric records and the summary shows (after any transform)
- **Count enabled on a non-array/object path** → UNKNOWN
- **Plain value** → shown in the summary (numeric values still get a metric)
- **Levels set on a non-numeric value** → WARN (so the misconfig is visible)
- **Path not found** → UNKNOWN
- **Endpoint request failed / not JSON** → that endpoint's services go UNKNOWN
  with the error; the other endpoints in the rule keep reporting normally

Values are rendered as they appear in JSON, so a regex matches
`true` / `false` / `null` — not Python's `True` / `False` / `None`.

Rules that used the old **Expected value (regex)** field are migrated
automatically to must-match with the CRIT no-match default, so their behaviour
is unchanged.

The service **Details** view additionally shows where the value came from — the
JSON path, the source endpoint URL, and the match pattern (when set) — which
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
| `Health` | `status` | must match `UP` (else CRIT) |
| `Database` | `components.db.status` | must match `UP` (else CRIT) |
| `DB connections` | `components.db.details.connections` | upper levels `50 / 100` |

Produces services `JSON Health`, `JSON Database`, `JSON DB connections`.

### Mapping a value to a state

For APIs that expose a semantic state, map each value to a Checkmk state instead
of demanding one exact match. Given `GET /status` → `{"mode": "degraded"}`:

| Service name | JSON path | Check |
|---|---|---|
| `Mode` | `mode` | state map: OK `ready`, WARN `degraded`, CRIT `failed` |

The regexes are tried OK → WARN → CRIT and the first full match wins, so `ready`
is OK, `degraded` is WARN and `failed` is CRIT; anything else falls back to the
no-match state (default OK).

### Transforming a numeric value

When an API reports a value in an awkward unit, transform it before levels and
the metric apply. Given `GET /status` → `{"heap_bytes": 734003200}`:

| Service name | JSON path | Transform | Check |
|---|---|---|---|
| `Heap` | `heap_bytes` | `value / 1024 / 1024` | upper levels `512 / 768` |

The service checks, graphs and displays the value in MiB, so the levels are set
in MiB too. A broken expression makes the service UNKNOWN.

### Array auto-discovery

Given a payload with a `nodes` array:

```json
{"nodes": [{"name": "web-1", "status": "UP"}, {"name": "web-2", "status": "DOWN"}]}
```

| Service name | JSON path | Item label path | Check |
|---|---|---|---|
| `Node` | `nodes[*].status` | `name` | must match `UP` (else CRIT) |

Produces `JSON Node web-1` (OK) and `JSON Node web-2` (CRIT). If a label value
repeats across elements, every occurrence is suffixed with its index so two
elements never collapse into one service.

### Counting elements

When you care about *how many*, not each one, enable **Count** instead of a
`[*]` wildcard. Given `GET /status` → `{"jobs": [{"id": 1}, {"id": 2}, {"id": 3}]}`:

| Service name | JSON path | Count | Check |
|---|---|---|---|
| `Queued jobs` | `jobs` | yes | upper levels `100 / 500` |

Produces a single service `JSON Queued jobs` that reports `3` and alerts on the
queue length — where `jobs[*]` would instead have created one service per job.
The same works on an object (e.g. `components` → number of components). If the
path resolves to anything other than an array or object, the service is UNKNOWN.

### Multiple endpoints

Add several endpoints to one rule to monitor related APIs together — e.g. a
frontend `/health` and a backend `/actuator/health`. Each endpoint carries its
own connection settings and fields; the services from all of them appear under
the same host. If the backend is unreachable, only its services go UNKNOWN while
the frontend's stay green. Keep service names unique across endpoints (a
collision is auto-suffixed with ` (2)`, but explicit names read better).

## JSON API Explorer

There are two Explorers — a standalone browser page and an in-site wizard.

**Standalone page** — [`explorer/index.html`](explorer/index.html) is a
dependency-free web page (open it directly in a browser — nothing is uploaded
anywhere). Configure one or more endpoints (URL, method, auth, request body,
headers, timeout, TLS/redirect toggles), paste each endpoint's sample JSON
response, click the fields to monitor, set thresholds/labels, and it generates:
the agent `--endpoint` command line for CLI testing, the rule value for
`rules.mk`, and a REST API request body + `curl` to create the rule on a site.
Auth is emitted as a password-store reference (create the entry under **Setup →
Passwords**).

**In-site wizard (companion MKP)** — `json_api_explorer` is an optional, separate
extension package that adds a guided setup under **Setup → Quick setup → Generic
JSON API**. It fetches each endpoint's live response from the site, lets you
point-and-pick fields with a preview of the resulting service states, and creates
the rule for you. It is a companion to this agent — install the `json_api`
package first — and requires **Checkmk 2.5+** (it builds on Checkmk's native
Quick-Setup UI). See [`docs/exchange-listing-explorer.md`](docs/exchange-listing-explorer.md).

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
