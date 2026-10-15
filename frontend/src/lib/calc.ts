// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The review step's port of an extraction's `calc` transform, so the preview
// shows the number the site will judge - `value / 1024 / 1024`, not the raw
// byte count - and grades it against the thresholds the way the check will.
//
// The check plugin's _apply_calc (cmk_addons/plugins/json_api/check_lib/
// calc.py) is what really decides, and the ruleset's _validate_calc
// (forms/extraction.py) what a rule may hold. Python gets its grammar from
// `ast.parse`; the browser has no such parser, and `eval`/`new Function` would
// run anything a rule smuggled in, so the same grammar is parsed by hand here.
// Kept in lib/ rather than in the component so it can be unit-tested and
// type-checked on a bare clone.

type Token = { t: 'num'; v: number } | { t: 'var'; v: 'value' | 'other' } | { t: 'op'; v: string }

function isDigit(c: string | undefined): boolean {
  return c !== undefined && c >= '0' && c <= '9'
}

function isLetter(c: string | undefined): boolean {
  return c !== undefined && ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'))
}

// Python's numeric literals, as ast.parse reads them (the Python reference's
// "Integer literals" / "Floating-point literals"): '_' between digits, a float
// with a point and/or an exponent, hex/octal/binary integers, and a decimal
// integer that may not start with 0 unless it is all zeros. Each is tried at the
// literal's first character; the float first, so '1e3' is not read as '1'.
const DIGITS = '[0-9](?:_?[0-9])*'
const EXPONENT = `[eE][+-]?${DIGITS}`
const NUMBER_LITERALS = [
  new RegExp(`(?:(?:${DIGITS})?\\.${DIGITS}|${DIGITS}\\.)(?:${EXPONENT})?|${DIGITS}${EXPONENT}`, 'y'),
  /0[xX](?:_?[0-9a-fA-F])+|0[oO](?:_?[0-7])+|0[bB](?:_?[01])+/y,
  /[1-9](?:_?[0-9])*|0+(?:_?0)*/y,
]

/** The numeric literal starting at `expr[start]`, or null where Python's
 * tokenizer raises a SyntaxError (which _apply_calc turns into UNKNOWN).
 *
 * A literal running straight into a letter, digit or '_' is such an error -
 * '012', '1_', '0x1G', '2value' - and so is the imaginary '1j', which parses
 * but is no int or float and is refused by _apply_calc. A float too large for
 * a double is inf on both sides and caught as non-finite. */
function numberLiteral(expr: string, start: number): { value: number; end: number } | null {
  for (const pattern of NUMBER_LITERALS) {
    pattern.lastIndex = start
    const match = pattern.exec(expr)
    if (match === null) {
      continue
    }
    const end = start + match[0].length
    const next = expr[end]
    if (isDigit(next) || isLetter(next) || next === '_') {
      return null
    }
    // Number() reads every one of these forms once the '_' are gone, the
    // 0x/0o/0b prefixes included.
    return { value: Number(match[0].replaceAll('_', '')), end }
  }
  return null
}

/** Split `expr` into numbers / `value` / operators / parens, or null on
 * anything outside the grammar. */
function tokenize(expr: string): Token[] | null {
  const tokens: Token[] = []
  let i = 0
  while (i < expr.length) {
    const c = expr[i]!
    if (c === ' ' || c === '\t' || c === '\n' || c === '\r') {
      i += 1
    } else if (c === '+' || c === '-' || c === '*' || c === '/' || c === '(' || c === ')') {
      tokens.push({ t: 'op', v: c })
      i += 1
    } else if (isDigit(c) || (c === '.' && isDigit(expr[i + 1]))) {
      const literal = numberLiteral(expr, i)
      if (literal === null) {
        return null
      }
      tokens.push({ t: 'num', v: literal.value })
      i = literal.end
    } else if (isLetter(c)) {
      // The only identifiers allowed are `value` and `other`; anything else
      // (function names, other variables) is rejected.
      let j = i + 1
      while (isLetter(expr[j])) {
        j += 1
      }
      const name = expr.slice(i, j)
      if (name !== 'value' && name !== 'other') {
        return null
      }
      tokens.push({ t: 'var', v: name })
      i = j
    } else {
      return null // unknown character
    }
  }
  return tokens
}

/**
 * Safely evaluate an arithmetic `calc` expression over the variables `value`
 * and `other`.
 *
 * Mirrors the special-agent grammar: Python's int and float literals (see
 * numberLiteral), the identifiers `value` and `other`,
 * parentheses, binary `+ - * /` (with `*`/`/` binding tighter than `+`/`-`) and
 * unary `+`/`-`. A hand-written recursive-descent parser is used deliberately —
 * NO `eval`/`new Function` — so nothing outside the grammar can run. Returns the
 * computed number, or `null` on any tokenize/parse error, unknown token, or a
 * non-finite result (e.g. division by zero).
 *
 * `other` is the value of the extraction's second path (`calc_path`, see
 * reviewpreview's calcOther), or null where it did not resolve to a number. An
 * expression naming it is then unresolved (null) rather than computed with a
 * made-up operand - the refusal _apply_calc makes ("the second path did not
 * resolve to a number"), which the check reports as UNKNOWN.
 */
export function evalCalc(expr: string, value: number, other: number | null = null): number | null {
  const tokens = tokenize(expr)
  if (tokens === null) {
    return null
  }

  // --- Recursive-descent parser with standard precedence. ------------------
  let p = 0
  const peek = (): Token | undefined => tokens[p]
  // expr := term (('+' | '-') term)*
  function parseExpr(): number {
    let acc = parseTerm()
    for (let tok = peek(); tok && tok.t === 'op' && (tok.v === '+' || tok.v === '-'); tok = peek()) {
      p += 1
      const rhs = parseTerm()
      acc = tok.v === '+' ? acc + rhs : acc - rhs
    }
    return acc
  }
  // term := factor (('*' | '/') factor)*
  function parseTerm(): number {
    let acc = parseFactor()
    for (let tok = peek(); tok && tok.t === 'op' && (tok.v === '*' || tok.v === '/'); tok = peek()) {
      p += 1
      const rhs = parseFactor()
      acc = tok.v === '*' ? acc * rhs : acc / rhs
    }
    return acc
  }
  // factor := ('+' | '-') factor | '(' expr ')' | number | value | other
  function parseFactor(): number {
    const tok = peek()
    if (!tok) {
      throw new Error('unexpected end')
    }
    if (tok.t === 'op' && (tok.v === '+' || tok.v === '-')) {
      p += 1
      const operand = parseFactor()
      return tok.v === '-' ? -operand : operand
    }
    if (tok.t === 'op' && tok.v === '(') {
      p += 1
      const inner = parseExpr()
      const close = peek()
      if (!close || close.t !== 'op' || close.v !== ')') {
        throw new Error('missing )')
      }
      p += 1
      return inner
    }
    if (tok.t === 'num') {
      p += 1
      return tok.v
    }
    if (tok.t === 'var') {
      p += 1
      if (tok.v === 'value') {
        return value
      }
      if (other === null) {
        throw new Error('the second path did not resolve to a number')
      }
      return other
    }
    throw new Error('unexpected token')
  }

  try {
    const result = parseExpr()
    if (p !== tokens.length) {
      return null // trailing tokens left unparsed
    }
    return Number.isFinite(result) ? result : null
  } catch {
    return null
  }
}
