<!-- Paste this into a new topic on forum.checkmk.com (Discourse renders
     Markdown). One file per release, so a past announcement is never silently
     overwritten and the next one can be diffed against it. Drop the H1 if you
     post it as a reply in an existing thread rather than a new topic. -->

# Generic JSON API 0.12.0 — one Checkmk host per array element, TLS cert expiry, response caching

Version **0.12.0** of the [Generic JSON API](https://github.com/otAAAh/checkmk-json-agent)
special agent is out. It monitors any HTTP/JSON API from one Setup rule — no custom
Python, no MKP per integration.

This release is mostly about the things you asked for once the basics worked: turning a
JSON collection into real Checkmk **hosts**, watching the **certificate** of the API you
are already polling, and not hammering a **rate-limited** API to death.

## 🖧 One Checkmk host per array element

Until now a `[*]` wildcard gave you one *service* per element: `nodes[*].health` on a
fleet of 50 became one host carrying `JSON Health node-01` … `node-50`.

Now you can point a field at a **host name** inside each element instead, and every
element becomes a Checkmk host of its own:

```
nodes[*].health   +   "one host per element, named by: name"

  →  host web-1 :  JSON Health
     host web-2 :  JSON Health
     host web-3 :  JSON Health
```

Set the same field on several fields of that endpoint (`nodes[*].load`,
`nodes[*].version`) and they all land on those same hosts.

Why bother, when the services already told you the same thing? Because a host is a
first-class object in Checkmk and an array element is not. Each element now gets its own
downtimes, acknowledgements, contact and host groups, availability report, parent/child
relationships and place in the folder tree. That is the difference between monitoring an
API and monitoring the fleet the API describes.

⚠️ **One thing to know before you enable it:** this uses piggyback, so the standard
Checkmk rule applies — **data for a host that does not exist in Checkmk is stored and
never monitored.** No error, no warning, nothing on the polling host. Create the hosts
first (by hand, or with Dynamic host management), then run a discovery. If you switch it
on and see nothing at all, that is why.

## 🔐 TLS certificate expiry, for free

Every endpoint already gets a `JSON API <name>` service reporting the request itself —
HTTP status, response time, response size. It now also reports **how long the TLS
certificate is still valid**, with optional lower levels in days:

```
JSON API frontend    OK
  HTTP 200
  Response time: 31 ms
  Certificate expires in: 42 days
```

It is read from the connection the agent is already making, so there is no extra request
and no second `check_http` rule against the same URL with the TLS settings configured
twice and drifting apart.

Only for HTTPS endpoints with certificate verification enabled — otherwise nothing about
the certificate is reported. That is *absent*, not *expired*, and never alerts.

## 🐢 Rate-limited or expensive API? Cache it

Every check interval, on every host using the rule, the agent requested every endpoint.
For a cheap `/health` that is right. For an API with a request quota — or one rule shared
across fifty hosts — it is how monitoring becomes the outage it was supposed to detect.

Give an endpoint a TTL (**"Re-read at most every N seconds"**) and the agent reuses the
last response while it is younger than that.

It is deliberately strict about the honest cases:

- **It never hides a failure.** A failing request is *not* answered from an expired cache
  — the endpoint goes CRIT as usual. A cache makes an endpoint less frequently *polled*,
  not more *available*.
- **It never caches an error.** Only a response that parsed as JSON is stored.
- **It never reports a response time it did not measure.** While a cached body is served
  the service says `from cache (N old)` and records no response-time metric. So gaps in
  that graph are intervals where nothing was measured, not intervals where the API was
  slow.

## Also in this release

- Two endpoints can no longer be given the **same name** — the name is a service item, and
  a collision could only be resolved positionally, so reordering endpoints silently
  swapped two services' history.
- **Fixes:** a future timestamp with unit *seconds* crashed the check (negative duration);
  `count` over a `[*]` path now counts the same elements the other aggregations do; an
  API key in a URL's **query string** no longer lands in a service description; the
  wizard's review step no longer previews a pre-filter element count.
- New **[UPGRADING.md](https://github.com/otAAAh/checkmk-json-agent/blob/main/UPGRADING.md)**
  — the operator-facing notes (service renames, new services appearing, changed results)
  now travel with each release, because a generated changelog can only say what changed in
  the code, not what it means for a running site.

## ⚠️ Two behaviour changes worth reading

Both are in the upgrade notes, and both are one-time:

1. **Endpoints without a name** take their service item from the URL, now **without the
   query string** — so a credential passed as `?api_key=…` no longer ends up in a service
   description (which reaches notifications, availability reports and the metric paths).
   Those services are renamed once. Naming your endpoints avoids this entirely.
2. **`count` over a `[*]` path that names a field** now counts only the elements that
   actually have that field, matching what `sum`/`avg`/`min`/`max` always did. If the
   field is sometimes absent the number drops; if no element has it (usually a typo in the
   path) the service goes UNKNOWN instead of silently reporting the element count.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.12.0)
  — `json_api-0.12.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition

```
mkp add json_api-0.12.0.mkp
mkp enable json_api 0.12.0
```

On **Checkmk 2.5+** there is also the optional companion package **Generic JSON API –
Explorer (extra)** (`json_api_explorer`): a guided in-site wizard under *Setup → Quick
setup* that fetches your API's real response, lets you click the fields to monitor, and
writes the rule for you. It covers the new per-element-hosts option too. There is a
dependency-free browser version in the repo (`explorer/index.html`) if you would rather
not install anything.

Feedback and bug reports very welcome — especially on the per-element hosts, which is the
biggest change in how this plugin can be used.
