<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- One node of the JSON field picker; recurses on itself. Each leaf row reveals
     a single "+ Add" dropdown on hover/focus (Monitor / Host label) to keep a
     large tree uncluttered; a green dot marks already-monitored rows. -->
<script setup lang="ts">
import { computed, ref } from 'vue'

import usei18n from '@/lib/i18n'

import CmkIcon from '@/components/CmkIcon'

import { useClickOutside } from '../../composables/useClickOutside'
import type { Json, TreeNode } from '../../lib/jsonpaths'

const props = defineProps<{ node: TreeNode; selectedPaths: string[] }>()
const emit = defineEmits<{
  toggle: [path: string, valueType: string, sampleValue?: Json]
  hostlabel: [path: string]
}>()

const { _t } = usei18n()

const selected = computed(() => props.selectedPaths.includes(props.node.path))
const monitorLabel = computed(() =>
  selected.value ? _t('Do not monitor') : props.node.wildcard ? _t('Monitor each [*]') : _t('Monitor'),
)

// Native <details> dropdown for the "+ Add" menu — no custom open/close JS,
// plus dismiss on Escape / outside click while it is open.
const menuOpen = ref(false)
const actionsEl = ref<HTMLElement | null>(null)
useClickOutside(menuOpen, actionsEl, () => {
  menuOpen.value = false
})
function monitor(): void {
  menuOpen.value = false
  emit('toggle', props.node.path, props.node.valueType, props.node.sampleValue)
}
function hostLabel(): void {
  menuOpen.value = false
  emit('hostlabel', props.node.path)
}
</script>

<template>
  <li class="je-json-tree-node">
    <span class="je-json-tree-node__leaf" v-if="node.kind === 'leaf'">
      <span
        class="je-json-tree-node__dot"
        :class="{ 'je-json-tree-node__dot--on': selected }"
        :title="selected ? _t('Monitored') : ''"
      />
      <span
        class="je-json-tree-node__key"
        :class="{ 'je-json-tree-node__key--wild': node.wildcard }"
        >{{ node.label }}</span
      >
      <span class="je-json-tree-node__type">{{ node.valueType }}</span>
      <span class="je-json-tree-node__value">{{ node.valuePreview }}</span>
      <details
        ref="actionsEl"
        class="je-json-tree-node__actions"
        :open="menuOpen"
        @toggle="menuOpen = ($event.target as HTMLDetailsElement).open"
      >
        <summary class="je-json-tree-node__add">
          <CmkIcon name="plus" size="xsmall" />
          {{ _t('Add') }}
        </summary>
        <div class="je-json-tree-node__menu">
          <button type="button" @click="monitor">{{ monitorLabel }}</button>
          <button type="button" @click="hostLabel">{{ _t('Host label') }}</button>
        </div>
      </details>
    </span>
    <details v-else :open="node.kind !== 'array'">
      <summary>
        <span
          class="je-json-tree-node__key"
          :class="{ 'je-json-tree-node__key--wild': node.wildcard }"
          >{{ node.label }}</span
        >
        <span class="je-json-tree-node__type">{{ node.valueType }}</span>
        <!-- Wildcard maps (e.g. components[*]) can become one host label per key. -->
        <button
          v-if="node.wildcard"
          class="je-json-tree-node__add je-json-tree-node__hostbtn"
          type="button"
          :title="_t('One host label per element (json_api/…), no service needed')"
          @click.stop.prevent="emit('hostlabel', node.path)"
        >
          <CmkIcon name="plus" size="xsmall" />
          {{ _t('Host label') }}
        </button>
      </summary>
      <ul class="je-json-tree-node__children">
        <JsonTreeNode
          v-for="(child, i) in node.children"
          :key="i"
          :node="child"
          :selected-paths="selectedPaths"
          @toggle="(p, t, v) => emit('toggle', p, t, v)"
          @hostlabel="(p) => emit('hostlabel', p)"
        />
      </ul>
    </details>
  </li>
</template>

<style scoped>
.je-json-tree-node {
  /* Base font is the app default (Inter); only the JSON content spans below are
     monospace, so the inline buttons render like a normal CmkButton. */
  font-size: 12px;
  line-height: 1.6;
  list-style: none;
}

.je-json-tree-node__children {
  padding-left: var(--dimension-6, 16px);
  list-style: none;
}

/* Object / array rows (the expandable <details> directly under the li) get the
   same full-line hover highlight as leaf rows. */
.je-json-tree-node > details > summary {
  padding: 1px var(--dimension-3, 6px);
  border-radius: 4px;
  cursor: pointer;
}

.je-json-tree-node > details > summary:hover {
  background: rgb(127 127 127 / 16%);
}

/* "+ host label" on a wildcard map row, revealed on hover / focus like leaves. */
.je-json-tree-node__hostbtn {
  margin-left: var(--dimension-4, 8px);
  visibility: hidden;
}

.je-json-tree-node > details > summary:hover .je-json-tree-node__hostbtn,
.je-json-tree-node > details > summary:focus-within .je-json-tree-node__hostbtn {
  visibility: visible;
}

.je-json-tree-node__leaf {
  display: flex;
  gap: var(--dimension-4, 8px);
  align-items: center;
  padding: 1px var(--dimension-3, 6px);
  white-space: nowrap;
  border-radius: 4px;
}

/* Full-line hover highlight (works in both themes). */
.je-json-tree-node__leaf:hover {
  background: rgb(127 127 127 / 16%);
}

/* Persistent marker: a green dot on already-monitored rows. */
.je-json-tree-node__dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: transparent;
}

.je-json-tree-node__dot--on {
  background: var(--success);
}

.je-json-tree-node__key,
.je-json-tree-node__type,
.je-json-tree-node__value {
  font-family: ui-monospace, monospace;
}

.je-json-tree-node__key {
  color: var(--color-corporate-blue-50, #7fb3ff);
}

.je-json-tree-node__key--wild {
  color: var(--color-corporate-orange-50, #e0b030);
}

.je-json-tree-node__type {
  color: var(--font-color-dimmed);
}

.je-json-tree-node__value {
  color: var(--success);
}

/* The "+ Add" dropdown, revealed only on row hover / keyboard focus. */
.je-json-tree-node__actions {
  position: relative;
  margin-left: auto;
  visibility: hidden;
}

.je-json-tree-node__leaf:hover .je-json-tree-node__actions,
.je-json-tree-node__leaf:focus-within .je-json-tree-node__actions {
  visibility: visible;
}

.je-json-tree-node__add {
  display: inline-flex;
  gap: var(--dimension-3, 6px);
  align-items: center;
  box-sizing: border-box;
  height: 20px;
  padding: 0 var(--dimension-4, 8px);
  font-size: 12px;
  font-weight: bold;
  line-height: normal;
  letter-spacing: unset;
  color: var(--font-color-dimmed);
  cursor: pointer;
  list-style: none;
  background: transparent;
  border: 1px solid var(--default-border-color);
  border-radius: 4px;
}

.je-json-tree-node__add::-webkit-details-marker {
  display: none;
}

.je-json-tree-node__add:hover {
  color: var(--font-color);
  border-color: var(--success);
}

.je-json-tree-node__menu {
  position: absolute;
  right: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  margin-top: 2px;
  background: var(--default-bg-color);
  border: 1px solid var(--default-border-color);
  border-radius: 4px;
  box-shadow: 0 2px 6px rgb(0 0 0 / 25%);
}

.je-json-tree-node__menu button {
  padding: var(--dimension-3, 6px) var(--dimension-5, 12px);
  font-size: 12px;
  color: var(--font-color);
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
  background: transparent;
  border: 0;
}

.je-json-tree-node__menu button:hover {
  color: var(--success);
  background: rgb(127 127 127 / 16%);
}
</style>
