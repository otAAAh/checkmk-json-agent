// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// Our own thin client for Checkmk's PUBLIC REST API. When the page is served
// in-site, `credentials: 'include'` authenticates as the logged-in user via the
// session cookie — no automation secret, no dependency on any internal frontend.

export interface CreateRuleParams {
  ruleset: string
  folder: string
  /** The rule value as a Python-literal string (the endpoint runs ast.literal_eval). */
  valueRaw: string
  properties?: Record<string, unknown>
  conditions?: unknown
}

export interface CreateRuleResult {
  ok: boolean
  status: number
  id?: string
  error?: string
}

/**
 * Resolve the site's REST API base from the current URL. The page is served at
 * `<origin><site>/check_mk/json_api/wizard.html`, so the API lives at
 * `<origin><site>/check_mk/api/1.0`. Falls back to a relative base if the marker
 * is absent (e.g. opened outside a site).
 */
function checkmkBase(): string {
  const marker = '/check_mk/'
  const path = window.location.pathname
  const idx = path.indexOf(marker)
  const sitePrefix = idx >= 0 ? path.slice(0, idx) : ''
  return `${window.location.origin}${sitePrefix}/check_mk`
}

export function apiBase(): string {
  return `${checkmkBase()}/api/1.0`
}

/** The Setup "edit ruleset" overview for our special agent — where the wizard
 * sends the user after a successful create (to see the rule + activate changes). */
export function rulesetOverviewUrl(ruleset = 'special_agents:json_api'): string {
  return `${checkmkBase()}/wato.py?mode=edit_ruleset&varname=${encodeURIComponent(ruleset)}`
}

export interface EndpointJson {
  ok: boolean
  json?: unknown
  status?: number
  error?: string
  /** Response headers, for the picker's Headers tab — an '@header.' path can
   * monitor one (a rate-limit budget, a Last-Modified age). Absent when the
   * request failed, or when the sample was pasted by hand. */
  headers?: Record<string, string>
}

/**
 * Fetch an endpoint's JSON via the server-side proxy (json_explorer_fetch.py) —
 * the browser can't reach operator endpoints (CORS / internal), so the Checkmk
 * server fetches it, like the agent does. Uses the logged-in session cookie.
 */
/** Visitor-converted placement (real folder path + host choice), from the
 * create endpoint. `host` is ['existing', name] | ['new', {host_name, site}]. */
export interface ConvertedPlacement {
  folder?: string
  host?: unknown
}

export interface ConvertResult {
  ok: boolean
  valueRaw?: string
  placement?: ConvertedPlacement
  error?: string
}

/**
 * Convert the wizard state (per-endpoint FormEdit connection value + picker
 * extractions) into the rule `value_raw`, server-side via the FormSpec visitor
 * (json_explorer_create.py). Server-side validation errors come back as .error.
 */
export async function convertWizardToValueRaw(payload: unknown): Promise<ConvertResult> {
  const target = `${checkmkBase()}/json_explorer_create.py`
  try {
    const res = await fetch(target, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ payload: JSON.stringify(payload) }),
    })
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      return { ok: false, error: text || `HTTP ${res.status}` }
    }
    const env = (await res.json()) as {
      result_code: number
      result: { ok: boolean; value_raw?: string; placement?: ConvertedPlacement; error?: string } | string
    }
    if (env.result_code !== 0 || typeof env.result === 'string') {
      return { ok: false, error: typeof env.result === 'string' ? env.result : 'conversion failed' }
    }
    // The page returns {ok:false, error} on validation problems (with the
    // offending field) — surface it instead of the generic fallback.
    if (!env.result.ok || !env.result.value_raw) {
      return { ok: false, error: env.result.error ?? 'conversion failed' }
    }
    return { ok: true, valueRaw: env.result.value_raw, placement: env.result.placement }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

export interface ValidationMessage {
  location: string[]
  message: string
  replacement_value: unknown
}

/**
 * Validate a FormEdit value against one of the wizard's FormSpecs (server-side
 * visitor) and return the ValidationMessages, ready to feed back into FormEdit's
 * `backend-validation` so errors bind to the field they belong to. Returns []
 * on transport failure (don't block the wizard on a validator outage).
 */
export async function validateSpec(spec: string, value: unknown): Promise<ValidationMessage[]> {
  const target = `${checkmkBase()}/json_explorer_validate.py`
  try {
    const res = await fetch(target, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ spec, value: JSON.stringify(value) }),
    })
    if (!res.ok) {
      return []
    }
    const env = (await res.json()) as {
      result_code: number
      result: { ok: boolean; messages?: ValidationMessage[] } | string
    }
    if (env.result_code !== 0 || typeof env.result === 'string') {
      return []
    }
    return env.result.messages ?? []
  } catch {
    return []
  }
}

/**
 * Fetch an endpoint's JSON via the server-side proxy, sending the full
 * connection FormEdit value so the proxy uses the configured method / body /
 * request headers / TLS verification / auth (basic or bearer, resolved from the
 * password store) — the preview then matches what the agent will actually see.
 */
export async function fetchEndpointJson(connection: unknown): Promise<EndpointJson> {
  const target = `${checkmkBase()}/json_explorer_fetch.py`
  try {
    const res = await fetch(target, {
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ connection: JSON.stringify(connection) }),
    })
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      return { ok: false, error: text || `HTTP ${res.status}` }
    }
    // AjaxPage wraps the return in {result_code, result, severity}.
    const env = (await res.json()) as { result_code: number; result: EndpointJson | string }
    if (env.result_code !== 0 || typeof env.result === 'string') {
      return { ok: false, error: typeof env.result === 'string' ? env.result : 'fetch failed' }
    }
    return env.result
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

export async function createRule(params: CreateRuleParams): Promise<CreateRuleResult> {
  const body: Record<string, unknown> = {
    ruleset: params.ruleset,
    folder: params.folder,
    properties: params.properties ?? { disabled: false },
    value_raw: params.valueRaw,
  }
  if (params.conditions) {
    body.conditions = params.conditions
  }
  try {
    const res = await fetch(`${apiBase()}/domain-types/rule/collections/all`, {
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    let json: Record<string, unknown> = {}
    try {
      json = (await res.json()) as Record<string, unknown>
    } catch {
      // non-JSON error body; fall through to statusText
    }
    if (!res.ok) {
      // Include the nested per-field errors (REST puts them in `fields`) so an
      // attributes validation failure names the exact offending key/value.
      const base = (json.detail as string) || (json.title as string) || res.statusText
      const fields = json.fields && typeof json.fields === 'object' ? json.fields : null
      const detail = fields ? `${base} — ${JSON.stringify(fields)}` : String(base)
      return { ok: false, status: res.status, error: detail }
    }
    return { ok: true, status: res.status, id: json.id as string | undefined }
  } catch (e) {
    return { ok: false, status: 0, error: e instanceof Error ? e.message : String(e) }
  }
}

export interface CreateHostParams {
  /** REST folder notation ("/" = Main, "/a/b"). */
  folder: string
  hostName: string
  /** Monitored-by site id (distributed setup); sets the host's `site` attribute. */
  site?: string
}

/** Create a Setup host via the public REST API (used when the wizard's Conditions
 * step chooses "create a new host"). Treats an existing host (400) as success so
 * re-running the wizard is idempotent. */
export async function createHost(params: CreateHostParams): Promise<CreateRuleResult> {
  try {
    // A JSON API host is monitored ONLY via the special agent (which runs on the
    // Checkmk server against the rule's URL) — so no Checkmk agent and no IP:
    //   tag_agent=special-agents  -> API integrations only, no Checkmk agent
    //                                (avoids a failing "Check_MK" agent service)
    //   tag_address_family=no-ip  -> no IP/DNS, no PING service
    const attributes: Record<string, unknown> = {
      tag_agent: 'special-agents',
      tag_address_family: 'no-ip',
    }
    if (params.site) {
      attributes.site = params.site
    }
    const res = await fetch(`${apiBase()}/domain-types/host_config/collections/all`, {
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({
        folder: params.folder,
        host_name: params.hostName,
        attributes,
      }),
    })
    let json: Record<string, unknown> = {}
    try {
      json = (await res.json()) as Record<string, unknown>
    } catch {
      // non-JSON error body; fall through to statusText
    }
    if (!res.ok) {
      const base = (json.detail as string) || (json.title as string) || res.statusText
      // A name clash means the host already exists — fine, bind to it.
      if (res.status === 400 && /exist/i.test(String(base))) {
        return { ok: true, status: res.status }
      }
      // Surface the nested per-field errors (REST puts them in `fields`) so an
      // attributes failure names the exact offending key/value.
      const fields = json.fields && typeof json.fields === 'object' ? json.fields : null
      const detail = fields ? `${base} — ${JSON.stringify(fields)}` : String(base)
      return { ok: false, status: res.status, error: detail }
    }
    return { ok: true, status: res.status, id: json.id as string | undefined }
  } catch (e) {
    return { ok: false, status: 0, error: e instanceof Error ? e.message : String(e) }
  }
}
