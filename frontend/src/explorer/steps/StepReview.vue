<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Wizard step 4: review — one accordion item per endpoint listing every chosen
     service with its defined values (unit / thresholds / match rule) and its
     CURRENT value + state, resolved live against the fetched sample JSON. Then
     create the rule via the API. The evaluation itself (calc, thresholds, match
     rules, host labels) lives in lib/, where it is unit-tested; this file turns
     its results into rows and words. -->
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
import { evalCalc } from '../../lib/calc'
import { labelWarnings, resolveLabelled, type LabelledValue } from '../../lib/elementlabels'
import { labelKeyOf, type Json } from '../../lib/jsonpaths'
import {
  calcOther,
  elementCount,
  formatJson,
  hasFilter,
  inventoryOnly,
  inventoryTarget,
  piggybackField,
  previewHostLabels,
  valueAsKind,
  type HostLabelPreview,
} from '../../lib/reviewpreview'
import { endpointUrl, extractionPath, extractionService, type ExtractionValue } from '../../lib/rulevalue'
import { elementNames, fieldNames, servicePrefix, type FieldNames } from '../../lib/servicenames'
import {
  collectChoiceTitles,
  evalState,
  levelsPair,
  matchMode,
  parseMatch,
  stateChoiceKind,
  titleOf as choiceTitleOf,
  type StateKind,
} from '../../lib/servicestate'

const { _t } = usei18n()
const { state, status, createRuleOnSite, extractionsSpec } = useExplorer()

// The raw extraction values are hashed choice idents; map them back to titles
// once per spec (see collectChoiceTitles).
const choiceTitles = computed(() =>
  collectChoiceTitles((extractionsSpec.value as { spec?: unknown } | null)?.spec),
)

/** The speaking title for a (possibly hashed) choice ident, else the value itself. */
function titleOf(value: unknown): string | null {
  return choiceTitleOf(value, choiceTitles.value)
}

// Review is read-only: auto-expand the first endpoint, but allow any number
// open at once (min/max-open 0) — unlike the single-open accordion on step 2.
const openedItems = ref<string[]>(['0'])

/** Human-readable list of the configured thresholds/unit/match rule. */
function definedSummary(x: ExtractionValue): string[] {
  const parts: string[] = []
  // Leads the summary: it changes WHERE the services land, which matters more
  // than any threshold on them. `piggyback_host` is a plain String, not a hashed
  // choice ident, so it can be shown verbatim.
  if (typeof x.piggyback_host === 'string' && x.piggyback_host.trim()) {
    parts.push(_t('one host per element, named by %{f}', { f: x.piggyback_host.trim() }))
  }
  // Also leads: an inventory field with `keep_service` off creates NO service at
  // all, so saying it here is what stops a "my field vanished" report.
  const inventory = inventoryTarget(x)
  if (inventory) {
    parts.push(
      inventory.keepService
        ? _t('inventory %{n}, and a service', { n: inventory.node })
        : _t('inventory %{n} — no service', { n: inventory.node })
    )
  }
  const unit = titleOf(x.unit)
  if (unit) {
    parts.push(_t('unit %{u}', { u: unit }))
  }
  const aggregate = titleOf(x.aggregate)
  if (aggregate) {
    parts.push(_t('aggregate: %{a}', { a: aggregate }))
  }
  const readAs = valueAsKind(x.value_as)
  if (readAs === 'counter') {
    parts.push(_t('per-second rate'))
  } else if (readAs === 'timestamp') {
    parts.push(_t('age of the timestamp'))
  }
  if (typeof x.calc === 'string' && x.calc) {
    parts.push(_t('calc %{c}', { c: x.calc }))
  }
  if (typeof x.summary === 'string' && x.summary) {
    // Shown verbatim: the placeholders are resolved by the agent against the
    // live response, which this preview does not have per element.
    parts.push(_t('summary %{s}', { s: x.summary }))
  }
  const upper = levelsPair(x.levels_upper)
  if (upper) {
    parts.push(_t('WARN ≥ %{w} / CRIT ≥ %{c}', { w: String(upper[0]), c: String(upper[1]) }))
  }
  const lower = levelsPair(x.levels_lower)
  if (lower) {
    // Strictly below: check_levels leaves a value ON a lower threshold alone.
    parts.push(_t('WARN < %{w} / CRIT < %{c}',{ w: String(lower[0]), c: String(lower[1]) }))
  }
  const parsed = parseMatch(x.match)
  if (parsed) {
    const { cfg } = parsed
    const mode = matchMode(cfg)
    if (mode === 'must_match' && typeof cfg.pattern === 'string' && cfg.pattern) {
      let desc: string = _t('must match /%{e}/', { e: cfg.pattern })
      const noMatch = titleOf(cfg.state_no_match)
      if (noMatch && stateChoiceKind(cfg.state_no_match, choiceTitles.value) !== 'crit') {
        desc += _t(' (else %{s})', { s: noMatch })
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
  return formatJson(value, _t('(not found in sample)'))
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
  // This field's name WITHIN its service, when several fields share one. null
  // for the ordinary case, where the field IS the service.
  line?: string | null
  path: string
  value: Json | undefined
  // Shown instead of the value when the monitored number is only computed on the
  // site (an aggregation, a rate, an age) and so cannot be previewed here.
  note?: string
  defined: string[]
  state: StateKind
  labels: string[]
  // The field goes into the inventory tree and creates no service. Still shown -
  // it IS configured - but it must not be counted as a service that will appear.
  inventoryOnly?: boolean
  // What the sample says is wrong with the '[*]' elements' names (a repeat the
  // site can only tell apart by position, a missing name field, ...). On the
  // field's first row only, so a field with fifty elements warns once.
  warnings?: string[]
}
interface EndpointReview {
  url: string
  rows: Row[]
  // Split because an inventory-only field produces a row but no service.
  serviceCount: number
  inventoryCount: number
  hostLabels: HostLabelPreview[]
  // The endpoint follows the API's pagination: the counts describe page one.
  paginates: boolean
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
    const hostLabels = previewHostLabels(sample, services?.hostLabels ?? [], {
      notFound: _t('(not found in sample)'),
      conditional: _t('(only when the condition matches)'),
    })
    const prefix = servicePrefix(connection)
    const rows: Row[] = (services?.extractions ?? [])
      .filter((x) => extractionPath(x))
      .flatMap((x): Row[] => {
        // Tagged on every row this extraction produces, so the header can count
        // the services that will actually be created - not the fields.
        const noService = inventoryOnly(x)
        const tag = (produced: Row[]): Row[] =>
          noService ? produced.map((row) => ({ ...row, inventoryOnly: true })) : produced
        const field = extractionService(x) || _t('(unnamed)')
        // The same two steps the agent takes: a shared service takes over the
        // name and pushes the field's own name down to its line, and the
        // endpoint's prefix goes in front of whatever that leaves.
        const { service: name, line } = fieldNames(x, field, prefix)
        const path = extractionPath(x)
        const defined = definedSummary(x)
        const labels = (Array.isArray(x.labels) ? (x.labels as Record<string, unknown>[]) : []).map(
          (l) => `json_api/${labelKeyOf(l)}`,
        )
        // Optional arithmetic transform applied to a NUMERIC value before
        // levels/display, e.g. `value / 1024 / 1024`.
        const calc = typeof x.calc === 'string' && x.calc ? x.calc : null
        // An aggregation collapses the whole collection into ONE service, and a
        // counter / timestamp is monitored as a rate / an age. Which function was
        // picked is a hashed ident here and the rate needs two checks, so those
        // numbers are deliberately NOT recomputed in the preview — the row states
        // what the site will do instead of showing a value that might differ.
        const aggregate = titleOf(x.aggregate)
        const readAs = valueAsKind(x.value_as)
        // Named the way the agent names them: by the label path's field, the
        // position where it is missing, and a repeat suffixed with its position.
        const labelPath = typeof x.label_path === 'string' ? x.label_path : ''
        // With a host per element, each element's host is resolved too: one
        // whose host field does not resolve stays on the polling host, under
        // the labelled name.
        const perElementHost = piggybackField(x)
        const resolved =
          sample !== null ? resolveLabelled(sample, path, labelPath, perElementHost ?? '') : null
        const matches = resolved?.values ?? []
        if (!matches.length) {
          return tag([
            { service: name, line, path, value: undefined, defined, state: 'none' as StateKind, labels },
          ])
        }
        if (aggregate) {
          const elements = elementCount(matches, path)
          const filtered = hasFilter(x)
          let note: string
          if (elements === null) {
            note = _t('not an array or object — the service will be UNKNOWN')
          } else if (filtered) {
            // The condition narrows this set, by how much only the site knows.
            note = _t('%{n} element(s) before the condition, filtered on the site', {
              n: String(elements),
            })
          } else {
            note = _t('%{n} element(s), aggregated on the site', { n: String(elements) })
          }
          return tag([
            {
              service: name,
              line,
              path,
              value: undefined,
              note,
              defined,
              state: 'none' as StateKind,
              labels,
            },
          ])
        }
        // With a host field, every element whose host resolves keeps the PLAIN
        // service name and lands on a host of its own, so the label suffix the
        // preview would otherwise show is not what gets created for it.
        // With a shared service the expansion fans out into LINES of that one
        // service, so the element label lands on the line and the service name
        // stays put — and with a host per element the host carries the identity
        // and the service keeps its plain name. Any '[*]' path fans out, also
        // over a collection of one: its single service still carries the
        // element's suffix on the site.
        const fansOut = path.includes('[*]')
        const named = (m: LabelledValue): FieldNames =>
          elementNames({ service: name, line }, m.label, {
            perElementHost: m.host !== null,
            fansOut,
          })
        const produced = matches.map((m): Row => {
          if (readAs !== null) {
            const { service, line: elementLine } = named(m)
            return {
              service,
              line: elementLine,
              path,
              value: m.value,
              note:
                readAs === 'counter'
                  ? _t('%{v} → the rate is computed on the site', { v: fmtValue(m.value) })
                  : _t('%{v} → the age is computed on the site', { v: fmtValue(m.value) }),
              defined,
              state: 'none' as StateKind,
              labels,
            }
          }
          const { service, line: elementLine } = named(m)
          let value: Json = m.value
          // Transform the value when `calc` is set and it is numeric. On a bad
          // expression evalCalc returns null: leave the value shown and mark the
          // preview unresolved rather than crashing.
          // `other` comes from the second path, resolved in this value's own
          // element, as the agent does; unresolved, the check goes UNKNOWN.
          if (calc !== null && typeof value === 'number') {
            const transformed = evalCalc(calc, value, calcOther(m.element, x.calc_path))
            if (transformed === null) {
              return { service, line: elementLine, path, value, defined, state: 'none' as StateKind, labels }
            }
            value = transformed
          }
          return { service, line: elementLine, path, value, defined, state: evalState(value, x, choiceTitles.value), labels }
        })
        // With a host per element the suffix only reaches the services of the
        // elements that stay on the polling host; resolveLabelled has already
        // narrowed the issues to those.
        const warnings = resolved ? labelWarnings(resolved.issues, _t) : []
        if (warnings.length && produced[0]) {
          produced[0] = { ...produced[0], warnings }
        }
        return tag(produced)
      })
    return {
      url: endpointUrl(connection) || _t('Endpoint %{n}', { n: ei + 1 }),
      rows,
      hostLabels,
      // An inventory-only field is configured and shown, but it becomes a tree
      // entry, not a service - counting it as one would promise a service that
      // never appears. Counted by NAME, not by row: fields sharing a service
      // are several rows and one service, and promising one per field is how
      // this number gets the answer wrong for the whole feature.
      serviceCount: new Set(
        rows.filter((row) => !row.inventoryOnly).map((row) => row.service),
      ).size,
      inventoryCount: rows.filter((row) => row.inventoryOnly).length,
      // The site follows this endpoint's pagination and merges the pages, but the
      // preview resolves against the ONE response the wizard fetched - so every
      // count below describes the first page. Said out loud rather than silently
      // shown, because a count that is off by the rest of the collection is
      // exactly what following the pages exists to fix.
      paginates: !!connection.pagination,
    }
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
            <span class="je-step-review__count">{{
              review.inventoryCount
                ? _t('%{n} services, %{i} inventory, %{h} host labels', { n: review.serviceCount, i: review.inventoryCount, h: review.hostLabels.length })
                : _t('%{n} services, %{h} host labels', { n: review.serviceCount, h: review.hostLabels.length })
            }}</span>
          </template>
          <template #content>
            <p v-if="review.paginates" class="je-step-review__note">{{ _t('This endpoint follows the API\'s pagination. The site merges every page, while the counts below come from the single response fetched here - so they describe the first page only.') }}</p>
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
                    <span v-if="row.line" class="je-step-review__line-name">{{ _t('line %{l}', { l: row.line }) }}</span>
                    <code class="je-step-review__path">{{ row.path }}</code>
                    <span class="je-step-review__value">{{ row.note ? row.note : `= ${fmtValue(row.value)}` }}</span>
                  </div>
                  <div v-if="row.defined.length" class="je-step-review__defined">
                    {{ row.defined.join(' · ') }}
                  </div>
                  <div v-if="row.labels.length" class="je-step-review__labels">
                    <span class="je-step-review__labels-title">{{ _t('Service labels:') }}</span>
                    <CmkTag
                      v-for="(lab, li) in row.labels"
                      :key="li"
                      size="small"
                      variant="fill"
                      color="default"
                      :content="_t('%{s}', { s: lab })"
                    />
                  </div>
                  <CmkAlertBox
                    v-for="(warning, wi) in row.warnings ?? []"
                    :key="`w${wi}`"
                    class="je-step-review__warning"
                    variant="warning"
                    size="small"
                  >{{ warning }}</CmkAlertBox>
                </div>
              </li>
            </ul>
            <div v-if="review.hostLabels.length" class="je-step-review__hostlabels">
              <span class="je-step-review__hostlabels-title">{{ _t('Host labels:') }}</span>
              <CmkTag
                v-for="(lab, li) in review.hostLabels"
                :key="li"
                size="small"
                variant="fill"
                color="default"
                :content="_t('%{k}:%{v}', { k: lab.label, v: lab.value })"
              />
            </div>
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

.je-step-review__line-name {
  font-style: italic;
  opacity: 0.8;
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

.je-step-review__labels {
  display: flex;
  flex-wrap: wrap;
  gap: var(--dimension-3, 6px);
  align-items: center;
  margin-top: 4px;
  font-size: 12px;
}

.je-step-review__labels-title {
  color: var(--font-color-dimmed);
}

.je-step-review__hostlabels {
  display: flex;
  flex-wrap: wrap;
  gap: var(--dimension-3, 6px);
  align-items: center;
  margin-top: var(--dimension-4, 8px);
  font-size: 12px;
}

.je-step-review__hostlabels-title {
  color: var(--font-color-dimmed);
}

.je-step-review__empty {
  color: var(--font-color-dimmed);
}

.je-step-review__note {
  color: var(--font-color-dimmed);
}

.je-step-review__warning {
  margin-top: 4px;
}

.je-step-review__create {
  display: flex;
  flex-direction: column;
  gap: var(--dimension-4, 8px);
  align-items: flex-start;
  margin-top: var(--dimension-5, 12px);
}
</style>
