// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The wizard's half of the picker contract. A path is written once — by
// clicking a field here — and read twice: by this port, to preview the value
// and its state, and by the special agent, to build the service. The shared
// fixture below is answered by BOTH (see tests/test_path_grammar_parity.py), so
// a grammar change that touches only one side fails on the other.
import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { buildTree, defaultService, joinKey, resolvePath, segFor, type Json } from './jsonpaths'

interface PathCase {
  name: string
  document: string
  path: string
  values: Json[]
}

const fixture = JSON.parse(
  readFileSync(new URL('../../../tests/fixtures/json_path_cases.json', import.meta.url), 'utf8'),
) as { documents: Record<string, Json>; cases: PathCase[] }

describe('resolvePath — the shared grammar', () => {
  it.each(fixture.cases.map((c) => [c.name, c] as const))('%s', (_name, testCase) => {
    const document = fixture.documents[testCase.document]!

    expect(resolvePath(document, testCase.path).map((r) => r.value)).toEqual(testCase.values)
  })

  it('refuses a malformed path instead of resolving the part it understood', () => {
    // Deliberately stricter than the agent, whose tokenizer skips the junk and
    // answers 'a.b' for both of these. Resolving a prefix would preview a value
    // under a path that names something else - a wrong number shown with
    // confidence, which is worse for the operator than an empty preview.
    expect(resolvePath({ a: { b: 1 } }, 'a..b')).toEqual([])
    expect(resolvePath({ a: { b: 1 } }, 'a.b[')).toEqual([])
  })

  it('labels each wildcard match by index or key, so services can be told apart', () => {
    const document = { nodes: [{ n: 'a' }, { n: 'b' }], comp: { db: { n: 'c' } } }

    expect(resolvePath(document, 'nodes[*].n')).toEqual([
      { label: '0', value: 'a' },
      { label: '1', value: 'b' },
    ])
    expect(resolvePath(document, 'comp[*].n')).toEqual([{ label: 'db', value: 'c' }])
  })
})

describe('segFor / joinKey — quoting a key so it stays one segment', () => {
  it('leaves an ordinary key unquoted', () => {
    expect(segFor('status')).toBe('.status')
    expect(joinKey('', 'status')).toBe('status')
    expect(joinKey('components', 'status')).toBe('components.status')
  })

  it('brackets a key the grammar would otherwise split', () => {
    expect(segFor('odd.key')).toBe("['odd.key']")
    expect(segFor('a[0]')).toBe("['a[0]']")
    expect(segFor('')).toBe("['']")
    expect(joinKey('root', 'odd.key')).toBe("root['odd.key']")
  })

  it('leaves a key the agent can read plainly alone, punctuation and all', () => {
    // The agent's plain-key token is "anything but . [ ]", so a dash, a space or
    // an apostrophe needs no quoting - and quoting it anyway would only make the
    // path harder to read in the rule.
    expect(segFor('content-type')).toBe('.content-type')
    expect(segFor('my key')).toBe('.my key')
    expect(segFor("quoted'key")).toBe(".quoted'key")
  })

  it('switches quote style when the key needs quoting AND holds an apostrophe', () => {
    // There is no escaping inside the quotes, so the style that survives is the
    // one the key does not contain.
    expect(segFor("odd'.key")).toBe('["odd\'.key"]')
  })
})

describe('defaultService — the service name a picked path suggests', () => {
  it('names the service after the last meaningful segment', () => {
    expect(defaultService('components.db.status')).toBe('Status')
    expect(defaultService("['odd.key'].value")).toBe('Value')
  })

  it('ignores wildcards and numeric indices, which name nothing', () => {
    expect(defaultService('nodes[*].load')).toBe('Load')
    // The index is not a label, so the name comes from the key before it - a
    // service called '0' would tell the operator nothing.
    expect(defaultService('nodes[0]')).toBe('Nodes')
  })

  it('falls back to a usable name for a path with no named segment', () => {
    expect(defaultService('')).toBe('Value')
  })
})

describe('buildTree — what the picker offers', () => {
  it('offers a wildcard subtree for an object map of records', () => {
    // A Spring Boot /health 'components' - one service per key, which the
    // operator cannot see is possible unless the picker offers it.
    const [components] = buildTree({ components: { db: { status: 'UP' } } })

    const wildcard = components!.children[0]!
    expect(wildcard.path).toBe('components[*]')
    expect(wildcard.kind).toBe('map')
    expect(wildcard.children.map((c) => c.path)).toEqual(['components[*].status'])
  })

  it('describes an array by its first element, under a [*] path', () => {
    const [nodes] = buildTree({ nodes: [{ name: 'a' }, { name: 'b' }] })

    expect(nodes!.kind).toBe('array')
    expect(nodes!.valueType).toContain('[2]')
    expect(nodes!.children.map((c) => c.path)).toEqual(['nodes[*].name'])
  })

  it('carries a leaf sample value, which prefills the service form', () => {
    const [status] = buildTree({ status: 'UP' })

    expect(status).toMatchObject({ kind: 'leaf', path: 'status', sampleValue: 'UP' })
  })

  it('treats a document that is itself an array as the root', () => {
    const [root] = buildTree([{ id: 1 }])

    expect(root!.path).toBe('')
    expect(root!.children.map((c) => c.path)).toEqual(['[*].id'])
  })

  it('has nothing to offer for a scalar or empty document', () => {
    expect(buildTree('just a string')).toEqual([])
    expect(buildTree({})).toEqual([])
  })
})
