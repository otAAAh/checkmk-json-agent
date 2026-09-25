// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The wizard's half of the element-naming contract. The agent alone decides
// what a '[*]' element's service is called; the review step previews that name
// and warns where it repeats, since the agent then tells the elements apart by
// position - and a reordered API swaps the services' readings. The shared cases
// are answered by the agent too (tests/test_label_path_parity.py) and by the
// standalone Explorer (tests/test_explorer.py).
import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import {
  hasLabelIssues,
  labelWarnings,
  pyStr,
  resolveLabelled,
  type LabelIssues,
  type Translate,
} from './elementlabels'
import { resolvePath, type Json } from './jsonpaths'

interface LabelCase {
  name: string
  document: Json
  path: string
  label_path: string
  piggyback_host?: string
  labels: string[]
  hosts?: Array<string | null>
  issues: LabelIssues
}

const fixture = JSON.parse(
  readFileSync(new URL('../../../tests/fixtures/label_path_cases.json', import.meta.url), 'utf8'),
) as { cases: LabelCase[] }

describe('resolveLabelled — the shared cases', () => {
  it.each(fixture.cases.map((c) => [c.name, c] as const))('%s', (_name, testCase) => {
    const { values, issues } = resolveLabelled(
      testCase.document,
      testCase.path,
      testCase.label_path,
      testCase.piggyback_host ?? '',
    )

    expect(values.map((v) => v.label)).toEqual(testCase.labels)
    expect(issues).toEqual(testCase.issues)
    if (testCase.hosts) expect(values.map((v) => v.host)).toEqual(testCase.hosts)
  })

  it.each(fixture.cases.map((c) => [c.name, c] as const))(
    'resolves the same values as resolvePath: %s',
    (_name, testCase) => {
      // Only the names may differ from the plain resolver: a value previewed
      // under a better name is still the value the check reads.
      const labelled = resolveLabelled(testCase.document, testCase.path, testCase.label_path)
      expect(labelled.values.map((v) => v.value)).toEqual(
        resolvePath(testCase.document, testCase.path).map((r) => r.value),
      )
    },
  )
})

describe('resolveLabelled — edges', () => {
  it('resolves nothing for a path the port cannot parse', () => {
    expect(resolveLabelled({ a: [{ b: 1 }] }, 'a[*]..b', 'name').values).toEqual([])
  })

  it('treats a label path with a wildcard of its own as missing everywhere', () => {
    const { values, issues } = resolveLabelled({ a: [{ n: [1], v: 1 }] }, 'a[*].v', 'n[*]')
    expect(values.map((v) => v.label)).toEqual(['0'])
    expect(issues.missing).toEqual(['0'])
  })

  it('reports nothing for a path without a wildcard', () => {
    const { values, issues } = resolveLabelled({ status: 'UP' }, 'status', 'name')
    expect(values).toEqual([{ label: '', value: 'UP', host: null }])
    expect(hasLabelIssues(issues)).toBe(false)
  })
})

describe('pyStr', () => {
  it('renders what str() would', () => {
    expect(pyStr('plain')).toBe('plain')
    expect(pyStr(false)).toBe('False')
    expect(pyStr({ k: "it's" })).toBe(`{'k': "it's"}`)
    expect(pyStr(['a\\b'])).toBe("['a\\\\b']")
  })
})

// The wizard's `_t`, minus the translating: substitute the %{placeholders}.
const t: Translate = (msg, vars = {}) => msg.replace(/%\{(\w+)\}/g, (_m, k: string) => String(vars[k]))

describe('labelWarnings', () => {
  it('says nothing when the names are fine', () => {
    const { issues } = resolveLabelled({ n: [{ id: 'a' }, { id: 'b' }] }, 'n[*]', 'id')
    expect(labelWarnings(issues, t)).toEqual([])
  })

  it('names the repeated value, the elements and the suffixes the site uses', () => {
    const { issues } = resolveLabelled({ n: [{ id: 'web' }, { id: 'db' }, { id: 'web' }] }, 'n[*]', 'id')
    const [warning] = labelWarnings(issues, t)
    expect(warning).toContain("'web' names 2 elements (0, 2)")
    expect(warning).toContain("as 'web [0]', 'web [2]'")
    expect(warning).toContain('swap readings')
  })

  it('says what happens to a missing, an empty and an object name', () => {
    const doc: Json = { n: [{ id: 'a' }, {}, { id: '' }, { id: { x: 1 } }] }
    expect(labelWarnings(resolveLabelled(doc, 'n[*]', 'id').issues, t)).toEqual([
      'The name field is missing in 1 element(s) (1): the site names those by their position instead.',
      'The name field is empty in 1 element(s) (2): those get no suffix at all.',
      'The name field is an object or a list in 1 element(s) (3): its whole content becomes part of the name.',
    ])
  })

  it('says which elements stay on the polling host, and warns about their repeats only', () => {
    const doc: Json = {
      n: [
        { host: 'a', id: 'web' },
        { id: 'web' },
        { host: '', id: 'web' },
      ],
    }
    expect(labelWarnings(resolveLabelled(doc, 'n[*]', 'id', 'host').issues, t)).toEqual([
      "'web' names 2 elements (1, 2). The site tells them apart by position, as 'web [1]', 'web [2]' - so their services swap readings when the API reorders its elements. A field that is unique per element avoids that.",
      'The host field does not resolve in 2 element(s) (1, 2): those get no host of their own - their services stay on the polling host, named by the element.',
    ])
  })

  it('says nothing when every element gets a host of its own', () => {
    const doc: Json = { n: [{ host: 'a', id: 'web' }, { host: 'b', id: 'web' }, { host: 'c' }] }
    expect(labelWarnings(resolveLabelled(doc, 'n[*]', 'id', 'host').issues, t)).toEqual([])
  })

  it('does not claim a repeat among IDs the browser has rounded', () => {
    // Two distinct 64-bit IDs: JSON.parse rounds both to 1234567890123456800,
    // while the agent (exact Python ints) names them apart.
    const doc = JSON.parse('{"n": [{"id": 1234567890123456789}, {"id": 1234567890123456788}]}') as Json
    const { issues } = resolveLabelled(doc, 'n[*]', 'id')
    expect(issues.duplicates).toEqual([])
    expect(issues.imprecise).toEqual(['0', '1'])
    expect(labelWarnings(issues, t)).toEqual([
      'The name field is a whole number too large for the browser to read exactly in 2 element(s) (0, 1): the names shown here are rounded, and whether two of them repeat cannot be told here. The site reads them exactly.',
    ])
  })

  it('still claims a repeat among IDs the browser holds exactly', () => {
    const doc = JSON.parse('{"n": [{"id": 9007199254740991}, {"id": 9007199254740991}]}') as Json
    const { issues } = resolveLabelled(doc, 'n[*]', 'id')
    expect(issues.imprecise).toEqual([])
    expect(issues.duplicates.map((d) => d.label)).toEqual(['9007199254740991'])
  })

  it('caps a long list of elements', () => {
    const doc = { n: Array.from({ length: 8 }, () => ({ id: 'same' })) }
    const [warning] = labelWarnings(resolveLabelled(doc, 'n[*]', 'id').issues, t)
    expect(warning).toContain('names 8 elements (0, 1, 2, 3, 4, …)')
  })
})
