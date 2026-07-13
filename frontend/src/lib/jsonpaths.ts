// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// JSON tree + path helpers for the field picker. Ported from the standalone
// Explorer (explorer/index.html) so the generated paths match the agent's path
// grammar — see the explorer-plugin-sync note. A '[*]' wildcard iterates arrays
// AND keyed object-maps (one service per element/key); keys containing . [ ] are
// bracket-quoted.

export type Json = string | number | boolean | null | Json[] | { [k: string]: Json }

export interface TreeNode {
  key: string
  label: string
  path: string
  kind: 'leaf' | 'object' | 'array' | 'map'
  valueType: string
  valuePreview: string
  /** For leaves: a representative raw value (used to prefill the service form). */
  sampleValue?: Json
  wildcard: boolean
  children: TreeNode[]
}

// A key with '.', '[' or ']' (or empty) must be bracket-quoted so the agent
// reads it as one segment; pick the quote style the key doesn't contain.
export function segFor(key: string): string {
  if (!/[.[\]]/.test(key) && key !== '') {
    return '.' + key
  }
  if (!key.includes("'")) {
    return "['" + key + "']"
  }
  return '["' + key + '"]'
}

export function joinKey(parent: string, key: string): string {
  const seg = segFor(key)
  if (!parent) {
    return seg.startsWith('.') ? seg.slice(1) : seg
  }
  return parent + seg
}

const _SEG = /[A-Za-z0-9_]+|\['[^']*'\]|\["[^"]*"\]|\[\d+\]/g

export function defaultService(path: string): string {
  const cleaned = path.replace(/\[\*\]/g, '')
  let last = 'value'
  for (const tok of cleaned.match(_SEG) ?? []) {
    if (tok.startsWith("['") || tok.startsWith('["')) {
      last = tok.slice(2, -2)
    } else if (tok.startsWith('[')) {
      continue // numeric index — not a useful label
    } else {
      last = tok
    }
  }
  return last.charAt(0).toUpperCase() + last.slice(1)
}

function preview(v: Json): string {
  return typeof v === 'string' ? JSON.stringify(v) : String(v)
}

function valueType(v: Json): string {
  return Array.isArray(v) ? 'array' : v === null ? 'null' : typeof v
}

function isRecord(v: Json): v is { [k: string]: Json } {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

// When every value of an object is a (non-array) object, it's a map of records
// the agent's '[*]' can iterate (one service per key), e.g. a Spring Boot
// /health 'components[*]'. Returns a '[*]' subtree from the first entry, else null.
function objectMapNode(key: string, obj: { [k: string]: Json }, path: string): TreeNode | null {
  const keys = Object.keys(obj)
  if (!keys.length || !keys.every((k) => isRecord(obj[k]!))) {
    return null
  }
  const wpath = path + '[*]'
  const first = obj[keys[0]!] as { [k: string]: Json }
  return {
    key: `${key}[*]`,
    label: `${key}[*]`,
    path: wpath,
    kind: 'map',
    valueType: `{${keys.length}} — one service per key`,
    valuePreview: '',
    wildcard: true,
    children: Object.keys(first).map((k) => buildNode(k, first[k]!, joinKey(wpath, k))),
  }
}

export function buildNode(key: string, value: Json, path: string): TreeNode {
  if (Array.isArray(value)) {
    const wpath = path + '[*]'
    const first = value[0]
    const children =
      value.length === 0
        ? []
        : isRecord(first)
          ? Object.keys(first).map((k) => buildNode(k, first[k]!, joinKey(wpath, k)))
          : [leaf(`${key}[*]`, first!, wpath)]
    return {
      key,
      label: key,
      path,
      kind: 'array',
      valueType: `[${value.length}] — one service per element`,
      valuePreview: '',
      wildcard: false,
      children,
    }
  }
  if (isRecord(value)) {
    const children: TreeNode[] = []
    const mapNode = objectMapNode(key, value, path)
    if (mapNode) {
      children.push(mapNode)
    }
    for (const k of Object.keys(value)) {
      children.push(buildNode(k, value[k]!, joinKey(path, k)))
    }
    return {
      key,
      label: key,
      path,
      kind: 'object',
      valueType: `{${Object.keys(value).length}}`,
      valuePreview: '',
      wildcard: false,
      children,
    }
  }
  return leaf(key, value, path)
}

function leaf(label: string, value: Json, path: string): TreeNode {
  return {
    key: label,
    label,
    path,
    kind: 'leaf',
    valueType: valueType(value),
    valuePreview: preview(value),
    sampleValue: value,
    wildcard: path.includes('[*]'),
    children: [],
  }
}

export function buildTree(root: Json): TreeNode[] {
  if (Array.isArray(root)) {
    return [buildNode('(root)', root, '')]
  }
  if (isRecord(root)) {
    return Object.keys(root).map((k) => buildNode(k, root[k]!, joinKey('', k)))
  }
  return []
}

/** One value found at a path (with a label from the wildcard indices/keys). */
export interface ResolvedValue {
  label: string
  value: Json
}

type Step = { kind: 'key'; key: string } | { kind: 'index'; index: number } | { kind: 'wildcard' }

/** Tokenize a path into steps, or null if any part is unparseable — callers
 * must treat null as "no match" rather than resolving only the parsed prefix
 * (which would show a misleading value/state for a malformed path). */
function tokenizePath(rawPath: string): Step[] | null {
  let path = rawPath.trim()
  if (path.startsWith('$.')) {
    path = path.slice(2)
  } else if (path.startsWith('$')) {
    path = path.slice(1)
  }
  const seg = /^(?:\.?([A-Za-z0-9_]+)|\['([^']*)'\]|\["([^"]*)"\]|\[(\d+)\]|(\[\*\]))/
  const steps: Step[] = []
  while (path.length) {
    const m = seg.exec(path)
    if (!m) {
      return null // unparseable remainder → not a resolvable path
    }
    if (m[1] !== undefined) {
      steps.push({ kind: 'key', key: m[1] })
    } else if (m[2] !== undefined) {
      steps.push({ kind: 'key', key: m[2] })
    } else if (m[3] !== undefined) {
      steps.push({ kind: 'key', key: m[3] })
    } else if (m[4] !== undefined) {
      steps.push({ kind: 'index', index: Number(m[4]) })
    } else {
      steps.push({ kind: 'wildcard' })
    }
    path = path.slice(m[0].length)
  }
  return steps
}

const joinLabel = (a: string, b: string): string => (a ? `${a} / ${b}` : b)

/** Resolve a picker path against a JSON value, expanding every '[*]' over arrays
 * (by index) and object-maps (by key) — the same expansion the agent does — and
 * returning each matched value with a label built from the wildcard positions. */
export function resolvePath(root: Json, path: string): ResolvedValue[] {
  const steps = tokenizePath(path)
  if (steps === null) {
    return []
  }
  let current: ResolvedValue[] = [{ label: '', value: root }]
  for (const step of steps) {
    const next: ResolvedValue[] = []
    for (const { label, value } of current) {
      if (step.kind === 'key') {
        if (isRecord(value) && step.key in value) {
          next.push({ label, value: value[step.key]! })
        }
      } else if (step.kind === 'index') {
        if (Array.isArray(value) && step.index < value.length) {
          next.push({ label, value: value[step.index]! })
        }
      } else if (Array.isArray(value)) {
        value.forEach((v, idx) => next.push({ label: joinLabel(label, String(idx)), value: v }))
      } else if (isRecord(value)) {
        for (const k of Object.keys(value)) {
          next.push({ label: joinLabel(label, k), value: value[k]! })
        }
      }
    }
    current = next
  }
  return current
}
