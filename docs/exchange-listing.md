<!-- Paste this into the "Description" field of the Checkmk Exchange
     "upload new package" form. The Exchange renders Markdown. -->

# Generic JSON API

Monitor **any** HTTP/JSON API in Checkmk. Point the special agent at a
`/health`, `/status`, or metrics endpoint, choose the fields you care about, and
get one Checkmk service per field — with thresholds, metrics, and string
matching. No custom Python and no per-integration plugin development: it is all
configured in **one Setup rule**.

## Why this agent

Many JSON integrations require the target API to already emit a fixed,
Checkmk-shaped response. This one is the opposite: it works against **arbitrary
JSON you do not control** — Spring Boot Actuator, vendor appliances, Kubernetes
and cluster APIs, or any app exposing a `/health` endpoint. You pick the fields
by path and define WARN/CRIT levels **in Checkmk**, not upstream.

## Features

- **HTTP/HTTPS** GET or POST, custom request headers, optional request body
- **Authentication**: none, HTTP basic, or bearer token — secrets are kept in
  the Checkmk password store, never in clear text on the command line
- **Field extraction** by dotted path: `status`, `components.db.status`,
  `items[0].count` (a leading `$.` is optional)
- **Array auto-discovery**: a `[*]` wildcard (e.g. `nodes[*].status`) creates one
  service per array element, labelled by a field you choose
- **Thresholds**: upper and lower WARN/CRIT for numeric values, exposed as a
  metric/graph
- **String matching**: a regular expression the value must match (validated when
  you save the rule)
- TLS verification on by default
- Robust failure handling: an unreachable endpoint fails the data source once,
  rather than turning every service CRIT at the same time

## Example

For `GET /actuator/health` returning:

```json
{"status": "UP", "components": {"db": {"status": "UP", "details": {"connections": 7}}}}
```

| Service name | JSON path | Check |
|---|---|---|
| Health | `status` | expected `UP` |
| Database | `components.db.status` | expected `UP` |
| DB connections | `components.db.details.connections` | upper levels `50 / 100` |

This creates the services `JSON Health`, `JSON Database`, and
`JSON DB connections`.

## Requirements

- Checkmk **2.4.0 or newer** (any edition). Tested on real 2.4 and 2.5 sites.

## Installation

As the site user:

```
mkp add json_api-<version>.mkp
mkp enable json_api <version>
```

…or upload the package under **Setup → Extension packages**. Then create a rule
under **Setup → Agents → Other integrations → Generic JSON API**.

## Bonus: a standalone JSON API Explorer

The source repository includes a dependency-free browser page: paste a sample
JSON response, click the fields to monitor, and it generates the rule values for
you (and a REST-API request to create the rule). Nothing is uploaded anywhere.

## Source & license

Source, issues, and documentation: <https://github.com/otAAAh/checkmk-json-agent>

Licensed under **GPL-2.0-only**.
