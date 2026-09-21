<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.20.0** is out. It finishes the dashboard-widget thread that 0.19.0 started — and
along the way it turned up a bug that was quietly throwing away one of your values.

## ⚠️ Two fields, one metric — and one of them was being dropped

This is the part to read even if you use nothing else here.

A service can hold **several fields** (the *Report in a shared service named* option).
Each line's metric is named after the line, and that name is built by folding every run
of punctuation to `_`. So these two:

| Field name | Metric |
|---|---|
| `Root used` | `json_api_bytes_root_used` |
| `Root-used` | `json_api_bytes_root_used` |

…were **the same metric**. Checkmk kept one of the two values and discarded the other,
picked by whichever came first, with nothing in the UI saying so. The service looked
fine. One of its graphs was simply somebody else's number.

Now each field gets its own: the second and any further collision take `_2`, `_3`, and
so on. **The field that used to win keeps its metric and its full history — nothing is
renamed.** The field that used to lose starts recording under the new suffixed name, so
its graph begins at the upgrade rather than continuing to show the other field's data.

**Worth checking after you upgrade:** any shared service whose field names differ only
in punctuation. If a graph there ever looked wrong, this was why, and it is now two
lines.

Fields whose names already differed by more than punctuation are unaffected, and so is
every service built from a single field — which is most of them.

### Setup now refuses that pair up front

The fix stops a value being *lost*, but it can only tell the two apart by their order in
the rule — so reordering the fields would move the suffix to the other one and two
metrics would silently trade histories. That is a worse bug than the one being fixed, so
Setup now **rejects the pair at save time**, naming both fields, the service and the
metric they would share.

**This can refuse a rule that saved perfectly well before.** If it does: rename one of
the two so they differ by more than punctuation, or set *Metric name* (below) on all but
one. Only that exact case is rejected — fields in *different* shared services never
collide, the same name under *different units* is two metrics and stays valid, and
ungrouped fields each own their service.

## 📉 Why your Gauge and Single metric widgets were blank

The question that started this: the **Graph** widget worked, **Gauge** and **Metric**
drew nothing.

They are not the same kind of widget. A Graph widget draws whatever the service happens
to carry, so it never has to know a name. **Gauge**, **Single metric** and **Bar chart**
are each bound to *one metric chosen by name* — and if the service does not emit that
exact name, the widget is blank, with nothing saying which name it wanted.

The trap is the order you configure it in. The metric dropdown only lists a service's
**real** metric names once the widget is already filtered to a host **and** a service.
Configure it the other way round — widget first, metric second — and the dropdown offers
the plugin's declared names (`json_api_value`, `json_api_bytes`, …). They look right.
They are named after this plugin. And a shared service never emits any of them, because
its metrics are named after its fields at runtime.

So: **filter the widget to a host and a service first, then pick the metric.** That
alone fixes it for most people, and it is the answer if your services hold a single
field each.

## 🏷️ Or name the metric yourself

If you would rather know the name in advance, fields now take an optional **Metric
name**:

| | |
|---|---|
| Metric name | `disk_free_pct` |

That is exactly what the service emits, shared service or not, and exactly what you type
into the widget. Letters, digits and underscores, starting with a letter or digit —
Checkmk's own rule, enforced in Setup and in the Explorer so a bad name fails where you
typed it rather than three screens later.

Two things it costs, both worth knowing before you use it:

- **The service loses its Perf-O-Meter.** Every bar this plugin draws is declared against
  one of its *own* metrics, so a name of your own matches none and the service-list bar
  goes empty. A field that wants its bar should keep the derived name and filter the
  widget instead.
- **Changing it later starts a new history** under the new name. The old one is not
  migrated, so pick it before the service has data worth keeping.

The plugin's own metric names are reserved, too. Naming a percentage field
`json_api_bytes` used to make it inherit that metric's unit and render `11.5%` as
`11.5 B`; Setup now refuses it and says why.

## Upgrading

**No configuration change, and nothing changes state.** *Metric name* is optional and off
by default.

Two things to look at:

1. Any shared service whose field names differ only in punctuation — one of its values
   was being dropped, and now is not.
2. If you have a rule with such a pair, the next save will be refused until you rename
   one or give it a *Metric name*. The message names both fields.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.20.0)
  — `json_api-0.20.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition — and the plugin now installs and runs on **3.0** as well

```
mkp add json_api-0.20.0.mkp
mkp enable json_api 0.20.0
```

On **Checkmk 2.5+** the optional companion package **Generic JSON API – Explorer (extra)**
(`json_api_explorer`) offers *Metric name* in the wizard with the same validation Setup
applies, so a name it would refuse is flagged under the field as you type it.

The whole release came out of one person saying "the graph widget works but the gauge
doesn't". If something in your service list is drawing nothing where it should draw
something, that is worth reporting — it is how most of the last two releases happened.
