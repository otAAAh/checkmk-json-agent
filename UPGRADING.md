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

## [0.21.0]

### Counters sharing a service each keep their own reading

Only affects a service built from **several fields** (*Report in a shared
service named*) where more than one of them is read as a **counter** — which
includes a `[*]` wildcard reporting into a shared service, since that fans out
into one line per element.

The previous reading a rate is computed from was stored per *service*, so every
counter line of one service read and wrote the same reading. Each line was
differenced against whichever line happened to be stored last: one reported a
rate that was arithmetic over two unrelated counters, and the next saw its
counter "go backwards" and reported no rate at all, keeping its previous state —
alternating on every check. Every line now keeps a reading of its own.

**Effect:** the rates on such a service become correct. They will also *change*,
because the old ones were wrong — check any levels you tuned against them, since
a threshold picked to fit a nonsense rate now fires differently. The first check
after the upgrade cannot compute a rate for these lines (there is no per-line
reading yet), so the service keeps its previous state for one interval. A
counter in a service of its own is untouched, history included.

### Inventory tables are keyed by an `element` column

Only affects fields written into the **HW/SW inventory** from a path with a `[*]`
wildcard — the ones that become one table row per element.

The rows were keyed by a column called `name`, which is also the most natural
column such a table could have (`nodes[*].name`, `pods[*].metadata.name`).
Checkmk refuses a column that is also the key column, and it refuses it while
writing the tree — so configuring it did not produce a warning, it failed the
**whole host's** inventory, every field of every rule with it. The key column is
now called `element`, and `element` is in turn rejected as an attribute name for
a wildcard field (Setup says so when you save the rule).

**Effect:** on the next inventory run the affected tables are rebuilt under the
new key: the old rows disappear and equivalent rows appear, which the inventory
history records as a change. No data is lost and nothing needs re-configuring —
but a view, report or *Search hosts by inventory data* query that names the
`name` column of one of these tables has to name `element` instead. A field whose
path has no wildcard is a plain attribute of its node, has no key column, and is
not affected at all.

### String matching alongside levels now says it is not applied

A numeric value with **both** levels and string matching configured has always
been decided by the levels alone — the matching never ran. The service Details
list the pattern (they list everything the extraction was configured with), so
they read as though it applied. They now carry one more line saying it does not.

**Effect:** Details only. No state, metric or summary changes.

## [0.21.0]

### A redirected endpoint's pagination now follows the redirect

Only affects endpoints with **Follow pagination** turned on whose URL is
answered by a **redirect** — an `http` → `https` hop, a `/api` → `/api/v2`
move, or a regional host.

Next-page links were resolved against the URL written in the rule instead of
the one the first page was actually served from. A relative link (`?page=2`)
therefore pointed at the pre-redirect path, which answered 404 and failed the
**whole endpoint** — every one of its services UNKNOWN, on an API that was
working. An absolute link to the redirect's own host was refused as "another
host", leaving the collection at its first page with the endpoint service
reporting it as incomplete. Both now resolve against the URL the pages really
come from.

This widens nothing: the redirect was followed because the rule allows
redirects, and the endpoint's credentials already travelled that hop. A link to
a host that is neither is refused exactly as before.

**Effect:** such an endpoint starts working — services that sat UNKNOWN report
values again, and a collection that stopped at page one is now read whole, so
counts and aggregations over it change to their true values. Check any levels
tuned while the count was truncated. Endpoints that are not redirected, or do
not follow pagination, are unaffected.

## [0.20.0]

### Fields in a shared service that collided now each keep their own metric

Only affects a service built from **several fields** (*Report in a shared
service named*) where two of the field names differ only in punctuation —
`Root used` and `Root-used`, say, or `CPU/core` and `CPU core`.

Each line's metric is named after the line, and the name is built by folding
every run of non-alphanumeric characters to `_`. Names that differed only there
produced the *same* metric name, so the service emitted it twice: Checkmk kept
one of the two values and dropped the other, chosen by iteration order, with
nothing in the UI saying so. The second and any further colliding field now get
`_2`, `_3`, … appended instead.

**Effect:** the field that used to win keeps its metric and its full history —
nothing is renamed. The field that used to lose starts recording under the new
suffixed name, so its graph begins at the upgrade rather than showing the other
field's data. Check any graph or dashboard widget over such a service: a line
that looked wrong there was the collision, and it is now two lines.

Fields whose names already differed by more than punctuation are unaffected, as
is every service built from a single field.

### New: a field can state its own metric name

*Metric name* (optional, per field) sets the name Checkmk stores the metric
under, instead of the one the plugin derives. Nothing changes for a field that
leaves it unset.

It exists for the dashboard widgets that are bound to one metric **by name** —
*Gauge*, *Single metric* and *Bar chart*. Their metric dropdown only lists a
service's real metric names once the widget is filtered to a host **and** a
service; configured the other way round it offers the plugin's declared names
(`json_api_value`, `json_api_bytes`, …), which a shared service never emits, and
the widget then stays blank. Naming the metric in the rule gives you a name to
point the widget at directly.

Setting it on a field that already has history starts a new history under the
new name; the old one is not migrated. It also costs the service its
**Perf-O-Meter**: every bar this plugin draws is declared against one of its own
metrics, so a name of your own matches none and the service list shows an empty
bar. A field that needs its bar is better off keeping the derived name and
filtering the dashboard widget to a host and a service instead.

The plugin's own metric names (`json_api_value`, `json_api_bytes`, `json_api_age`
and the rest) are **reserved**. A field given one of those would inherit that
metric's unit, colour and Perf-O-Meter — a percentage named `json_api_bytes`
renders as `11.5 B` — so Setup rejects it.

### Setup now rejects two fields of one shared service that would share a metric

**This can reject a rule that saved before the upgrade**, so it is worth knowing
before you next open one.

The fix above stops the two fields *losing* a value, but it can only
disambiguate them positionally — the first gets `…_root_used`, the second
`…_root_used_2`, in rule order. Reorder the fields and the suffix moves to the
other one, so two metrics silently trade histories, graphs and any dashboard
widget bound to the name. That is the same hazard as duplicate endpoint names
(#116), one level down, and the same answer: catch it at config time, where both
fields are in front of you.

Saving an endpoint whose shared service holds two fields that resolve to one
metric name now fails, naming the fields, the service and the metric. Only that
exact case is rejected:

- fields in **different** shared services never collide — metric names only have
  to be unique within a service;
- fields with the **same slug but different units** are two names
  (`json_api_bytes_root_used` vs `json_api_count_root_used`) and are accepted;
- **ungrouped** fields each own their service and cannot collide.

**Effect:** if you hit it, rename one of the two fields so they differ by more
than punctuation, or set *Metric name* on all but one. Nothing changes for
rules that do not have such a pair, and the runtime disambiguation stays in
place as a backstop for hand-written `--endpoint` blobs, which never pass
through Setup.

## [0.17.0]

### A prefixed endpoint's own service is renamed to sort with its group

Only affects endpoints with **Prefix the field service names with the endpoint
name** turned on — the option added in 0.16.0. Nothing else changes.

Such an endpoint's fields already read `JSON <name> Status`. Its own service kept
the old shape, `JSON API <name>`, which sorted it away from every service it
describes: all the `JSON API ...` services clustered together, and none of them
sat with its own group. It is now `JSON <name> API`.

**Effect:** on the next discovery, `JSON API <name>` goes stale for those
endpoints and `JSON <name> API` appears. It is the same check with the same
**Generic JSON API endpoint** check-parameters rule, but Checkmk needs a second
check plugin to render a different service name, so the two are discovered
separately — which is why this is a rename rather than a relabel. Re-run a
service discovery on the affected hosts, and move any check-parameters rule or
notification condition that matched the old name.

Endpoints without the prefix keep `JSON API <name>` exactly as before, and an
endpoint always has exactly one of the two services — never both.

## [0.14.0]

### A timestamp field with format `auto` now reads HTTP-dates

`Interpret the value as → a timestamp` with format `auto` previously accepted a
number (epoch seconds or milliseconds) or an ISO 8601 string, and reported
anything else as `Not a valid timestamp` (UNKNOWN). It now also accepts an
**HTTP-date** — `Wed, 21 Oct 2015 07:28:00 GMT` — because that is what
`Last-Modified`, `Expires` and a date-form `Retry-After` contain, and those only
became reachable in this release (see the `@header.` paths below).

**Effect:** a field that was stuck at UNKNOWN because its value was an HTTP-date
now resolves to a real age. If that field has upper levels configured, it can go
WARN or CRIT on the next check where it previously sat UNKNOWN. This only affects
fields explicitly configured as timestamps whose value is an HTTP-date; the
explicit `ISO 8601` format is unchanged and stays strict.

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
