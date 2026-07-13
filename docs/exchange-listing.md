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
- 🔁 **Auto-discover arrays *and* objects** — `nodes[*].status` becomes one
  service per array element, and `components[*].status` one per object key
  (e.g. a Spring Boot Actuator `/health` map), automatically.
- 🔢 **Count elements** — flip a switch to monitor *how many* elements a path
  holds (array length or object keys) instead of the value: queue length,
  number of unhealthy nodes, ... The count is a number, so units, WARN/CRIT
  levels and a metric all apply. Where `[*]` fans out one service per element,
  `count` collapses the whole collection into one "how many" service.
- 🔗 **Many endpoints, one rule** — poll several APIs together, each with its own
  method, auth, and fields; an unreachable one only affects its own services.
- 📈 **Thresholds & graphs in Checkmk** — WARN/CRIT and metrics live in *your*
  rule, not upstream in the API.
- 🧮 **Transform the numeric value** — apply a small arithmetic expression like
  `value / 1024 / 1024` (bytes→MiB) or `(value - 32) * 5 / 9` (°F→°C) before
  levels and the metric; safely evaluated, no `eval`.
- 🔤 **String matching, two ways** — require a value to match a regex (pick the
  state when it doesn't, default CRIT), or map values like `ready` / `degraded`
  / `failed` straight to OK / WARN / CRIT.
- 🔐 **Secure by default** — basic or bearer auth via the password store, TLS
  verification on.
- 🧰 **Bonus field picker** — paste your JSON in the bundled explorer, click what
  to monitor, copy the ready-made rule.

## In 30 seconds

`GET /actuator/health` → `{"status": "UP", "components": {"db": {"status": "UP"}}}`

Tick `status` (expect `UP`) and `components.db.status` → instant services
`JSON Health` and `JSON Database`. That's the whole setup.

## Details

- **Checkmk 2.4+**, any edition. Tested on real 2.4 and 2.5 sites.
- Install via `mkp add` / `mkp enable`, or **Setup → Extension packages**.
- Source, docs & issues: <https://github.com/otAAAh/checkmk-json-agent> · GPL-2.0-only
