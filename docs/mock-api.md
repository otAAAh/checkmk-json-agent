<!-- SPDX-License-Identifier: GPL-2.0-only -->
# The mock API

`dev/mock_api.py` is a small HTTP/JSON API for trying the special agent and the
Explorer wizard against something real, without a real API. It covers every
connection setting of the *Generic JSON API* rule. It lives in the repo and is
not part of either MKP.

It is a development tool: fixed dev credentials, no real security, and it listens
on `127.0.0.1` by default. Do not expose it.

## Starting it

Run it where the special agent runs, so on the Checkmk server or in the site
container. It needs only Python 3, plus the `openssl` command for `--tls`:

```sh
python3 dev/mock_api.py                    # HTTP on 8642
python3 dev/mock_api.py --tls --proxy      # + HTTPS on 8643, a proxy on 8644
```

| Option | Default | What it does |
|---|---|---|
| `--host` | `127.0.0.1` | Address of all three listeners |
| `--port` | `8642` | HTTP port |
| `--tls` | off | Also serve HTTPS, with a throwaway CA it creates on start |
| `--tls-port` | `8643` | HTTPS port |
| `--tls-days` | `20` | Validity of the server certificate, in days. 20 is inside the 30 / 7 days the certificate levels suggest, so with those levels set the endpoint service goes WARN |
| `--tls-dir` | a new temp dir | Where the CA, server and client certificates are written. The paths are printed on start |
| `--proxy` | off | Also run a forward proxy |
| `--proxy-port` | `8644` | Proxy port |

The request log goes to stderr, one line per request. It is the quickest way to
see what the agent actually asked for: which page, with which parameters, how
many retries, through the proxy or not.

In the rule, use `http://localhost:8642/<path>`. For HTTPS use
`https://localhost:8643/<path>`: the server certificate is issued for
`localhost` and `127.0.0.1`.

## Paths and what they test

### Reading values

| Path | Returns | Try in the rule |
|---|---|---|
| `/health` | A rich document: nested objects, a map, arrays, nested arrays, a key with a dot | `status` with string matching, `components[*].status` (map wildcard), `nodes[*].load` with *Label from field* `name`, *Only elements matching a condition*, aggregations, `pods[*].containers[*].ready`, `data['foo.bar'].value`, service and host labels, piggyback hosts from `nodes[*]` with `name`, inventory from `version` |
| `/metrics` | Flat numbers | Upper and lower levels, a transform (`value * 1024`), a second path for the transform |
| `/sensors` | One value per unit, plus a `sensors` list | Every *Unit*: `disk_used_bytes` bytes, `backup_duration_seconds` seconds, `cpu_percent` percent, `temperature_celsius` °C, `supply_volts` V, `load_amperes` A, `power_watts` W, `cpu_hertz` Hz, `open_files_count` count; `sensors[*].celsius` labelled by `name` |
| `/counter` | `requests_total` and `bytes_sent` rise steadily; `served` counts the requests; headers `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `Last-Modified`, `ETag` | *Interpret the value as → a counter* (a per-second rate); `@header.X-RateLimit-Remaining`; `@header.Last-Modified` as a timestamp; *Report the response* with headers |
| `/times` | The same moment (two hours ago) in every timestamp format, a future `expires_at`, and a value that is no timestamp | *Interpret the value as → a timestamp*: format `auto` on `epoch_seconds`, `epoch_milliseconds`, `iso_utc`, `iso_offset`, `iso_naive`, `iso_nanoseconds`, `http_date`, `go_layout`; format *custom* with `%d.%m.%Y %H:%M:%S` on `custom_german`; a negative age on `expires_at`; UNKNOWN on `not_a_timestamp` |
| `/big?kb=200` | A large body (`?kb=` or `?mb=`, up to 64 MB) | Truncation in *Report the response* and *Report the JSON context*. `?mb=60` is over the agent's 50 MiB limit, so the endpoint fails with a clear message |
| `/query` (POST) | Echoes the posted JSON under `echo` | *HTTP method* POST with a *Request body*, e.g. `{"q": "$HOSTNAME$"}` to see macros resolved |

### Authentication

Dev credentials: API key `dev-key`; OAuth2 client `monitoring` / `s3cret`.

| Path | Accepts | Try in the rule |
|---|---|---|
| `/secure` | Any `Authorization` header | *Basic authentication* (any user and password), *Bearer token* (any token) |
| `/apikey` | `X-API-Key: dev-key`, or `?api_key=dev-key` | *API key in a header* (header `X-API-Key`), *API key in a query parameter* (parameter `api_key`). `?echo=1` puts the key back into the body, as some APIs do: the reported response, the final URL and `--debug` must show `<redacted>` instead |
| `/token` (POST) | The client-credentials grant, credentials as HTTP basic or in the body | *OAuth 2.0 client credentials* with token URL `http://localhost:8642/token`, both settings of *How to send the client credentials* |
| `/oauth` | A bearer token issued by `/token` | The same OAuth2 setting. `tokens_issued` must not climb on every check: the agent caches the token until shortly before it expires (300 s) |

### Connection behaviour

| Path | Behaviour | Try in the rule |
|---|---|---|
| `/redirect` | 302 to `/health` | *Follow HTTP redirects* on: reads `/health`. Off: the endpoint fails with "redirects disabled" |
| `/redirect?cross=1` | 302 to `/apikey` on the other loopback name (`localhost` ↔ `127.0.0.1`) | With *API key in a header*: `/apikey` answers 401, because the agent does not send the key to another host |
| `/maintenance` | 503 with a JSON body and `Retry-After` | Without *Accept HTTP status*: the endpoint fails. With `503` accepted: `status` reads `maintenance` |
| `/flaky?fail=2&id=x` | 503 for the first `fail` requests of the series `id`, then 200; the series then starts over | *Retry*: with 2 attempts the endpoint is OK and reports that a retry was needed; with 1 attempt it fails |
| `/counter` | `served` counts every request it answers | *Re-read at most every*: `served` stops climbing while the cached body is served, and the endpoint service says so |
| `/slow` | Answers after 5 seconds | *Request timeout* below 5 s: the endpoint fails with a timeout |
| `/down` | Always 500 | An endpoint that is down: its own service and its field services |
| `/notjson` | Plain text | The "not JSON" error |
| any other path | 404 with a JSON body | Accepting `404` |

### Pagination

| Path | Pages | Try in the rule |
|---|---|---|
| `/jobs` | 7 jobs, 3 per page. The next page is in `links.next` and in an RFC 8288 `Link` header, both relative | *Follow pagination*: next link from the body (`links.next`) or from the `Link` header, collection `items`. A `count` over `items` reports 7 with it, 3 without. `?loop=1` points every page at itself, which the agent must refuse |
| `/items` | 23 items, 5 per page by default, `total` in every page, no next link | Page number: parameter `page`, page size 5, size parameter `per_page`. Offset: parameter `offset`, size parameter `limit`, total `total`. 1-based offset (SCIM): parameter `startIndex`, start 1, size parameter `count`. Every variant must count 23 over 5 pages |

### TLS (`--tls`)

The same paths over HTTPS on `--tls-port`, plus one:

| Path | Behaviour | Try in the rule |
|---|---|---|
| any | Server certificate signed by the mock CA | Verification on, no *CA bundle*: the endpoint fails with a certificate error. *CA bundle* = the printed `ca.pem`: works, and the endpoint service reports the certificate's remaining validity (`--tls-days`) |
| `/mtls` | 403 unless a client certificate signed by the mock CA is presented | *Client certificate* = the printed `client.pem`, key `client.key`: returns the client's name |

### Proxy (`--proxy`)

A forward proxy on `--proxy-port`. It forwards plain HTTP and tunnels HTTPS
(CONNECT), but only to the mock itself: `localhost` or `127.0.0.1` on the mock's
own HTTP and HTTPS ports. Anything else, including other services on the same
machine, gets a 403.

| Path | Behaviour | Try in the rule |
|---|---|---|
| `/proxied` | `through_proxy` is `true` when the request came through the proxy | *HTTP proxy* = `http://localhost:8644`. A forwarded response also carries `X-Mock-Proxy: forwarded`, readable as `@header.X-Mock-Proxy`. For HTTPS the tunnel is opaque: the proxy's log line shows the `CONNECT` |

## Running the agent by hand

The program call from `cmk -D <host>` works against the mock as it is. Add
`--debug` to see the requests, the responses and the resolved paths on stderr.
To try one endpoint without a rule, call the agent with a hand-written blob:

```sh
PYTHONPATH=. python3 -m cmk_addons.plugins.json_api.special_agent.agent_json_api \
  --endpoint '{"url": "http://localhost:8642/items",
               "pagination": {"next": ["offset", {"parameter": "offset", "page_size": 5,
                                                  "size_parameter": "limit", "total": "total"}],
                              "items": "items"},
               "extractions": [{"path": "items", "service": "Items", "aggregate": "count"}]}' \
  --debug
```

This needs a Python with the `cmk` packages, e.g. the site's own or a Checkmk
dev venv.

## Tests

`tests/test_mock_api.py` runs the real agent against the mock: API keys, the
cross-host redirect, an accepted 503, retry, both counted pagination modes, and
(where `openssl` exists) HTTPS with the mock CA, mutual TLS and the proxy. When
you add a path, add it to this page and give it a test there.
