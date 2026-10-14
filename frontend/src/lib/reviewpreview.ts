// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// What the review step reads off an extraction and the fetched sample to
// preview a rule: which settings an extraction carries (an aggregation's element
// count, a rate or an age, a host per element, an inventory target, a
// condition) and the host labels an endpoint will set.
//
// The agent (cmk_addons/plugins/json_api/special_agent/) is what really
// decides; this is the part of its behaviour the preview can reproduce from ONE
// response without guessing. Where it cannot - a hashed operator ident whose
// only handle is a translated title - the helpers here report the setting's
// presence and leave the outcome to the site. Kept in lib/ rather than in the
// component so it can be unit-tested and type-checked on a bare clone; the
// component's translated words come in as parameters.

import { labelKeyOf, resolvePath, type Json } from './jsonpaths'
import type { ExtractionValue, FilterValue, LabelValue } from './rulevalue'

/** A JSON value as the review shows it; `notFound` where the sample lacks it. */
export function formatJson(value: Json | undefined, notFound: string): string {
  if (value === undefined) {
    return notFound
  }
  if (value === null) {
    return 'null'
  }
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

/** Infer the 'Interpret the value as' choice from the serialized value.
 *
 * `['<ident>', null]` is the counter (its parameter form is a FixedValue),
 * `['<ident>', {format}]` the timestamp — the mode ident is hashed, so the shape
 * of the sub-value is what tells them apart. */
export function valueAsKind(value: unknown): 'counter' | 'timestamp' | null {
  if (!Array.isArray(value) || typeof value[0] !== 'string') {
    return null
  }
  const cfg = value[1]
  if (cfg !== null && typeof cfg === 'object' && !Array.isArray(cfg) && 'format' in cfg) {
    return 'timestamp'
  }
  return cfg === null ? 'counter' : null
}

/** How many elements an aggregation would reduce, for the preview note. A
 * wildcard path resolves to one match per element that HAS the named field —
 * which is exactly the set the site aggregates; a wildcard-free path resolves to
 * the array/object holding them. Does NOT account for a condition; see
 * `hasFilter`. */
export function elementCount(matches: { value: Json }[], path: string): number | null {
  if (path.includes('[*]')) {
    return matches.length
  }
  const value = matches[0]?.value
  if (Array.isArray(value)) {
    return value.length
  }
  if (value !== null && typeof value === 'object') {
    return Object.keys(value).length
  }
  return null
}

/** The 'one host per element' field, or null. A plain String on the wire (not a
 * hashed choice ident), so it can be read and shown directly. */
export function piggybackField(x: ExtractionValue): string | null {
  const field = x.piggyback_host
  return typeof field === 'string' && field.trim() ? field.trim() : null
}

/** Whether the extraction carries an 'Only elements matching a condition' filter.
 *
 * Only its PRESENCE is determined, never its outcome, and that is deliberate: the
 * operator is a hashed SingleChoice ident whose only handle is its title, and
 * those titles are translated ('does not equal' → 'ist ungleich'), so telling
 * 'equals' from 'matches regex' here would work in an English GUI and silently
 * mispreview in every other one. Duplicating Python's `re.fullmatch` semantics in
 * JS would be a second source of drift. A number labelled as pre-filter is honest;
 * a confidently wrong one is what this preview is meant to avoid.
 *
 * `path` and `value` are plain Strings, so presence is reliable. */
export function hasFilter(x: ExtractionValue): boolean {
  const filt = x.filter
  if (!filt || typeof filt !== 'object' || Array.isArray(filt)) {
    return false
  }
  const path = (filt as Record<string, unknown>).path
  return typeof path === 'string' && path.trim() !== ''
}

/** The inventory target of an extraction, if it has one. */
export function inventoryTarget(x: ExtractionValue): { node: string; keepService: boolean } | null {
  const inventory = x.inventory as { node?: unknown; keep_service?: unknown } | undefined
  if (!inventory || typeof inventory !== 'object' || typeof inventory.node !== 'string') {
    return null
  }
  return { node: inventory.node, keepService: Boolean(inventory.keep_service) }
}

/** Whether this field is written to the inventory INSTEAD of creating a service. */
export function inventoryOnly(x: ExtractionValue): boolean {
  const target = inventoryTarget(x)
  return target !== null && !target.keepService
}

/** Whether a label spec carries an element condition.
 *
 * The preview cannot evaluate one: FormEdit serializes the operator as a hashed
 * ident and the only thing recoverable from it is a (translated) title, so
 * guessing it would be worse than not trying. The preview therefore shows the
 * labels the spec CAN produce and marks them as conditional. */
export function hasCondition(filter: FilterValue | undefined): boolean {
  return typeof filter?.path === 'string' && filter.path !== ''
}

export interface HostLabelPreview {
  label: string
  value: string
}

/** The component's translated words for the host-label preview. */
export interface HostLabelTexts {
  /** Shown as the value of a plain path the sample does not have. */
  notFound: string
  /** Appended to a value that only appears when the label's condition matches. */
  conditional: string
}

/** The host labels an endpoint's label specs produce against the sample.
 *
 * Mirrors the agent's host-label resolution: a plain path -> one label with the
 * scalar at the path; a '[*]' path -> one label per element, keyed
 * <base>/<element> with value from value_field (default 'true'). A literal value
 * collapses a '[*]' path to ONE label keyed as configured, and an optional
 * condition decides which elements produce a label at all. `sample` is null when
 * there is none (or it does not parse); a label that depends on nothing but the
 * rule is still shown then. */
export function previewHostLabels(
  sample: Json | null,
  specs: readonly LabelValue[],
  texts: HostLabelTexts,
): HostLabelPreview[] {
  // A previewed label value, marked where a condition decides whether it is set.
  const mark = (value: string, filter: FilterValue | undefined): string =>
    hasCondition(filter) ? `${value} ${texts.conditional}` : value
  const hostLabels: HostLabelPreview[] = []
  for (const l of specs) {
    const base = labelKeyOf(l)
    const path = typeof l.path === 'string' ? l.path : ''
    const literal = typeof l.value === 'string' && l.value ? l.value : null
    const valueField = typeof l.value_field === 'string' ? l.value_field : ''
    if (!base) {
      continue
    }
    if (!path) {
      // Described by the rule alone: nothing but the condition is read.
      if (literal !== null) {
        hostLabels.push({ label: `json_api/${base}`, value: mark(literal, l.filter) })
      }
      continue
    }
    const matches = sample !== null ? resolvePath(sample, path) : []
    if (!path.includes('[*]')) {
      hostLabels.push({
        label: `json_api/${base}`,
        value: mark(
          literal ?? (matches.length ? formatJson(matches[0]!.value, texts.notFound) : texts.notFound),
          l.filter,
        ),
      })
      continue
    }
    if (literal !== null) {
      // One label for the whole collection, keyed exactly as configured - not
      // one per element - as soon as an element matches.
      if (sample === null || matches.length) {
        hostLabels.push({ label: `json_api/${base}`, value: mark(literal, l.filter) })
      }
      continue
    }
    for (const m of matches) {
      const key = m.label ? `${base}/${m.label}` : base
      let value = 'true'
      if (valueField) {
        const inner = resolvePath(m.value, valueField)
        if (!inner.length) {
          continue
        }
        value = formatJson(inner[0]!.value, texts.notFound)
      }
      hostLabels.push({ label: `json_api/${key}`, value: mark(value, l.filter) })
    }
  }
  return hostLabels
}
