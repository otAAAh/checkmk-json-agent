<!-- Paste this into the "Description" field of the Checkmk Exchange
     "upload new package" form for the json_api_explorer package.
     The Exchange renders Markdown. No links are used on purpose.

     Dependency: the Exchange upload form has a separate "Dependencies"
     field — list the "Generic JSON API" (json_api) package there. Checkmk
     .mkp manifests carry no dependency field, so this is the only place the
     requirement is recorded for users. -->

# Generic JSON API – Explorer (extra)

**A guided setup wizard for the Generic JSON API agent — build a monitoring rule from your API's real response, right inside Checkmk.**

This is the optional companion to the **Generic JSON API** package. It adds an in-site wizard under **Setup → Quick setup** that walks you from a live API response to a finished rule — no `rules.mk`, no `curl`, no leaving Checkmk.

## Requires

- The **Generic JSON API** (`json_api`) package must be installed and enabled first — this Explorer only *builds* rules for that agent; it does not monitor anything on its own.
- **Checkmk 2.5 or newer**, any edition. The wizard is built on Checkmk's native Quick-Setup UI, which does not exist on 2.4.

## What it does

- 🧭 **Guided, step by step** — choose the target folder and host, define one or more endpoints (URL, method, auth, headers, TLS/redirect options), then pick the fields to monitor.
- 🔎 **Fetches the real response** — the wizard calls each endpoint from the site and shows you the actual JSON, so you click the fields that exist instead of guessing paths.
- 🎯 **Point-and-pick fields** — select values by path, set WARN/CRIT thresholds, units, a numeric transform, an aggregation over a collection, a counter's rate or a timestamp's age, string matching, or turn each element of a `[*]` collection into a Checkmk host of its own — the same options the agent supports.
- ✅ **Live preview before you commit** — the review step evaluates every chosen field against the fetched sample and shows the resulting service state, so you catch a wrong path or threshold before the rule exists.
- 🔐 **Secure by default** — credentials are stored in the Checkmk password store and referenced, never written in clear text; TLS verification stays on.
- 🚀 **One click to create** — the wizard writes the finished Generic JSON API rule for you.

## In short

Install the **Generic JSON API** agent, then install this Explorer. Open **Setup → Quick setup → Generic JSON API**, point it at an endpoint, tick the fields you care about, and press create. The services appear on your host.

## Details

- Extra/companion package — install alongside, and after, the Generic JSON API agent.
- Checkmk **2.5+**, any edition.
- Install via `mkp add` / `mkp enable`, or **Setup → Extension packages**.
- GPL-2.0-only.
