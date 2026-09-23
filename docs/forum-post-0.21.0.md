<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.21.0** is out. No new options this time: it is seven fixes, and three of them
were costing someone data without saying so. If you only read one section, read
the one that matches something you use.

## 📋 A table column called `name` took the whole host's inventory down

Only matters if you send a `[*]` wildcard into the **HW/SW inventory**, which is
the case where each element becomes one table row.

Those rows were keyed by a column called `name`. That is also the most natural
column such a table could have (`nodes[*].name`, `pods[*].metadata.name`).
Checkmk refuses a column that is also the key column, and it refuses it while
**writing** the tree. So configuring one never showed a form error. It failed the
**whole host's** inventory: every field of every rule, not just the one table.

The key column is now called **`element`**, so `name` is just another column.

**Two things to check after you upgrade:**

- The affected tables are rebuilt under the new key on the next inventory run.
  Old rows disappear and equivalent ones appear, and the inventory history
  records that as a change. No data is lost. But a view, a report or a *Search
  hosts by inventory data* query that names the `name` key column of such a
  table has to name `element` instead.
- `element` is now reserved as an attribute name for a wildcard field, so Setup
  refuses to save a rule that uses it. The bundled field picker flags it too. A
  field without a wildcard has no key column and keeps every name it had.

## ⏱️ Counters in a shared service were reading each other's values

Only matters for a service built from **several fields** (*Report in a shared
service named*) where more than one field is read as a **counter**. That includes
a `[*]` wildcard reporting into a shared service, which is the usual way to end up
there.

A rate is computed from the previous reading, and that reading was stored per
*service*. So every counter line of the service overwrote the same one. Each line
was compared against whichever line had been stored last. One line reported a
rate made from two unrelated counters. The next one saw its counter "go
backwards", reported no rate at all and kept its previous state. This alternated
on every check, and nothing said anything was wrong.

Each line now keeps its own reading. **The rates change, because the old ones
were wrong**, so look at any levels you tuned against them. The first check after
the upgrade has no per-line reading yet, so these lines keep their previous state
for one interval. A counter in a service of its own is untouched, history
included.

## 📚 Pagination behind a redirect

Only matters with **Follow pagination** on and a URL that **redirects** (an
`http` → `https` hop, `/api` → `/api/v2`, a regional host).

Next-page links were resolved against the URL written in the rule, not the one
the page actually came from:

- a relative link (`?page=2`) went to the pre-redirect path, got a 404 and failed
  the **whole endpoint**, so every one of its services went UNKNOWN on an API
  that was working;
- an absolute link to the redirect's host was refused as "another host", so the
  collection stopped at page one and the endpoint service reported it as
  incomplete.

Both now follow the redirect. This does not widen anything: a link to a host that
is neither the configured nor the redirected one is still refused. If a count or
an aggregation over such a collection was stuck at one page, it now jumps to its
real value, so check its levels.

## Smaller fixes

- **Levels + string matching on a number.** The levels have always decided the
  state and the matching never ran. The service details still listed the
  pattern, though, which made it look as if it applied. They now say it did not.
  States, metrics and summaries are unchanged.
- **Translations.** In all eight languages, the message that explains why
  piggyback labels without a host field cannot be saved showed a field *title*
  instead of the explanation. It now says what is wrong. A new CI check catches
  that kind of mix-up.
- **A hand-edited `--endpoint` that is not valid JSON** used to stop the agent
  before it wrote anything, so every service on the host went stale. It now
  becomes one failed endpoint service that says what is wrong, and the other
  endpoints report normally. Setup cannot produce such a rule, so this only
  matters if you edit rules or program calls by hand.
- **Wizard review step (2.5+ Explorer).** The preview now shows the service names
  the site will actually create. It used to ignore the endpoint prefix
  (`JSON Status` instead of `JSON frontend Status`) and showed each field of a
  shared service as a service of its own.

## Upgrading

**No configuration change.** Check three things:

1. **Inventory:** views, reports or searches that name the `name` key column of
   a `[*]` inventory table now need `element`.
2. **Shared services with several counters:** the rates are now correct, so they
   are different. Re-check their levels.
3. **Paginated endpoints behind a redirect:** they start working, so counts over
   them may jump to their real values.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.21.0)
  — `json_api-0.21.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition, verified on **3.0**

```
mkp add json_api-0.21.0.mkp
mkp enable json_api 0.21.0
```

On **Checkmk 2.5+**, update the optional companion package **Generic JSON API –
Explorer (extra)** (`json_api_explorer`) as well, to get the corrected review
preview.

None of these fixes gave an error message. They showed up as a wrong number, a
stale service or an empty inventory. If something in your setup looks plausible
but wrong, please report it, even without a clear reproduction.
