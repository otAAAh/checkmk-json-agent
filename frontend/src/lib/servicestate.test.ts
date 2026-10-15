// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The review step tags every service with the state the site will give it. The
// check plugin decides that (cmk_addons/plugins/json_api/agent_based/
// json_api.py: check_levels over levels_upper / levels_lower, _evaluate_match
// and _coerce_state for strings); these cases pin the preview to the same
// outcomes - must_match is CRIT on a miss, a state map tries OK, WARN, CRIT in
// that order and stays OK on no match, and only a FULL match counts.
//
// What makes the port more than a copy is the wire format: FormEdit hands the
// wizard a ServiceState as a hashed ident, so the preview recovers the state
// from the choice's title in the form spec.
import { describe, expect, it } from 'vitest'

import {
  collectChoiceTitles,
  evalState,
  fullMatch,
  levelsPair,
  matchMode,
  parseMatch,
  serviceStateToKind,
  stateChoiceKind,
  titleOf,
} from './servicestate'

// A slice of a serialized extractions spec: choices nest at any depth.
const spec = {
  type: 'dictionary',
  elements: [
    {
      name: 'unit',
      parameter_form: {
        type: 'single_choice',
        elements: [{ name: 'h-percent', title: 'Percent' }],
      },
    },
    {
      name: 'state_no_match',
      parameter_form: {
        elements: [
          { name: 'h-ok', title: 'OK' },
          { name: 'h-warn', title: 'WARN' },
          { name: 'h-crit', title: 'CRIT' },
          { name: 'h-unknown', title: 'UNKNOWN' },
        ],
      },
    },
  ],
}
const titles = collectChoiceTitles(spec)

describe('collectChoiceTitles / titleOf', () => {
  it('maps every nested choice ident to its title', () => {
    expect(titles.get('h-percent')).toBe('Percent')
    expect(titles.get('h-unknown')).toBe('UNKNOWN')
  })

  it('skips a named node without a title', () => {
    expect(titles.has('unit')).toBe(false) // a name without a title
  })

  it('yields nothing for a missing spec', () => {
    expect(collectChoiceTitles(undefined).size).toBe(0)
    expect(collectChoiceTitles(null).size).toBe(0)
  })

  it('falls back to the value itself, and is null for no value', () => {
    expect(titleOf('h-percent', titles)).toBe('Percent')
    expect(titleOf('bytes', titles)).toBe('bytes')
    expect(titleOf('', titles)).toBeNull()
    expect(titleOf(3, titles)).toBeNull()
  })
})

describe('stateChoiceKind', () => {
  it('reads a hashed ServiceState through its title', () => {
    expect(stateChoiceKind('h-ok', titles)).toBe('ok')
    expect(stateChoiceKind('h-warn', titles)).toBe('warn')
    expect(stateChoiceKind('h-crit', titles)).toBe('crit')
    expect(stateChoiceKind('h-unknown', titles)).toBe('none')
  })

  it('reads a plain state number, as a hand-written rule carries it', () => {
    expect(stateChoiceKind(0, titles)).toBe('ok')
    expect(stateChoiceKind(1, titles)).toBe('warn')
    expect(stateChoiceKind(2, titles)).toBe('crit')
    expect(stateChoiceKind(3, titles)).toBe('none')
  })

  it('is null where nothing says which state', () => {
    expect(stateChoiceKind(undefined, titles)).toBeNull()
    expect(stateChoiceKind('h-percent', titles)).toBeNull()
  })
})

describe('serviceStateToKind', () => {
  it('previews UNKNOWN as no state, and anything unexpected as OK', () => {
    expect([0, 1, 2, 3].map(serviceStateToKind)).toEqual(['ok', 'warn', 'crit', 'none'])
    expect(serviceStateToKind('2')).toBe('ok')
  })
})

describe('levelsPair / parseMatch / matchMode', () => {
  it('takes fixed levels only', () => {
    expect(levelsPair(['fixed', [80, 90]])).toEqual([80, 90])
    expect(levelsPair(['no_levels', null])).toBeNull()
    expect(levelsPair(['fixed', [80]])).toBeNull()
    expect(levelsPair(undefined)).toBeNull()
  })

  it('splits a match value into mode and config', () => {
    expect(parseMatch(['h-mode', { pattern: 'UP' }])).toEqual({ mode: 'h-mode', cfg: { pattern: 'UP' } })
    expect(parseMatch(['h-mode', null])).toBeNull()
    expect(parseMatch(['h-mode', ['UP']])).toBeNull()
    expect(parseMatch('UP')).toBeNull()
  })

  it('tells the modes apart by their sub-fields, since the mode is hashed', () => {
    expect(matchMode({ pattern: '', state_no_match: 'h-crit' })).toBe('must_match')
    expect(matchMode({ ok: 'UP', warn: '', crit: '' })).toBe('state_map')
  })

  it('reads a cfg without a pattern as a state map, also with no pattern set', () => {
    // must_match's pattern is required (lib.py string_match); the check reads
    // the rest as StateMap(None, None, None) (check_lib/section.py _coerce_match).
    expect(matchMode({ ok: '', warn: '', crit: '' })).toBe('state_map')
    expect(matchMode({ state_no_match: 'h-warn' })).toBe('state_map')
    expect(matchMode({})).toBe('state_map')
  })
})

describe('fullMatch', () => {
  it('anchors at both ends, like re.fullmatch', () => {
    expect(fullMatch('UP', 'UP')).toBe(true)
    expect(fullMatch('UPGRADING', 'UP')).toBe(false)
    expect(fullMatch('NOT UP', 'UP')).toBe(false)
  })

  it('anchors every alternative, not just the first and last', () => {
    expect(fullMatch('UPx', 'UP|DOWN')).toBe(false)
    expect(fullMatch('DOWN', 'UP|DOWN')).toBe(true)
  })

  it('is null, not a miss, on a pattern that does not compile', () => {
    expect(fullMatch('x', '(')).toBeNull()
    expect(fullMatch('(', '(')).toBeNull()
  })
})

describe('evalState — numbers', () => {
  const upper = { levels_upper: ['fixed', [80, 90]] }
  const lower = { levels_lower: ['fixed', [20, 10]] }

  it('grades against the upper levels, inclusive', () => {
    expect(evalState(79, upper, titles)).toBe('ok')
    expect(evalState(80, upper, titles)).toBe('warn')
    expect(evalState(90, upper, titles)).toBe('crit')
  })

  it('grades against the lower levels', () => {
    expect(evalState(50, lower, titles)).toBe('ok')
    expect(evalState(15, lower, titles)).toBe('warn')
    expect(evalState(5, lower, titles)).toBe('crit')
  })

  // _check_fixed_levels in cmk/agent_based/v2/_check_levels.py: `value >=
  // crit_level` / `value >= warn_level` upwards, `value < crit_level` /
  // `value < warn_level` downwards. The boundaries are where the two differ.
  it('is inclusive on the upper thresholds, as check_levels is', () => {
    expect(evalState(79.999, upper, titles)).toBe('ok')
    expect(evalState(80, upper, titles)).toBe('warn')
    expect(evalState(89.999, upper, titles)).toBe('warn')
    expect(evalState(90, upper, titles)).toBe('crit')
  })

  it('is strict on the lower thresholds, as check_levels is', () => {
    expect(evalState(20, lower, titles)).toBe('ok')
    expect(evalState(19.999, lower, titles)).toBe('warn')
    expect(evalState(10, lower, titles)).toBe('warn')
    expect(evalState(9.999, lower, titles)).toBe('crit')
  })

  it('has no state to preview without levels', () => {
    expect(evalState(50, {}, titles)).toBe('none')
  })

  it('does not grade a number by a match rule', () => {
    expect(evalState(1, { match: ['h', { pattern: '1' }] }, titles)).toBe('none')
  })
})

describe('evalState — strings', () => {
  const mustMatch = (cfg: Record<string, unknown>) => ({ match: ['h-must', { pattern: 'UP', ...cfg }] })
  const stateMap = { match: ['h-map', { ok: 'UP', warn: 'DEGRADED|UP.*', crit: 'DOWN' }] }

  it('must_match is OK on a full match and CRIT otherwise', () => {
    expect(evalState('UP', mustMatch({}), titles)).toBe('ok')
    expect(evalState('UPGRADING', mustMatch({}), titles)).toBe('crit')
  })

  it('must_match goes to the configured no-match state', () => {
    expect(evalState('DOWN', mustMatch({ state_no_match: 'h-warn' }), titles)).toBe('warn')
    expect(evalState('DOWN', mustMatch({ state_no_match: 1 }), titles)).toBe('warn')
  })

  it('a state map takes the first of OK, WARN, CRIT that fully matches', () => {
    // 'UP' also matches the WARN pattern; OK is tried first.
    expect(evalState('UP', stateMap, titles)).toBe('ok')
    expect(evalState('UPGRADING', stateMap, titles)).toBe('warn')
    expect(evalState('DOWN', stateMap, titles)).toBe('crit')
  })

  it('a state map stays OK on no match, unless told otherwise', () => {
    expect(evalState('???', stateMap, titles)).toBe('ok')
    const strict = { match: ['h-map', { ok: 'UP', state_no_match: 'h-crit' }] }
    expect(evalState('???', strict, titles)).toBe('crit')
  })

  it('has no state to preview without a match rule', () => {
    expect(evalState('UP', {}, titles)).toBe('none')
  })

  // _evaluate_match (agent_based/json_api.py) over a StateMap with no pattern
  // set tries nothing and returns its state_no_match - OK unless told otherwise.
  it('a state map with no pattern set is its no-match state, as on the site', () => {
    expect(evalState('UP', { match: ['h-map', { ok: '' }] }, titles)).toBe('ok')
    expect(evalState('UP', { match: ['h-map', { ok: '', warn: '', crit: '' }] }, titles)).toBe('ok')
    expect(evalState('UP', { match: ['h-map', { state_no_match: 'h-warn' }] }, titles)).toBe('warn')
  })

  // _value_results (agent_based/json_api.py) catches the re.error that
  // _evaluate_match raises and yields State.UNKNOWN "Invalid match pattern" -
  // UNKNOWN previews as 'none', never as the no-match state.
  it('a pattern that does not compile is UNKNOWN, not a miss', () => {
    expect(evalState('UP', mustMatch({ pattern: '(' }), titles)).toBe('none')
    expect(evalState('UP', mustMatch({ pattern: '(', state_no_match: 'h-ok' }), titles)).toBe('none')
    expect(evalState('UP', { match: ['h-map', { ok: '[' }] }, titles)).toBe('none')
  })

  it('a state map only fails on a broken pattern it actually tries', () => {
    // re.fullmatch compiles lazily: once OK matched, the WARN pattern is never
    // compiled, so it cannot raise.
    const map = { match: ['h-map', { ok: 'UP', warn: '(', crit: 'DOWN' }] }
    expect(evalState('UP', map, titles)).toBe('ok')
    expect(evalState('DOWN', map, titles)).toBe('none')
  })
})

describe('evalState — other values', () => {
  it('has no state for a missing value, a boolean, null or a collection', () => {
    const x = { levels_upper: ['fixed', [1, 2]], match: ['h', { pattern: 'x' }] }
    for (const value of [undefined, true, null, [1], { a: 1 }]) {
      expect(evalState(value, x, titles)).toBe('none')
    }
  })
})
