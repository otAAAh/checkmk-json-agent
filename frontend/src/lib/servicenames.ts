// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// What a field's service will actually be CALLED on the site.
//
// The wizard's review step previews the services a rule creates, and a name it
// gets wrong is worse than one it does not show: the operator checks the
// preview against what they meant, not against what the agent will do. Two
// settings move the names, and both were missing here — an endpoint prefix, and
// a shared service that takes the name over and pushes the field's own name
// down to its line.
//
// Ported from the agent's _service_prefix / _shared_service / _prefixed and the
// naming in _extract (cmk_addons/plugins/json_api/libexec/agent_json_api), which
// is the only place these names are really decided. Kept in lib/ rather than in
// the component so it can be unit-tested and type-checked on a bare clone.

import type { ConnectionValue, ExtractionValue } from './rulevalue'

/** The endpoint name put in front of this endpoint's field service names, or ''.
 *
 * Opt-in per endpoint, and it needs a name: the agent deliberately does not
 * fall back to the URL (a URL in a service description travels into
 * notifications, availability reports and the metric paths on disk). */
export function servicePrefix(connection: ConnectionValue | undefined): string {
  if (!connection?.service_prefix) {
    return ''
  }
  const name = connection['name']
  return typeof name === 'string' && name.trim() ? name.trim() : ''
}

/** The shared service this field reports INTO, or null for one of its own. */
export function sharedService(extraction: ExtractionValue): string | null {
  const group = extraction['group']
  return typeof group === 'string' && group.trim() ? group.trim() : null
}

export interface FieldNames {
  /** The service this field ends up on. */
  service: string
  /** Its name WITHIN that service, or null where the field IS the service. */
  line: string | null
}

/** The service (and line) one field is reported under, before any `[*]` label.
 *
 * Naming a shared service moves the names one step along: that service is what
 * the field reports into, and the field's own name becomes the label of its
 * line inside it. The endpoint prefix then goes in front of whatever that
 * leaves, so an endpoint cannot end up with some of its services prefixed and
 * some not. */
export function fieldNames(
  extraction: ExtractionValue,
  fieldName: string,
  prefix: string,
): FieldNames {
  const shared = sharedService(extraction)
  const base = shared ?? fieldName
  return {
    service: prefix ? `${prefix} ${base}` : base,
    line: shared === null ? null : fieldName,
  }
}

/** The names for ONE element of a `[*]` expansion.
 *
 * Where the element becomes its own host, or reports into a shared service, the
 * element label does NOT go on the service name: a piggyback host carries the
 * identity itself, and a shared service fans out into lines rather than into
 * services. Only the remaining case — one service per element on the polling
 * host — appends the label to the service. */
export function elementNames(
  names: FieldNames,
  label: string | undefined,
  { perElementHost, fansOut }: { perElementHost: boolean; fansOut: boolean },
): FieldNames {
  if (names.line !== null) {
    return { service: names.service, line: label && fansOut ? `${names.line} ${label}` : names.line }
  }
  if (perElementHost || !fansOut || !label) {
    return names
  }
  return { service: `${names.service} ${label}`, line: null }
}
