// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// Shared wizard state + actions (module-level singleton). Every editable step is
// a FormEdit value rendered from the ruleset's own FormSpecs; the rule value_raw
// is assembled server-side by the visitors on create.
import { computed, reactive, ref, watch } from 'vue'

import {
  convertWizardToValueRaw,
  createHost,
  createRule,
  fetchEndpointJson,
  rulesetOverviewUrl,
  validateSpec,
  type ValidationMessage,
} from '../api/rules'
import { defaultService } from '../lib/jsonpaths'
import {
  endpointUrl,
  extractionPath,
  extractionService,
  newServices,
  type ConnectionValue,
  type ExtractionValue,
  type WizardState,
} from '../lib/rulevalue'

export type Status = { kind: 'idle' | 'busy' | 'ok' | 'err'; msg?: string; id?: string }
export type FormSpecPayload = { id: string; spec: unknown; data: unknown; validation: unknown[] }

/** The `element_default_value` template of a serialized `List` spec (for seeding
 * a fresh list entry), or an empty object as a safe fallback. */
function listElementDefault(payload: FormSpecPayload | null): Record<string, unknown> {
  const spec = payload?.spec as { element_default_value?: unknown } | undefined
  const template = spec?.element_default_value
  return template && typeof template === 'object'
    ? (structuredClone(template) as Record<string, unknown>)
    : {}
}

const currentStep = ref(1)
const state = reactive<WizardState>({ connections: [], services: [] })
const status = ref<Status>({ kind: 'idle' })

// Serialized FormSpecs from the page (endpoints List / extractions List /
// placement Dictionary). Null when the site could not serialize a spec.
const connectionSpec = ref<FormSpecPayload | null>(null)
const extractionsSpec = ref<FormSpecPayload | null>(null)
const hostLabelsSpec = ref<FormSpecPayload | null>(null)
const placementSpec = ref<FormSpecPayload | null>(null)

// Placement FormEdit value (target folder + host condition).
const placementData = ref<Record<string, unknown>>({})

/** Keep `services` paired with `connections` across add/remove. On a length
 * change we can't tell WHICH entry was removed from the array alone, so re-pair
 * each endpoint's services by its (unique, enforced) URL — deleting a middle
 * endpoint then keeps every surviving endpoint's services instead of shifting
 * them onto the wrong URL. URL edits (no length change) don't fire this, so
 * services stay put while typing. */
watch(
  () => state.connections.map((c) => endpointUrl(c).trim()),
  (urls, prevUrls) => {
    if (urls.length === (prevUrls?.length ?? -1)) {
      return
    }
    const byUrl = new Map<string, EndpointServices>()
    ;(prevUrls ?? []).forEach((url, i) => {
      if (url && !byUrl.has(url)) {
        byUrl.set(url, state.services[i]!)
      }
    })
    const old = state.services
    state.services = state.connections.map((_, j) => {
      const url = urls[j]!
      const found = url ? byUrl.get(url) : undefined
      if (found && url) {
        byUrl.delete(url)
      }
      return found ?? old[j] ?? newServices()
    })
  },
  { immediate: true },
)

function setConnectionSpec(payload: FormSpecPayload): void {
  connectionSpec.value = payload
  if (state.connections.length === 0) {
    // Seed the value from the serialized default (a List of one entry, or the
    // element default when the list default is empty) so step 1 opens with one
    // endpoint ready to fill in — native "+ Add endpoint" adds more.
    const listDefault = payload.data
    state.connections =
      Array.isArray(listDefault) && listDefault.length > 0
        ? (structuredClone(listDefault) as ConnectionValue[])
        : [listElementDefault(payload)]
  }
  // The connections watcher (immediate) re-pairs `services` to the new list.
}
function setExtractionsSpec(payload: FormSpecPayload): void {
  extractionsSpec.value = payload
}
function setHostLabelsSpec(payload: FormSpecPayload): void {
  hostLabelsSpec.value = payload
}
function setPlacementSpec(payload: FormSpecPayload): void {
  placementSpec.value = payload
  if (Object.keys(placementData.value).length === 0 && payload.data && typeof payload.data === 'object') {
    placementData.value = structuredClone(payload.data) as Record<string, unknown>
  }
}

// The folder chooser's value is a Setup folder path ("" = Main, "a/b" nested).
// The REST rule API wants a leading-delimiter path ("/" = Main, "/a/b").
const folder = computed(() => {
  const value = placementData.value.folder
  const path = typeof value === 'string' ? value : ''
  return path ? `/${path}` : '/'
})
// The host is a CascadingSingleChoice:
//   ['existing', <name string>]
//   ['new', { host_name, site }]
//   ['folder', null]            -> scope the rule to the target folder only
const hostChoice = computed<{ mode: 'existing' | 'new' | 'folder'; name: string; site?: string }>(
  () => {
    const value = placementData.value.host
    if (Array.isArray(value)) {
      const [mode, sub] = value
      if (mode === 'folder') {
        return { mode: 'folder', name: '' }
      }
      if (mode === 'new' && sub && typeof sub === 'object') {
        const obj = sub as Record<string, unknown>
        return {
          mode: 'new',
          name: typeof obj.host_name === 'string' ? obj.host_name : '',
          site: typeof obj.site === 'string' ? obj.site : undefined,
        }
      }
      if (mode === 'existing' && typeof sub === 'string') {
        return { mode: 'existing', name: sub }
      }
    }
    return { mode: 'existing', name: '' }
  },
)
const host = computed(() => hostChoice.value.name)

const validExtractions = computed(() =>
  state.services.flatMap((s) => s.extractions).filter((x) => extractionPath(x).trim()),
)

// A minimum on-screen time for loaders so a fast fetch doesn't flash by.
const MIN_LOADING_MS = 1000
const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

// Field-picker samples, fetched server-side (see fetchAllSamples).
const samplesLoading = ref(false)
const sampleLoading = reactive<Record<number, boolean>>({})
const sampleErrors = reactive<Record<number, string>>({})
// The response headers of the last successful fetch, per endpoint index. Kept
// out of the wizard state on purpose: unlike the sample JSON they are never
// edited or submitted - they only feed the picker's Headers tab, so an
// '@header.' path can be clicked rather than typed from memory.
const sampleHeaders = reactive<Record<number, Record<string, string>>>({})

async function fetchSample(i: number): Promise<void> {
  const url = endpointUrl(state.connections[i])
  const services = state.services[i]
  if (!url.trim() || !services) {
    return
  }
  delete sampleErrors[i]
  sampleLoading[i] = true
  try {
    // Hold the loader for at least MIN_LOADING_MS (no flicker on a fast fetch).
    const [result] = await Promise.all([
      fetchEndpointJson(state.connections[i]),
      delay(MIN_LOADING_MS),
    ])
    if (result.ok) {
      services.sampleJson = JSON.stringify(result.json, null, 2)
      sampleHeaders[i] = result.headers ?? {}
    } else {
      sampleErrors[i] = result.error ?? 'fetch failed'
      // A failed refetch must not leave the previous response's headers on
      // offer: they no longer describe anything the endpoint returned.
      delete sampleHeaders[i]
    }
  } finally {
    sampleLoading[i] = false
  }
}

async function fetchAllSamples(): Promise<void> {
  samplesLoading.value = true
  try {
    await Promise.all(state.connections.map((_, i) => fetchSample(i)))
  } finally {
    samplesLoading.value = false
  }
}

watch(currentStep, (next, prev) => {
  // Entering the Services step (3) from Endpoints (2): fetch all samples.
  if (next === 3 && prev === 2) {
    void fetchAllSamples()
  }
})

// Step 1 pre-flight: probe endpoints (via the proxy), keyed by list index.
type Preflight = { kind: 'loading' | 'ok' | 'err'; msg?: string }
const preflight = reactive<Record<number, Preflight>>({})
const validating = ref(false)

/** A proper http(s) URI (scheme http/https + a host). Mirrors the ruleset's
 * `_validate_url` so the wizard flags a bad URL before it ever hits the proxy. */
function isHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') && Boolean(parsed.host)
  } catch {
    return false
  }
}

async function testEndpoint(i: number): Promise<void> {
  const url = endpointUrl(state.connections[i])
  if (!url.trim()) {
    delete preflight[i]
    return
  }
  if (!isHttpUrl(url)) {
    preflight[i] = { kind: 'err', msg: "Enter a valid http(s) URL, e.g. 'https://host/path'" }
    return
  }
  preflight[i] = { kind: 'loading' }
  const result = await fetchEndpointJson(state.connections[i])
  preflight[i] = result.ok
    ? { kind: 'ok', msg: `HTTP ${result.status} · valid JSON` }
    : { kind: 'err', msg: result.error }
}

async function testAllEndpoints(): Promise<void> {
  validating.value = true
  try {
    // Hold the spinner for at least MIN_LOADING_MS (no flicker on a fast probe).
    await Promise.all([delay(MIN_LOADING_MS), ...state.connections.map((_, i) => testEndpoint(i))])
  } finally {
    validating.value = false
  }
}

/** Keep integers integer, round the rest to 2 decimals (for seeded levels). */
function niceRound(n: number): number {
  return Number.isInteger(n) ? n : Math.round(n * 100) / 100
}

/** A fresh extraction entry seeded from the extractions List's element default,
 * with the picked path + a derived service name, and — for a numeric sample —
 * fixed upper `levels_upper` warn/crit with headroom. The user refines the rest
 * in the form.
 *
 * NOTE: we deliberately do NOT pre-seed `unit` or `match`. Those are a
 * SingleChoice / CascadingSingleChoice, whose values are HASHED idents on the
 * FormEdit wire (not the raw 'percent' / 'must_match' name); a raw pre-seed is
 * rejected on submit as "Invalid choice". `levels_upper` (SimpleLevels) is not
 * hashed, so seeding ['fixed', [w, c]] is safe. The user picks unit / matching
 * from the native dropdowns, which store the correct hashed ident. */
function newExtractionEntry(path: string, valueType?: string, sampleValue?: unknown): ExtractionValue {
  const entry: ExtractionValue = {
    ...listElementDefault(extractionsSpec.value),
    service: defaultService(path),
    path,
  }
  if (valueType === 'number' && typeof sampleValue === 'number') {
    // Upper levels with headroom above the current value; SimpleLevels value
    // shape is ['fixed', [warn, crit]]. Seed off a positive magnitude so a
    // sampled 0 (or negative) doesn't produce [0,0]/inverted levels that make
    // the service permanently CRIT — warn < crit, both > 0.
    const base = Math.abs(sampleValue) || 1
    entry.levels_upper = ['fixed', [niceRound(base * 1.5), niceRound(base * 2)]]
  }
  return entry
}

function togglePath(
  endpointIndex: number,
  path: string,
  valueType?: string,
  sampleValue?: unknown,
): void {
  const services = state.services[endpointIndex]
  if (!services) {
    return
  }
  // Reassign (not mutate) so the bound extractions FormEdit's `watch(data)`
  // fires and rebuilds its parallel element-validation to match the new length.
  const existing = services.extractions.findIndex((x) => extractionPath(x) === path)
  services.extractions =
    existing >= 0
      ? services.extractions.filter((_, idx) => idx !== existing)
      : [...services.extractions, newExtractionEntry(path, valueType, sampleValue)]
}

/** Add a HOST label from the picker's "+ host label" button. Host labels are
 * endpoint-level (resolved from the response root) and need NO service, so they
 * live in the per-endpoint `hostLabels` store rather than on an extraction. */
function addHostLabel(endpointIndex: number, path: string): void {
  const services = state.services[endpointIndex]
  if (!services) {
    return
  }
  const existing = services.hostLabels ?? []
  if (existing.some((l) => l.path === path)) {
    return // already added
  }
  services.hostLabels = [...existing, { path }]
}

/** The label key the agent will use for a stored label spec: the explicit key,
 * else the path's last segment (mirrors the agent's _label_key_from_path). */
function labelKeyOf(spec: Record<string, unknown>): string {
  if (typeof spec.key === 'string' && spec.key) {
    return spec.key
  }
  const path = typeof spec.path === 'string' ? spec.path : ''
  const tokens = path.replace(/\[\*\]/g, '').match(/[A-Za-z0-9_]+|\['[^']*'\]|\["[^"]*"\]/g)
  if (!tokens || !tokens.length) {
    return path
  }
  const last = tokens[tokens.length - 1]!
  return last.startsWith("['") || last.startsWith('["') ? last.slice(2, -2) : last
}

/** Entry in an endpoint's label summary — a host label (endpoint-level, `hi`
 * indexes hostLabels) or a service label (`xi`/`li` locate it on an extraction). */
type LabelSummary =
  | { key: string; scope: 'host'; path: string; hi: number }
  | { key: string; scope: 'service'; path: string; xi: number; li: number }

/** A flat readout of every label configured on an endpoint — host labels (from
 * the endpoint store) plus service labels (from each extraction) — for the
 * picker's summary list; shown as json_api/<key>. */
function labelsForEndpoint(endpointIndex: number): LabelSummary[] {
  const services = state.services[endpointIndex]
  if (!services) {
    return []
  }
  const out: LabelSummary[] = []
  ;(services.hostLabels ?? []).forEach((spec, hi) => {
    out.push({ key: labelKeyOf(spec), scope: 'host', path: spec.path, hi })
  })
  services.extractions.forEach((ext, xi) => {
    const labels = Array.isArray(ext.labels) ? (ext.labels as Record<string, unknown>[]) : []
    labels.forEach((spec, li) => {
      out.push({
        key: labelKeyOf(spec),
        scope: 'service',
        path: typeof spec.path === 'string' ? spec.path : '',
        xi,
        li,
      })
    })
  })
  return out
}

/** Remove a configured label (host label from the endpoint store, or a service
 * label from its extraction). */
function removeEndpointLabel(endpointIndex: number, entry: LabelSummary): void {
  const services = state.services[endpointIndex]
  if (!services) {
    return
  }
  if (entry.scope === 'host') {
    services.hostLabels = (services.hostLabels ?? []).filter((_, i) => i !== entry.hi)
    return
  }
  const ext = services.extractions[entry.xi]
  if (!ext) {
    return
  }
  const labels = Array.isArray(ext.labels) ? (ext.labels as Record<string, unknown>[]) : []
  const updated = { ...ext, labels: labels.filter((_, i) => i !== entry.li) }
  services.extractions = services.extractions.map((x, i) => (i === entry.xi ? updated : x))
}

// URLs that appear on more than one endpoint (mirrors the ruleset's
// _validate_unique_endpoints; enforced server-side too at rule creation).
const duplicateUrls = computed(() => {
  const urls = state.connections.map((c) => endpointUrl(c).trim()).filter(Boolean)
  return [...new Set(urls.filter((url, i) => urls.indexOf(url) !== i))]
})

// Inline field validation for the endpoints List, fed back into FormEdit's
// backend-validation so errors (e.g. an empty URL) bind to the exact field.
const connectionValidation = ref<ValidationMessage[]>([])
async function validateConnection(): Promise<boolean> {
  if (state.connections.length === 0) {
    return false
  }
  // Field-level validation via the ruleset's own visitor (empty/invalid URL,
  // required fields) — errors render inline on the offending field.
  connectionValidation.value = await validateSpec('connections', state.connections)
  if (connectionValidation.value.length > 0) {
    return false
  }
  // No two endpoints may target the same URL (list-level, shown as a banner).
  if (duplicateUrls.value.length > 0) {
    return false
  }
  // Probe them all (spinner shows while `validating`), then only allow the
  // transition when EVERY endpoint responded OK — a failing endpoint blocks
  // Next and stays flagged ✗ in the results list.
  await testAllEndpoints()
  return state.connections.every((_, i) => preflight[i]?.kind === 'ok')
}
async function validateServices(): Promise<boolean> {
  // At least one service OR one host label anywhere — an endpoint may contribute
  // only host labels (no monitored service), which is a valid configuration.
  const hasHostLabel = state.services.some((s) => (s.hostLabels?.length ?? 0) > 0)
  return validExtractions.value.length > 0 || hasHostLabel
}
function extractionLabel(x: ExtractionValue): string {
  return extractionService(x) || '(unnamed service)'
}

const conditionsInvalid = ref(false)
async function validateConditions(): Promise<boolean> {
  // "Apply to the whole target folder" needs no host; the other modes require a
  // host name (existing binding or new-host name).
  const ok = hostChoice.value.mode === 'folder' || hostChoice.value.name.trim().length > 0
  conditionsInvalid.value = !ok
  return ok
}

async function createRuleOnSite(): Promise<void> {
  status.value = { kind: 'busy' }
  if (hostChoice.value.mode !== 'folder' && !hostChoice.value.name.trim()) {
    status.value = { kind: 'err', msg: 'Choose a host, or apply to the whole folder, on the Conditions step.' }
    return
  }
  const payload = {
    endpoints: state.connections.map((connection, i) => ({
      connection,
      extractions: state.services[i]?.extractions ?? [],
      host_labels: state.services[i]?.hostLabels ?? [],
    })),
    // Placement goes through the visitor too — its SingleChoice fields (folder,
    // site) are hashed tokens on the wire; only the visitor yields real values.
    placement: placementData.value,
  }
  // Server-side: FormSpec visitors -> value_raw + real placement values.
  const converted = await convertWizardToValueRaw(payload)
  if (!converted.ok || !converted.valueRaw) {
    status.value = { kind: 'err', msg: converted.error ?? 'could not build the rule' }
    return
  }

  // Real placement (folder path / host choice / site) from the visitor.
  const placement = converted.placement ?? {}
  const folderPath =
    typeof placement.folder === 'string' && placement.folder ? `/${placement.folder}` : '/'
  let hostMode = 'existing'
  let hostName = ''
  let hostSite: string | undefined
  if (Array.isArray(placement.host)) {
    hostMode = String(placement.host[0])
    const sub = placement.host[1]
    if (hostMode === 'new' && sub && typeof sub === 'object') {
      const obj = sub as Record<string, unknown>
      hostName = typeof obj.host_name === 'string' ? obj.host_name : ''
      hostSite = typeof obj.site === 'string' ? obj.site : undefined
    } else if (typeof sub === 'string') {
      hostName = sub
    }
  }
  // "Apply to the whole target folder": no host to require or create, and the
  // rule carries no host_name condition — the folder placement is the scope.
  const folderOnly = hostMode === 'folder'
  if (!folderOnly && !hostName.trim()) {
    status.value = { kind: 'err', msg: 'Choose a host, or apply to the whole folder, on the Conditions step.' }
    return
  }

  // "New host": create it (in the target folder) before binding the rule.
  if (hostMode === 'new') {
    const created = await createHost({ folder: folderPath, hostName: hostName.trim(), site: hostSite })
    if (!created.ok) {
      status.value = { kind: 'err', msg: `${created.status}: ${created.error ?? 'host creation failed'}` }
      return
    }
  }
  const result = await createRule({
    ruleset: 'special_agents:json_api',
    folder: folderPath,
    valueRaw: converted.valueRaw,
    conditions: folderOnly
      ? undefined
      : { host_name: { match_on: [hostName], operator: 'one_of' } },
  })
  status.value = result.ok
    ? { kind: 'ok', id: result.id }
    : { kind: 'err', msg: `${result.status}: ${result.error ?? 'failed'}` }

  // On success the wizard is locked (no going back) — briefly show the
  // confirmation, then switch to the ruleset overview (rule + activate changes).
  if (result.ok) {
    window.setTimeout(() => {
      window.location.href = rulesetOverviewUrl()
    }, 1200)
  }
}

export function useExplorer() {
  return {
    currentStep,
    state,
    folder,
    host,
    hostChoice,
    validateConditions,
    conditionsInvalid,
    status,
    validExtractions,
    duplicateUrls,
    connectionValidation,
    connectionSpec,
    extractionsSpec,
    hostLabelsSpec,
    placementSpec,
    placementData,
    setConnectionSpec,
    setExtractionsSpec,
    setHostLabelsSpec,
    setPlacementSpec,
    validating,
    samplesLoading,
    sampleLoading,
    sampleErrors,
    sampleHeaders,
    preflight,
    testEndpoint,
    testAllEndpoints,
    fetchSample,
    fetchAllSamples,
    togglePath,
    addHostLabel,
    labelsForEndpoint,
    removeEndpointLabel,
    validateConnection,
    validateServices,
    extractionLabel,
    createRuleOnSite,
  }
}
