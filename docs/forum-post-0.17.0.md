<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.17.0** is out. One new feature and one correction, and both exist because people
replied on the [Ideas board](https://github.com/otAAAh/checkmk-json-agent/discussions/categories/ideas)
— including to 0.16.0, three days old at the time.

## 🧩 One service for several fields

Not every API deserves a service per field. A small health endpoint —

```json
{"status": "UP", "component": "nginx", "timestamp": "2026-08-29T18:14:55+00:00"}
```

— gave you three services, when what you wanted was one service that is OK while all
three are fine.

Set the new **Report in a shared service named** to the same name on each of those
fields, and they report into that one service as lines:

```
JSON Health   OK   Status: UP, Component: nginx, Timestamp: 12 m
```

The service's state is the **worst of its lines**. That is not a new rule invented for
this: it is how Checkmk aggregates *any* check that yields several results, which is
also why this feature is small. The agent labels each value with the name of its line,
the check yields one result per line, and Checkmk does the rest.

**Service name** now names the line, and the shared name names the service — so each
line says which field it is, in the summary, in its own block of the Details, and on the
threshold line:

```
[Status]
Status: UP (matched OK)
JSON path: status
[Timestamp]
Timestamp: 1 hour 16 minutes (warn/crit at 1 hour 0 minutes/2 hours 0 minutes)
JSON path: timestamp
```

Every line keeps its **own** levels, string matching, transform, unit and
counter/timestamp handling. They are independent fields that happen to share a service,
not a merged value. A `[*]` wildcard fans out into *lines* rather than services, so a
whole collection can be one service that goes CRIT if any element does.

Two things change, both because one service now holds several fields, and both are in the
inline help:

- **A check-parameters rule is not applied to such a service.** One set of levels cannot
  describe several fields, and seeding them from whichever field happened to be first
  would quietly apply that field's thresholds to all of them. Thresholds for combined
  fields live in the agent rule, per line. A service with a single field is unaffected
  and still takes the rule as before.
- **Each line's metric is named after the line** (`json_api_bytes_root_used`), because
  metric names must be unique within a service and fields routinely share a unit. Such a
  name is not one the plugin declares, so Checkmk titles it after itself and draws it as
  a plain number rather than in the field's unit. A field that needs its unit on the
  graph is better off with a service of its own.

A field written to the **inventory** creates no service, so it cannot join one either;
Setup says so rather than ignoring half of the configuration.

## 🔖 The endpoint's own service joins its group

0.16.0 let an endpoint prefix its field service names, so that two applications monitored
by one rule stop producing `JSON STATUS` and `JSON STATUS (2)`. The endpoint's *own*
service was left as `JSON API <name>` — and, as the person who asked for the feature
pointed out within hours, that sorts it away from everything it describes:

```
JSON API MY_APP1_HEALTH      ← every endpoint's status service lands here
JSON API MY_APP2_HEALTH
JSON MY_APP1_HEALTH STATUS   ← …and its own group is down here
JSON MY_APP2_HEALTH STATUS
```

Which rather defeats the point of grouping. For a prefixed endpoint it is now
`JSON <name> API`:

```
JSON MY_APP1_HEALTH API
JSON MY_APP1_HEALTH STATUS
JSON MY_APP1_HEALTH TIMESTAMP
JSON MY_APP2_HEALTH API
JSON MY_APP2_HEALTH STATUS
JSON MY_APP2_HEALTH TIMESTAMP
```

It is the same service, with the same **Generic JSON API endpoint** check-parameters rule
— Checkmk simply needs a second check plugin to render a different service name, which is
also what keeps the change confined to the endpoints that opted into prefixing.

## Upgrading

**Nothing to do** unless you turned the endpoint-name prefix on in 0.16.0. If you did,
that endpoint's own service is renamed on the next discovery: `JSON API <name>` goes
stale and `JSON <name> API` appears. Re-run a service discovery on the affected hosts,
and move any check-parameters rule or notification condition that matched the old name.
See `UPGRADING.md` for the detail.

Everything else is additive. Shared services are opt-in per field, and no existing rule
or service changes.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.17.0)
  — `json_api-0.17.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition

```
mkp add json_api-0.17.0.mkp
mkp enable json_api 0.17.0
```

On **Checkmk 2.5+** the optional companion package **Generic JSON API – Explorer (extra)**
(`json_api_explorer`) is updated too; the shared-service field appears in the wizard's
field picker step with the same validation Setup applies.

Two releases in a row driven by replies on the Ideas board — thank you. It is much easier
to build the right thing when someone says what the wrong thing looks like in their
service list.
