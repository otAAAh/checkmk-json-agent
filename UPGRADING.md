<!-- SPDX-License-Identifier: GPL-2.0-only -->
# Upgrade notes

What an operator needs to know *before* upgrading — service renames, new
services appearing on the next discovery, changed check results.

`CHANGELOG.md` is generated from the git history, so it can only say what
changed in the code. This file is hand-written and says what that means for a
running site. Only versions that need a note appear here; a version missing from
this list needs none.

`scripts/gen_changelog.py --version X.Y.Z` appends the matching section to the
GitHub Release body, so these notes travel with the release people actually read.

## [0.13.0]

### Sending a field to the inventory takes its service away

Purely additive — nothing changes unless you set the new **Write into the HW/SW
inventory** on a field. But it defaults to creating **no service**, which is the
point of the feature: a version or a licence tier is a fact, not a state, and it
should not cost a service slot and a check interval to report something that
changes twice a year.

**Effect:** turning it on for a field that is *already* a service removes that
service on the next discovery, and its metric history goes with it. If you want
both — a value in the tree *and* an alert on it — tick **Also create a service
for this field** in the same edit.

Inventory also runs on its own, slower schedule, so a newly configured field
appears in the tree at the next inventory run rather than at the next check.

### Cached endpoints refetch once after upgrading

The response cache now keys entries on the credential as well as the URL, method,
body, headers and TLS settings. Without that, several rules polling the same
multi-tenant URL with a different API key each shared one cache entry and served
each other's data for the whole TTL. Only a hash of the credential is used; the
credential itself still reaches neither the agent's command line nor the disk.

**Effect:** the identity of every *authenticated* cached endpoint changes, so the
first check after upgrading fetches fresh instead of serving a cache hit. One
extra request per endpoint, once. The superseded files are pruned automatically.

## [0.12.0]

### The response cache is opt-in, and deliberately fails loudly

Nothing changes unless you set the new per-endpoint **Re-read at most every
(seconds)**. When you do, two behaviours are worth knowing before you rely on it:

- **A failing request is never answered from an expired cache.** The endpoint goes
  CRIT as it would without caching. This is deliberate — monitoring that serves
  stale data through an outage is worse than useless — but it does mean a cache
  does not make an endpoint more available, only less frequently polled.
- **A cached serve records no response-time metric.** The `JSON API <name>`
  service reports `from cache (N old)` instead. So a graph of an endpoint with a
  long TTL will have gaps: those are intervals where nothing was measured, not
  intervals where the API was slow.

### Piggyback hosts must exist before they are monitored

Purely additive — nothing changes unless you set the new **Create one host per
element, named by this field** on a `[*]` field. But when you do, be aware of the
standard Checkmk piggyback rule: **data for a host that does not exist in Checkmk
is stored and never monitored.** No error, no service, no warning on the polling
host — it simply sits in the piggyback directory.

**Effect:** create the hosts first (by hand, or with Dynamic host management on
Enterprise/Cloud), then run a discovery. If you enable the option and see nothing,
this is why.

Switching an existing `[*]` field over to per-element hosts also *moves* its
services: the label-suffixed services on the polling host (`JSON Node web-1`)
disappear and are replaced by plainly-named ones on the new hosts (`JSON Node` on
host `web-1`), so their metric history restarts.

### The endpoint service item drops the URL's query string

Endpoints **without** a configured **Name** take their service item from the URL,
which is now used without its query string (`?api_key=…`) — a credential passed
as a query parameter no longer lands in a service description, which reaches
notifications, availability reports and the metric paths on disk. The full URL
is unchanged in the service details.

**Effect:** the `JSON API <url>` service of every *unnamed* endpoint whose URL has
a query string is renamed once, on the next discovery, and its metric history
restarts. Named endpoints are unaffected. Give your endpoints a **Name** to
insulate them from this and from any future change to the fallback.

### `count` over a `[*]` path counts only the elements that have the field

An aggregation over a wildcard path that names a field — `nodes[*].load` — now
counts only the elements that actually *have* that field, matching what
`sum`/`avg`/`min`/`max` over the same path have always done.

**Effect, for rules combining `count` with a `[*]` path:**

- where the field is *sometimes* absent, the reported number drops to the number
  of elements that have it;
- where the field is *never* present (typically a typo in the path), the service
  goes UNKNOWN with `path not found in any element` instead of silently
  reporting the element count.

A wildcard-free path (`aggregate: count` on `jobs`) names no field and is
unchanged. A condition that matches nothing still counts `0`.

## [0.11.0]

### Every endpoint gains a service of its own

The new `json_api_endpoint` check discovers one **`JSON API <name>`** service per
configured endpoint, reporting the request itself (HTTP status, response time,
response size). It needs no field configuration.

**Effect:** the first service discovery after upgrading turns up one new
undecided service per endpoint, on every host using this plugin. That is the
intended design, not a bug. If you do not want them, remove them the standard
way with a **Disabled services** rule.

### Name your endpoints *before* that first discovery

An endpoint's service item is its **Name**, falling back to its URL. Adding a
Name to an endpoint *later* therefore changes the item, and Checkmk treats a
changed item as a different service: it is re-discovered under the new
description and the old one's metric history is orphaned.

**Effect:** set the Name on every endpoint in the same edit as the upgrade, or
accept a one-time rename whenever you get round to it.
