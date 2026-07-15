// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// Wizard data model. Every editable step is a FormEdit value rendered from the
// ruleset's own FormSpecs:
//   - endpoints: one `List` FormEdit (connection per entry) — step 1
//   - per endpoint: an `extractions` `List` FormEdit + the JSON picker — step 2
//   - placement: a small synthesized Dictionary FormEdit — step 3
// The per-endpoint extractions/sample live in a `services` array parallel to
// `connections` (same index = same endpoint). The rule value_raw is assembled
// server-side by the visitors (json_explorer_create.py).

/** One entry of the endpoints `List` value (the connection FormSpec). */
export type ConnectionValue = Record<string, unknown>

/** One entry of an endpoint's `extractions` `List` value (service/path/...). */
export type ExtractionValue = Record<string, unknown>

/** One endpoint `host_labels` entry ({path, optional key, optional value field
 * for '[*]' paths}). */
export type LabelValue = { path: string; key?: string; value_field?: string }

/** Per-endpoint state that is NOT part of the connection FormSpec. */
export interface EndpointServices {
  /** FormEdit value for the endpoint's `extractions` `List`. */
  extractions: ExtractionValue[]
  /** Endpoint-level host labels (decoupled from any service) — the picker's
   * "+ host label" writes here; resolved from the response root. */
  hostLabels: LabelValue[]
  /** Transient: fetched/pasted sample for the field picker. */
  sampleJson: string
}

export interface WizardState {
  /** FormEdit v-model value for the endpoints `List` FormSpec. */
  connections: ConnectionValue[]
  /** Parallel to `connections` (same index = same endpoint). */
  services: EndpointServices[]
}

export function newServices(): EndpointServices {
  return { extractions: [], hostLabels: [], sampleJson: '' }
}

/** The endpoint URL out of a connection FormEdit entry (for picker/preflight). */
export function endpointUrl(connection: ConnectionValue | undefined): string {
  const url = connection?.url
  return typeof url === 'string' ? url : ''
}

/** The JSON path of an extraction entry (used to sync with the picker). */
export function extractionPath(entry: ExtractionValue | undefined): string {
  const path = entry?.path
  return typeof path === 'string' ? path : ''
}

/** The service name of an extraction entry (used for the review summary). */
export function extractionService(entry: ExtractionValue | undefined): string {
  const service = entry?.service
  return typeof service === 'string' ? service : ''
}
