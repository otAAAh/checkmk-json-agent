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
- 🔎 **Fetches the real response** — the wizard calls each endpoint from the site and shows you the actual JSON, so you click the fields that exist instead of guessing paths. It makes the *whole* connection the agent will make: method, body, headers, authentication (OAuth 2.0 included — the wizard performs the token exchange itself), TLS verification with a private CA bundle or a client certificate, and the configured HTTP proxy. A private-CA, mutual-TLS or proxy-only endpoint previews exactly as the check will see it.
- 🧾 **Body *and* headers** — a tab beside the field picker lists the response headers, so a rate-limit budget or a `Last-Modified` age is one click away instead of a path typed from memory.
- 🎯 **Point-and-pick fields** — select values by path, set WARN/CRIT thresholds, units, a numeric transform, an aggregation over a collection, a counter's rate or a timestamp's age, string matching, or turn each element of a `[*]` collection into a Checkmk host of its own — the same options the agent supports.
- 🔖 **Names that survive contact with a second endpoint** — an endpoint can put its own name in front of its field service names, so two applications monitored by one rule do not both produce `JSON STATUS`; the endpoint's own status service joins that group as well. The wizard offers it in the connection step and applies Setup's own rule: the prefix needs a name.
- 🧩 **One service for several fields** — a field can report into a shared service instead of getting one of its own, so a small API becomes one service whose state is the worst of its fields. The wizard offers it on each field, and the review step previews the result like any other. A field that needs a metric name you can point a **Gauge** or **Single metric** dashboard widget at can state one, checked against Checkmk's naming rule as you type so a name Setup would refuse is flagged under the field rather than on submit.
- 📄 **Keep the response that caused the state** — an endpoint can report its raw body and response headers in its own service details, so the response that made a service CRIT is readable from the service itself instead of from a URL your browser may not even reach. Credentials are masked first. The same endpoint step can put the JSON on the **field** services too — the ones that actually alert and notify — where by default it is just the element the value came from.
- 📚 **Paginated collections** — an endpoint that answers a collection one page at a time can be told where its next-page link is (a field in the body, or the `Link` header) and which collection to merge, so a count or a per-element service describes the whole collection rather than its first page. One honest note: the wizard's preview resolves the single response it fetched, so with this on, the element counts it shows are the first page's — it says so, and the site merges the pages.
- ✅ **Live preview before you commit** — the review step evaluates every chosen field against the fetched sample and shows the resulting service state under the name the site will give it — endpoint prefix and shared services included — so you catch a wrong path or threshold before the rule exists.
- 🔐 **Secure by default** — credentials are stored in the Checkmk password store and referenced, never written in clear text; TLS verification stays on. That covers basic auth, bearer tokens, API keys and OAuth 2.0 client credentials alike. A key sent in a header of the API's own choosing is dropped if the endpoint redirects to another host, exactly as the agent drops it — the preview is gated on Setup access for the same reason.
- 🚀 **One click to create** — the wizard writes the finished Generic JSON API rule for you.

## In short

Install the **Generic JSON API** agent, then install this Explorer. Open **Setup → Quick setup → Generic JSON API**, point it at an endpoint, tick the fields you care about, and press create. The services appear on your host.

## Details

- Extra/companion package — install alongside, and after, the Generic JSON API agent.
- Checkmk **2.5+**, any edition.
- Install via `mkp add` / `mkp enable`, or **Setup → Extension packages**.
- GPL-2.0-only.
