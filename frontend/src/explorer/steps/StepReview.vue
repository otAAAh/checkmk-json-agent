<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Wizard step 4: review — one accordion item per endpoint listing every chosen
     service with its defined values (unit / thresholds / expected regex) and its
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

/** Evaluate a value against the extraction's thresholds/expected-regex. */
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
  if (typeof value === 'string' && typeof x.expected === 'string' && x.expected) {
    try {
      return new RegExp(`^(?:${x.expected})$`).test(value) ? 'ok' : 'crit'
    } catch {
      return 'none'
    }
  }
  return 'none'
}

/** Human-readable list of the configured thresholds/unit/expected. */
function definedSummary(x: ExtractionValue): string[] {
  const parts: string[] = []
  if (typeof x.unit === 'string' && x.unit) {
    parts.push(_t('unit %{u}', { u: x.unit }))
  }
  const upper = levelsPair(x.levels_upper)
  if (upper) {
    parts.push(_t('WARN ≥ %{w} / CRIT ≥ %{c}', { w: String(upper[0]), c: String(upper[1]) }))
  }
  const lower = levelsPair(x.levels_lower)
  if (lower) {
    parts.push(_t('WARN ≤ %{w} / CRIT ≤ %{c}', { w: String(lower[0]), c: String(lower[1]) }))
  }
  if (typeof x.expected === 'string' && x.expected) {
    parts.push(_t('expected /%{e}/', { e: x.expected }))
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
        const matches = sample !== null ? resolvePath(sample, path) : []
        if (!matches.length) {
          return [{ service: name, path, value: undefined, defined, state: 'none' as StateKind }]
        }
        return matches.map((m) => ({
          service: matches.length > 1 && m.label ? `${name} ${m.label}` : name,
          path,
          value: m.value,
          defined,
          state: evalState(m.value, x),
        }))
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
