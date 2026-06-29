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
- 🔁 **Auto-discover arrays** — `nodes[*].status` becomes one service per node,
  automatically.
- 📈 **Thresholds & graphs in Checkmk** — WARN/CRIT and metrics live in *your*
  rule, not upstream in the API.
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
