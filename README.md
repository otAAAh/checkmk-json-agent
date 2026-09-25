<!-- SPDX-License-Identifier: GPL-2.0-only -->
# checkmk-json-agent

[![CI](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/ci.yml)
[![Frontend build](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/frontend-build.yml/badge.svg)](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/frontend-build.yml)
[![Checkmk agent 2.4+](https://img.shields.io/badge/Checkmk_agent-2.4%2B-15d1a0?logo=checkmk&logoColor=white)](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/ci.yml)
[![Checkmk explorer 2.5+](https://img.shields.io/badge/Checkmk_explorer-2.5%2B-15d1a0?logo=checkmk&logoColor=white)](https://github.com/otAAAh/checkmk-json-agent/actions/workflows/frontend-build.yml)
[![Checkmk 3.0 ready](https://img.shields.io/badge/Checkmk_3.0-ready-15d1a0?logo=checkmk&logoColor=white)](#compatibility)
[![License: GPL v2](https://img.shields.io/badge/License-GPLv2-blue.svg)](LICENSE)

A generic Checkmk **special agent for monitoring any HTTP/JSON API** — query a
`/health` or `/status` endpoint, extract fields by path, and turn them into
Checkmk services with thresholds and metrics. No custom Python, no MKP
development per integration: it's all one Setup rule.

Targets **Checkmk 2.4+** and the current stable plugin APIs
(`cmk.agent_based.v2`, `cmk.rulesets.v1`, `cmk.server_side_calls.v1`,
`cmk.graphing.v1`). See [Compatibility](#compatibility) for what that covers on
the 3.0 line.

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
- **The raw response in the check details**, per endpoint and opt-in: the body
  (capped, credentials stripped) and the response headers on the endpoint's own
  service — including the body of a *rejected* response, which is where an API
  explains itself
- **The JSON context on the services that alert**: the raw response above sits
  on the endpoint's own service, which stays OK and notifies nobody. Opt in per
  endpoint and the *field* services carry the JSON too — by default just the
  element the value was read from, with all its sibling fields — so a CRIT
  notification's `$LONGSERVICEOUTPUT$` shows what the API actually said, which
  matters most for the on-call person who cannot reach the endpoint at all
- **Response headers** as a value source: prefix the name with `@header.`
  (e.g. `@header.X-RateLimit-Remaining`) to monitor an API quota, a
  `Retry-After` or the age of a `Last-Modified`
- **One service for several fields**, where a service per field is too much:
  name a shared service on each field and they become lines of it, the service
  taking the **worst** of their states - so `status`, `component` and
  `timestamp` can be one service that is OK while all three are fine
- **One service per field**, named as you choose - optionally prefixed with
  the **endpoint's name**, so two endpoints monitoring the same fields do not
  produce `JSON STATUS` and `JSON STATUS (2)`
- **Array & object auto-discovery**: a `[*]` wildcard (e.g. `nodes[*].health`)
  creates one service per array element - or per key when it lands on a JSON
  object/map, such as a Spring Boot Actuator `/health` `components[*].status` -
  labelled by a field you pick (or the index/key); multiple wildcards
  (e.g. `pods[*].containers[*].ready`) expand the cartesian product, with
  composite `<pod> / <container>` labels
- **Host labels on the created hosts**: a piggyback host can carry labels
  resolved from its own element (`region`, `role`, …), so folder rules and views
  can target the hosts a `[*]` rule created
- **Classify the host from a collection**: a host label can be a *conclusion*
  rather than a copied field — "if any element of `services[*]` has a `name`
  matching `^MyApp`, put `json_api/MyApp: yes` on the host". A condition picks
  the elements, a literal value is typed in the rule, and the whole collection
  collapses to **one** label instead of one per element — so thresholds, contact
  groups, folder rules and views attach themselves from what the API actually
  reports
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
- **Follow the API's pagination**: a collection answered one page at a time
  otherwise leaves every count, aggregation and `[*]` wildcard describing the
  *first page* — `count` over a queue that pages at 25 reports 25 however long
  the queue is, and nothing in any service says so. Opt in per endpoint, say
  where the next page's URL is (a field in the body, or the RFC 8288 `Link`
  header) — or, for an API with no link, let the agent count a **page number**
  (`?page=2`) or an **offset** (`?offset=50&limit=25`) itself, stopping at an
  empty page, a short one, a stated total or a 404 past the last page — and
  which collection to merge,
  and the pages are appended into one
  document that the wildcards, aggregations, filters and host labels all see
  whole. A link to another host is refused, the pages read are reported, and
  where a cap left a page behind the endpoint's own service says the collection
  is **incomplete** and goes WARN
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
- **A unit for the value**: count, bytes, seconds, percent, a rate per second,
  bytes or bits per second, °C, volts, amperes, watts or hertz — the summary,
  the levels line and the graph then read `1.50 MiB/s`, `41.2 °C` or `2.40 GHz`
  instead of a bare number. The value itself is never converted: levels and the
  value range are entered in the unit the API reports
- **A Perf-O-Meter on every service**: the bar in the service list is scaled to
  the field's own CRIT level where one is configured — so it reads as "how close
  to critical", the only scale a JSON value really has — and falls back to an
  open range for the unit otherwise. A service built from several fields has
  none: its metrics are named after the fields at runtime — and neither has a
  field whose **Metric name** is set in the rule, for the same reason
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

## Compatibility

| Checkmk line | Status |
| --- | --- |
| **2.4** | Supported. Covered by CI on every push (`2.4.0-latest`). |
| **2.5** | Supported. Covered by CI on every push (newest published daily). |
| **3.0** | Supported. Verified by hand — the plugin installs and all of its modules import against 3.0's `cmk.agent_based.v2`, `cmk.rulesets.v1`, `cmk.server_side_calls.v1` and `cmk.graphing.v1` on Python 3.14. **Not yet covered by CI**, because no public `checkmk/check-mk-raw` 3.0 image exists to run it against; the CI job already asks for one on every run and starts testing 3.0 the day it appears. |

The agent branches on the password-store API (public
`cmk.password_store.v1_unstable` on 2.5+, an internal fallback on 2.4), which is
the only place the lines differ for this plugin. The MKP declares a minimum of
2.4.0 and no upper bound, so it installs on any newer line.

## Installation

Download the `.mkp` from the [Releases](https://github.com/otAAAh/checkmk-json-agent/releases)
page (or [build it](#building-from-source)), then, as the site user:

```sh
mkp add json_api-0.21.0.mkp
mkp enable json_api 0.21.0
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
| **Endpoint name** | Optional short name (e.g. `frontend`). Names the endpoint's own `JSON API <name>` service; without it the URL is used. Macros are resolved here too. The field service names keep their plain names unless *Prefix the field service names* is on |
| **URL** | Full endpoint URL incl. scheme, e.g. `https://app.example.com/actuator/health`. Checkmk macros (`$HOSTNAME$`, `$HOSTADDRESS$`, custom host macros, ...) are resolved against the monitored host, so one rule can be shared across many hosts. |
| **HTTP method** | `GET` or `POST` |
| **Request body** | Optional body for `POST` (defaults `Content-Type: application/json` unless you set one). Macros are resolved here too. |
| **Additional request headers** | Name/value pairs; macros are resolved in the values. Stored in clear text — an API key belongs under *Authentication* instead |
| **Authentication** | None, basic (username/password), bearer token, an API key in a request header (you name the header, e.g. `X-API-Key`), or an API key in a query parameter. All secrets come from the password store; the query-parameter key is appended for the request only and is redacted from every URL the agent reports |
| **Prefix the field service names with the endpoint name** | Off by default. Names this endpoint's field services after it — `JSON Status` becomes `JSON <name> Status` — so two endpoints extracting the same fields stay distinguishable. Needs an endpoint name; see [Naming services per endpoint](#naming-services-per-endpoint) |
| **Verify the TLS certificate** | On by default |
| **Custom CA bundle file** | Optional; path on the Checkmk server to a PEM file with the CA(s) to verify the server against — trust a private CA without disabling verification. Ignored when verification is off |
| **Client certificate (mutual TLS)** | Optional; paths on the Checkmk server to the client certificate (PEM) and, if separate, the private key. The key must be unencrypted |
| **Follow HTTP redirects** | On by default; turn off to harden against redirect-based SSRF |
| **Re-read at most every (seconds)** | Optional cache TTL. Reuses the last response for this endpoint while it is younger than this, instead of requesting it again — see [Caching responses](#caching-responses). Unset (the default) always fetches fresh |
| **Retry a failed request** | Optional: a number of retries (1–5) and a backoff in seconds, doubled for each further attempt and capped at 30 s in total. Only a connection error, a timeout, an HTTP 429 or a 5xx is retried — a 4xx, a body that is not JSON and an oversized response answer the same however often they are asked. A cached response makes no request, so nothing is retried. Off by default |
| **Follow pagination** | Optional; read a collection that the API answers one page at a time and merge the pages, instead of describing only the first. Needs where the next page's URL is (a JSON path in the body, or the `Link` header's `rel="next"`) and the path to the collection each page carries; capped by a page count (1–100, default 10) and optionally an element count. Off by default — see [Following pagination](#following-pagination) |
| **Request timeout (seconds)** | Optional; defaults to 30 |
| **Additional accepted HTTP status codes** | Optional; by default only 2xx responses are read (any other status → UNKNOWN). List extra codes (e.g. `503`) to parse and extract their body too. 2xx is always accepted |
| **Report the raw response** | Optional; put the response itself into the *Details* of this endpoint's own `JSON API <name>` service — the body (capped at a byte budget you set, 2048 by default) and, unless you turn them off, the response headers. Includes the body of a *rejected* response. Credentials are stripped first. Off by default — see [Reporting the raw response](#reporting-the-raw-response) |
| **HTTP proxy** | Optional; route via the environment's `HTTP_PROXY`/`HTTPS_PROXY`, an explicit proxy URL, or bypass. Unset = honour the environment |
| **Fields to monitor** | One entry per service (see below) |

Service names must be unique across the whole rule; if two endpoints produce the
same name, the check disambiguates the later one with a ` (2)` suffix. Rather
than living with that, name the endpoints and let them prefix their services —
see [Naming services per endpoint](#naming-services-per-endpoint).

Each **field to monitor** has:

| Field | Purpose |
|---|---|
| **Service name** | Becomes the service (shown as `JSON <name>`) — or, with a shared service set below, the name of this field's *line* inside it |
| **Report in a shared service named** | Optional: report this field into one shared service alongside the other fields naming it, instead of creating one of its own. The service's state is the **worst** of its lines. See [One service for several fields](#one-service-for-several-fields) |
| **JSON path** | Dotted path; use `[*]` for array discovery |
| **Per-element name suffix** | For `[*]`: field within each element, appended to the service name to tell the per-element services apart (defaults to the array index); it does not replace the service name. A value two elements share is told apart by position (`web [0]`, `web [3]`), which the Explorer and the wizard warn about from the sample |
| **Create one host per element, named by this field** | Optional, for `[*]`: field within each element holding a **Checkmk host name**. Each element then becomes a piggyback host carrying this service under its plain name (the host says which element it is, so no name suffix is added). Set the same field on several fields of the endpoint to collect them on the same hosts. Only host-name-safe characters are kept (letters, digits, `-`, `_`, `.`); anything else becomes `_`. An element whose field is missing keeps its service on the polling host. **The hosts must exist in Checkmk** or the data is held and never monitored — see [One host per element](#one-host-per-element) |
| **Service labels** | Optional: attach Checkmk service labels to *this* service from response fields, each key prefixed with `json_api/`. For a `[*]` path the value is resolved within each element (e.g. `name`), so each per-element service gets its own label; otherwise from the response root. Host-wide facts go in the endpoint's **Host labels** instead. Set at discovery, so pick stable, low-cardinality fields |
| **Aggregate a collection into one value** | Optional: `count` (number of elements) / `sum` / `avg` / `min` / `max` over the array or object at the path — or over the values a `[*]` wildcard expands to, which then yields *one* service instead of one per element. The result is a number, so unit, levels, transform and metric all apply. Where the `[*]` path names a field, every function — `count` included — only sees the elements that have it (so `nodes[*].load` counts the nodes reporting a load, and a path no element has becomes UNKNOWN). A path that is neither an array nor an object becomes UNKNOWN, as does `avg`/`min`/`max` over no elements (`sum` over none is `0`) |
| **Only elements matching a condition** | Optional: for a `[*]` wildcard or an aggregation, keep only elements whose sub-field matches (path + operator equals/not-equals/regex/not-regex + value). Resolved within each element; an element missing the field is dropped. No effect without a wildcard or an aggregation |
| **Interpret the value as** | Optional: *a counter* — monitor the change **per second** rather than the total (the first check, and any check after the counter went backwards, keeps the previous state because no rate can be computed yet); or *a timestamp* — monitor its **age in seconds** (format `auto` / epoch seconds / epoch milliseconds / ISO 8601; no time zone means UTC). The derived number is what the transform, levels and metric use; string matching does not apply to it |
| **Unit** | Optional: `count` / `bytes` / `seconds` / `percent` / `per second` / `bytes per second` / `bits per second` / `°C` / `volts` / `amperes` / `watts` / `hertz` — renders the value in the summary/details *and* the metric and graph with that unit (numeric values only). The value is not converted, so levels and the value range are in the API's own unit; a unit the API does not report as it stands goes through the transform first (a latency in milliseconds is `seconds` with `value / 1000`). A value the API already reports per second (`per second`, `bytes per second`) records into the same metric as a counter's rate. A **counter** takes only `count` / `bytes` / `seconds` / `percent` or no unit — its rate is that unit per second, so a rate or a measurement such as a temperature is refused for one |
| **Value range** | Optional lowest/highest value this field can take (a battery is 0–100, a queue with a cap is 0–that cap). It never changes the state: it tells Checkmk what "full" means, so the graph keeps a steady scale instead of rescaling to whatever the last hour contained, a **gauge dashboard widget** has a dial to draw, and the service list's bar is filled against the real maximum. Either end may be given alone; with both ends the Perf-O-Meter uses the range, otherwise it falls back to the critical level |
| **Metric name** | Optional: the name Checkmk stores this field's metric under. Unset, the plugin picks one — the unit's metric for a field with a service of its own (`json_api_bytes`), or that plus the line's name for a field in a shared service (`json_api_bytes_root_used`). Set it where the name has to be known in advance: the **Gauge**, **Single metric** and **Bar chart** dashboard widgets are bound to one metric *by name*. Letters, digits and underscores only, starting with a letter or a digit (Checkmk's own rule), and not one of this plugin's own metric names — a percentage named `json_api_bytes` would inherit that metric's unit and render as bytes, so Setup rejects it. **Two costs:** the service loses its Perf-O-Meter (every bar is declared against one of this plugin's metrics, so a name of your own matches none), and changing the name later starts a new history under it |
| **Transform the numeric value** | Optional arithmetic expression on the variable `value` (e.g. `value / 1024 / 1024`), applied to a numeric value before levels and the metric; only numbers, parentheses and `+ - * /` are allowed |
| **Second path for the transform** | Optional second field, available to the transform as `other` — which is what turns a used/total pair into a percentage (`value / other * 100`). Resolved in the **same scope** as the value: within each `[*]` element, or the response root without a wildcard, so every element is compared against its own total. The transform must use `other` and `other` requires this path — either alone is rejected in Setup. A path that does not resolve fails the calculation rather than substituting a value |
| **Extra text in the service summary** | Optional text appended to the summary, after the value. `{path}` inserts another field of the same response — resolved *within the current element* for a `[*]` wildcard, from the response root otherwise — e.g. `{message} (leader {leader})`. A path that is not in the response renders as `(n/a)`; a value that is an object or array renders as its size. Presentation only: it never changes the state, the levels or the metric. Put on one line and truncated if long |
| **Write into the HW/SW inventory** | Optional: a tree node (e.g. `software.applications.json_api`, starting with `hardware`, `software` or `networking`) and an attribute name (defaults to the JSON path's last segment). The value goes into the host's inventory tree and, by default, creates **no service** — tick *Also create a service* if you want both. A `[*]` wildcard writes one **table row** per element, keyed by an `element` column holding the element's label, so several fields over the same collection fill in columns of the same row — which is also why a wildcard's attribute name cannot itself be `element`. Use it for values that rarely change: every change is recorded in the inventory history. Inventory runs on its own, slower schedule |
| **Upper / lower levels** | WARN/CRIT for numeric values |
| **String matching** | Either *must match a regex* (choose the state when it does **not** match, default CRIT) or *map the value to a state* (separate OK / WARN / CRIT regexes, first full match wins; choose the state when nothing matches, default OK) |

Each **endpoint** also has an optional **Report the JSON context in the field services** setting — what the services that actually alert show of the response; see [The JSON context on the field services](#the-json-context-on-the-field-services).

Each **endpoint** also has an optional **Host labels** list: fields resolved from the response root and attached to the monitored *host* (e.g. `version`, `cluster.region`) as `json_api/<key>` — host-wide, needing no service. A path may contain a `[*]` wildcard (e.g. `components[*]`) to emit **one label per element**, keyed `json_api/<key>/<element>` (unique keys), with the value taken from an optional per-element **value field** (default `true`, i.e. set-membership tags). Two more fields turn that into a *classification* — a **label value** typed in the rule instead of read from the response, and **only elements matching a condition** (the same path + operator + value predicate a field's filter uses) — see [Classifying the host from a collection](#classifying-the-host-from-a-collection). In the wizard, the JSON picker's **`+ host label`** button adds these.

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

### Naming services per endpoint

Two endpoints of the same shape — say a health endpoint on each of two
applications — extract the same fields, so they produce the same service names
and the second copy is disambiguated with a ` (2)` suffix:

```text
JSON API my_app1_health
JSON API my_app2_health
JSON STATUS
JSON STATUS (2)
JSON TIMESTAMP
JSON TIMESTAMP (2)
```

Nothing in `JSON STATUS (2)` says which application it belongs to, and which
endpoint gets the suffix depends on the order of the endpoints in the rule.

Give each endpoint a **name** and tick **Prefix the field service names with the
endpoint name**, and that endpoint's services are named after it:

```text
JSON my_app1_health API
JSON my_app1_health STATUS
JSON my_app1_health TIMESTAMP
JSON my_app2_health API
JSON my_app2_health STATUS
JSON my_app2_health TIMESTAMP
```

Every service of one application now shares a prefix, which sorts them together
in the service list and makes them addressable as a group in service rules and
notification conditions. The leading `JSON ` stays: it is part of the check
plugin's service name and is the same for every service the plugin creates.

Notes:

- It is **per endpoint**, so one rule can prefix the endpoints that collide and
  leave a single-endpoint one alone.
- The endpoint's own service follows: `JSON API <name>` becomes
  `JSON <name> API`, so it sorts with the services it describes rather than
  clustering with every other endpoint's status service. It is the same service
  with the same check-parameters rule — Checkmk just needs a second check plugin
  to render the other name, so the two are discovered separately and an endpoint
  always has exactly one of them.
- The prefix needs an endpoint name — Setup rejects the combination without one
  rather than silently doing nothing. The URL is deliberately never used as a
  prefix: it would carry a query string (and any key in it) into every service
  description.
- With **one host per element** the element is its own host, so the service
  keeps its plain per-element name and only gains the endpoint prefix.
- Turning it on **renames** services: the old ones go stale and the renamed ones
  have to be discovered. Re-run a service discovery on the affected hosts
  afterwards, and expect to move any check-parameters rule that matched the old
  names.

### Reporting the raw response

A field service's *Details* name the **source URL** the value came from. That
link is often useless in practice: the API may sit behind a firewall or in
another network, so the browser reading the service cannot open it — and even
where it can, it shows the API as it is *now*, not as it was when the check ran
and went CRIT.

Tick **Report the raw response** on an endpoint and the response itself lands in
the *Details* of that endpoint's own `JSON API <name>` service:

```text
HTTP 403
URL: https://app.example.com/api/v1/health
Response headers:
  content-type: application/json
  x-request-id: 7f3c1a
Response body (first 2048 of 8412 bytes):
{"error": "tenant disabled", "since": "2026-09-08T11:20:00Z"}
```

- It is reported for a **rejected** response too — an unexpected HTTP status, or
  a body that is not JSON. That is the case this exists for: the status code
  alone does not say *why*, and the body is where the API explains itself. The
  body of a rejected response is only read when this option is on, so a failing
  endpoint costs nothing extra otherwise.
- The body is cut off at your byte budget (default 2048, hard maximum 65536) and
  the service says what was cut. Keep it small: these details are stored with
  **every** check result of the service.
- It goes on the **endpoint's own service**, not on each field service — one
  copy per endpoint rather than one per field, and it is the service that
  describes the request in the first place.
- A **cached** response (see below) reports the cached body, which is what the
  check actually read.

**Credentials are stripped before anything is reported:** `Set-Cookie` (a live
session token) and any authorization header are masked, and the endpoint's own
secret is removed wherever it appears in the body or a header value — an API that
echoes back the key it was given cannot leak it into the monitoring history.

**Everything else is reported verbatim.** A service's details are stored with
every check result and travel into notifications, so do not turn this on for a
response carrying personal or otherwise sensitive data.

> The raw response is **not archived**: the details always describe the most
> recent check of that service. While the service stays CRIT that *is* the
> failing response; once it recovers, the failure's body is gone.

### The JSON context on the field services

The section above puts the response on the endpoint's **own** service. But that
service is usually OK: the one that goes CRIT — and therefore the one that
**notifies** — is a field service, and its *Details* say only which path was
read:

```text
Status: DOWN (expected to match 'UP')
JSON path: services[*].status
Source: https://app.example.com/api/v1/health
```

The JSON that would explain the failure was in the check's hand at that moment,
and sat on a different service. **Report the JSON context in the field
services** puts it where the alert is:

```text
Status: DOWN (expected to match 'UP')
JSON path: services[*].status
Source: https://app.example.com/api/v1/health
Response context:
{
  "name": "payments",
  "status": "DOWN",
  "since": "2026-09-14T08:12:00Z"
}
```

Two sources, both capped by a byte budget (default 1024, hard maximum 65536):

| What to report | Shown |
|---|---|
| **The JSON this value was read from** (default) | For a `[*]` path: *that element*, with all its sibling fields and nothing else. Otherwise the object holding the value. An aggregation has no single element and an `@header.` path is not in the body at all, so both fall back to the whole response |
| **The whole response body** | Always the whole document, on every field service of the endpoint |

- It reaches notifications as `$LONGSERVICEOUTPUT$` — which is the point. The
  person reading the alert is often on the wrong network, or has no credentials
  for the API, and cannot check it themselves.
- It is reported on a **missing path** too: "the path did not resolve" is
  exactly when *what did the API return?* is the question.
- Where several fields **share a service**, each line brings its own context
  under its own `[<line>]` heading.
- **Keep the budget small.** Unlike the raw response, this text is stored with
  every check result of *every* field service of the endpoint — the whole-body
  source multiplies it by the number of fields.
- Credentials are stripped exactly as they are for the raw response, and the
  same warning applies: details travel into notifications, so do not turn this
  on for a response carrying personal or otherwise sensitive data.

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

### Following pagination

Most REST APIs answer a collection **one page at a time**. The agent makes one
request per endpoint, so without this setting every service built from such a
collection describes the *first page*:

```text
JSON Queued jobs    OK - Value: 25          (the queue is 812 long)
JSON Node web-1 ... JSON Node web-25        (there are 300 nodes)
```

Nothing in either service hints at it. That is worse than an error: the answer
is not missing, it is **wrong**, and it stays wrong while the graph looks flat
and the service stays green.

**Follow pagination** reads the rest. It needs two things, because APIs disagree
only about where they put them:

| Setting | What it is |
|---|---|
| **Where the next page's URL comes from** | A **field in the body** — `links.next`, `next`, `meta.next_page_url` — or the **`Link` response header**'s `rel="next"`, the RFC 8288 convention the GitHub / GitLab / Jenkins style APIs use. A page carrying no next link (absent field, JSON `null`, empty string, no header) is the last one: that is how pagination ends. For an API with **no link at all**, the agent counts instead — see [below](#an-api-without-a-next-page-link) |
| **The collection to merge** | The array (or object) each page carries a slice of — `items`, `data.jobs`, or `$` where the response *is* the array |

Each page's collection is appended to the first page's, and **the rest of the
document stays as the first page sent it** — so a `total` or a `generated_at`
next to the collection still resolves and every path in the rule is unchanged.
A JSON object pages by key rather than by position, so both container kinds
merge, exactly as a `[*]` wildcard already treats them alike.

Every page is the first page's request at a different URL — same session,
method, headers, authentication and timeout — so a cursor only the API
understands needs no configuration here. A relative link (`/v1/jobs?page=2`) is
resolved against the page it came from, which is the URL the first page was
**served** from: where an endpoint redirects, the pages follow it to the host
and path actually answering, not to the one the rule names. A link anywhere
else is still refused.

#### An API without a next-page link

Plenty of APIs never say where the next page is: they take the position as a
query parameter and leave the counting to the client. Two more choices under
*Where the next page's URL comes from* cover them, and the agent does the
counting:

| Choice | The pages it asks for |
|---|---|
| **No link: count the pages** | `?page=1`, `?page=2`, … — from *Number of the first page* (1, or 0 for an API that counts from zero), one up per page |
| **No link: count the elements** | `?offset=0`, `?offset=25`, … — each offset is the number of elements received so far, *not* the page size asked for, so an API that sends fewer than requested loses nothing. Counting starts at 0; set *Offset of the first element* to 1 for an API that numbers its elements from one (SCIM's `startIndex`), or every page would repeat one element |

The parameter's name is configurable (`page`, `p`, `offset`, `skip`, …), and it
is set on the **first** request too, replacing one of that name in the URL —
every other query parameter goes out exactly as written. Optionally, a **page
size** and the parameter to send it as (`limit`, `per_page`, `size`) are added to
every page, the first included.

With no link to fall silent, three things say a page was the last one:

- a page whose collection is **empty**;
- a page with **fewer elements than the page size** *and* than a page before
  it, when a page size is set — which saves the request for the empty page
  after it. Short of the page size alone is not enough: an API may cap the size
  it is asked for (`limit=500` answered with 100), and taking its first capped
  page as the last would report 100 of 2000 elements as the whole collection.
  So a first page never ends it by being short;
- the elements read reaching the number at an optional **total** path
  (`total`, `meta.total_count`), where the API states one. A total that is
  absent or not a number decides nothing.

Otherwise the last page is recognised by the answer to the request after it:
one request more, never a wrong answer. That answer may be an empty page, but
APIs say "there is none" in other ways too, and these end the collection as
**complete** as well:

- **HTTP 404** (Django REST Framework's `Invalid page.`) or **416**. Any other
  error still fails the endpoint — a 400 in particular, which is as likely an
  API refusing to page past a window with the rest still there;
- a page with **no collection** at the path, a `null` one, or an empty one of
  the other kind (`{}` where the pages carry a list);
- the **last page once more** (an API that clamps the page number).

The endpoint's service names which one it was (`End of the collection: page 4
answered HTTP 404 Not Found`), in its details and OK. The exception is **page 2
repeating page 1**: that is an API that **ignores the parameter** — the usual
sign of a misspelt name. The agent does not merge the copy and reports the
collection as incomplete, rather than counting page one ten times.

#### The caps, and why they are not optional

Each page is a request made *while the check runs*, and Checkmk kills a special
agent that overruns. So **Read at most this many pages** is required (1–100,
default 10; the agent clamps to 100 whatever a hand-written rule says), and an
optional element cap can stop earlier — checked between pages, so a page is
never cut in half and the collection may end slightly above it. For a
collection that does not change every check interval, combine this with a
[cache TTL](#caching-responses): the merged document is what gets cached.

#### Nothing is hidden

The endpoint's own `JSON API <name>` service reports what it took:

```text
HTTP 200
Pages read: 3, 137 elements
```

and where a further page existed but was **not** read, it says so in the
summary and goes WARN — configurable with *State when the collection was read
incompletely*, and worth lowering to OK only where reading the first N pages is
deliberate:

```text
Collection incomplete: the page limit (10) was reached
```

Four things stop pagination that way rather than failing the endpoint, so what
*was* read still monitors the API: a cap, a next link pointing at **another
host**, a link **already fetched**, and — where the agent counts — a page that
**repeats the one before it**. A cap reached where the agent counts means the
last page was full and nothing said it was the last; set a page size or a total
path and an API with exactly as many pages as the cap reads as complete. The host restriction is deliberate — the
response body must not decide where the Checkmk server sends an authenticated
request, which is the same SSRF shape the *Follow HTTP redirects* switch closes
— and a repeated link means the API is pointing at itself, which would
otherwise spend the whole page budget re-reading one page.

A page that cannot be read at all (a 5xx, a timeout, a body that is not JSON, a
page missing the collection) **fails the endpoint** instead: half a collection
looks exactly like a shrinking one, and a retry policy, if configured, re-reads
the endpoint from page one.

> **The in-site wizard's preview shows one page.** It resolves against the
> single response it fetched, so with pagination on, its element counts describe
> the first page — the review step says so. The site itself merges the pages.

### One service for several fields

Not every API deserves a service per field. A small health endpoint —

```json
{"status": "UP", "component": "nginx", "timestamp": "2026-08-29T18:14:55+00:00"}
```

— produces three services by default, when what you wanted was one service that
is OK while all three are fine.

Set **Report in a shared service named** to the same name on each of those
fields (e.g. `Health`) and they report into that one service as lines:

```text
JSON Health    OK    Status: UP, Component: nginx, Timestamp: 12 m
```

The service's state is the **worst of its lines** — that is Checkmk's own
aggregation, the same rule that makes any check with several results take the
worst one. If `status` goes to `DOWN` and its string matching says that is CRIT,
the service is CRIT and the summary still shows the other two.

Each line keeps its own **levels, string matching, transform, unit and
timestamp/counter handling** from the agent rule — they are independent fields
that happen to share a service. **Service name** names the line; the shared name
names the service.

A `[*]` wildcard inside a shared service fans out into *lines*, not services, so
`nodes[*].load` with a name suffix of `name` adds `Load n1`, `Load n2`, … to the
service — one service for a whole collection, going CRIT if any element does.

Two things change, both because one service now holds several fields:

- **A check-parameters rule does not apply to it.** One set of levels cannot
  describe several fields, so thresholds for these fields live in the agent rule.
  A service with a single field is unaffected and still takes the rule.
- **Each line's metric is named after the line** (`json_api_bytes_root_used`),
  which keeps the history of several fields apart — but it is not one of the
  metrics the plugin declares, so it renders as a plain number rather than in the
  field's unit. A field that needs its unit on the graph is better off with a
  service of its own.

  That runtime name also decides which **dashboard widgets** can use the field.
  A *Graph* widget draws whatever the service carries, so it is unaffected;
  *Gauge*, *Single metric* and *Bar chart* are each bound to one metric **chosen
  by name**, and their dropdown only lists a service's real metric names once
  the widget is filtered to a host **and** a service. Configured the other way
  round — widget first, metric second — the dropdown offers the declared names
  (`json_api_value`, `json_api_bytes`, …), which a shared service never emits,
  and the widget then stays permanently blank with nothing saying why. Either
  filter the widget down before picking the metric, or set **Metric name** on
  the field and point the widget at that.

A field written to the **inventory** creates no service, so it cannot report into
a shared one either; Setup rejects that combination unless *Also create a service
for this field* is ticked.

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
- **Numeric value with levels *and* string matching** → the levels decide, and
  the Details say the matching was not applied; matching a number against a
  regex while levels already answer the same question is a contradiction rather
  than a combination
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
- **Pagination left a page behind** → the field services report the part of the
  collection that was read, and the endpoint's own service goes WARN
  (configurable) saying the collection is incomplete; a page that could not be
  read at all fails the endpoint as above

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

### Classifying the host from a collection

Host labels normally *mirror* a field: `version` becomes `json_api/version`. The
other common need is a **conclusion drawn from a collection** — the host should
be classified by what it actually runs, so that thresholds, contact groups,
folder rules, additional checks and views attach themselves to it automatically.

Given `GET /status` → `{"services": [{"name": "MyAppWeb", "state": "running"},
{"name": "postgres", "state": "running"}]}`, an endpoint **Host label**:

| Field | Value |
|---|---|
| JSON path | `services[*]` |
| Only elements matching a condition | `name` *matches regex* `^MyApp.*` |
| Label key | `MyApp` |
| Label value (literal) | `yes` |

→ the host gets **one** label, `json_api/MyApp:yes`, and nothing at all when no
element matches. Two things make that work:

- the **condition** is the same predicate a field's filter uses (path within the
  element + equals / not-equals / regex / not-regex), and
- the **literal value** replaces the per-element value *and* the
  `<key>/<element>` suffixing — the key is unique on its own, so the whole
  collection collapses to one label rather than one per element.

Without the literal value the condition simply narrows the per-element labels
(`json_api/<key>/<element>`), which is the right shape when you want to see
*which* elements matched rather than *that* any did.

The condition also works without a wildcard, where it is checked once in the
same scope the path is read from — `version` with the condition `mode` *equals*
`production` sets the label only on the production hosts. And a label needs no
path at all when the condition and the literal value describe it entirely; give
it a key and a value and it becomes a pure "if this holds, tag the host".

The same two fields exist on the **Labels for the created host** of a `[*]`
field, where the scope is the element that becomes the host: a condition on
`role` plus the literal `yes` tags only the created hosts it holds for.

Labels are set at **discovery**, so a filtered label is more stable than the
per-element form, not less — but re-discovery is still what updates it.

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

### Reading a paginated collection

`GET /api/v1/jobs` answers 25 jobs at a time and says where the next page is:

```json
{"items": [{"id": 1, "state": "running"}, "..."],
 "total": 812,
 "links": {"next": "https://app.example.com/api/v1/jobs?page=2"}}
```

Set **Follow pagination** on the endpoint — next page URL from the body at
`links.next`, collection `items`, at most 40 pages — and then:

| Service name | JSON path | Aggregate / condition | Reports |
|---|---|---|---|
| `Jobs` | `items` | number of elements | `812`, not `25` |
| `Failed jobs` | `items` | number of elements, condition `state` equals `failed` | every failed job in the queue, not just page one's |

Without it, both services would report a number bounded by the page size and
nothing would say so. Note that `total` in the body is a *fact the API already
computed*: where an API offers one, a plain field on `total` is cheaper than
walking the pages — pagination is for the cases where the number you need is
not in the document (a condition, a sum, one service per element).

An API that offers no link, only `?offset=` and `?limit=`:

```json
{"results": [{"name": "web-1", "healthy": true}, "..."], "count": 312}
```

Choose **No link: count the elements** — parameter `offset`, page size `100`
sent as `limit`, total at `count`, collection `results` — and the agent asks for
`?offset=0&limit=100`, `?offset=100&limit=100`, … and stops after the fourth
page, when 312 elements have been read. A `results[*].healthy` field then
creates a service for all 312 nodes.

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
response, click the fields to monitor, set thresholds/labels/host labels, and it
generates:
the agent `--endpoint` command line for CLI testing, the rule value for
`rules.mk`, and a REST API request body + `curl` to create the rule on a site.
Auth is emitted as a password-store reference (create the entry under **Setup →
Passwords**).

**In-site wizard (companion MKP)** — `json_api_explorer` is an optional, separate
extension package that adds a guided setup under **Setup → Quick setup → Generic
JSON API**. It fetches each endpoint's live response from the site, lets you
point-and-pick fields with a preview of the resulting service states, and creates
the rule for you. The preview is made with the endpoint's *whole* connection —
method, body, headers, authentication (including the OAuth 2.0 token exchange),
TLS verification with a custom CA bundle or a client certificate, and the
configured HTTP proxy — so what you see is what the agent will see; the
exceptions are the retry policy and pagination, which the wizard names where
they matter rather than reproducing. It is a companion to this agent — install the `json_api`
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
preview of the raw response, one line per page where the endpoint follows
pagination (with the reason it stopped, if it did), and how each configured path
resolved (found/​not-found, one line per resulting service). This makes a wrong path or an
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
- A fixed set of units (see **Unit** above); anything else is either converted
  into one of them with the transform, or left unit-less on the `json_api_value`
  metric
- `label_path` uniqueness is enforced by index-suffixing at runtime, not
  validated by Setup (the JSON isn't known then): a repeated name becomes
  `web [0]`, `web [3]`, which moves with the API's element order. The JSON API
  Explorer and the in-site wizard warn about a repeat - and a missing, empty or
  object-valued name field - from the sample, before the rule is saved
- A per-second rate needs two checks before it can be computed, so a counter
  field is uninformative on its first check (and after the counter resets)
- Pagination follows a next-page **link** or counts a **page number** / an
  **offset**: an API that hands back a bare cursor token (`"next_cursor":
  "abc"`) the client must put into a query parameter itself, or one that pages
  by a request *body* field, is not followed
- The in-site wizard's review step does not preview an aggregated value, a rate
  or an age: which aggregation was picked is a hashed ident on the form's wire
  and a rate needs two checks, so it says what the site will compute instead of
  showing a number that might differ

## License

GPL-2.0-only. See [LICENSE](LICENSE).
