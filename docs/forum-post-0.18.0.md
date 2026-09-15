<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.18.0** is out, with three changes that have one thing in common: each of them is
about a service that was *confidently wrong* or *unhelpfully silent*, rather than a
feature that was missing.

## 📄 The JSON now reaches the service that alerts

0.16.0 let an endpoint report its raw response — and put it on the endpoint's **own**
service. Which is the one service that stays OK. The service that goes CRIT, and
therefore the one that **notifies**, is a field service, and its Details said only which
path had been read:

```
Status: DOWN (expected to match 'UP')
JSON path: services[*].status
Source: https://app.example.com/api/v1/health
```

So the notification described the failure without the answer that explains it, while the
JSON sat on a service that told nobody. This plugin is in that position more than most:
the monitored thing is an HTTP endpoint the person on call often *cannot reach* — wrong
network, no credentials — and the check had the answer in its hand at the moment it
alerted.

The new per-endpoint **Report the JSON context in the field services** puts it where the
alert is:

```
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

By default it is **just the element the value was read from**, with its sibling fields
and nothing else — for a `[*]` path that element, otherwise the object holding the value
— so a fleet of 300 nodes does not put 300 nodes into every one of 300 services. *The
whole response body* is the other choice, for the cases where the answer is elsewhere in
the document. It reaches notifications as `$LONGSERVICEOUTPUT$`, which is the point, and
it is reported for a path that did **not** resolve as well: "the path is not in the
response" is exactly when *what did the API return?* is the question.

Keep the byte budget small (1024 by default): unlike the raw response, this text is
stored with every check result of *every* field service of that endpoint. Credentials are
stripped exactly as they are for the raw response — and, exactly as there, service
Details travel into notifications, so leave it off for a response carrying personal data.

## 🧩 Following the API's pagination

Most REST APIs answer a collection **one page at a time**. The agent made one request per
endpoint, so everything built from such a collection described the *first page*:

```
JSON Queued jobs    OK - Value: 25          ← the queue is 812 long
JSON Node web-1 … JSON Node web-25          ← there are 300 nodes
```

Nothing in either service said so. That is worse than an error: the answer was not
missing, it was **wrong**, and it stayed wrong while the graph looked flat and the service
stayed green. A `count` you cannot trust is a count you have to verify by hand, which is
the one thing monitoring is for.

Turn on the new per-endpoint **Follow pagination** and the agent reads the rest. It needs
two things, because APIs disagree only about where they put them:

- **where the next page's URL is** — a field in the body (`links.next`,
  `meta.next_page_url`), or the RFC 8288 **`Link` header**'s `rel="next"`, the convention
  the GitHub / GitLab / Jenkins style APIs use. A page carrying no next link is the last
  one: that is how pagination ends.
- **which collection to merge** — `data.items`, or `$` where the response *is* the array.

Each page's collection is appended to the first page's, and the rest of the document
stays as the first page sent it — so a `total` next to the collection still resolves and
**every path in your rule is unchanged**. The wildcards, aggregations, conditions and host
labels then all see the whole thing. Every page is the first page's request at a different
URL (same session, method, headers, authentication, timeout), so a cursor only the API
understands needs no configuration here.

Two links are **refused** rather than followed: one pointing at **another host** — the
response body must not decide where your Checkmk server sends an authenticated request,
which is the same SSRF shape the *Follow HTTP redirects* switch closes — and one already
fetched, which is an API pointing at itself. Both stop pagination with a reason instead of
failing the endpoint, so what *was* read keeps monitoring the API.

And nothing is hidden. The endpoint's own service reports what it took, and says plainly
when the collection is short:

```
HTTP 200
Pages read: 3, 137 elements
```

```
Collection incomplete: the page limit (10) was reached
```

That second one is a **WARN** by default (configurable, and worth lowering to OK where
reading the first N pages is deliberate), because a collection quietly missing its tail is
precisely what following the pages was meant to prevent. A page that cannot be read *at
all* fails the endpoint instead: half a collection looks exactly like a shrinking one.

**Mind the cost.** Each page is a request made while the check runs, and Checkmk kills a
special agent that overruns — so the page cap (1–100, default 10) is required, an optional
element cap can stop earlier, and for a collection that does not change every check
interval this pairs well with a cache TTL. Where an API already returns a `total`, a plain
field on it is still cheaper than walking the pages: this is for the numbers that are
*not* in the document — a filtered count, a sum, one service per element.

## 🏷️ Classifying the host from what it runs

Host labels mirrored a field: `version` became `json_api/version`. The other common need
is a **conclusion drawn from a collection** — given

```json
{"services": [{"name": "MyAppWeb", "state": "running"}, {"name": "postgres", "state": "running"}]}
```

what you want is not one label per service but one label saying *this host runs MyApp*, so
that thresholds, contact groups, folder rules, extra checks and views attach themselves.

A **Host label** can now be that conclusion: point it at `services[*]`, add the condition
`name` *matches regex* `^MyApp.*`, and give it the key `MyApp` with the **literal value**
`yes`. The host gets exactly one label, `json_api/MyApp:yes` — and nothing at all when no
element matches. The literal value is what collapses the collection: it replaces both the
per-element value and the `<key>/<element>` suffix, so the key is unique on its own.

Leave the literal value out and the condition simply narrows the per-element labels, which
is the right shape when you want to see *which* elements matched rather than *that* any
did. The condition works without a wildcard too — `version` with `mode` *equals*
`production` labels only the production hosts — and a label needs no path at all when the
condition and the value describe it entirely. The same two fields exist on the labels of
the hosts a `[*]` field creates, where the scope is the element that becomes the host.

Labels are set at **discovery**, so re-discovery is still what updates them.

## Upgrading

**Nothing to do.** All three are opt-in and off by default: no existing rule changes, no
service is renamed, and no service changes state because of this release. The one thing
worth knowing is what you are turning on when you do:

- the JSON context is stored with every check result of every field service of that
  endpoint, so keep its byte budget small;
- following pagination costs one request per page, inside the check.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.18.0)
  — `json_api-0.18.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition

```
mkp add json_api-0.18.0.mkp
mkp enable json_api 0.18.0
```

On **Checkmk 2.5+** the optional companion package **Generic JSON API – Explorer (extra)**
(`json_api_explorer`) is updated too: all three settings appear in the wizard with the same
validation Setup applies. One honest limitation there — the wizard's review step resolves
against the single response it fetched, so with pagination on, the element counts it
previews describe the first page. It says so; the site itself merges the pages.

As ever: if a service in your list is telling you something you cannot act on, that is a
bug worth reporting, not a quirk to live with. Two of the three changes above started
exactly that way.
