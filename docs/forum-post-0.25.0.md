<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.25.0** is out. It is a quality release: a full review of the plugin against
Checkmk's own conventions, and everything it found fixed. No new rule options,
but a few behaviour changes worth reading before you update (see *Upgrading*),
and an important security fix in the optional Explorer package.

## 🔒 Explorer: stored passwords and CSRF

The wizard's live preview resolved any password-store entry the browser asked
for. A Setup user could therefore have used it to read stored passwords they
were not allowed to use. The preview now only resolves entries the user may use,
like the password field in a rule does. Its pages also check Checkmk's CSRF
token now, and the wizard needs the *Rule sets* permission. **If you run the
Explorer package, please update it.**

On Checkmk 3.0 the wizard's last step (target folder and host) was missing; it
is back.

## 🎚️ Levels and rules behave the way you expect

- **Levels edited in the agent rule apply immediately.** Before, discovery
  copied them into the service, so a change only took effect after a
  re-discovery.
- **Old rules work again.** A rule saved by a very old version (before 0.10.0,
  or with a flat *expected* regex) and never opened since reached the agent in
  its old shape and was partly ignored. It is upgraded on the way now.
- **A field named like an endpoint's own service** (`API frontend` next to an
  endpoint called `frontend`) no longer disappears; it is discovered as
  `… (2)`.
- **A broken transform** (`value +`) makes the service UNKNOWN instead of
  crashing it.

## 📜 Certificate validity in seconds

The endpoint service now records the TLS certificate's remaining validity in
seconds, like Checkmk's own certificate checks (`42 days 0 hours`). Its levels
are entered as a time span. Existing levels are converted automatically, and
rules nobody opens keep alerting at the same point as before.

## 🧩 Smaller things

- Timeouts, the cache time and the retry wait are entered as time spans; the
  regex fields show Checkmk's standard regex help; fields that resolve macros
  say so.
- `--debug` keeps an echoed credential out of its output, and lets unexpected
  errors through with their traceback, as Checkmk's own agents do.
- Cache and token files are written privately from the start.
- The wizard's review step now grades every value exactly like the check:
  lower levels, numeric strings, booleans, a second path in a transform, and
  levels on a value that is not a number.

Under the hood the plugin was typed and split into smaller modules, and a mock
API with a path for every connection setting now ships in the repository for
anyone who wants to try a rule without a real API
([docs/mock-api.md](https://github.com/otAAAh/checkmk-json-agent/blob/main/docs/mock-api.md)).
New and changed texts are translated into all eight languages.

## Upgrading

**No configuration change is required.** Things to know:

- **Certificate graph:** the remaining validity is a new metric, so its graph
  starts empty. The old history (in days) is kept.
- **Re-discovery once:** services discovered by an earlier version still carry
  the old levels from discovery. Run a service discovery and accept the changed
  parameters once.
- **Old rules may start producing services**, and an old *expected* regex
  alerts again.
- **Explorer:** users need the *Rule sets* permission to see it.

Details in
[UPGRADING.md](https://github.com/otAAAh/checkmk-json-agent/blob/main/UPGRADING.md).

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.25.0)
  — `json_api-0.25.0.mkp` and `json_api_explorer-0.25.0.mkp`, with SHA256 sums
  and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition, verified on **3.0**; the Explorer needs **2.5+**

```
mkp add json_api-0.25.0.mkp
mkp enable json_api 0.25.0
```

The optional companion package **Generic JSON API – Explorer (extra)**
(`json_api_explorer`) has security fixes in this release; **update it too** if
you have it installed:

```
mkp add json_api_explorer-0.25.0.mkp
mkp enable json_api_explorer 0.25.0
```
