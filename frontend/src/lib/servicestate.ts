// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The state the review step previews for each service: the field's current
// value from the fetched sample, graded against the extraction's thresholds or
// string-match rule the way the check will grade it on the site.
//
// Ported from the check plugin (cmk_addons/plugins/json_api/agent_based/
// json_api.py): the upper/lower levels and _evaluate_match / _coerce_state,
// which are what really decide. One thing the check never has to deal with is
// what makes this harder: FormEdit hands the wizard SingleChoice and
// ServiceState values as hashed idents, so a mode or a state is only
// recoverable through its title, looked up in the serialized form spec.
// Kept in lib/ rather than in the component so it can be unit-tested and
// type-checked on a bare clone.

import type { Json } from './jsonpaths'
import type { ExtractionValue } from './rulevalue'

/** A previewed service state. There is no 'unknown': UNKNOWN previews as 'none',
 * the same tag as a value the preview cannot grade at all. */
export type StateKind = 'ok' | 'warn' | 'crit' | 'none'

/** Choice ident -> human-readable title, for every choice in a form spec. */
export type ChoiceTitles = ReadonlyMap<string, string>

/** Walk a serialized form spec once and map every choice ident to its title.
 *
 * FormEdit serializes SingleChoice / CascadingSingleChoice / ServiceState values
 * as hashed idents on the wire (e.g. unit "Percent" -> "438dc5f6…"), so the raw
 * extraction values are hashes, not speaking names. */
export function collectChoiceTitles(spec: unknown): Map<string, string> {
  const map = new Map<string, string>()
  const walk = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(walk)
      return
    }
    if (node && typeof node === 'object') {
      const o = node as Record<string, unknown>
      if (typeof o.name === 'string' && typeof o.title === 'string') {
        map.set(o.name, o.title)
      }
      Object.values(o).forEach(walk)
    }
  }
  walk(spec)
  return map
}

/** The speaking title for a (possibly hashed) choice ident, else the value itself. */
export function titleOf(value: unknown, titles: ChoiceTitles): string | null {
  if (typeof value !== 'string' || !value) {
    return null
  }
  return titles.get(value) ?? value
}

/** Map a Checkmk service-state number (0=OK,1=WARN,2=CRIT,3=UNKNOWN) to a
 * StateKind. There is no 'unknown' StateKind, so 3 maps to 'none'. */
export function serviceStateToKind(n: unknown): StateKind {
  return n === 1 ? 'warn' : n === 2 ? 'crit' : n === 3 ? 'none' : 'ok'
}

/** A ServiceState choice (hashed ident or int) -> StateKind, via its title. */
export function stateChoiceKind(value: unknown, titles: ChoiceTitles): StateKind | null {
  if (typeof value === 'number') {
    return serviceStateToKind(value)
  }
  const title = (titleOf(value, titles) ?? '').toUpperCase()
  if (title.includes('WARN')) {
    return 'warn'
  }
  if (title.includes('CRIT')) {
    return 'crit'
  }
  if (title.includes('UNKNOWN')) {
    return 'none'
  }
  if (title.includes('OK')) {
    return 'ok'
  }
  return null
}

/** ['fixed', [warn, crit]] -> [warn, crit], else null. */
export function levelsPair(value: unknown): [number, number] | null {
  if (Array.isArray(value) && value[0] === 'fixed' && Array.isArray(value[1])) {
    const [warn, crit] = value[1] as unknown[]
    if (typeof warn === 'number' && typeof crit === 'number') {
      return [warn, crit]
    }
  }
  return null
}

/** Narrow a serialized match value `[mode, cfg]` into its parts, or null. */
export function parseMatch(match: unknown): { mode: string; cfg: Record<string, unknown> } | null {
  if (
    Array.isArray(match) &&
    typeof match[0] === 'string' &&
    match[1] !== null &&
    typeof match[1] === 'object' &&
    !Array.isArray(match[1])
  ) {
    return { mode: match[0], cfg: match[1] as Record<string, unknown> }
  }
  return null
}

/** Infer the match mode from the cfg keys (the mode string is a hashed ident, so
 * we key off the non-hashed sub-fields instead).
 *
 * must_match's `pattern` is a required field (lib.py string_match), so a cfg
 * without one is a state map - also one with none of its OK/WARN/CRIT patterns
 * set. The check reads that as StateMap(None, None, None) (check_lib/section.py
 * _coerce_match), and _evaluate_match then lands on its `state_no_match`. */
export function matchMode(cfg: Record<string, unknown>): 'must_match' | 'state_map' {
  return typeof cfg.pattern === 'string' ? 'must_match' : 'state_map'
}

/** Whether `value` fully matches `pattern` (anchored); null when the pattern
 * does not compile.
 *
 * Null rather than false because the check does not treat a bad pattern as a
 * miss: re.fullmatch raises re.error and _value_results turns that into UNKNOWN
 * ("Invalid match pattern"). The dialects differ at the edges - Python's
 * `(?P<name>…)` is not JavaScript, and some JavaScript is not Python - so a
 * pattern only one side compiles previews differently; the common syntax is
 * shared. */
export function fullMatch(value: string, pattern: string): boolean | null {
  try {
    return new RegExp(`^(?:${pattern})$`).test(value)
  } catch {
    return null
  }
}

/** Evaluate a value against the extraction's thresholds / match rule. */
export function evalState(value: Json | undefined, x: ExtractionValue, titles: ChoiceTitles): StateKind {
  if (typeof value === 'number') {
    // Checkmk's check_levels (cmk/agent_based/v2/_check_levels.py,
    // _check_fixed_levels) is inclusive upwards and STRICT downwards: a value
    // exactly on an upper threshold is already WARN/CRIT, one exactly on a lower
    // threshold is still OK/WARN.
    const upper = levelsPair(x.levels_upper)
    if (upper) {
      if (value >= upper[1]) {
        return 'crit'
      }
      if (value >= upper[0]) {
        return 'warn'
      }
    }
    const lower = levelsPair(x.levels_lower)
    if (lower) {
      if (value < lower[1]) {
        return 'crit'
      }
      if (value < lower[0]) {
        return 'warn'
      }
    }
    return upper || lower ? 'ok' : 'none'
  }
  if (typeof value === 'string') {
    const parsed = parseMatch(x.match)
    if (!parsed) {
      return 'none'
    }
    const { cfg } = parsed
    const mode = matchMode(cfg)
    // A pattern that does not compile is UNKNOWN on the site (see fullMatch),
    // which previews as 'none'.
    if (mode === 'must_match' && typeof cfg.pattern === 'string') {
      const matched = fullMatch(value, cfg.pattern)
      if (matched === null) {
        return 'none'
      }
      if (matched) {
        return 'ok'
      }
      return stateChoiceKind(cfg.state_no_match, titles) ?? 'crit'
    }
    // Tried in _evaluate_match's order, and lazily like it: a broken WARN
    // pattern is never compiled - so never an error - once OK has matched.
    for (const [key, kind] of [
      ['ok', 'ok'],
      ['warn', 'warn'],
      ['crit', 'crit'],
    ] as const) {
      const pat = cfg[key]
      if (typeof pat === 'string' && pat) {
        const matched = fullMatch(value, pat)
        if (matched === null) {
          return 'none'
        }
        if (matched) {
          return kind
        }
      }
    }
    return stateChoiceKind(cfg.state_no_match, titles) ?? 'ok'
  }
  return 'none'
}
