<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.24.0** is out. It is about timestamps: `auto` now reads one more common
layout, and anything it cannot guess can be read with a pattern of your own.
Existing rules keep their stored value, but a field that was stuck at UNKNOWN may
now get a real state (see *Upgrading*).

## 🕰️ Go's time layout is read automatically

With **Interpret the value as → a timestamp**, format `auto` read a number (epoch
seconds or milliseconds), an ISO 8601 string or an HTTP-date. It now also reads
the layout that APIs written in Go put into their JSON:

```
2027-07-11 08:40:00 +0000 UTC
```

Cloudflare's origin certificates report `expires_on` this way, so their expiry
can now be monitored directly. The numeric offset decides the time; the zone
name after it is ignored. Thanks to **jrz84** for the report and the Cloudflare
example ([#246](https://github.com/otAAAh/checkmk-json-agent/issues/246)).

## 🧩 A pattern for everything else

Some layouts cannot be guessed safely: `01/02/2023` is January or February
depending on the locale, and a wrong guess would be a wrong age with no warning.
For those, choose the new format **Custom format** and enter a Python `strptime`
pattern, for example:

- `%d.%m.%Y %H:%M:%S` for `14.11.2023 22:13:20`
- `%Y%m%d` for `20231114` — this also works when the API sends it as a number

Without `%z` in the pattern, the time is read as UTC, the same as an ISO 8601
timestamp without a zone. Setup refuses a pattern that cannot work: a custom
format without a pattern, an unknown directive such as `%Q`, a stray `%`, or text
without any directive. If a rule imported over the REST API still carries such a
pattern, the service reports `Not a valid timestamp` instead of crashing.

The standalone Explorer offers the new format too, with a pattern field that
checks your input while you type. The in-site wizard builds its form from the
rule, so it has the option as well, without an update.

New texts are translated into all eight languages.

## Upgrading

**No configuration change.** One effect to know about:

- A timestamp field with format `auto` whose value is in Go's layout used to be
  **UNKNOWN** (`Not a valid timestamp`). It now gets a real age, so **if that
  field has upper levels, it can go WARN or CRIT** on the next check. Details in
  [UPGRADING.md](https://github.com/otAAAh/checkmk-json-agent/blob/main/UPGRADING.md).

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.24.0)
  — `json_api-0.24.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition, verified on **3.0**

```
mkp add json_api-0.24.0.mkp
mkp enable json_api 0.24.0
```

The optional companion package **Generic JSON API – Explorer (extra)**
(`json_api_explorer`) has no changes of its own in this release; updating it is
optional.
