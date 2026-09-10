<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.16.0** is out. Both features in it came straight off the
[Ideas board](https://github.com/otAAAh/checkmk-json-agent/discussions/categories/ideas),
and both are about the same thing from two directions: making a service tell you what
it is actually about.

## 🏷️ The endpoint's name in its service names

Two endpoints of the same shape — a `/health` on each of two applications — extract the
same fields, so until now they produced the same service names and the second copy got a
` (2)` suffix:

```
JSON API my_app1_health
JSON API my_app2_health
JSON STATUS
JSON STATUS (2)
JSON TIMESTAMP
JSON TIMESTAMP (2)
```

Nothing in `JSON STATUS (2)` says which application it belongs to, and *which* endpoint
gets the suffix depends on the order of the endpoints in the rule. Naming the endpoints
did not help: the name only reached the endpoint's own `JSON API <name>` service.

Give each endpoint a **name** and tick the new **Prefix the field service names with the
endpoint name**, and that endpoint's services are named after it:

```
JSON API my_app1_health
JSON my_app1_health STATUS
JSON my_app1_health TIMESTAMP
JSON API my_app2_health
JSON my_app2_health STATUS
JSON my_app2_health TIMESTAMP
```

Every service of one application now shares a prefix — which sorts them together in the
service list and, more usefully, makes them addressable **as a group** in service rules
and notification conditions.

Three things worth knowing:

- It is **per endpoint**, so one rule can prefix the two endpoints that collide and leave
  a third one alone.
- The leading `JSON ` stays. It is the check plugin's own service name and is the same
  for every service the plugin creates; dropping it would rename every service in every
  existing installation, which is not a thing to do to people for the sake of tidiness.
- The **URL is never used** as a prefix, even when the endpoint has no name. A URL in a
  service description travels much further than the details do — notifications,
  availability reports, the metric paths on disk — and a URL with a query string in it can
  carry a credential. So Setup asks for a name instead of quietly doing nothing.

## 🔍 The raw response, in the check details

A field service's details name the **source URL** the value came from. That link is often
useless exactly when you need it: the API may sit behind a firewall or in another network,
so the browser you are reading the service in cannot open it — and even where it can, it
shows the API as it is *now*, not as it was when the check ran and went CRIT.

Tick **Report the raw response** on an endpoint and the response itself lands in the
details of that endpoint's own `JSON API <name>` service:

```
HTTP 403
URL: https://app.example.com/api/v1/health
Response headers:
  content-type: application/json
  x-request-id: 7f3c1a
Response body (first 2048 of 8412 bytes):
{"error": "tenant disabled", "since": "2026-09-08T11:20:00Z"}
```

That example is the point of the feature. A **rejected** response is reported too — an
unexpected HTTP status, or a body that is not JSON — and that is where an API explains
itself. `HTTP 403` on its own sends you to the credentials; `tenant disabled` sends you to
the right place. That body was previously never read at all, and it still is not unless
you ask for it; when you do, reading stops at your byte budget rather than buffering a
50 MiB error page in order to show you 2 KB of it.

Two settings: how much of the body to report (2048 bytes by default, 64 KiB maximum) and
whether the response headers come along (they do, by default — they are small, and a rate
limit or a cache directive is announced in them).

**Credentials are stripped before anything is reported.** `Set-Cookie` — a live session
token — and any authorization header are masked, and the endpoint's own secret is removed
wherever it appears in the body or in a header value, so an API that echoes back the key
it was given cannot leak it into your monitoring history.

**Everything else is reported verbatim**, and that is the part to think about before
switching it on: a service's details are stored with every check result and travel into
notifications. If the response carries personal data, leave this off.

One limitation, stated plainly: the details always describe the **most recent** check.
While the service stays CRIT that *is* the failing response, but once it recovers, the
failure's body is gone. Archiving every raw response to disk — the RobotMK-style half of
[the original idea](https://github.com/otAAAh/checkmk-json-agent/discussions/168) — needs
a retention policy and a way to serve the files, and is a separate piece of work.

## Upgrading

**Nothing to do.** Both options are off by default, no existing rule is touched, and no
service changes state on upgrade.

One thing to do *deliberately* rather than by accident: turning the service-name prefix on
**renames** that endpoint's services. The old ones go stale and the renamed ones have to be
discovered, so re-run a service discovery on the affected hosts afterwards, and expect to
move any check-parameters rule that matched the old names.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.16.0)
  — `json_api-0.16.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition

```
mkp add json_api-0.16.0.mkp
mkp enable json_api 0.16.0
```

On **Checkmk 2.5+** the optional companion package **Generic JSON API – Explorer (extra)**
(`json_api_explorer`) is updated too; both new options appear in the wizard's connection
step, with the same validation Setup applies.

Keep the ideas coming — this release is two of them, and the Ideas board is where the next
one comes from.
