<!-- Paste this into the "Description" field of the Checkmk Exchange
     "upload new package" form. The Exchange renders Markdown. -->

# Generic JSON API

**Monitor any JSON API in Checkmk — without writing a line of code.**

Point it at a `/health`, `/status`, or metrics endpoint, pick the fields you care
about, and get a Checkmk service for each — thresholds, graphs, and alerts
included. One rule. Any API. Done.

## What you get

- 🎯 **Any endpoint, unmodified** — Spring Boot, Kubernetes, vendor appliances,
  your own apps. No special response format required.
- 🧭 **Pick fields by path** — `components.db.status`, `items[0].count`, done.
- 🧾 **Monitor the response headers too** — prefix a path with `@header.` and watch
  what the API says *outside* the JSON: `@header.X-RateLimit-Remaining` alerts
  before you exhaust your quota, `@header.Last-Modified` (as a timestamp) tells
  you how stale the data is.
- 🔁 **Auto-discover arrays *and* objects** — `nodes[*].status` becomes one
  service per array element, and `components[*].status` one per object key
  (e.g. a Spring Boot Actuator `/health` map), automatically.
- 🔢 **Aggregate a collection** — where `[*]` fans out one service per element,
  an aggregation collapses the whole collection into one service: the **number
  of elements** (queue length, unhealthy nodes) or the **sum / average / min /
  max** of the values (`queues[*].depth`). The result is a number, so units,
  WARN/CRIT levels and a metric all apply.
- 📚 **Follow the API's pagination** — a collection answered one page at a time
  otherwise leaves every count, aggregation and `[*]` service describing the
  **first page**, with nothing saying so: `count` over a queue that pages at 25
  reports 25 however long the queue is. Say where the next page's URL is (a
  field in the body, or the RFC 8288 `Link` header) and which collection to
  merge, and the pages become one document that the wildcards, aggregations and
  labels see whole. Capped in pages and elements, a link to another host is
  refused, and where a cap left a page behind the endpoint's own service says
  the collection is **incomplete** instead of reporting part of it as if it were
  all of it.
- 🔍 **Filter elements by a condition** — restrict a `[*]` wildcard or an
  aggregation to the elements that match: one service per node whose `status` is
  *not* `ok`, or a count of only the pods that aren't `Running`
  (equals / not-equals / regex / not-regex).
- ⏱️ **Counters and timestamps, done right** — mark a field as a **counter** and
  monitor its per-second **rate** instead of an ever-growing total
  (`requests_total`); mark it as a **timestamp** and monitor its **age**, so
  upper levels alert on stale data (`last_backup` older than 26 h → WARN).
- 🖧 **One Checkmk host per element** — point a `[*]` field at a field holding a
  host name and every element becomes a **host of its own**, not just another
  service. An API describing a fleet gives you hosts with their own downtimes,
  contact groups and availability, instead of one host with a hundred services.
- 📋 **Facts into the HW/SW inventory** — a version, a build, a region, a licence
  tier is not a state worth a service that is OK forever. Send it to the
  **inventory tree** instead, where it is searchable *across* hosts ("which hosts
  still run a version below 4.2?") and keeps its own change history. A `[*]`
  wildcard becomes a **table**, one row per element.
- 💬 **Show the reason next to the value** — an optional summary text with
  `{path}` placeholders, so a service on `status` reads
  `Value: DEGRADED, replica lag 42s (leader db-3)` instead of needing a second
  service for the message the API already returned.
- 🩺 **Every endpoint monitors itself** — each endpoint also gets a
  `JSON API <name>` service with the **HTTP status, response time** (thresholds
  optional), response size and, for HTTPS, the **TLS certificate's remaining
  validity** — read from the connection it is already making, so no second check
  against the same URL. Zero configuration; it comes with the rule.
- 📄 **See the response that caused the state** — optionally report the raw body
  and the response headers in that endpoint's own service details, including for
  a **rejected** response (an unexpected status, or a body that is not JSON) —
  which is where an API explains itself: `HTTP 403` sends you to the credentials,
  `tenant disabled` sends you to the right place. Capped at a byte budget you
  set, with `Set-Cookie`, authorization headers and the endpoint's own secret
  masked first. Off by default.
- 🔔 **…on the service that actually alerts** — the raw response above sits on
  the endpoint's own service, which stays OK and notifies nobody. Opt in per
  endpoint and the **field** services carry the JSON too — by default just the
  element the value was read from, with its sibling fields — so a CRIT
  notification's `$LONGSERVICEOUTPUT$` shows what the API said, including when
  the path did not resolve at all. It matters most for the person on call who
  cannot reach the endpoint themselves: wrong network, no credentials.
- 🐢 **Rate-limited API? Cache it** — give an endpoint a TTL and the agent reuses
  its last response instead of asking again, so monitoring cannot exhaust a
  request quota. It never caches an error and never answers a failed request from
  an expired cache, so a real outage still shows up.
- 🔁 **Retry a blip instead of alerting on it** — an optional per-endpoint retry
  with backoff for the failures a repeat can fix (connection reset, timeout,
  429/5xx), so a load balancer dropping connections during a rolling restart is
  not a CRIT and a notification. A 4xx or a non-JSON body is never retried, and
  the service reports when a retry *was* needed — it cannot hide a degrading API.
- 🔗 **Many endpoints, one rule** — poll several APIs together, each with its own
  method, auth, and fields; an unreachable or misconfigured one only affects its
  own services.
- 🧩 **One service for several fields** — where a service per field is too much:
  name a shared service on each field and they become **lines** of one service,
  which takes the **worst** of their states. `status`, `component` and
  `timestamp` become one service that is OK while all three are fine, each line
  keeping its own levels, matching, transform and unit. A `[*]` wildcard fans out
  into lines too, so a whole collection can be one service that goes CRIT if any
  element does.
- 🔖 **Services named after the endpoint** — two endpoints of the same shape
  extract the same fields, which would give you `JSON STATUS` and
  `JSON STATUS (2)`. Name the endpoints instead and every service of one
  application shares its prefix — `JSON app1-health STATUS` — which sorts them
  together and makes them addressable as a **group** in service rules and
  notification conditions. The endpoint's own status service joins that group
  too, as `JSON app1-health API`.
- 📈 **Thresholds & graphs in Checkmk** — WARN/CRIT and metrics live in *your*
  rule, not upstream in the API, and can be retuned per folder, host or service
  from a normal check-parameters rule without touching the connection.
- 📊 **A Perf-O-Meter that means something** — the bar in the service list is
  scaled to the field's own **critical level**, so it reads as "how close to
  critical" rather than against a maximum nobody can know for a JSON value.
  Where a field has no levels it falls back to an open range for its unit.
- 📐 **State the value range** — a battery is 0–100, a queue with a cap is 0 to
  that cap. Say so and Checkmk stops guessing: the graph keeps a **steady
  scale** instead of rescaling to whatever the last hour contained, a **gauge
  dashboard widget** has a dial to draw, and the bar fills against the real
  maximum. It never changes the state — presentation only, and optional.
- 📛 **Name the metric yourself** — the **Gauge**, **Single metric** and **Bar
  chart** dashboard widgets are each bound to one metric *by name*, and their
  dropdown only lists a service's real names once the widget is filtered to a
  host **and** a service — so a widget configured the other way round points at
  a name the service never emits and stays blank. Give a field an explicit
  **metric name** and that is what it records, so you can point a widget
  straight at it. Validated against Checkmk's own naming rule, in Setup and in
  the wizard.
- 🏷️ **Labels from the response** — attach Checkmk **host** and **service labels**
  built from fields (`json_api/version`, `json_api/region`), so views, rules and
  filters can key off what the API says about itself. The hosts a `[*]` rule
  creates can carry labels from **their own element**, so 50 generated hosts are
  addressable by region or role instead of being an anonymous crowd.
- 🧭 **Or let the host classify itself** — a label can be a *conclusion* rather
  than a copied field: "if any element of `services[*]` has a `name` matching
  `^MyApp`, put `json_api/MyApp: yes` on the host". A condition picks the
  elements, the value is typed in the rule, and the whole collection collapses
  to **one** label — so thresholds, contact groups, folder rules and views
  attach themselves from what the API reports the host is actually running.
- 🧮 **Transform the numeric value** — apply a small arithmetic expression like
  `value / 1024 / 1024` (bytes→MiB) or `(value - 32) * 5 / 9` (°F→°C) before
  levels and the metric; safely evaluated, no `eval`. A **second field** can join
  in as `other`, which is what turns the used/total pair most APIs actually
  return into a percentage: `value / other * 100`, resolved per `[*]` element so
  every disk is measured against its own capacity.
- 🔤 **String matching, two ways** — require a value to match a regex (pick the
  state when it doesn't, default CRIT), or map values like `ready` / `degraded`
  / `failed` straight to OK / WARN / CRIT.
- 🔐 **Secure by default** — basic auth, bearer tokens, **API keys** (in a header
  of the API's choosing, or a query parameter) **and OAuth 2.0 client
  credentials** all come from the Checkmk **password store**, never from clear
  text in the rule or on a command line. For OAuth the agent exchanges the client
  ID and secret for a short-lived token itself and caches it until shortly before
  it expires, so monitoring does not hammer your identity provider once a minute.
  TLS
  verification is on by default, with a **custom CA bundle** for a private CA and
  **client certificates** for mutual TLS. Non-2xx status codes can be opted in per
  endpoint (read a `/health` that reports its problems with a 503), and an
  endpoint reachable only through a corporate egress **proxy** is supported.
- 🧰 **Bonus field picker** — paste your JSON in the bundled explorer, click what
  to monitor, copy the ready-made rule. On Checkmk 2.5+, install the optional
  companion package **Generic JSON API – Explorer (extra)** for a guided in-site
  wizard that builds the rule for you from a live API response.

## In 30 seconds

`GET /actuator/health` → `{"status": "UP", "components": {"db": {"status": "UP"}}}`

Tick `status` (expect `UP`) and `components.db.status` → instant services
`JSON Health` and `JSON Database`. That's the whole setup.

## Details

- **Checkmk 2.4+**, any edition — and verified on **3.0**. CI runs the suite
  inside the official Checkmk container for every release line that publishes
  one.
- Install via `mkp add` / `mkp enable`, or **Setup → Extension packages**.
- Source, docs & issues: <https://github.com/otAAAh/checkmk-json-agent> · GPL-2.0-only
