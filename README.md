<!-- SPDX-License-Identifier: GPL-2.0-only -->
# checkmk-json-agent

A generic Checkmk **special agent for monitoring any HTTP/JSON API** — query a
`/health` or `/status` endpoint, extract fields by path, and turn them into
Checkmk services with thresholds and metrics. No custom Python, no MKP
development per integration: it's all one Setup rule.

Targets **Checkmk 2.4+** and the current stable plugin APIs
(`cmk.agent_based.v2`, `cmk.rulesets.v1`, `cmk.server_side_calls.v1`,
`cmk.graphing.v1`).

## Features

- HTTP/HTTPS **GET or POST**, custom headers, optional request body
- **Auth**: none, HTTP basic (username/password), or bearer token — secrets go
  through the Checkmk password store, never onto the command line in clear text
- **Path extraction** with a dotted syntax: `status`, `components.db.status`,
  `items[0].count` (leading `$.` optional)
- **One service per field**, named as you choose
- **Array auto-discovery**: a `[*]` wildcard (e.g. `nodes[*].health`) creates
  one service per array element, labelled by a field you pick
- **Thresholds**: WARN/CRIT upper and lower levels for numeric values, exposed
  as a metric/graph
- **String matching**: a regex the value must fully match, else CRIT
- TLS verification on by default (with an explicit opt-out)
- Unreachable hosts and non-JSON responses surface as a clear CRIT, not a crash

## Requirements

- Checkmk 2.4.0 or newer (any edition)

## Installation

Download the `.mkp` (see [Building](#building-from-source) until releases are
published), then, as the site user:

```sh
mkp add json_api-0.1.0.mkp
mkp enable json_api 0.1.0
```

Or upload it in the GUI under **Setup → Extension packages**.

## Configuration

Create a rule under **Setup → Agents → Other integrations → Generic JSON API**
(ruleset `special_agents:json_api`). One rule fully describes a monitored
endpoint:

| Field | Purpose |
|---|---|
| **URL** | Full endpoint URL incl. scheme, e.g. `https://app.example.com/actuator/health` |
| **HTTP method** | `GET` or `POST` |
| **Request body** | Optional body for `POST` (defaults `Content-Type: application/json` unless you set one) |
| **Additional request headers** | Name/value pairs |
| **Authentication** | None, basic, or bearer token |
| **Verify the TLS certificate** | On by default |
| **Request timeout (seconds)** | Optional; defaults to 30 |
| **Fields to monitor** | One entry per service (see below) |

Each **field to monitor** has:

| Field | Purpose |
|---|---|
| **Service name** | Becomes the service (shown as `JSON <name>`) |
| **JSON path** | Dotted path; use `[*]` for array discovery |
| **Item label path** | For `[*]`: field within each element to label the service (defaults to the array index) |
| **Upper / lower levels** | WARN/CRIT for numeric values |
| **Expected value (regex)** | Value must fully match, else CRIT |

### Service states

- **Numeric value with levels** → checked against the levels, emitted as the
  `json_api_value` metric
- **Value with an expected regex** → OK if it fully matches, else CRIT
- **Plain value** → shown in the summary (numeric values still get a metric)
- **Levels set on a non-numeric value** → WARN (so the misconfig is visible)
- **Path not found** → UNKNOWN
- **Request failed / not JSON** → the data source fails (one CRIT on the host's
  `Check_MK` service; the JSON services go stale) — not a CRIT on every service

Values are rendered as they appear in JSON, so an `expected` regex matches
`true` / `false` / `null` — not Python's `True` / `False` / `None`.

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

## JSON API Explorer

[`explorer/index.html`](explorer/index.html) is a standalone, dependency-free
web page (open it directly in a browser — nothing is uploaded anywhere). Paste a
sample JSON response, click the fields to monitor, set thresholds/labels, and it
generates: the agent `--extractions` blob for CLI testing, the rule value for
`rules.mk`, and a REST API request body + `curl` to create the rule on a site.

## Security notes

- The agent performs **HTTP requests from the Checkmk server** to operator-configured
  URLs. Treat the rule as trusted input: a URL pointing at internal services (or one
  that **redirects** there — redirects are followed) can be used as an SSRF vector.
  Restrict who can edit the rule accordingly.
- Credentials are stored in the Checkmk **password store** and passed to the agent as
  a store reference, not in clear text on the command line.
- TLS verification is **on by default**; disabling it is insecure and opt-in per rule.

## Building from source

```sh
make mkp        # -> json_api-<version>.mkp
```

The builder uses only the standard library, so no Checkmk install is needed to
package.

## Development

The plugin imports the `cmk.*` APIs, which only exist inside a Checkmk site or a
Checkmk dev virtualenv. Point the tooling at one:

```sh
make format
make lint
PYTHON=/path/to/checkmk/.venv/bin/python make test     # 30 tests
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

- A single `[*]` wildcard per path (no multiple/nested wildcards yet)
- One shared, unit-less metric (`json_api_value`) for all numeric services
- `label_path` uniqueness is enforced by index-suffixing at runtime, not
  validated at config time (the JSON isn't known then)

## License

GPL-2.0-only. See [LICENSE](LICENSE).
