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

  // _apply_calc (check_lib/calc.py) gets its literals from ast.parse and takes
  // every int or float Constant (not bool, not complex); each value below is
  // what ast.literal_eval reads for the same text.
  it.each([
    ['1e3', 1000],
    ['1E-3', 0.001],
    ['1.5e+2', 150],
    ['.5e2', 50],
    ['5.e2', 500],
    ['1e1_0', 1e10],
    ['1_000', 1000],
    ['0x1F', 31],
    ['0x_1f', 31],
    ['0o17', 15],
    ['0b11', 3],
    ['00', 0],
    ['0_0', 0],
    ['012.5', 12.5],
    ['012e1', 120],
  ])('reads the Python literal %s as %s', (literal, expected) => {
    expect(evalCalc(`value * ${literal}`, 1)).toBe(expected)
  })

  // ast.parse raises SyntaxError on these (now a ValueError -> UNKNOWN), and
  // `1j` parses to a complex Constant that _apply_calc refuses.
  it.each(['012', '1_', '1__0', '1e', '0x', '0x1G', '0b12', '1j', '2value', '1._5'])(
    'refuses %s, which Python does not read as an int or float',
    (literal) => {
      expect(evalCalc(`value * ${literal}`, 1)).toBeNull()
    },
  )

  it('treats a literal too large for a double as non-finite, as the check does', () => {
    // 1e400 is inf in Python too; _value_results reports a non-finite result.
    expect(evalCalc('value * 1e400', 1)).toBeNull()
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
