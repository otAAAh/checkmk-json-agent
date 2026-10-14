// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The preview grades the number the site will grade only if it computes `calc`
// the way the check does. The check plugin's _apply_calc
// (cmk_addons/plugins/json_api/agent_based/json_api.py) and the ruleset's
// _validate_calc (rulesets/special_agent.py) decide; the arithmetic cases below
// are ported from test_apply_calc_* in tests/agent_based/test_json_api.py, so a
// change on either side shows up as a disagreement here.
import { describe, expect, it } from 'vitest'

import { evalCalc } from './calc'

describe('evalCalc — the cases _apply_calc is tested with', () => {
  it.each([
    [1048576, 'value / 1024 / 1024', 1],
    [0.5, 'value * 1000', 500],
    [212, '(value - 32) * 5 / 9', 100],
    [3, '-value', -3],
    [1024, 'value / 1024', 1],
  ])('%s with %s is %s', (value, expr, expected) => {
    expect(evalCalc(expr, value)).toBe(expected)
  })

  it.each(['foo', "__import__('os')", 'value.bit_length()', 'value ** 2', 'abs(value)'])(
    'rejects %s, as the check does',
    (expr) => {
      expect(evalCalc(expr, 2)).toBeNull()
    },
  )

  it('rejects names other than value', () => {
    expect(evalCalc('value + elsewhere', 1)).toBeNull()
  })

  it('treats division by zero as unresolved, where the check goes UNKNOWN', () => {
    expect(evalCalc('value / 0', 5)).toBeNull()
    expect(evalCalc('0 / 0', 5)).toBeNull()
  })
})

describe('evalCalc — precedence and grammar', () => {
  it('binds * and / tighter than + and -, left to right', () => {
    expect(evalCalc('1 + 2 * 3', 0)).toBe(7)
    expect(evalCalc('(1 + 2) * 3', 0)).toBe(9)
    expect(evalCalc('8 / 4 / 2', 0)).toBe(1)
    expect(evalCalc('10 - 4 - 3', 0)).toBe(3)
  })

  it('applies unary signs, also repeated and inside a product', () => {
    expect(evalCalc('--value', 4)).toBe(4)
    expect(evalCalc('+value', 4)).toBe(4)
    expect(evalCalc('2 * -value', 4)).toBe(-8)
  })

  it('reads decimals, with or without a leading digit', () => {
    expect(evalCalc('value * .5', 4)).toBe(2)
    expect(evalCalc('value * 0.25', 4)).toBe(1)
    expect(evalCalc('value * 2.', 4)).toBe(8)
  })

  it('ignores whitespace, including newlines and tabs', () => {
    expect(evalCalc(' value\t*\n2 ', 3)).toBe(6)
  })

  it.each(['', '   ', '.', 'value +', '(value', 'value)', 'value 2', '2 value', 'value % 2', 'Value'])(
    'refuses the malformed %j instead of guessing',
    (expr) => {
      expect(evalCalc(expr, 1)).toBeNull()
    },
  )

  it('leaves the second operand unresolved - the preview has no second path', () => {
    // _apply_calc binds `other` to the second path's value and refuses when it
    // is missing; the preview never has it, so it refuses too.
    expect(evalCalc('value / other * 100', 25)).toBeNull()
  })
})
