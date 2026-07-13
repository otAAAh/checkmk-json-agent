<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Wizard step 4: review — one accordion item per endpoint listing every chosen
     service with its defined values (unit / thresholds / match rule) and its
     CURRENT value + state, resolved live against the fetched sample JSON. Then
     create the rule via the API. -->
<script setup lang="ts">
import { computed, ref } from 'vue'

import usei18n from '@/lib/i18n'

import CmkAccordion from '@/components/CmkAccordion/CmkAccordion.vue'
import CmkAccordionItem from '@/components/CmkAccordion/CmkAccordionItem.vue'
import CmkAlertBox from '@/components/CmkAlertBox.vue'
import CmkButton from '@/components/CmkButton'
import CmkTag from '@/components/CmkTag.vue'
import { CmkWizardButton, CmkWizardStep } from '@/components/CmkWizard'
import CmkHeading from '@/components/typography/CmkHeading.vue'
import CmkParagraph from '@/components/typography/CmkParagraph.vue'

import { useExplorer } from '../../composables/useExplorer'
import { resolvePath, type Json } from '../../lib/jsonpaths'
import {
  endpointUrl,
  extractionPath,
  extractionService,
  type ExtractionValue,
} from '../../lib/rulevalue'

const { _t } = usei18n()
const { state, status, createRuleOnSite } = useExplorer()

// Review is read-only: auto-expand the first endpoint, but allow any number
// open at once (min/max-open 0) — unlike the single-open accordion on step 2.
const openedItems = ref<string[]>(['0'])

type StateKind = 'ok' | 'warn' | 'crit' | 'none'

/** ['fixed', [warn, crit]] -> [warn, crit], else null. */
function levelsPair(value: unknown): [number, number] | null {
  if (Array.isArray(value) && value[0] === 'fixed' && Array.isArray(value[1])) {
    const [warn, crit] = value[1] as unknown[]
    if (typeof warn === 'number' && typeof crit === 'number') {
      return [warn, crit]
    }
  }
  return null
}

/** Map a Checkmk service-state number (0=OK,1=WARN,2=CRIT,3=UNKNOWN) to a
 * StateKind. There is no 'unknown' StateKind, so 3 maps to 'none'. */
function serviceStateToKind(n: unknown): StateKind {
  return n === 1 ? 'warn' : n === 2 ? 'crit' : n === 3 ? 'none' : 'ok'
}

/** Narrow a serialized match value `[mode, cfg]` into its parts, or null. */
function parseMatch(match: unknown): { mode: string; cfg: Record<string, unknown> } | null {
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

/**
 * Safely evaluate an arithmetic `calc` expression over the variable `value`.
 *
 * Mirrors the special-agent grammar: decimal numbers, the identifier `value`,
 * parentheses, binary `+ - * /` (with `*`/`/` binding tighter than `+`/`-`) and
 * unary `+`/`-`. A hand-written recursive-descent parser is used deliberately —
 * NO `eval`/`new Function` — so nothing outside the grammar can run. Returns the
 * computed number, or `null` on any tokenize/parse error, unknown token, or a
 * non-finite result (e.g. division by zero).
 */
function evalCalc(expr: string, value: number): number | null {
  // --- Tokenize into numbers / 'value' / operators / parens. ---------------
  type Token = { t: 'num'; v: number } | { t: 'value' } | { t: 'op'; v: string }
  const tokens: Token[] = []
  let i = 0
  while (i < expr.length) {
    const c = expr[i]!
    if (c === ' ' || c === '\t' || c === '\n' || c === '\r') {
      i += 1
    } else if (c === '+' || c === '-' || c === '*' || c === '/' || c === '(' || c === ')') {
      tokens.push({ t: 'op', v: c })
      i += 1
    } else if (c >= '0' && c <= '9') {
      // A decimal number: leading digits, optional single '.' with more digits.
      let j = i + 1
      while (j < expr.length && expr[j]! >= '0' && expr[j]! <= '9') j += 1
      if (j < expr.length && expr[j] === '.') {
        j += 1
        while (j < expr.length && expr[j]! >= '0' && expr[j]! <= '9') j += 1
      }
      tokens.push({ t: 'num', v: Number(expr.slice(i, j)) })
      i = j
    } else if (c === '.') {
      // A number starting with '.', e.g. `.5`.
      let j = i + 1
      while (j < expr.length && expr[j]! >= '0' && expr[j]! <= '9') j += 1
      if (j === i + 1) return null // a lone '.'
      tokens.push({ t: 'num', v: Number(expr.slice(i, j)) })
      i = j
    } else if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) {
      // The only identifier allowed is `value`; anything else (function names,
      // other variables) is rejected.
      let j = i + 1
      while (j < expr.length) {
        const d = expr[j]!
        if ((d >= 'a' && d <= 'z') || (d >= 'A' && d <= 'Z')) j += 1
        else break
      }
      if (expr.slice(i, j) !== 'value') return null
      tokens.push({ t: 'value' })
      i = j
    } else {
      return null // unknown character
    }
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
    if (!tok) throw new Error('unexpected end')
    if (tok.t === 'op' && (tok.v === '+' || tok.v === '-')) {
      p += 1
      const operand = parseFactor()
      return tok.v === '-' ? -operand : operand
    }
    if (tok.t === 'op' && tok.v === '(') {
      p += 1
      const inner = parseExpr()
      const close = peek()
      if (!close || close.t !== 'op' || close.v !== ')') throw new Error('missing )')
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
    if (p !== tokens.length) return null // trailing tokens left unparsed
    return Number.isFinite(result) ? result : null
  } catch {
    return null
  }
}

/** Whether `value` fully matches `pattern` (anchored); false on a bad regex. */
function fullMatch(value: string, pattern: string): boolean {
  try {
    return new RegExp(`^(?:${pattern})$`).test(value)
  } catch {
    return false
  }
}

/** Evaluate a value against the extraction's thresholds / match rule. */
function evalState(value: Json | undefined, x: ExtractionValue): StateKind {
  if (typeof value === 'number') {
    const upper = levelsPair(x.levels_upper)
    if (upper) {
      if (value >= upper[1]) return 'crit'
      if (value >= upper[0]) return 'warn'
    }
    const lower = levelsPair(x.levels_lower)
    if (lower) {
      if (value <= lower[1]) return 'crit'
      if (value <= lower[0]) return 'warn'
    }
    return upper || lower ? 'ok' : 'none'
  }
  if (typeof value === 'string') {
    const parsed = parseMatch(x.match)
    if (!parsed) return 'none'
    const { mode, cfg } = parsed
    if (mode === 'must_match' && typeof cfg.pattern === 'string') {
      let matched: boolean
      try {
        matched = new RegExp(`^(?:${cfg.pattern})$`).test(value)
      } catch {
        return 'none'
      }
      if (matched) return 'ok'
      return serviceStateToKind(typeof cfg.state_no_match === 'number' ? cfg.state_no_match : 2)
    }
    if (mode === 'state_map') {
      for (const [key, kind] of [
        ['ok', 'ok'],
        ['warn', 'warn'],
        ['crit', 'crit'],
      ] as const) {
        const pat = cfg[key]
        if (typeof pat === 'string' && pat && fullMatch(value, pat)) {
          return kind
        }
      }
      return serviceStateToKind(typeof cfg.state_no_match === 'number' ? cfg.state_no_match : 0)
    }
    return 'none'
  }
  return 'none'
}

/** Label a Checkmk service-state number for the no-match case. */
function stateNoMatchLabel(n: number): string {
  return n === 0 ? _t('OK') : n === 1 ? _t('WARN') : n === 3 ? _t('UNKNOWN') : _t('CRIT')
}

/** Human-readable list of the configured thresholds/unit/match rule. */
function definedSummary(x: ExtractionValue): string[] {
  const parts: string[] = []
  if (typeof x.unit === 'string' && x.unit) {
    parts.push(_t('unit %{u}', { u: x.unit }))
  }
  if (typeof x.calc === 'string' && x.calc) {
    parts.push(_t('calc %{c}', { c: x.calc }))
  }
  const upper = levelsPair(x.levels_upper)
  if (upper) {
    parts.push(_t('WARN ≥ %{w} / CRIT ≥ %{c}', { w: String(upper[0]), c: String(upper[1]) }))
  }
  const lower = levelsPair(x.levels_lower)
  if (lower) {
    parts.push(_t('WARN ≤ %{w} / CRIT ≤ %{c}', { w: String(lower[0]), c: String(lower[1]) }))
  }
  const parsed = parseMatch(x.match)
  if (parsed) {
    const { mode, cfg } = parsed
    if (mode === 'must_match' && typeof cfg.pattern === 'string' && cfg.pattern) {
      let desc: string = _t('must match /%{e}/', { e: cfg.pattern })
      if (typeof cfg.state_no_match === 'number' && cfg.state_no_match !== 2) {
        desc += _t(' (else %{s})', { s: stateNoMatchLabel(cfg.state_no_match) })
      }
      parts.push(desc)
    } else if (mode === 'state_map') {
      const patterns: string[] = []
      for (const [key, label] of [
        ['ok', 'OK'],
        ['warn', 'WARN'],
        ['crit', 'CRIT'],
      ] as const) {
        const pat = cfg[key]
        if (typeof pat === 'string' && pat) {
          patterns.push(`${label} /${pat}/`)
        }
      }
      if (patterns.length) {
        parts.push(_t('state map: %{s}', { s: patterns.join(', ') }))
      }
    }
  }
  return parts
}

function fmtValue(value: Json | undefined): string {
  if (value === undefined) return _t('(not found in sample)')
  if (value === null) return 'null'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

const tagColor: Record<StateKind, 'success' | 'warning' | 'danger' | 'unknown'> = {
  ok: 'success',
  warn: 'warning',
  crit: 'danger',
  none: 'unknown',
}
function tagLabel(s: StateKind): string {
  return s === 'ok' ? _t('OK') : s === 'warn' ? _t('WARN') : s === 'crit' ? _t('CRIT') : _t('n/a')
}

interface Row {
  service: string
  path: string
  value: Json | undefined
  defined: string[]
  state: StateKind
}
interface EndpointReview {
  url: string
  rows: Row[]
}

const reviews = computed<EndpointReview[]>(() =>
  state.connections.map((connection, ei) => {
    const services = state.services[ei]
    let sample: Json | null = null
    try {
      sample = services?.sampleJson ? (JSON.parse(services.sampleJson) as Json) : null
    } catch {
      sample = null
    }
    const rows: Row[] = (services?.extractions ?? [])
      .filter((x) => extractionPath(x))
      .flatMap((x) => {
        const name = extractionService(x) || _t('(unnamed)')
        const path = extractionPath(x)
        const defined = definedSummary(x)
        // Optional arithmetic transform applied to a NUMERIC sample value before
        // levels/display, e.g. `value / 1024 / 1024`.
        const calc = typeof x.calc === 'string' && x.calc ? x.calc : null
        const matches = sample !== null ? resolvePath(sample, path) : []
        if (!matches.length) {
          return [{ service: name, path, value: undefined, defined, state: 'none' as StateKind }]
        }
        return matches.map((m): Row => {
          // Transform the value when `calc` is set and the sample is numeric.
          // On a bad expression evalCalc returns null: leave the raw value shown
          // and mark the numeric preview unresolved rather than crashing.
          let value = m.value
          if (calc !== null && typeof m.value === 'number') {
            const transformed = evalCalc(calc, m.value)
            if (transformed === null) {
              return {
                service: matches.length > 1 && m.label ? `${name} ${m.label}` : name,
                path,
                value: m.value,
                defined,
                state: 'none' as StateKind,
              }
            }
            value = transformed
          }
          return {
            service: matches.length > 1 && m.label ? `${name} ${m.label}` : name,
            path,
            value,
            defined,
            state: evalState(value, x),
          }
        })
      })
    return { url: endpointUrl(connection) || _t('Endpoint %{n}', { n: ei + 1 }), rows }
  }),
)
</script>

<template>
  <CmkWizardStep :index="4" :is-completed="() => status.kind === 'ok'">
    <template #header>
      <CmkHeading>{{ _t('Review & create') }}</CmkHeading>
      <CmkParagraph>{{ _t('Each endpoint\'s services with their configured values and the current state from the fetched sample.') }}</CmkParagraph>
    </template>
    <template #content>
      <CmkAccordion v-model="openedItems" :min-open="0" :max-open="0">
        <CmkAccordionItem
          v-for="(review, ei) in reviews"
          :key="ei"
          :value="String(ei)"
          header-as="div"
        >
          <template #header>
            <CmkHeading type="h3" class="je-step-review__title">{{ review.url }}</CmkHeading>
            <span class="je-step-review__count">{{ _t('%{n} services', { n: review.rows.length }) }}</span>
          </template>
          <template #content>
            <p v-if="!review.rows.length" class="je-step-review__empty">{{ _t('No services selected.') }}</p>
            <ul v-else class="je-step-review__services">
              <li v-for="(row, ri) in review.rows" :key="ri" class="je-step-review__service">
                <CmkTag
                  class="je-step-review__state"
                  size="medium"
                  variant="fill"
                  :color="tagColor[row.state]"
                  :content="tagLabel(row.state)"
                />
                <div class="je-step-review__body">
                  <div class="je-step-review__line">
                    <span class="je-step-review__name">{{ row.service }}</span>
                    <code class="je-step-review__path">{{ row.path }}</code>
                    <span class="je-step-review__value">= {{ fmtValue(row.value) }}</span>
                  </div>
                  <div v-if="row.defined.length" class="je-step-review__defined">
                    {{ row.defined.join(' · ') }}
                  </div>
                </div>
              </li>
            </ul>
          </template>
        </CmkAccordionItem>
      </CmkAccordion>

      <div class="je-step-review__create">
        <CmkButton variant="success" :disabled="status.kind === 'busy'" @click="createRuleOnSite">
          {{ status.kind === 'busy' ? _t('Creating…') : _t('Create rule on this site') }}
        </CmkButton>
        <CmkAlertBox v-if="status.kind === 'ok'" variant="success" size="small">
          {{ _t('Rule created%{id}. Opening the ruleset…', { id: status.id ? ` (id ${status.id})` : '' }) }}
        </CmkAlertBox>
        <CmkAlertBox v-if="status.kind === 'err'" variant="error" size="small">{{ status.msg }}</CmkAlertBox>
      </div>
    </template>
    <template #actions>
      <CmkWizardButton type="previous" :disabled="status.kind === 'ok'" />
    </template>
  </CmkWizardStep>
</template>

<style scoped>
.je-step-review__title {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.je-step-review__count {
  flex-shrink: 0;
  margin-left: auto;
  padding-left: var(--dimension-4, 8px);
  font-weight: normal;
  color: var(--font-color-dimmed);
}

/* stylelint-disable-next-line checkmk/vue-bem-naming-convention */
:deep(.cmk-accordion-item__content-wrapper) {
  padding: var(--dimension-4, 8px) var(--dimension-6, 16px);
  background: var(--default-bg-color);
}

.je-step-review__services {
  padding: 0;
  margin: 0;
  list-style: none;
}

.je-step-review__service {
  display: flex;
  gap: var(--dimension-4, 8px);
  align-items: flex-start;
  padding: var(--dimension-3, 6px) 0;
  border-top: 1px solid var(--default-border-color);
}

.je-step-review__service:first-child {
  border-top: 0;
}

.je-step-review__state {
  box-sizing: border-box;
  flex-shrink: 0;
  align-self: center;
  width: 80px;
  margin: 0;
  text-align: center;
}

.je-step-review__body {
  min-width: 0;
}

.je-step-review__line {
  display: flex;
  flex-wrap: wrap;
  gap: var(--dimension-4, 8px);
  align-items: baseline;
}

.je-step-review__name {
  font-weight: bold;
}

.je-step-review__path,
.je-step-review__value {
  font: 12px/1.45 ui-monospace, monospace;
  color: var(--font-color-dimmed);
}

.je-step-review__defined {
  margin-top: 2px;
  font-size: 12px;
  color: var(--font-color-dimmed);
}

.je-step-review__empty {
  color: var(--font-color-dimmed);
}

.je-step-review__create {
  display: flex;
  flex-direction: column;
  gap: var(--dimension-4, 8px);
  align-items: flex-start;
  margin-top: var(--dimension-5, 12px);
}
</style>
