<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- JSON field picker (controlled): the sample JSON lives in the wizard state
     (v-model) and is fetched server-side for all endpoints on the step 1→2
     switch. Renders the sample as a tree with inline "monitor" buttons that emit
     the JSON path; a textarea allows manual paste/override and a Refetch button
     re-pulls this endpoint. -->
<script setup lang="ts">
import { computed, ref } from 'vue'

import usei18n from '@/lib/i18n'

import CmkAlertBox from '@/components/CmkAlertBox.vue'
import CmkButton from '@/components/CmkButton'
import CmkIcon from '@/components/CmkIcon'
import CmkLoading from '@/components/CmkLoading.vue'
import CmkScrollContainer from '@/components/CmkScrollContainer.vue'
import CmkToggleButtonGroup from '@/components/CmkToggleButtonGroup.vue'

import { buildTree, type Json } from '../../lib/jsonpaths'
import JsonTreeNode from './JsonTreeNode.vue'

const props = defineProps<{
  modelValue: string
  selectedPaths: string[]
  loading?: boolean
  error?: string
  canRefetch?: boolean
  /** Response headers of the last successful fetch, for the Headers tab. */
  headers?: Record<string, string>
}>()
const emit = defineEmits<{
  'update:modelValue': [value: string]
  toggle: [path: string, valueType: string, sampleValue?: Json]
  hostlabel: [path: string]
  refetch: []
}>()

const text = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
})

const parseError = computed(() => {
  if (!props.modelValue.trim()) {
    return ''
  }
  try {
    JSON.parse(props.modelValue)
    return ''
  } catch (e) {
    return e instanceof Error ? e.message : String(e)
  }
})

const tree = computed(() => {
  if (parseError.value || !props.modelValue.trim()) {
    return []
  }
  return buildTree(JSON.parse(props.modelValue) as Json)
})

const { _t } = usei18n()

const view = ref<'picker' | 'headers' | 'raw'>('picker')
const viewOptions = [
  { label: _t('Picker'), value: 'picker' },
  { label: _t('Headers'), value: 'headers' },
  { label: _t('Raw JSON'), value: 'raw' },
]

// The response headers as sorted rows. A header is monitored with an '@header.'
// path, which the agent answers from the response headers rather than the body -
// so it is offered here rather than in the JSON tree, which has no place for it.
const headerRows = computed(() =>
  Object.entries(props.headers ?? {})
    .map(([name, value]) => ({ name, value, path: `@header.${name}` }))
    .sort((a, b) => a.name.localeCompare(b.name)),
)

function headerSelected(path: string): boolean {
  // Case-insensitively, because the agent looks a header up that way: a path
  // already added as 'X-Ratelimit-Remaining' must not offer to add it again
  // just because the server spells it 'x-ratelimit-remaining' this time.
  const wanted = path.toLowerCase()
  return props.selectedPaths.some((p) => p.toLowerCase() === wanted)
}
</script>

<template>
  <div class="je-json-picker">
    <div class="je-json-picker__bar">
      <CmkToggleButtonGroup v-model="view" :options="viewOptions" />
    </div>
    <div class="je-json-picker__view">
      <div v-if="loading" class="je-json-picker__overlay">
        <CmkLoading height="14px" />
      </div>
      <textarea
        v-show="view === 'raw'"
        v-model="text"
        class="je-json-picker__input"
        spellcheck="false"
        :placeholder="_t('Auto-filled from the endpoint on the way here, or paste a sample JSON response…')"
      ></textarea>
      <template v-if="view === 'picker'">
        <CmkAlertBox v-if="parseError" variant="error" size="small">{{ _t('Invalid JSON: %{error}', { error: parseError }) }}</CmkAlertBox>
        <p v-else-if="!tree.length" class="je-json-picker__hint">
          {{ _t('No JSON yet — fetch from the endpoint or switch to Raw JSON to paste a sample.') }}
        </p>
        <CmkScrollContainer v-else height="clamp(160px, 55vh, 640px)" class="je-json-picker__tree">
          <ul class="je-json-picker__tree-list">
            <JsonTreeNode
              v-for="(node, i) in tree"
              :key="i"
              :node="node"
              :selected-paths="selectedPaths"
              @toggle="(p, t, v) => emit('toggle', p, t, v)"
              @hostlabel="(p) => emit('hostlabel', p)"
            />
          </ul>
        </CmkScrollContainer>
      </template>
      <template v-else-if="view === 'headers'">
        <p v-if="!headerRows.length" class="je-json-picker__hint">
          {{ _t('No headers yet — they come from fetching the endpoint, so a hand-pasted sample has none.') }}
        </p>
        <CmkScrollContainer v-else height="clamp(160px, 55vh, 640px)" class="je-json-picker__tree">
          <ul class="je-json-picker__headers">
            <li v-for="row in headerRows" :key="row.name" class="je-json-picker__header-row">
              <span class="je-json-picker__header-name">{{ row.name }}</span>
              <span class="je-json-picker__header-value">{{ row.value }}</span>
              <CmkButton
                variant="optional"
                :disabled="headerSelected(row.path)"
                @click="emit('toggle', row.path, 'string', row.value)"
              >
                {{ headerSelected(row.path) ? _t('Monitored') : _t('Monitor') }}
              </CmkButton>
            </li>
          </ul>
        </CmkScrollContainer>
      </template>
      <CmkAlertBox v-else-if="parseError" variant="error" size="small">{{ _t('Invalid JSON: %{error}', { error: parseError }) }}</CmkAlertBox>
    </div>
    <div class="je-json-picker__actions">
      <CmkButton
        variant="optional"
        class="je-json-picker__refresh"
        :disabled="!canRefetch || loading"
        @click="emit('refetch')"
      >
        <CmkIcon name="reload" size="small" />
        {{ loading ? _t('Refreshing…') : _t('Refresh data') }}
      </CmkButton>
      <span v-if="error" class="je-json-picker__error">{{ error }}</span>
    </div>
  </div>
</template>

<style scoped>
.je-json-picker__bar {
  display: flex;
  flex-direction: column;
  gap: var(--dimension-3, 6px);
  align-items: flex-start;
  margin-bottom: var(--dimension-3, 6px);
}

/* The toggle group ships an 8px bottom margin that offsets it from the Fetch
   button in this centered row — drop it so the two controls line up. */
/* stylelint-disable-next-line checkmk/vue-bem-naming-convention */
.je-json-picker__bar :deep(.cmk-toggle-button-group__container) {
  margin-bottom: 0;
}

.je-json-picker__view {
  position: relative;
}

.je-json-picker__overlay {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgb(from var(--toggle-button-group-inactive-bg-color, #000) r g b / 65%);
  border-radius: 5px;
}

.je-json-picker__actions {
  display: flex;
  gap: var(--dimension-4, 8px);
  align-items: center;
  margin-top: var(--dimension-3, 6px);
}

.je-json-picker__refresh {
  gap: var(--dimension-3, 6px);
}

.je-json-picker__input {
  box-sizing: border-box;
  width: 100%;
  height: clamp(160px, 55vh, 640px);
  padding: var(--dimension-4, 8px);
  font: 12px/1.45 ui-monospace, monospace;
  color: var(--font-color);
  background: var(--toggle-button-group-inactive-bg-color);
  border: 1px solid var(--toggle-button-group-border-color);
  border-radius: 5px;
  resize: vertical;
}

.je-json-picker__tree {
  box-sizing: border-box;
  padding: var(--dimension-4, 8px);
  background: var(--toggle-button-group-inactive-bg-color);
  border: 1px solid var(--toggle-button-group-border-color);
  border-radius: 5px;
}

.je-json-picker__tree-list {
  width: max-content;
  min-width: 100%;
  padding: 0;
  margin: 0;
  list-style: none;
}

.je-json-picker__headers {
  padding: 0;
  margin: 0;
  list-style: none;
}

.je-json-picker__header-row {
  display: flex;
  gap: var(--dimension-4, 8px);
  align-items: center;
  padding: 1px var(--dimension-3, 6px);
  font-size: 12px;
  line-height: 1.6;
  border-radius: 4px;
}

.je-json-picker__header-row:hover {
  background: var(--default-form-element-bg-color);
}

.je-json-picker__header-name {
  font-family: ui-monospace, monospace;
  white-space: nowrap;
}

.je-json-picker__header-value {
  flex: 1;
  overflow: hidden;
  color: var(--font-color-dimmed);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.je-json-picker__error {
  color: var(--color-danger, #e05a5a);
}

.je-json-picker__hint {
  color: var(--font-color-dimmed);
}
</style>
