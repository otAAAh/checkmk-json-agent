<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.15.0** is out, and it is a single-topic release: **OAuth 2.0**.

## 🔐 OAuth 2.0 (client credentials)

Plenty of APIs worth monitoring cannot be reached with a static credential at all.
They want a short-lived token, and they want you to go and get one. Until now that put
them out of reach of this plugin: you can paste a bearer token into a rule, but it stops
working an hour later.

Pick **OAuth 2.0 (client credentials)** as the authentication and give it the identity
provider's token endpoint — not the API URL:

| | |
|---|---|
| Token URL | `https://login.example.com/oauth2/v2.0/token` |
| Client ID | `monitoring` |
| Client secret | from the password store, like every other secret |
| Scope | `api://monitoring/.default` (optional) |
| Audience | optional; some providers, Auth0 among them, require it |
| Send credentials | in the Authorization header, or in the request body |

The agent trades the client ID and secret for an access token and sends it as
`Authorization: Bearer <token>`. Nothing else about the rule changes — levels, `[*]`
wildcards, aggregation, the endpoint's own service all work exactly as before.

**The token is cached**, which matters more than it sounds. A rule polling every minute
would otherwise hit your identity provider every minute, on every host, forever — and
providers rate-limit token endpoints harder than they rate-limit APIs. The agent keeps
the token until shortly before the provider says it expires, and only then asks again.

Two details that exist because the alternative bites:

- **"Send credentials" is a choice, not a guess.** RFC 6749 allows the client ID and
  secret either in an HTTP basic header or in the request body, and providers disagree
  about which they accept. Getting it wrong produces a 401 *from the token URL*, which
  looks exactly like a wrong password and sends you debugging the wrong thing. Try the
  header first; switch if the provider refuses.
- **A revoked token recovers by itself.** If a cached token is rejected with a 401 — a
  rotated secret, a revoked grant — the agent throws it away and retries once with a
  fresh one. A token it just fetched and got rejected is reported as-is, because that
  means the credentials or the scope are wrong and asking again would only double every
  check's requests.

Both Explorers offer it. In the in-site wizard it is simply another entry in the
Authentication dropdown, and the wizard's "fetch the real response" step performs the
token exchange too — so the preview is authenticated exactly like the agent will be, and
a failure at the token endpoint is reported separately from a failure at the API. They
are different problems and they send you to different URLs.

### What this is not

This is the machine-to-machine grant. If your API can only be reached with a token that a
*person* obtained by logging in through a browser, this mode cannot help — that flow
needs an interactive consent step and rotating refresh tokens, neither of which a
special agent can own. The reasoning is written up in `docs/spike-oauth2.md` in the
repository, including why Checkmk's own built-in OAuth2 connections are shaped the way
they are.

## 🧪 Testing it

`dev/mock_api.py` in the repository — the dependency-free mock API — now has a token
endpoint, so the whole flow can be tried without an identity provider:

```
python3 dev/mock_api.py --port 8642

  Token URL   http://localhost:8642/token
  API URL     http://localhost:8642/oauth
  Client      monitoring / s3cret
```

It accepts the credentials either way, so both settings of *"Send credentials"* can be
exercised, and `/oauth` reports how many tokens have been issued — monitor that field and
watch it stay flat while the agent reuses its cached token.

## Upgrading

**Nothing to do.** OAuth 2.0 is a new choice in an existing dropdown; every existing rule
is untouched, and no service changes state on upgrade.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.15.0)
  — `json_api-0.15.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition

```
mkp add json_api-0.15.0.mkp
mkp enable json_api 0.15.0
```

On **Checkmk 2.5+** the optional companion package **Generic JSON API – Explorer (extra)**
(`json_api_explorer`) is updated too — that is where the wizard's OAuth 2.0 support and
the authenticated preview live.

As ever: bug reports welcome, and if you hit an identity provider that this mode cannot
talk to, that is worth an issue — the shape of the next step depends on which ones.
