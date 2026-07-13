<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- One node of the JSON field picker; recurses on itself. Emits the JSON path
     to toggle (monitor / stop monitoring) when the inline button is clicked. -->
<script setup lang="ts">
import { computed } from 'vue'

import usei18n from '@/lib/i18n'

import CmkIcon from '@/components/CmkIcon'

import type { Json, TreeNode } from '../../lib/jsonpaths'

const props = defineProps<{ node: TreeNode; selectedPaths: string[] }>()
const emit = defineEmits<{ toggle: [path: string, valueType: string, sampleValue?: Json] }>()

const { _t } = usei18n()

const selected = computed(() => props.selectedPaths.includes(props.node.path))
const buttonLabel = computed(() =>
  selected.value ? _t('do not monitor') : props.node.wildcard ? _t('monitor each [*]') : _t('monitor'),
)
</script>

<template>
  <li class="je-json-tree-node">
    <span v-if="node.kind === 'leaf'" class="je-json-tree-node__leaf">
      <span
        class="je-json-tree-node__key"
        :class="{ 'je-json-tree-node__key--wild': node.wildcard }"
        >{{ node.label }}</span
      >
      <span class="je-json-tree-node__type">{{ node.valueType }}</span>
      <span class="je-json-tree-node__value">{{ node.valuePreview }}</span>
      <button
        class="je-json-tree-node__add"
        :class="{ 'je-json-tree-node__add--selected': selected }"
        type="button"
        @click="emit('toggle', node.path, node.valueType, node.sampleValue)"
      >
        <CmkIcon :name="selected ? 'hyphen' : 'plus'" size="xsmall" />
        {{ buttonLabel }}
      </button>
    </span>
    <details v-else :open="node.kind !== 'array'">
      <summary>
        <span
          class="je-json-tree-node__key"
          :class="{ 'je-json-tree-node__key--wild': node.wildcard }"
          >{{ node.label }}</span
        >
        <span class="je-json-tree-node__type">{{ node.valueType }}</span>
      </summary>
      <ul class="je-json-tree-node__children">
        <JsonTreeNode
          v-for="(child, i) in node.children"
          :key="i"
          :node="child"
          :selected-paths="selectedPaths"
          @toggle="(p, t, v) => emit('toggle', p, t, v)"
        />
      </ul>
    </details>
  </li>
</template>

<style scoped>
.je-json-tree-node {
  font: 12px/1.6 ui-monospace, monospace;
  list-style: none;
}

.je-json-tree-node__children {
  padding-left: var(--dimension-6, 16px);
  list-style: none;
}

.je-json-tree-node__leaf {
  display: inline-flex;
  gap: var(--dimension-4, 8px);
  align-items: center;
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

.je-json-tree-node__add {
  display: inline-flex;
  gap: var(--dimension-3, 6px);
  align-items: center;
  padding: 0 var(--dimension-4, 8px);
  color: var(--font-color-dimmed);
  cursor: pointer;
  background: transparent;
  border: 1px solid var(--default-border-color);
  border-radius: 4px;
}

.je-json-tree-node__add:hover {
  color: var(--font-color);
  border-color: var(--success);
}

.je-json-tree-node__add--selected {
  color: var(--success);
  border-color: var(--success);
}
</style>
