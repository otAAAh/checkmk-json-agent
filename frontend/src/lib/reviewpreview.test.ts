// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The review step's reading of an extraction and of an endpoint's host labels.
// Two promises are pinned here: a setting whose outcome the preview cannot know
// (a condition, an aggregation's function) is reported as present rather than
// guessed, and the host labels shown are the ones the agent sets - one per
// element for a '[*]' path, one for the whole collection with a literal value
// (cmk_addons/plugins/json_api/special_agent/; tests/test_explorer.py covers the
// agent's side).
import { describe, expect, it } from 'vitest'

import { evalCalc } from './calc'
import { resolveLabelled } from './elementlabels'
import type { Json } from './jsonpaths'
import {
  calcOther,
  elementCount,
  formatJson,
  hasCondition,
  hasFilter,
  inventoryOnly,
  inventoryTarget,
  piggybackField,
  previewHostLabels,
  valueAsKind,
  type HostLabelTexts,
} from './reviewpreview'

// The agent's _resolve_calc_other (special_agent/extract.py) resolves calc_path
// in the value's scope - each '[*]' element, else the document - and the check
// keeps only a finite non-boolean number of it (check_lib/section.py
// _optional_number); anything else leaves `other` None, which _apply_calc
// refuses (UNKNOWN).
describe('calcOther', () => {
  it('resolves the second path in the scope it is given', () => {
    expect(calcOther({ used: 1, total: 4 }, 'total')).toBe(4)
    expect(calcOther({ quota: { total: 8 } }, '$.quota.total')).toBe(8)
    expect(calcOther({ limits: [10, 20] }, 'limits[1]')).toBe(20)
  })

  it('compares each element against its own total, as the agent does', () => {
    const doc = { total: 1000, disks: [{ used: 1, total: 4 }, { used: 3, total: 4 }] }
    const percents = resolveLabelled(doc, 'disks[*].used', '').values.map((m) =>
      evalCalc('value / other * 100', m.value as number, calcOther(m.element, 'total')),
    )
    expect(percents).toEqual([25, 75])
  })

  it('takes the document root as the scope of a path without a wildcard', () => {
    const doc = { used: 30, total: 120 }
    const [m] = resolveLabelled(doc, 'used', '').values
    expect(evalCalc('value / other * 100', 30, calcOther(m!.element, 'total'))).toBe(25)
  })

  it('is null without a path, also a blank one', () => {
    expect(calcOther({ total: 4 }, undefined)).toBeNull()
    expect(calcOther({ total: 4 }, '')).toBeNull()
    expect(calcOther({ total: 4 }, '   ')).toBeNull()
  })

  it('is null where the path does not resolve, or not to a number', () => {
    expect(calcOther({ total: 4 }, 'missing')).toBeNull()
    expect(calcOther({ total: '4' }, 'total')).toBeNull()
    expect(calcOther({ total: true }, 'total')).toBeNull()
    expect(calcOther({ total: null }, 'total')).toBeNull()
    expect(calcOther({ total: [4] }, 'total')).toBeNull()
  })
})

describe('formatJson', () => {
  it('shows scalars plainly and collections as JSON', () => {
    expect(formatJson('UP', '-')).toBe('UP')
    expect(formatJson(1.5, '-')).toBe('1.5')
    expect(formatJson(false, '-')).toBe('false')
    expect(formatJson(null, '-')).toBe('null')
    expect(formatJson({ a: [1] }, '-')).toBe('{"a":[1]}')
  })

  it('says so where the sample has no value', () => {
    expect(formatJson(undefined, 'not found')).toBe('not found')
  })
})

describe('valueAsKind', () => {
  it('tells counter from timestamp by the sub-value, since the mode is hashed', () => {
    expect(valueAsKind(['h-counter', null])).toBe('counter')
    expect(valueAsKind(['h-timestamp', { format: 'iso8601' }])).toBe('timestamp')
  })

  it('is null for a plain value or anything unexpected', () => {
    expect(valueAsKind(undefined)).toBeNull()
    expect(valueAsKind(['h-other', {}])).toBeNull()
    expect(valueAsKind(['h-other', ['format']])).toBeNull()
    expect(valueAsKind([1, null])).toBeNull()
  })
})

describe('elementCount', () => {
  it('counts the matches of a wildcard path - one per element that has the field', () => {
    expect(elementCount([{ value: 1 }, { value: 2 }], 'items[*].size')).toBe(2)
    expect(elementCount([], 'items[*].size')).toBe(0)
  })

  it('counts the collection a wildcard-free path points at', () => {
    expect(elementCount([{ value: [1, 2, 3] }], 'items')).toBe(3)
    expect(elementCount([{ value: { a: 1, b: 2 } }], 'items')).toBe(2)
  })

  it('is null for a scalar - there is nothing to aggregate', () => {
    expect(elementCount([{ value: 7 }], 'items')).toBeNull()
    expect(elementCount([{ value: null }], 'items')).toBeNull()
  })
})

describe('piggybackField', () => {
  it('is the trimmed host field, or null', () => {
    expect(piggybackField({ piggyback_host: ' name ' })).toBe('name')
    expect(piggybackField({ piggyback_host: '  ' })).toBeNull()
    expect(piggybackField({})).toBeNull()
  })
})

describe('hasFilter / hasCondition', () => {
  it('reports a condition by its path being set, never by its outcome', () => {
    expect(hasFilter({ filter: { path: 'state', op: 'h-eq', value: 'up' } })).toBe(true)
    expect(hasFilter({ filter: { path: '  ' } })).toBe(false)
    expect(hasFilter({ filter: ['state'] })).toBe(false)
    expect(hasFilter({})).toBe(false)
  })

  it('marks a label spec with a condition path', () => {
    expect(hasCondition({ path: 'state' })).toBe(true)
    expect(hasCondition({ path: '' })).toBe(false)
    expect(hasCondition(undefined)).toBe(false)
  })
})

describe('inventoryTarget / inventoryOnly', () => {
  it('reads the inventory node and whether a service is kept', () => {
    expect(inventoryTarget({ inventory: { node: 'software.os', keep_service: true } })).toEqual({
      node: 'software.os',
      keepService: true,
    })
    expect(inventoryTarget({ inventory: { keep_service: true } })).toBeNull()
    expect(inventoryTarget({})).toBeNull()
  })

  it('counts a field as inventory-only unless it keeps its service', () => {
    expect(inventoryOnly({ inventory: { node: 'software.os' } })).toBe(true)
    expect(inventoryOnly({ inventory: { node: 'software.os', keep_service: true } })).toBe(false)
    expect(inventoryOnly({})).toBe(false)
  })
})

describe('previewHostLabels', () => {
  const texts: HostLabelTexts = { notFound: '(not found)', conditional: '(if)' }
  const sample: Json = {
    region: 'eu',
    envs: [
      { name: 'prod', tier: 1 },
      { name: 'dev' },
    ],
  }

  it('sets one label with the value at a plain path', () => {
    expect(previewHostLabels(sample, [{ path: 'region' }], texts)).toEqual([
      { label: 'json_api/region', value: 'eu' },
    ])
    expect(previewHostLabels(sample, [{ path: 'region', key: 'site' }], texts)).toEqual([
      { label: 'json_api/site', value: 'eu' },
    ])
  })

  it('still shows a plain-path label the sample lacks', () => {
    expect(previewHostLabels(sample, [{ path: 'zone' }], texts)).toEqual([
      { label: 'json_api/zone', value: '(not found)' },
    ])
    expect(previewHostLabels(null, [{ path: 'zone' }], texts)).toEqual([
      { label: 'json_api/zone', value: '(not found)' },
    ])
  })

  it('sets one label per element of a wildcard path, valued true', () => {
    expect(previewHostLabels(sample, [{ path: 'envs[*]' }], texts)).toEqual([
      { label: 'json_api/envs/0', value: 'true' },
      { label: 'json_api/envs/1', value: 'true' },
    ])
  })

  it('values each element by its value field, skipping elements without one', () => {
    expect(previewHostLabels(sample, [{ path: 'envs[*]', value_field: 'tier' }], texts)).toEqual([
      { label: 'json_api/envs/0', value: '1' },
    ])
  })

  it('collapses a wildcard path with a literal value into ONE label', () => {
    expect(previewHostLabels(sample, [{ path: 'envs[*]', value: 'yes' }], texts)).toEqual([
      { label: 'json_api/envs', value: 'yes' },
    ])
    // ... set only once an element matches - but shown when there is no sample
    // to say otherwise.
    expect(previewHostLabels(sample, [{ path: 'none[*]', value: 'yes' }], texts)).toEqual([])
    expect(previewHostLabels(null, [{ path: 'none[*]', value: 'yes' }], texts)).toEqual([
      { label: 'json_api/none', value: 'yes' },
    ])
  })

  it('takes a literal value over the one at a plain path', () => {
    expect(previewHostLabels(sample, [{ path: 'region', value: 'fixed' }], texts)).toEqual([
      { label: 'json_api/region', value: 'fixed' },
    ])
  })

  it('shows a label described by the rule alone, and drops one without a key', () => {
    expect(previewHostLabels(sample, [{ key: 'managed', value: 'yes' }], texts)).toEqual([
      { label: 'json_api/managed', value: 'yes' },
    ])
    expect(previewHostLabels(sample, [{ key: 'managed' }], texts)).toEqual([])
    expect(previewHostLabels(sample, [{ value: 'yes' }], texts)).toEqual([])
  })

  it('marks a label whose condition decides whether it is set', () => {
    const filter = { path: 'envs[*].name', op: 'h-eq', value: 'prod' }
    expect(previewHostLabels(sample, [{ path: 'region', filter }], texts)).toEqual([
      { label: 'json_api/region', value: 'eu (if)' },
    ])
    expect(previewHostLabels(sample, [{ key: 'managed', value: 'yes', filter }], texts)).toEqual([
      { label: 'json_api/managed', value: 'yes (if)' },
    ])
  })
})
