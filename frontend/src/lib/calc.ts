// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The review step's port of an extraction's `calc` transform, so the preview
// shows the number the site will judge - `value / 1024 / 1024`, not the raw
// byte count - and grades it against the thresholds the way the check will.
//
// The check plugin's _apply_calc (cmk_addons/plugins/json_api/agent_based/
// json_api.py) is what really decides, and the ruleset's _validate_calc
// (rulesets/special_agent.py) what a rule may hold. Python gets its grammar from
// `ast.parse`; the browser has no such parser, and `eval`/`new Function` would
// run anything a rule smuggled in, so the same grammar is parsed by hand here.
// Kept in lib/ rather than in the component so it can be unit-tested and
// type-checked on a bare clone.

type Token = { t: 'num'; v: number } | { t: 'value' } | { t: 'op'; v: string }

function isDigit(c: string | undefined): boolean {
  return c !== undefined && c >= '0' && c <= '9'
}

function isLetter(c: string | undefined): boolean {
  return c !== undefined && ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'))
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
    } else if (isDigit(c)) {
      // A decimal number: leading digits, optional single '.' with more digits.
      let j = i + 1
      while (isDigit(expr[j])) {
        j += 1
      }
      if (expr[j] === '.') {
        j += 1
        while (isDigit(expr[j])) {
          j += 1
        }
      }
      tokens.push({ t: 'num', v: Number(expr.slice(i, j)) })
      i = j
    } else if (c === '.') {
      // A number starting with '.', e.g. `.5`.
      let j = i + 1
      while (isDigit(expr[j])) {
        j += 1
      }
      if (j === i + 1) {
        return null // a lone '.'
      }
      tokens.push({ t: 'num', v: Number(expr.slice(i, j)) })
      i = j
    } else if (isLetter(c)) {
      // The only identifier allowed is `value`; anything else (function names,
      // other variables) is rejected.
      let j = i + 1
      while (isLetter(expr[j])) {
        j += 1
      }
      if (expr.slice(i, j) !== 'value') {
        return null
      }
      tokens.push({ t: 'value' })
      i = j
    } else {
      return null // unknown character
    }
  }
  return tokens
}

/**
 * Safely evaluate an arithmetic `calc` expression over the variable `value`.
 *
 * Mirrors the special-agent grammar: decimal numbers, the identifier `value`,
 * parentheses, binary `+ - * /` (with `*`/`/` binding tighter than `+`/`-`) and
 * unary `+`/`-`. A hand-written recursive-descent parser is used deliberately —
 * NO `eval`/`new Function` — so nothing outside the grammar can run. Returns the
 * computed number, or `null` on any tokenize/parse error, unknown token, or a
 * non-finite result (e.g. division by zero).
 *
 * The second operand `other` is NOT bound: the preview resolves no second path,
 * so an expression naming it is unresolved here (null) rather than computed with
 * a made-up operand - the same refusal _apply_calc makes when it is missing.
 */
export function evalCalc(expr: string, value: number): number | null {
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
  // factor := ('+' | '-') factor | '(' expr ')' | number | value
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
    if (tok.t === 'value') {
      p += 1
      return value
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
