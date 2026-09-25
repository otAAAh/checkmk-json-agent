<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.22.0** is out. There are two new options this time: pagination for APIs that
have no next-page link, and eight more units. The wizard also warns you before a
`[*]` field gives two services the same name. Nothing changes for an existing
rule.

## 📚 Pagination without a next-page link

0.18.0 taught the agent to follow an API's pagination, but only when the API
**tells** it where the next page is: a field in the body, or a `Link` header.
Plenty of APIs don't. They take the position as a query parameter and leave the
counting to the client. For those, a count or a `[*]` over the collection still
described the **first page only**, and nothing said so.

*Follow pagination* now has two more ways to find the next page:

- **Page number:** `?page=1`, `?page=2`, … You choose the parameter and the
  first page's number (1, or 0 for an API that counts from zero).
- **Offset:** `?offset=0`, `?offset=25`, … The offset advances by the number of
  elements actually received. The first element's offset can be set too, for
  APIs that count from 1, such as SCIM's `startIndex`.

Both can set a **page size** (`&limit=25`, `&per_page=50`), and both can read the
collection's **total** from the body.

The hard part is knowing when to stop, because there is no missing link to say
so. The agent stops at:

- an empty page;
- a page shorter than the ones before it;
- the total, when you point it at one;
- an API that answers the request past the end with a **404** or with **no
  collection**. Django REST Framework's `Invalid page.` is the classic case.

An API that caps the page size below what you asked for (`limit=500` answered
with 100) is still read to the end, not cut off after page one. An API that
ignores the parameter altogether, which is usually a misspelt name, sends back
the same page again. The agent notices, keeps the one real page, and the
endpoint service says the collection is **incomplete**.

The existing page and element caps, the host check on links and the
*incomplete → WARN* all apply unchanged.

## 📏 Eight more units

A field could be a count, bytes, seconds or a percentage. Anything else was a
bare number in the summary, the levels line and the graph, and the Gauge
dashboard widget had no unit to draw. There are now eight more:

| Unit | Renders as |
|---|---|
| Per second | `20/s` |
| Bytes per second | `1.50 MiB/s` |
| Bits per second | `250.00 Mbit/s` |
| Degrees Celsius | `41.2 °C` |
| Volts / Amperes / Watts / Hertz | `230.00 V`, `20.00 mA`, `1.50 kW`, `50.00 Hz` |

Values are **never converted**. Levels and the value range stay in the unit the
API reports. For a unit the API doesn't use as it stands, go through the
transform: a latency in milliseconds is *Seconds* with `value / 1000`.

Values the API already reports per second (`requests_per_second`,
`bytes_per_second`) record into the same metric a counter's rate does. They are
the same quantity, and they graph the same way.

A counter's rate is its unit per second, so Setup now refuses a **counter in a
rate or a measurement unit**, where "watts per second" would mean nothing. Every
existing combination (count, bytes, seconds, percent) is unaffected.

°C, volts and amperes can be negative (a freezer, a discharging battery), so
their Perf-O-Meter no longer starts at zero, and a reading of −18 °C still draws
a bar.

## 🏷️ A warning before two services get the same name

A `[*]` field can name each element's service after one of its fields, e.g.
`nodes[*].health` named by `name`. If two elements have the same name, or none,
the agent has always told the services apart at runtime by appending the
element's position: `web [0]`, `web [2]`. That works, but a position is not an
identity. When the API returns its elements in a different order, those two
services swap their history, their state and their downtimes.

The **Explorer** and the **wizard (2.5+)** now say so while you pick the field.
The warning covers:

- names used by more than one element, listing the elements and the names the
  site will give them;
- elements with no name at all;
- a name field that holds an object or a list.

With a host per element, the warning only concerns the elements whose host field
does not resolve, because those stay on the polling host. A 64-bit ID that the
browser cannot compare exactly is noted rather than reported as a clash.

The wizard's review step also used to ignore the name field altogether and name
every element by its position. It now previews the names the site creates.

## Smaller fixes

- **Wizard preview with counted pagination.** The preview fetched the rule's URL
  without the page parameters, so it showed the API's default page size. It now
  requests the first page the way the agent does.

## Upgrading

**No configuration change**, and nothing to check. Everything above is new
behaviour you opt into, or a warning in the Explorer and the wizard.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.22.0)
  — `json_api-0.22.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition, verified on **3.0**

```
mkp add json_api-0.22.0.mkp
mkp enable json_api 0.22.0
```

On **Checkmk 2.5+**, update the optional companion package **Generic JSON API –
Explorer (extra)** (`json_api_explorer`) as well, to get the name warnings and
the corrected pagination preview.

If your API pages in a way none of these modes covers (a cursor token in the
body is the obvious one), please describe it in an issue, with a redacted sample
of two consecutive pages.
