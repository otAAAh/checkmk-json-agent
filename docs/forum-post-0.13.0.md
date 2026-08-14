<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.13.0** is out. Four additions, and the first one changes what this plugin is
allowed to do with a field — so it leads, because it is also the one most likely to
have you asking where your service went.

## 📋 A field can be a *fact* instead of a service

Plenty of what an API tells you about itself is not a state at all: a version, a build,
a region, a licence tier, the list of nodes in a cluster. Until now the only thing this
plugin could do with those was make a service that is OK forever — a service slot and a
check every interval to report something that changes twice a year.

Point such a field at an **inventory tree node** instead:

```
version   +   "write into the HW/SW inventory: software.applications.json_api"

  →  Host → Inventory → Software → Applications → json_api
       version   4.2.1
```

A `[*]` wildcard becomes a **table**, one row per element, keyed by the element's own
label — so `nodes[*].version` and `nodes[*].role` fill in two columns of the same rows:

```
nodes[*].version  →  name    version   role
                     web-1   4.2.1     leader
                     web-2   4.2.1     follower
```

The reason to bother is the thing services cannot do: the inventory is searchable
**across hosts**. *"Which of our 300 API hosts still run a version below 4.2?"* is one
query against the inventory and no query at all against a service summary. It also keeps
its own change history.

⚠️ **The one thing to know:** an inventory field creates **no service** — that is the
point of it, but it means turning it on for a field that *already* is a service removes
that service on the next discovery, and its metric history goes with it. If you want
both, tick **"Also create a service for this field"** in the same edit. Inventory also
runs on its own, slower schedule, so a newly configured field turns up at the next
inventory run, not the next check.

## 🔑 API keys from the password store

Basic auth and bearer tokens always went through the Checkmk password store. A plain
**API key** — `X-API-Key`, `PRIVATE-TOKEN`, `apikey`, whatever the vendor calls it — did
not, because there was no auth choice for it. The only way was *Additional request
headers*, which stores the key in clear text in the rule, puts it on the agent's command
line, and prints it in `--debug` output.

There are now two proper choices: **API key in a request header** (you name the header)
and **API key in a query parameter**. Both take the key from the password store, so it
is rotated in one place and never written into the configuration.

The query-parameter one is redacted from every URL the agent reports — the endpoint
service's final URL, request errors, debug output. It still travels inside the URL to
the server, so prefer a header where the API offers one.

## 🔁 Retry a request instead of alerting on a blip

A connection reset, a DNS hiccup, or a 502 from an ingress during a rolling restart used
to be an unreachable endpoint: CRIT, notification, RECOVERY a minute later, nothing
learned.

An endpoint can now retry (1–5 times, with a doubling backoff). What gets retried is
decided per failure rather than by a blanket count:

| | |
|---|---|
| connection error, timeout, HTTP 429/5xx | retried |
| HTTP 4xx, a body that is not JSON, an oversized response | never — they answer the same however often you ask |

It does not hide what it absorbs: the endpoint service reports **"succeeded after N
retries"**, and you can set that to WARN. A retry policy that quietly turned a degrading
API into a permanently green service would be worse than the noise it removes.

Off by default. Worst case is `(1 + retries) × timeout` plus the waits, and total backoff
is capped at 30 s.

## 💬 Show the reason next to the value

Health endpoints rarely put the whole story in one field:

```json
{"status": "DEGRADED", "message": "replica lag 42s", "leader": "db-3"}
```

Monitoring `status` gave you `Value: DEGRADED` and nothing else; the explanation the API
already returned needed a second service, which then alerted separately and arrived in
notifications as an unrelated line.

A field now takes an optional **summary text** with `{path}` placeholders:

```
{message} (leader {leader})

  →  JSON DB status   CRIT   Value: DEGRADED, replica lag 42s (leader db-3)
```

Paths resolve within the current `[*]` element, so the text describes *that* node. It is
presentation only — it never changes the state, the levels or the metric.

## Upgrading

Both notes are in
[UPGRADING.md](https://github.com/otAAAh/checkmk-json-agent/blob/main/UPGRADING.md) and
both are one-time:

1. **Sending an existing field to the inventory takes its service away** (above).
2. **Cached endpoints refetch once.** The response cache now keys entries on the
   credential as well as the URL — without it, several rules polling the same
   multi-tenant URL with a different API key each shared one cache entry. One extra
   request per endpoint, once.

Nothing else changes for an existing rule: all four additions are opt-in.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.13.0)
  — `json_api-0.13.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition

```
mkp add json_api-0.13.0.mkp
mkp enable json_api 0.13.0
```

On **Checkmk 2.5+** the optional companion package **Generic JSON API – Explorer (extra)**
(`json_api_explorer`) is updated too: its review step now tells you which fields will
become inventory entries rather than services, and counts them separately, so the
"where did my service go" question is answered before you create the rule. The
dependency-free browser version in the repo (`explorer/index.html`) covers all four
additions as well.

As ever, feedback and bug reports welcome — particularly on the inventory side, which is
the biggest change in what a field can be.
