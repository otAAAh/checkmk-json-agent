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
- **Auth**: none, HTTP basic (username/password), bearer token, an **API key**
  in a header of the API's choosing (`X-API-Key`, `PRIVATE-TOKEN`, ...) or in a
  query parameter, or **OAuth 2.0 client credentials** (the agent exchanges a
  client ID/secret for a short-lived token and caches it) — every secret goes
  through the Checkmk password store, never onto the command line, into the
  configuration or into a log in clear text
- **Path extraction** with a dotted syntax: `status`, `components.db.status`,
  `items[0].count` (leading `$.` optional); keys containing `.` or `[` can be
  bracket-quoted, e.g. `data['foo.bar'].value`
- **Response headers** as a value source: prefix the name with `@header.`
  (e.g. `@header.X-RateLimit-Remaining`) to monitor an API quota, a
  `Retry-After` or the age of a `Last-Modified`
- **One service per field**, named as you choose
- **Array & object auto-discovery**: a `[*]` wildcard (e.g. `nodes[*].health`)
  creates one service per array element - or per key when it lands on a JSON
  object/map, such as a Spring Boot Actuator `/health` `components[*].status` -
  labelled by a field you pick (or the index/key); multiple wildcards
  (e.g. `pods[*].containers[*].ready`) expand the cartesian product, with
  composite `<pod> / <container>` labels
- **Host labels on the created hosts**: a piggyback host can carry labels
  resolved from its own element (`region`, `role`, …), so folder rules and views
  can target the hosts a `[*]` rule created
- **One Checkmk host per element** (piggyback): instead of many services on the
  polling host, name a field within each `[*]` element and every element becomes
  a host of its own — so an API describing a fleet gives you hosts, each with its
  own services, downtimes, contact groups and availability, not just a long
  service list
- **Aggregate a collection into one value**: where `[*]` fans a collection out
  into one service per element, an aggregation collapses it into a single
  service — the **number of elements** (queue length, number of unhealthy
  nodes), or their **sum / average / minimum / maximum**. Point the path at an
  array or object (`jobs`) or at a `[*]` wildcard over the values
  (`nodes[*].load`). The result is a number, so a unit, WARN/CRIT levels, the
  transform and a metric all apply to it; a path that is neither an array nor
  an object becomes UNKNOWN. Where a `[*]` wildcard names a field, all five
  functions see the same elements — the ones that have it — so `nodes[*].load`
  counts the nodes reporting a load
- **Filter elements by a condition**: restrict a `[*]` wildcard (or an
  aggregation) to the elements whose sub-field matches — e.g. one service per
  node whose `status` is *not* `ok`, or count only the pods that aren't
  `Running` (operators: equals / not-equals / regex / not-regex)
- **Counters → per-second rate**: mark a field as a counter and the check
  monitors its change per second instead of the ever-growing total
  (`requests_total`, `bytes_sent`), with a rate metric of its own
- **Timestamps → age**: mark a field as a timestamp (Unix epoch seconds or
  milliseconds, or ISO 8601 — auto-detected by default) and the check monitors
  the seconds since, so upper levels alert on stale data (`last_backup`,
  `updated_at`)
- **One service per endpoint, for free**: every endpoint also gets a
  `JSON API <name>` service reporting the request itself — HTTP status,
  **response time** (with optional levels), response size and, for HTTPS, the
  **TLS certificate's remaining validity** (with optional levels in days, read
  from the connection the agent already makes — no second check against the same
  URL) — no field configuration needed
- **Transform the numeric value**: an optional arithmetic expression (using the
  variable `value`) applied to a numeric value before levels and the metric —
  e.g. `value / 1024 / 1024` for bytes→MiB or `(value - 32) * 5 / 9` for °F→°C;
  only numbers, parentheses and `+ - * /` are allowed and it is evaluated safely.
  A **second path** can supply `other`, so a used/total pair becomes a
  percentage: `value / other * 100`
  (never `eval`)
- **Show the context next to the value**: an optional summary text with `{path}`
  placeholders — `{message} (leader {leader})` — appended to the service summary,
  resolved within the same `[*]` element (or from the response root). So a
  service on `status` shows the reason the API already returned alongside the
  CRIT, instead of that reason needing a second service of its own. Presentation
  only: it never changes the state, the levels or the metric
- **Facts into the HW/SW inventory**: point a field at an inventory tree node
  instead of a service — a version, a build, a region, a licence tier. These are
  not states worth a service that is OK forever, and the inventory does what
  services cannot: it is searchable *across* hosts ("which hosts still run a
  version below 4.2?") and keeps a change history. A `[*]` wildcard becomes one
  table row per element. No service is created unless you ask for one
- **Thresholds**: WARN/CRIT upper and lower levels for numeric values, exposed
  as a metric/graph
- **String matching**: two modes — either a regex the value must fully match
  (with a configurable state when it does not, default CRIT), or map the value
  to a state by matching it against separate OK / WARN / CRIT regexes (tried in
  that order, first full match wins; configurable state when nothing matches)
- TLS verification on by default (with an explicit opt-out); optionally a
  **custom CA bundle** (to trust a private CA) and a **client certificate**
  for mutual TLS
- **Accept non-2xx status codes**: by default only 2xx is read, but you can
  opt extra codes in per endpoint — e.g. accept `503` to read a health
  endpoint that reports its problems with a 503 and a JSON body
- **HTTP proxy** support per endpoint (environment variables, an explicit proxy
  URL, or bypass) — for APIs reachable only through a corporate egress proxy
- **Retry a failed request**: an optional per-endpoint retry (with a doubling
  backoff) for the failures a repeat can fix — a connection reset, a timeout, an
  HTTP 429/5xx — so a load balancer dropping connections for a second during a
  rolling restart does not become a CRIT and a notification. A 4xx, a non-JSON
  body and an oversized response are never retried. Nothing is hidden: the
  endpoint service reports that a retry was needed, and can be told to go WARN
  when one is. Off by default
- **Per-endpoint response cache**: an optional TTL reuses the last response
  instead of asking again — for APIs with a request quota, expensive endpoints,
  or one rule shared across many hosts, where the request *rate* is the problem
  rather than the freshness. Off by default
- Unreachable endpoints and non-JSON responses surface as UNKNOWN on the
  affected services, not a crash

## Requirements

- Checkmk 2.4.0 or newer (any edition)

## Installation

Download the `.mkp` from the [Releases](https://github.com/otAAAh/checkmk-json-agent/releases)
page (or [build it](#building-from-source)), then, as the site user:

```sh
mkp add json_api-0.14.0.mkp
mkp enable json_api 0.14.0
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
| **Endpoint name** | Optional short name (e.g. `frontend`). Names the endpoint's own `JSON API <name>` service; without it the URL is used. Macros are resolved here too. It does not change the field service names |
| **URL** | Full endpoint URL incl. scheme, e.g. `https://app.example.com/actuator/health`. Checkmk macros (`$HOSTNAME$`, `$HOSTADDRESS$`, custom host macros, ...) are resolved against the monitored host, so one rule can be shared across many hosts. |
| **HTTP method** | `GET` or `POST` |
| **Request body** | Optional body for `POST` (defaults `Content-Type: application/json` unless you set one). Macros are resolved here too. |
| **Additional request headers** | Name/value pairs; macros are resolved in the values. Stored in clear text — an API key belongs under *Authentication* instead |
| **Authentication** | None, basic (username/password), bearer token, an API key in a request header (you name the header, e.g. `X-API-Key`), or an API key in a query parameter. All secrets come from the password store; the query-parameter key is appended for the request only and is redacted from every URL the agent reports |
| **Verify the TLS certificate** | On by default |
| **Custom CA bundle file** | Optional; path on the Checkmk server to a PEM file with the CA(s) to verify the server against — trust a private CA without disabling verification. Ignored when verification is off |
| **Client certificate (mutual TLS)** | Optional; paths on the Checkmk server to the client certificate (PEM) and, if separate, the private key. The key must be unencrypted |
| **Follow HTTP redirects** | On by default; turn off to harden against redirect-based SSRF |
| **Re-read at most every (seconds)** | Optional cache TTL. Reuses the last response for this endpoint while it is younger than this, instead of requesting it again — see [Caching responses](#caching-responses). Unset (the default) always fetches fresh |
| **Retry a failed request** | Optional: a number of retries (1–5) and a backoff in seconds, doubled for each further attempt and capped at 30 s in total. Only a connection error, a timeout, an HTTP 429 or a 5xx is retried — a 4xx, a body that is not JSON and an oversized response answer the same however often they are asked. A cached response makes no request, so nothing is retried. Off by default |
| **Request timeout (seconds)** | Optional; defaults to 30 |
| **Additional accepted HTTP status codes** | Optional; by default only 2xx responses are read (any other status → UNKNOWN). List extra codes (e.g. `503`) to parse and extract their body too. 2xx is always accepted |
| **HTTP proxy** | Optional; route via the environment's `HTTP_PROXY`/`HTTPS_PROXY`, an explicit proxy URL, or bypass. Unset = honour the environment |
| **Fields to monitor** | One entry per service (see below) |

Service names must be unique across the whole rule; if two endpoints produce the
same name, the check disambiguates the later one with a ` (2)` suffix.

Each **field to monitor** has:

| Field | Purpose |
|---|---|
| **Service name** | Becomes the service (shown as `JSON <name>`) |
| **JSON path** | Dotted path; use `[*]` for array discovery |
| **Per-element name suffix** | For `[*]`: field within each element, appended to the service name to tell the per-element services apart (defaults to the array index); it does not replace the service name |
| **Create one host per element, named by this field** | Optional, for `[*]`: field within each element holding a **Checkmk host name**. Each element then becomes a piggyback host carrying this service under its plain name (the host says which element it is, so no name suffix is added). Set the same field on several fields of the endpoint to collect them on the same hosts. Only host-name-safe characters are kept (letters, digits, `-`, `_`, `.`); anything else becomes `_`. An element whose field is missing keeps its service on the polling host. **The hosts must exist in Checkmk** or the data is held and never monitored — see [One host per element](#one-host-per-element) |
| **Service labels** | Optional: attach Checkmk service labels to *this* service from response fields, each key prefixed with `json_api/`. For a `[*]` path the value is resolved within each element (e.g. `name`), so each per-element service gets its own label; otherwise from the response root. Host-wide facts go in the endpoint's **Host labels** instead. Set at discovery, so pick stable, low-cardinality fields |
| **Aggregate a collection into one value** | Optional: `count` (number of elements) / `sum` / `avg` / `min` / `max` over the array or object at the path — or over the values a `[*]` wildcard expands to, which then yields *one* service instead of one per element. The result is a number, so unit, levels, transform and metric all apply. Where the `[*]` path names a field, every function — `count` included — only sees the elements that have it (so `nodes[*].load` counts the nodes reporting a load, and a path no element has becomes UNKNOWN). A path that is neither an array nor an object becomes UNKNOWN, as does `avg`/`min`/`max` over no elements (`sum` over none is `0`) |
| **Only elements matching a condition** | Optional: for a `[*]` wildcard or an aggregation, keep only elements whose sub-field matches (path + operator equals/not-equals/regex/not-regex + value). Resolved within each element; an element missing the field is dropped. No effect without a wildcard or an aggregation |
| **Interpret the value as** | Optional: *a counter* — monitor the change **per second** rather than the total (the first check, and any check after the counter went backwards, keeps the previous state because no rate can be computed yet); or *a timestamp* — monitor its **age in seconds** (format `auto` / epoch seconds / epoch milliseconds / ISO 8601; no time zone means UTC). The derived number is what the transform, levels and metric use; string matching does not apply to it |
| **Unit** | Optional: `count` / `bytes` / `seconds` / `percent` — renders the value in the summary/details *and* the metric and graph with that unit (numeric values only) |
| **Transform the numeric value** | Optional arithmetic expression on the variable `value` (e.g. `value / 1024 / 1024`), applied to a numeric value before levels and the metric; only numbers, parentheses and `+ - * /` are allowed |
| **Second path for the transform** | Optional second field, available to the transform as `other` — which is what turns a used/total pair into a percentage (`value / other * 100`). Resolved in the **same scope** as the value: within each `[*]` element, or the response root without a wildcard, so every element is compared against its own total. The transform must use `other` and `other` requires this path — either alone is rejected in Setup. A path that does not resolve fails the calculation rather than substituting a value |
| **Extra text in the service summary** | Optional text appended to the summary, after the value. `{path}` inserts another field of the same response — resolved *within the current element* for a `[*]` wildcard, from the response root otherwise — e.g. `{message} (leader {leader})`. A path that is not in the response renders as `(n/a)`; a value that is an object or array renders as its size. Presentation only: it never changes the state, the levels or the metric. Put on one line and truncated if long |
| **Write into the HW/SW inventory** | Optional: a tree node (e.g. `software.applications.json_api`, starting with `hardware`, `software` or `networking`) and an attribute name (defaults to the JSON path's last segment). The value goes into the host's inventory tree and, by default, creates **no service** — tick *Also create a service* if you want both. A `[*]` wildcard writes one **table row** per element, keyed by the element's label, so several fields over the same collection fill in columns of the same row. Use it for values that rarely change: every change is recorded in the inventory history. Inventory runs on its own, slower schedule |
| **Upper / lower levels** | WARN/CRIT for numeric values |
| **String matching** | Either *must match a regex* (choose the state when it does **not** match, default CRIT) or *map the value to a state* (separate OK / WARN / CRIT regexes, first full match wins; choose the state when nothing matches, default OK) |

Each **endpoint** also has an optional **Host labels** list: fields resolved from the response root and attached to the monitored *host* (e.g. `version`, `cluster.region`) as `json_api/<key>` — host-wide, needing no service. A path may contain a `[*]` wildcard (e.g. `components[*]`) to emit **one label per element**, keyed `json_api/<key>/<element>` (unique keys), with the value taken from an optional per-element **value field** (default `true`, i.e. set-membership tags). In the wizard, the JSON picker's **`+ host label`** button adds these.

### The endpoint's own service

Besides the field services, each endpoint gets one service of its own —
**`JSON API <name>`**, named by the endpoint's optional **Name** (its URL,
without any query string, when it has none — so a key passed as a query
parameter never lands in a service description). It reports the *request*
rather than the data in it: the HTTP
status code, the response time (measured until the whole body has been read)
and the response size, with the URL — and the redirect target, when a redirect
moved the request — in the Details. It needs no field configuration and appears
for every rule.

If the request fails outright (connection refused, TLS error, timeout, an HTTP
status the rule does not accept, or a response that is not JSON) the service is
CRIT and reports the error, while the field services of that endpoint go UNKNOWN
as before.

Response-time levels, certificate-expiry levels, the state when a retry was
needed and the state for an unreachable endpoint are configured in a
check-parameters rule of its own:

> **Setup → Service monitoring rules → Applications → Generic JSON API endpoint**
> (ruleset `checkgroup_parameters:json_api_endpoint`)

Without levels the response time and the certificate's remaining validity are
only recorded as metrics. To get rid of these services, use a **Disabled
services** rule.

The certificate is read off the connection the agent is already making, so it
costs no extra request — but that also means it is only available for **HTTPS**
endpoints with **certificate verification enabled**. Otherwise nothing about the
certificate is reported (which is *absent*, not *expired*, and never alerts): a
plain-HTTP endpoint has no certificate, `verify_cert` off yields none, and a
connection reused from the pool may not expose one either.

### Caching responses

Every check interval, on every host using the rule, the agent requests every
configured endpoint. For a cheap `/health` that is exactly right. For a
rate-limited API, an endpoint that takes seconds to compute, or one rule shared
across fifty hosts, it is how monitoring becomes the outage it was meant to
detect.

Set **Re-read at most every (seconds)** on such an endpoint and the agent reuses
the last response while it is younger than that, without touching the network.

Checkmk's own fetcher cache does not cover this: it is host-wide, all-or-nothing,
and sized by a site-global setting during checking — so it cannot say "cache this
one rate-limited endpoint for 15 minutes while the cheap one next to it stays
live".

What the cache deliberately does *not* do:

- **It never hides a failure.** A failing request is not answered from an expired
  cache. Serving stale data through an outage would defeat the point of
  monitoring, so the endpoint goes CRIT as it normally would.
- **It never caches an error.** Only a response that parsed as JSON is stored;
  otherwise a bad response would be replayed for the whole TTL instead of retried.
- **It never reports a response time it did not measure.** While a cached body is
  served, the `JSON API <name>` service says `from cache (N old)` and records no
  response-time metric — replaying the original measurement would chart a request
  that never happened. For the same reason a cached serve never reports a retry:
  no request was made, so nothing was retried.

The cache lives in the site's `tmp` (so it is cleared with the site), one
owner-only file per endpoint identity: URL, method, body, headers, TLS settings
and a **hash** of the credential. The hash matters when several rules poll the
same multi-tenant URL with a different API key each — without it they would share
one entry and serve each other's data for the whole TTL. The credential itself
reaches neither the agent's endpoint blob nor the disk. Stale files from edited
rules are pruned automatically.

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
- **Aggregation on an array or object** → the aggregated number is what the
  levels check, the metric records and the summary shows (after any transform)
- **Aggregation on a non-array/object path** → UNKNOWN; likewise `avg`/`min`/`max`
  when no element is left to aggregate, and any aggregation over a non-numeric
  element
- **Read as a counter** → the per-second rate is what the levels check, the
  metric records and the summary shows ("Rate: …"); the counter reading itself
  moves to the Details. Until a rate can be computed (first check, or the counter
  went backwards) the service keeps its previous state
- **Read as a timestamp** → the age in seconds is checked, recorded and shown as
  a duration ("Age: …"), the raw timestamp moves to the Details; an unparseable
  value is UNKNOWN, a future timestamp gives a negative age
- **Plain value** → shown in the summary (numeric values still get a metric)
- **Levels set on a non-numeric value** → WARN (so the misconfig is visible)
- **Path not found** → UNKNOWN
- **Endpoint request failed / not JSON** → that endpoint's services go UNKNOWN
  with the error, and its own `JSON API <name>` service goes CRIT (configurable);
  the other endpoints in the rule keep reporting normally

Values are rendered as they appear in JSON, so a regex matches
`true` / `false` / `null` — not Python's `True` / `False` / `None`.

Rules that used the old **Expected value (regex)** field are migrated
automatically to must-match with the CRIT no-match default, so their behaviour
is unchanged.

The service **Details** view additionally shows where the value came from — the
JSON path, the source endpoint URL, the aggregation and value interpretation
(when set), and the match pattern — which makes a misconfigured extraction
(wrong path or wrong endpoint) easy to spot. This is details-only and never
changes the summary line or the service state.

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

### Turning a used/total pair into a percentage

Most APIs report a pair rather than a percentage. A **second path** supplies the
variable `other` to the transform, resolved in the same scope as the value —
within each `[*]` element, so every element is compared against *its own* total.

Given `GET /storage` → `{"disks": [{"id": "sda", "used": 25, "total": 100},
{"id": "sdb", "used": 180, "total": 200}]}`:

| Service name | JSON path | Second path | Transform | Unit | Check |
|---|---|---|---|---|---|
| `Disk` | `disks[*].used` | `total` | `value / other * 100` | `percent` | upper levels `80 / 90` |

That yields `JSON Disk sda` at `Value: 25.00%` (OK) and `JSON Disk sdb` at
`Value: 90.00%` (CRIT) — one service per disk, each against its own capacity.

The transform must actually use `other`, and `other` requires the second path;
either half alone is rejected in Setup rather than becoming a puzzling UNKNOWN.
If the second path does not resolve for some element, that service reports the
calculation as failed instead of substituting a value — a missing `total` must
not turn into a plausible-looking ratio.

### Array auto-discovery

Given a payload with a `nodes` array:

```json
{"nodes": [{"name": "web-1", "status": "UP"}, {"name": "web-2", "status": "DOWN"}]}
```

| Service name | JSON path | Per-element name suffix | Check |
|---|---|---|---|
| `Node` | `nodes[*].status` | `name` | must match `UP` (else CRIT) |

Produces `JSON Node web-1` (OK) and `JSON Node web-2` (CRIT). If a label value
repeats across elements, every occurrence is suffixed with its index so two
elements never collapse into one service.

### One host per element

The same payload, but with **Create one host per element, named by this field**
set to `name` instead of a name suffix:

| Service name | JSON path | Create one host per element | Check |
|---|---|---|---|
| `Node` | `nodes[*].status` | `name` | must match `UP` (else CRIT) |

Now the elements are *hosts*, not services: host `web-1` gets `JSON Node` (OK)
and host `web-2` gets `JSON Node` (CRIT). Add more fields with the same host
field (`nodes[*].load`, `nodes[*].version`) and they land on those same hosts.

Why bother, when the services already told you the same thing? Because a host is
a first-class object in Checkmk and an array element isn't: each element gets its
own downtimes, acknowledgements, contact and host groups, availability report,
parent/child relationships and place in the folder tree. That is the difference
between monitoring an API and monitoring the fleet it describes.

Three things to know:

- **The hosts must already exist in Checkmk.** Piggyback data for an unknown host
  is stored but never monitored — this is standard Checkmk behaviour and the most
  common way to be confused by it. Create them by hand, or automatically with
  Dynamic host management (Enterprise/Cloud).
- **The `JSON API <name>` endpoint service stays on the polling host.** It reports
  the *request*, which belongs to the host holding the rule, not to any element.
  So does anything you extract from outside the wildcard, such as an aggregation.
- **If an endpoint is unreachable**, its services report the failure on the
  polling host: there is no response to read host names out of.

### Labelling the hosts a `[*]` rule creates

A piggyback host *is* the element it came from, so it can carry that element's
own facts as **host labels** — which is what lets folder rules, views and
filters target them. Given `GET /cluster` → `{"nodes": [{"name": "node-01",
"health": "UP", "region": "eu-west", "role": "worker"}, …]}`:

| Service name | JSON path | One host per element | Labels for that host |
|---|---|---|---|
| `Health` | `nodes[*].health` | `name` | `region`, `role` (as key `tier`) |

Each created host then carries `json_api/region:eu-west` and
`json_api/tier:worker`, resolved from *its own* element. The key defaults to the
path's last segment.

These are **host** labels on the created host, distinct from *Service labels*
(which describe the individual service) and from the endpoint's own *Host
labels* (which are resolved from the response root and stay on the polling host,
because they describe the API rather than any element of it). Setup rejects them
without a piggyback host name — there would be no host to attach them to.

### Aggregating a collection

When you care about the collection as a whole, not each element, pick an
**aggregation** instead of a `[*]` wildcard. Given `GET /status`:

```json
{"jobs": [{"id": 1}, {"id": 2}, {"id": 3}],
 "queues": [{"name": "a", "depth": 3}, {"name": "b", "depth": 12}]}
```

| Service name | JSON path | Aggregate | Check |
|---|---|---|---|
| `Queued jobs` | `jobs` | number of elements | upper levels `100 / 500` |
| `Total queue depth` | `queues[*].depth` | sum of the values | upper levels `50 / 100` |
| `Deepest queue` | `queues[*].depth` | largest of the values | upper levels `20 / 40` |

Produces one service each: `JSON Queued jobs` reports `3`, `JSON Total queue
depth` reports `15` and `JSON Deepest queue` reports `12` — where `jobs[*]` or
`queues[*].depth` alone would have created one service per element. Counting
works on an object too (e.g. `components` → number of components). Add a
**condition** to aggregate only part of the collection, e.g. *count only the
nodes whose `status` does not equal `ok`*.

### Monitoring a counter's rate

Many APIs only expose ever-growing totals, where the interesting number is the
change per second. Given `GET /metrics` → `{"requests_total": 184203219}`:

| Service name | JSON path | Interpret as | Check |
|---|---|---|---|
| `Requests` | `requests_total` | a counter | upper levels `500 / 1000` |

`JSON Requests` then reports e.g. `Rate: 212/s` and alerts on the request rate,
not on the (meaningless) total; the total itself stays visible in the Details.
The very first check has no previous reading to compare against, so it keeps the
service's state and says so.

### Monitoring how stale a timestamp is

Given `GET /status` → `{"last_backup": "2026-07-28T02:00:00Z"}`:

| Service name | JSON path | Interpret as | Check |
|---|---|---|---|
| `Backup age` | `last_backup` | a timestamp (format `auto`) | upper levels `93600 / 172800` |

`JSON Backup age` reports the age as a duration (e.g. `Age: 1 day 2 hours`) and
goes WARN once the last backup is older than 26 hours, CRIT after two days.
Epoch seconds and milliseconds work the same way; a timestamp without a time
zone is read as UTC.

### An API behind OAuth 2.0

Pick **OAuth 2.0 (client credentials)** as the authentication and give the
identity provider's token endpoint — not the API URL:

| | |
|---|---|
| Token URL | `https://login.example.com/oauth2/v2.0/token` |
| Client ID | `monitoring` |
| Client secret | from the password store |
| Scope | `api://monitoring/.default` (optional) |
| Audience | (optional; some providers, e.g. Auth0, require it) |
| Send credentials | in the Authorization header, or in the request body |

The agent POSTs `grant_type=client_credentials`, and sends the resulting token
to the API as `Authorization: Bearer <token>`.

**The token is cached** until shortly before it expires (using the provider's
own `expires_in`, minus a safety margin), so a rule polling every minute does
not ask the provider every minute. The cache is keyed on the token URL, client
ID, scope, audience and a *hash* of the secret — so two endpoints of the same
rule sharing a client share one token, and two rules with different credentials
never share one. It lives in the site's `tmp`, mode 0600, like the response
cache.

If the provider rejects a *cached* token with a 401 — a rotated secret, a
revoked grant — the agent discards it and retries once with a fresh one. A token
minted seconds ago and rejected is reported as-is: that means the credentials or
the scope are wrong, and asking again would only double every check's requests.

> **Which "Send credentials"?** RFC 6749 allows both the Authorization header and
> the request body, and providers disagree about which they accept. A wrong
> choice shows up as an unhelpful 401 *from the token URL*. Try the header first.

This is the machine-to-machine grant only. If your API can only be reached with a
token a *person* obtained by logging in through a browser, this mode cannot help —
see [docs/spike-oauth2.md](docs/spike-oauth2.md).

### Monitoring an API rate-limit budget

A path starting with `@header.` reads a **response header** instead of a field
of the body. Both Explorers offer them for picking rather than making you type
one from memory: the in-site wizard shows a **Headers** tab next to the field
picker (the Checkmk server fetched the response, so it has them), and the
standalone Explorer has a **Response headers** paste area — paste `curl -sSi`
output and it lists the header names to click. Given a `GET /v4/projects` that answers with
`RateLimit-Remaining: 137` and `Last-Modified: Wed, 21 Oct 2015 07:28:00 GMT`:

| Service name | JSON path | Interpret as | Check |
|---|---|---|---|
| `API budget` | `@header.RateLimit-Remaining` | (as it stands) | lower levels `100 / 20` |
| `Data age` | `@header.Last-Modified` | a timestamp (format `auto`) | upper levels `3600 / 86400` |

`JSON API budget` goes WARN once fewer than 100 calls remain in the window and
CRIT below 20 — so the quota is visible before it runs out and the endpoint
starts answering 429. Header names are matched case-insensitively, and none of
the body path syntax (`[*]`, aggregation, filters, bracket-quoting) applies to
them: a header is a single scalar. `auto` also reads an HTTP-date, which is
what `Last-Modified` and a date-form `Retry-After` contain.

A response served from the [cache](#caching-responses) replays the headers it
was stored with, so a cached serve never mixes one response's body with
another's headers.

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
headers, timeout, cache TTL, TLS/redirect toggles), paste each endpoint's sample JSON
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

## Troubleshooting

When a service reports "path not found" or an endpoint won't come up, run the
agent by hand with `--debug`. Copy the program call from `cmk -D <host>` (or
build one with the Explorer) and add `--debug`:

```sh
agent_json_api --endpoint '{"url": "https://app/health", "extractions": [...]}' --debug
```

Diagnostics go to **stderr** (the parsed section still goes to stdout, so the
run stays valid), and show, per endpoint: the request method/URL, the request
headers (the `Authorization` value is masked), the HTTP status and body size, a
preview of the raw response, and how each configured path resolved
(found/​not-found, one line per resulting service). This makes a wrong path or an
unexpected response shape obvious without reproducing the request elsewhere.

## Security notes

- The agent performs **HTTP requests from the Checkmk server** to operator-configured
  URLs. Treat the rule as trusted input: a URL pointing at internal services (or one
  that **redirects** there) can be used as an SSRF vector. Restrict who can edit the
  rule accordingly.
- **Follow HTTP redirects** is on by default (for back-compat). In locked-down
  environments, turn it off per endpoint so a redirect to an internal address fails
  instead of being followed — closing the redirect-based SSRF amplification path.
- Credentials are stored in the Checkmk **password store** and passed to the agent as
  a store reference, not in clear text on the command line. That includes an API key:
  use the *Authentication* choices for it rather than typing it into *Additional
  request headers* or into the URL, where it would be stored in clear text, appear in
  the agent's command line and be printed by a `--debug` run.
- Credentials do not survive a redirect to a **different host**: an API key in a
  header is stripped there, exactly as Checkmk's HTTP layer already does for
  `Authorization`. A same-host redirect (`/health` → `/health/`) keeps it, so
  ordinary endpoints still work.
- An API key placed in a **query parameter** is appended by the agent for the request
  only. It is redacted from the endpoint's reported final URL, from request-error
  messages and from debug output — but it still travels inside the URL, so it can
  reach proxy and server access logs along the way. Prefer a header where the API
  offers one.
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
for the per-version history, and [`UPGRADING.md`](UPGRADING.md) for the notes an
operator needs *before* upgrading — service renames, new services turning up on
the next discovery, changed check results.

1. Bump `version` in `pyproject.toml` and the `mkp add`/`mkp enable` examples above.
2. `make changelog` to refresh `CHANGELOG.md`, then commit both.
3. If this release renames services, adds services to existing rules, or changes
   what an existing rule reports, move the `[Unreleased]` block in
   `UPGRADING.md` under a `## [X.Y.Z]` heading. `gen_changelog.py --version`
   picks it up automatically; nothing else reads the file, so a missing note is
   silent — this step is the whole safeguard.
4. Tag and push. Push **both refs in one command**: the CI changelog check
   regenerates from the tags, so a `main` push that lands before the tag sees no
   `X.Y.Z` section and fails.

   ```sh
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin main vX.Y.Z
   ```

5. Announce it, if the release is worth announcing: refresh
   [`docs/exchange-listing.md`](docs/exchange-listing.md) (it enumerates features
   and quietly falls behind) and write `docs/forum-post-X.Y.Z.md` for
   forum.checkmk.com — see the 0.12.0 one for the shape. Lead with whatever will
   generate support questions, not with the longest feature.

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
- A per-second rate needs two checks before it can be computed, so a counter
  field is uninformative on its first check (and after the counter resets)
- The in-site wizard's review step does not preview an aggregated value, a rate
  or an age: which aggregation was picked is a hashed ident on the form's wire
  and a rate needs two checks, so it says what the site will compute instead of
  showing a number that might differ

## License

GPL-2.0-only. See [LICENSE](LICENSE).
