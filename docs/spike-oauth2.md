<!-- SPDX-License-Identifier: GPL-2.0-only -->
# Spike: OAuth2 for the JSON API special agent

**Question asked:** Checkmk 2.5 ships built-in *OAuth2 connections* — can this plugin
reuse them instead of growing its own OAuth2 support?

**Decision: build a generic OAuth2 client-credentials mode on public APIs only, and
leave the Setup-managed connection types alone for now.**

The built-in connections are not reusable by the agent package (§2), and the flow they
serve — interactive consent, then refresh — is an edge case we are explicitly deferring
(§2a). What *is* reusable is the pattern they follow, which a second, simpler precedent
in the very same Checkmk plugin lets us copy almost line for line (§3).

Evidence below is from the `2.5.0` branch, which is what `frontend-build.yml` pins and
what a 2.5 site actually runs.

---

## 1. What Checkmk 2.5 actually ships

Two *different* things share the name "OAuth2", and conflating them is the trap:

### a) "OAuth2 connections" — a Setup-managed connection object

`cmk/gui/oauth2_connections/` registers a Setup page (main module + modes +
a `PermissionUseOAuthConnections`) where an admin creates a named connection. The stored
shape is `cmk.utils.oauth2_connection.OAuth2Connection`:

```python
class OAuth2Connection(TypedDict):
    title: str
    client_secret:  tuple[Literal["cmk_postprocessed"], Literal["stored_password"], tuple[str, str]]
    access_token:   tuple[...]                     # ← stored
    refresh_token:  tuple[...]                     # ← stored
    client_id: str
    tenant_id: str
    authority: str
    connector_type: OAuth2ConnectorType
    sites: OAuth2Sites
```

A rule embeds one via the `OAuth2ConnectionSetup` form spec
(`cmk/gui/form_specs/unstable/oauth2_connection_setup.py`).

### b) A plain client-credentials triple

`cmk/plugins/emailchecks/server_side_calls/options_models.py` also has, for the
IMAP/EWS auth choice:

```python
class Oauth2Parameters(BaseModel):
    client_id: str
    client_secret: Secret     # cmk.server_side_calls.v1
    tenant_id: str
```

No connection object, no internal API — ordinary form specs and a `Secret`.

**The only plugin in the whole tree that consumes (a) is `emailchecks`.** Nothing else
uses it.

---

## 2. Why we cannot reuse (a)

Four independent blockers, any one of which would be enough:

| # | Blocker | Evidence |
|---|---|---|
| 1 | **Does not exist in 2.4** | `git ls-tree origin/2.4.0 \| grep oauth2_connections` → **0 files**. Our agent package is `min_required = "2.4.0"`. |
| 2 | **Microsoft Entra ID only** | `OAuth2ConnectorType = Literal["microsoft_entra_id"]`. There is no other connector type. A plugin called *Generic JSON API* cannot ship auth that only works against one vendor's IdP. |
| 3 | **Internal + unstable APIs** | The server-side type is `cmk.server_side_calls.internal.OAuth2Connection`; the form spec is `cmk.gui.form_specs.unstable.OAuth2ConnectionSetup`. Neither is in `cmk.server_side_calls.v1` — checked on **both** `2.5.0` and `master`, so this is not about to become public. |
| 4 | **A different flow from the one we need** | It stores an `access_token` *and* a `refresh_token` — authorization-code with interactive consent. That is correct for what it serves (§2a), but it is not what most JSON APIs need, and a special agent cannot own it alone. |

Blocker 3 is the one that matters most for this repo specifically. The two-package split
exists *precisely* so the agent depends only on public, stable plugin APIs — that is why
the Explorer is a separate MKP with its own `min_required = "2.5.0"`. Pulling
`cmk.server_side_calls.internal` into the agent would undo that deliberate decoupling and
tie the monitoring agent to an unversioned internal contract.

Blockers 1 and 2 are fatal on their own regardless of packaging: we would drop every 2.4
user and still only support Entra.

### 2a. Why the built-in stores a refresh token — recorded, then deferred

The stored `access_token` / `refresh_token` is not over-engineering. **Some cloud
vendors give you no choice:** the data is only reachable with *delegated* permissions on
behalf of a real user, so a human has to consent in a browser once, and unattended
operation afterwards means holding a refresh token and exchanging it as the access token
expires. Client credentials is simply not on offer for those APIs.

That is why it is a Setup-managed *connection object* rather than rule fields: the
consent step needs a browser, which a headless agent does not have, and refresh tokens
rotate at runtime, so the credential cannot live in static rule configuration. (See
`emailchecks`' Graph client, which keeps the current tokens in a password-store file it
owns and writes the rotated values back — `_get_stored_token` / `_store_token`.)

**We are treating this as an edge case and leaving it out of scope.** It is recorded here
so the design is not mistaken for over-engineering next time someone reads it, and so
that if a user does turn up needing it, we start from "the interactive half cannot live
in a special agent" rather than trying to hand-roll it.

---

## 3. What we *should* reuse: the pattern

`emailchecks` proves the whole mechanism end to end, and it is all public API:

**Server-side call** decomposes the config into individual args, passing each secret as a
`Secret` reference rather than a value:

```python
case tuple(("oauth2", Oauth2Parameters() as auth)):
    args += [
        f"--fetch-client-id={auth.client_id}",
        "--fetch-client-secret-reference", auth.client_secret,
        f"--fetch-tenant-id={auth.tenant_id}",
    ]
```

**The agent does the token exchange itself.** Checkmk hands over references; the program
resolves them from the password store and talks to the IdP. There is no framework helper
for the exchange, and none is expected — `cmk/plugins/emailchecks/lib/graph_api_client.py`
hand-rolls it.

Everything that needs is already in our agent:

| Need | We already have |
|---|---|
| several secrets per endpoint | `_add_secret_option()` / `_reveal_secret()`, indexed `--secret_<i>` |
| 2.4-vs-2.5 password-store shim | the `_HAVE_PWSTORE_V1` fallback |
| a place to cache the token | `_cache_dir()`, 0600, write-then-rename, pruning |
| a cache key that includes the credential | `_cache_key()` — already hashes the secret, for exactly this class of reason |
| retry on a transient IdP failure | `_retry_policy()` / `_retryable_status()` |

Note the token cache is *not* the response cache and must not reuse its entry: the token
is keyed by credential + token URL + scope, and its lifetime comes from `expires_in`, not
from the endpoint's `cache_ttl`.

---

## 4. Proposed shape (for the implementing PR, not this spike)

A fifth auth choice, `auth_oauth2`, alongside the existing four:

```
Token URL         https://login.example.com/oauth2/v2.0/token
Client ID         monitoring
Client secret     ← password store
Scope             (optional)  api://monitoring/.default
Audience          (optional)
Send credentials  [ HTTP Basic header | POST body ]     ← RFC 6749 allows both;
                                                          IdPs disagree on which
```

Agent side:

1. POST `grant_type=client_credentials` (+ scope/audience) to the token URL.
2. Cache the access token under `sha256(token_url + client_id + hash(secret) + scope)`,
   with an expiry of `expires_in` minus a skew (60 s).
3. Put it on the request as `Authorization: Bearer …` — which is exactly what the
   existing `auth_token` branch of `_build_session()` already does, so the request path
   needs no new code.
4. On a 401 with a cached token, drop the entry and retry **once** — an IdP can revoke
   early, and this is the one case where a retry is not just noise.

Reused verbatim: the redaction machinery (`_redacted_headers`, `_redact_secret`), the
`_Session` cross-host header stripping, proxy/TLS/timeout handling.

**Effort:** comparable to #159 + #160 combined. Ruleset + server-side call + agent +
README + 8 catalogs + Explorer mirror. The token exchange itself is perhaps 60 lines;
the ceremony around it is the bulk, as always in this repo.

**Risks worth naming up front:**

- **Client auth method** varies by IdP (basic header vs POST body). Guessing wrong looks
  like an opaque 401. Hence the explicit toggle above rather than a default.
- **Testing** needs a real IdP or a fake token endpoint; `dev/mock_api.py` would have to
  grow one.
- **A token in a cache file** is a bearer credential at rest. Same 0600 treatment as the
  response cache, and it must never reach `--debug` output or an error message.

---

## 5. Decision

**Build a generic OAuth2 client-credentials mode, on public APIs only.** It covers the
unattended machine-to-machine case that most monitored JSON APIs offer, it works on 2.4
alongside every other auth mode, and it needs nothing internal or unstable.

**The Setup-managed connection types are out of scope.** Not because they are badly
designed — for the flow they serve they are right (§2a) — but because for us they are
2.5-only, Entra-only and internal API, in service of an edge case. Ignoring them costs us
nothing today: an endpoint authenticated either way still arrives at `_build_session()`
as a bearer token, so nothing about this decision blocks adding the other flow later.

Reopen the question only if a user actually needs an API that offers no client-credentials
grant. At that point the answer is still *not* to hand-roll it in the agent — the
realistic options would be Checkmk promoting an OAuth2 type into
`cmk.server_side_calls.v1` with a non-Entra connector (not true on `master` today), or
hosting the GUI half in the **Explorer** package, which is already 2.5+ and already
depends on internal GUI APIs by design.

## 6. Next step

One implementing PR, scoped as §4. No further investigation needed — the mechanism is
proven by `emailchecks` and every supporting piece already exists in our agent.
