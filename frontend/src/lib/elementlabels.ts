// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// What each element of a `[*]` expansion is CALLED on the site, and what is
// wrong with that name as far as the sample can tell.
//
// A '[*]' field creates one service per element, told apart by a suffix: the
// field its 'label_path' points at, else the array index (or object key). The
// agent guarantees the suffixes are unique by appending the element's position
// to EVERY occurrence of a repeated one — 'web [0]', 'web [3]' — which keeps the
// services apart but ties them to the order the API happens to list its
// elements in: reorder the array and the two services swap readings. Nothing on
// the site says so; the sample here can, before the rule exists.
//
// Ported from the agent's _expand_wildcards / _element_labels
// (cmk_addons/plugins/json_api/libexec/agent_json_api), the only place these
// names are really decided: the label path is resolved at EVERY wildcard level
// against that level's element, a repeat is suffixed within its own container
// (two pods may each have a container called 'app'), and the per-level parts
// are joined with ' / '. With a host per element (_piggyback_host, resolved
// against the leaf element) only the elements whose host does not resolve keep
// the suffix - they stay on the polling host - so only their names are judged.
// The shared cases in tests/fixtures/label_path_cases.json hold both sides to
// that. The standalone Explorer mirrors this file.

import { isRecord, tokenizePath, type Json, type Step } from './jsonpaths'

/** One value found at a '[*]' path, with the name suffix the site gives it. */
export interface LabelledValue {
  label: string
  value: Json
  /** The piggyback host the element becomes, when a host field is set and
   * resolves; null otherwise - and then its service stays on the polling host
   * under the labelled name. */
  host: string | null
}

/** A label value shared by several elements of ONE container. */
export interface DuplicateLabel {
  label: string
  /** The elements that share it, by position (index or key; ' / '-joined when nested). */
  elements: string[]
  /** The suffix each of them gets instead, in the same order: 'web [0]', 'web [3]'. */
  names: string[]
}

/** What the sample says about the chosen label path. Elements are named by
 * position — the index or key, ' / '-joined across nested wildcards — because
 * that is the one thing about them that is certain. */
export interface LabelIssues {
  /** Repeated labels: the site suffixes every occurrence with its position. */
  duplicates: DuplicateLabel[]
  /** No such field in the element: the site names it by its position instead. */
  missing: string[]
  /** The field is an empty string: the element gets no suffix at all. */
  empty: string[]
  /** The field is an object or a list: its whole rendering lands in the name. */
  nonScalar: string[]
  /** With a host per element: the elements whose host field does not resolve,
   * so their services stay on the polling host under the labelled name. */
  fallback: string[]
  /** The field is a whole number beyond 2^53: the browser has rounded it, so
   * whether two of them are equal cannot be told here (the site reads them
   * exactly). Never reported as a duplicate. */
  imprecise: string[]
}

export interface LabelledResolution {
  values: LabelledValue[]
  issues: LabelIssues
}

/** Python's repr() of a JSON value, near enough for a service name.
 *
 * The agent names an element with `str(value)`, which for anything but a string
 * is its repr: `True`, `None`, `{'a': 1}`. A JSON number cannot be told from a
 * whole float once JavaScript has parsed it ('1.0' arrives as 1), so '1.0' is
 * shown as '1' — the one place the preview may differ, and only for an API that
 * names its elements by a float. */
function pyRepr(value: Json): string {
  if (value === null) return 'None'
  if (value === true) return 'True'
  if (value === false) return 'False'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') {
    // repr() picks double quotes only when the text has a ' and no ".
    const quote = value.includes("'") && !value.includes('"') ? '"' : "'"
    const body = value.replace(/\\/g, '\\\\').replace(quote === "'" ? /'/g : /"/g, `\\${quote}`)
    return `${quote}${body}${quote}`
  }
  if (Array.isArray(value)) return `[${value.map(pyRepr).join(', ')}]`
  return `{${Object.entries(value)
    .map(([k, v]) => `${pyRepr(k)}: ${pyRepr(v)}`)
    .join(', ')}}`
}

/** Python's str() of a JSON value — what the agent puts in the service name. */
export function pyStr(value: Json): string {
  return typeof value === 'string' ? value : pyRepr(value)
}

/** Walk key/index steps (no wildcard) from `node`; undefined when not found. */
function walk(node: Json, steps: Step[]): { value: Json } | undefined {
  let current = node
  for (const step of steps) {
    if (step.kind === 'key') {
      // Own keys only: `in` would also find 'constructor', 'toString', ...
      if (!isRecord(current) || !Object.hasOwn(current, step.key)) return undefined
      current = current[step.key]!
    } else if (step.kind === 'index') {
      if (!Array.isArray(current) || step.index >= current.length) return undefined
      current = current[step.index]!
    } else {
      return undefined
    }
  }
  return { value: current }
}

// The agent's _PATH_TOKEN, SCANNED the way its _resolve_path does it: the
// tokens it finds are read and whatever lies between them is skipped, so
// 'meta..name' is 'meta.name' and 'a[*]' looks for a key called '*'. The value
// path goes through the strict tokenizePath on purpose (the picker must not
// preview a path it did not understand), but a label path is typed by hand and
// only the agent's reading of it decides the names - a stricter one here would
// warn about names the site gets right.
const AGENT_TOKEN = /\['([^']*)'\]|\["([^"]*)"\]|\[(\d+)\]|([^.[\]]+)/g

/** The agent's _resolve_path, token for token: {value} when found. */
function resolveAsAgent(node: Json, path: string): { value: Json } | undefined {
  let cleaned = path.trim()
  if (cleaned.startsWith('$.')) cleaned = cleaned.slice(2)
  else if (cleaned.startsWith('$')) cleaned = cleaned.slice(1)
  let current = node
  for (const m of cleaned.matchAll(AGENT_TOKEN)) {
    if (m[3] !== undefined) {
      const index = Number(m[3])
      if (!Array.isArray(current) || index >= current.length) return undefined
      current = current[index]!
    } else {
      // Tested against undefined, not for truthiness: [''] is a key too.
      const key = m[1] ?? m[2] ?? m[4]!
      if (!isRecord(current) || !Object.hasOwn(current, key)) return undefined
      current = current[key]!
    }
  }
  return { value: current }
}

/** (default label, element) pairs for a wildcard-expandable container, or null. */
function pairsOf(container: Json): Array<[string, Json]> | null {
  if (Array.isArray(container)) return container.map((element, index) => [String(index), element])
  if (isRecord(container)) return Object.entries(container)
  return null
}

const noIssues = (): LabelIssues => ({
  duplicates: [],
  missing: [],
  empty: [],
  nonScalar: [],
  fallback: [],
  imprecise: [],
})

// The agent's _HOST_NAME_INVALID: a host name ends up in file paths and config.
const HOST_NAME_INVALID = /[^-0-9A-Za-z_.]/g

/** The agent's _label_value: a JSON scalar as text, null for null/object/list. */
function labelValue(value: Json): string | null {
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') return value
  return null
}

/** The agent's _piggyback_host: the host an element becomes, or null when its
 * field is missing, not a scalar, or sanitises to nothing - the element then
 * stays on the polling host. */
export function piggybackHost(element: Json, hostPath: string): string | null {
  if (!hostPath.trim()) return null
  const found = resolveAsAgent(element, hostPath.trim())
  if (!found) return null
  const text = labelValue(found.value)
  if (text === null) return null
  const cleaned = text
    .trim()
    .replace(HOST_NAME_INVALID, '_')
    .replace(/^[_.]+|[_.]+$/g, '')
  return cleaned || null
}

/** A whole number JavaScript cannot hold exactly: JSON.parse has rounded it. */
const isImprecise = (value: Json): boolean =>
  typeof value === 'number' && Number.isInteger(value) && !Number.isSafeInteger(value)

/** Resolve a '[*]' path the way the agent does, naming each found value by the
 * suffix the site will give it, and collect what is wrong with those names.
 *
 * Values come out exactly as `resolvePath` returns them (found leaves only, in
 * order); only the labels differ, because `resolvePath` names every element by
 * its position and ignores the label path. A path the port cannot parse
 * resolves to nothing, as it does there. */
export function resolveLabelled(
  root: Json,
  path: string,
  labelPath: string,
  hostPath = '',
): LabelledResolution {
  const issues = noIssues()
  const steps = tokenizePath(path)
  if (steps === null) return { values: [], issues }
  // One group of plain steps per wildcard level, plus the trailing value path.
  const groups: Step[][] = [[]]
  for (const step of steps) {
    if (step.kind === 'wildcard') groups.push([])
    else groups[groups.length - 1]!.push(step)
  }
  // An empty label path is no label path (the agent tests it for truthiness).

  const values: LabelledValue[] = []
  // With a host per element, the positions (' / '-joined) of the leaves that
  // stay on the polling host - the only ones whose names reach a service.
  const onPollingHost: string[] = []
  const expand = (node: Json, level: number, parts: string[], positions: string[]): void => {
    if (level === groups.length - 1) {
      const found = walk(node, groups[level]!)
      if (!found) return
      // Resolved against the LEAF element, as the agent does.
      const host = hostPath ? piggybackHost(node, hostPath) : null
      if (hostPath && host === null) onPollingHost.push(positions.join(' / '))
      values.push({ label: parts.join(' / '), value: found.value, host })
      return
    }
    const container = walk(node, groups[level]!)
    const pairs = container ? pairsOf(container.value) : null
    if (pairs === null) return
    const at = (position: string): string => [...positions, position].join(' / ')
    // The texts of the labels the browser has rounded (see isImprecise).
    const rounded = new Set<string>()
    const labels = pairs.map(([position, element]) => {
      if (!labelPath) return position
      const found = resolveAsAgent(element, labelPath)
      if (!found) {
        issues.missing.push(at(position))
        return position
      }
      if (found.value !== null && typeof found.value === 'object') issues.nonScalar.push(at(position))
      if (isImprecise(found.value)) {
        issues.imprecise.push(at(position))
        rounded.add(pyStr(found.value))
      }
      const text = pyStr(found.value)
      if (text === '') issues.empty.push(at(position))
      return text
    })
    const byLabel = new Map<string, number[]>()
    labels.forEach((label, index) => byLabel.set(label, [...(byLabel.get(label) ?? []), index]))
    for (const [label, indices] of byLabel) {
      // A repeat of a rounded number may be two distinct IDs the site tells
      // apart: not claimed - the 'imprecise' note says why.
      if (indices.length > 1 && !rounded.has(label)) {
        issues.duplicates.push({
          label,
          elements: indices.map((i) => at(pairs[i]![0])),
          names: indices.map((i) => `${label} [${i}]`),
        })
      }
    }
    pairs.forEach(([position, element], index) => {
      const label = labels[index]!
      // Suffixed with its ENUMERATION index, also for an object map, exactly
      // as the agent does: 'web [2]' is the third entry, whatever its key.
      const unique = byLabel.get(label)!.length > 1 ? `${label} [${index}]` : label
      expand(element, level + 1, [...parts, unique], [...positions, position])
    })
  }
  expand(root, 0, [], [])
  // An element that named itself '' is also a duplicate when two do; report the
  // repeat once, under duplicates, rather than twice.
  const emptyRepeats = new Set(issues.duplicates.filter((d) => d.label === '').flatMap((d) => d.elements))
  issues.empty = issues.empty.filter((e) => !emptyRepeats.has(e))
  if (hostPath) restrictToPollingHost(issues, onPollingHost)
  return { values, issues }
}

/** With a host per element, keep only what concerns the elements that stay on
 * the polling host: the others each get a host of their own and keep the plain
 * service name, so their suffix never reaches a service. An issue's element
 * (possibly an outer level of a nested expansion) counts when any leaf under it
 * stays behind. A repeat is only kept while at least two of its elements do:
 * only then do two services on ONE host depend on the order. */
function restrictToPollingHost(issues: LabelIssues, leaves: string[]): void {
  const concerns = (element: string): boolean =>
    leaves.some((leaf) => leaf === element || leaf.startsWith(`${element} / `))
  issues.duplicates = issues.duplicates.flatMap((dup) => {
    const kept = dup.elements.flatMap((element, i) => (concerns(element) ? [i] : []))
    if (kept.length < 2) return []
    return [
      {
        label: dup.label,
        elements: kept.map((i) => dup.elements[i]!),
        names: kept.map((i) => dup.names[i]!),
      },
    ]
  })
  issues.missing = issues.missing.filter(concerns)
  issues.empty = issues.empty.filter(concerns)
  issues.nonScalar = issues.nonScalar.filter(concerns)
  issues.imprecise = issues.imprecise.filter(concerns)
  issues.fallback = leaves
}

/** Whether the sample found anything worth warning about. */
export function hasLabelIssues(issues: LabelIssues): boolean {
  return (
    issues.duplicates.length > 0 ||
    issues.missing.length > 0 ||
    issues.empty.length > 0 ||
    issues.nonScalar.length > 0 ||
    issues.fallback.length > 0 ||
    issues.imprecise.length > 0
  )
}

/** A translate function in the shape of the wizard's `_t`. */
export type Translate = (msg: string, vars: Record<string, string | number>) => string

const LIST_CAP = 5

/** `a, b, c` — capped, since a collection of 500 must not become a paragraph. */
function listOf(items: string[], quote: boolean): string {
  const shown = items.slice(0, LIST_CAP).map((i) => (quote ? `'${i}'` : i))
  return items.length > LIST_CAP ? `${shown.join(', ')}, …` : shown.join(', ')
}

/** The warnings the review step shows under a field, one sentence per issue.
 *
 * Non-blocking by design: the agent copes with every one of these, and the
 * rule may well be what the operator wants. What they cannot see anywhere else
 * is HOW it copes — and a position suffix is a name that changes meaning when
 * the API reorders its elements. */
export function labelWarnings(issues: LabelIssues, t: Translate): string[] {
  const warnings: string[] = []
  for (const dup of issues.duplicates) {
    warnings.push(
      t(
        "'%{label}' names %{n} elements (%{elements}). The site tells them apart by position, as %{names} - so their services swap readings when the API reorders its elements. A field that is unique per element avoids that.",
        { label: dup.label, n: dup.elements.length, elements: listOf(dup.elements, false), names: listOf(dup.names, true) },
      ),
    )
  }
  if (issues.missing.length) {
    warnings.push(
      t('The name field is missing in %{n} element(s) (%{elements}): the site names those by their position instead.', {
        n: issues.missing.length,
        elements: listOf(issues.missing, false),
      }),
    )
  }
  if (issues.empty.length) {
    warnings.push(
      t('The name field is empty in %{n} element(s) (%{elements}): those get no suffix at all.', {
        n: issues.empty.length,
        elements: listOf(issues.empty, false),
      }),
    )
  }
  if (issues.imprecise.length) {
    warnings.push(
      t(
        'The name field is a whole number too large for the browser to read exactly in %{n} element(s) (%{elements}): the names shown here are rounded, and whether two of them repeat cannot be told here. The site reads them exactly.',
        { n: issues.imprecise.length, elements: listOf(issues.imprecise, false) },
      ),
    )
  }
  if (issues.nonScalar.length) {
    warnings.push(
      t('The name field is an object or a list in %{n} element(s) (%{elements}): its whole content becomes part of the name.', {
        n: issues.nonScalar.length,
        elements: listOf(issues.nonScalar, false),
      }),
    )
  }
  if (issues.fallback.length) {
    warnings.push(
      t(
        'The host field does not resolve in %{n} element(s) (%{elements}): those get no host of their own - their services stay on the polling host, named by the element.',
        { n: issues.fallback.length, elements: listOf(issues.fallback, false) },
      ),
    )
  }
  return warnings
}
