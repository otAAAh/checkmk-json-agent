<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.19.0** is out. This one is about what the *display* tells you — the bar in the
service list, the scale of a graph, and the preview in the setup wizard. Three of the
four changes started with a forum question about dashboard widgets that would not plot.

## 📊 A bar on every service

Every service this plugin creates drew an **empty Perf-O-Meter**. The number was in the
summary, but the column that lets you read a list of fifty services at a glance — which
ones are near their limit — was blank for all of them.

It was blank for a reason worth stating: a JSON field has no natural maximum. "Queue
length" could be 5 or 5 million, and a bar drawn against a guessed ceiling is a bar that
lies. What a *monitored* field usually does have is a **critical level** — so that is
what the bar is scaled to:

```
JSON Queue depth      WARN - Value: 62          [██████████░░░]   ← CRIT at 80
JSON Requests/s       OK   - Value: 41.2/s      [████░░░░░░░░░]
```

Where the field has no levels, the bar falls back to an open range for its unit — still
showing magnitude, without pretending to a scale. A percentage is closed at 100, because
that one really is the maximum. The endpoint's own service gets one bar, on its response
time: the quantity that actually moves from check to check.

One service kind still has none: a service holding **several fields** (the *Report into
one service* option). Its metric names are built from the field names at runtime, so
there is nothing to declare in advance. That is the price of an open set of fields in one
service, and it is the only case.

## 📐 Telling Checkmk what "full" means

Checkmk only knows the values a metric has *already taken*. So a graph rescales itself to
whatever the last hour happened to contain, a **gauge dashboard widget has no dial to
draw**, and the bar above has nothing to fill against but the critical level.

For a JSON field that is often needless. A battery percentage runs 0 to 100. A queue with
a configured cap runs 0 to that cap. A disk pool's usage runs 0 to its size. You know the
range; there was no way to say so.

The new optional **Value range** per field is exactly that — lowest, highest, either end
on its own:

| | |
|---|---|
| Lowest possible value | `0` |
| Highest possible value | `100` |

It changes **nothing about the state**. It becomes the metric's boundaries, which is what
fixes the graph's scale, gives the gauge widget its dial, and lets the bar fill against
the real maximum instead of the critical level — on a value that runs 0 to 100, half full
should look half full even when CRIT sits at 80.

Give both ends and the bar uses the range; give one and it still fixes half the scale.
Leave it unset for a value with no natural limit, and nothing changes from today.

## 🔍 The wizard's preview is the agent's request now

The Explorer wizard (the optional 2.5+ companion package) shows you a real response
fetched from the site, and its whole value rests on that being *the request the agent will
make*. It was not. It honoured the URL, method, body, headers, authentication, TLS
verification and redirects — and silently dropped everything else the rule offers:

- a **custom CA bundle** was ignored, so a private-CA endpoint the agent verifies happily
  could only be previewed by turning verification off;
- a **client certificate** was ignored, so a mutual-TLS endpoint could not be previewed at
  all;
- the **HTTP proxy** was ignored, so an endpoint reachable only through a corporate egress
  proxy looked dead in the wizard while the check would have been fine.

All three are now used, the proxy resolved through Checkmk's own global-proxy setting, and
a proxy that cannot be resolved is *reported* rather than quietly bypassed. The OAuth 2.0
token exchange goes through the same proxy and TLS material as the API call, as the agent
already did.

### One of them was a credential leak

The same gap had a sharper edge. `requests` strips the `Authorization` header by itself
when a redirect crosses to another host, so the basic, bearer and OAuth 2.0 modes were
always safe. An **API key lives in a header the API names** (`X-API-Key`, `PRIVATE-TOKEN`,
…), which nothing was stripping — so previewing an endpoint that redirects off-host handed
the key to the redirect target in full.

The agent has guarded against exactly this from the start; the wizard's preview never got
the same guard. It does now.

Reaching it needed Setup access and an endpoint that redirects to another host, and **the
agent itself was never affected** — no monitoring request ever leaked a key. If you have
the Explorer package installed, update it; if you only have the agent package, there is
nothing to do. If you previewed an endpoint whose URL you do not fully control, rotating
that API key is the cautious move.

## 🔤 Fields whose key contains a dash

In the wizard, a field keyed `Content-Type`, `my key` or `x-request/id` was offered by the
picker, produced a correct path, and then previewed as **unresolvable** — no value, no
state, and a service name truncated at the dash (`meta.content-type` suggested "Type").
The check read it perfectly well the whole time; only the wizard's own preview could not.

The picker's path grammar had been narrowed when it was ported to Vue: it accepted only
letters, digits and underscores in a plain key, where the agent accepts anything that is
not `.`, `[` or `]`. The two now read the same grammar, and a shared set of test cases is
answered by *both* implementations so they cannot drift apart again.

## Upgrading

**Nothing to do, and nothing changes state.** The value range is optional and off by
default; the Perf-O-Meters are display only; the wizard fixes need no configuration.

One thing is worth a look after upgrading: a field that already has levels gets its bar
immediately, and a field with a known range is worth stating one for — that is the
difference between a gauge widget that draws and one that does not.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.19.0)
  — `json_api-0.19.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition

```
mkp add json_api-0.19.0.mkp
mkp enable json_api 0.19.0
```

On **Checkmk 2.5+** the optional companion package **Generic JSON API – Explorer (extra)**
(`json_api_explorer`) carries the preview fixes above and offers the new value range in the
wizard, with the same validation Setup applies.

As ever: if a service in your list is telling you something you cannot act on — or drawing
nothing where it should draw something — that is a bug worth reporting. Most of this
release came from someone doing exactly that.
